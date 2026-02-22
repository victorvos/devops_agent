"""Service Bus queue consumer that runs the LangGraph agent.

Picks up messages from the ``agent-requests`` queue, executes the agent,
and posts results as a work item comment (append-only).  Failed messages
are abandoned and eventually land in the dead-letter queue after the
Service Bus max-delivery-count is exceeded (default 3).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.infrastructure.agent.graph import build_graph
from src.core.agent.state import AgentState
from src.infrastructure.clients.devops import AzureDevOpsClient
from src.infrastructure.clients.llm import get_chat_model
from src.core.config import get_settings

from src.core.interfaces.job_store import JobStore

logger = logging.getLogger(__name__)

# In-process job store shared with the API (imported lazily to avoid
# circular imports when the worker is started independently).
_job_store: JobStore | None = None


async def _get_job_store() -> JobStore:
    global _job_store
    if _job_store is None:
        try:
            from src.presentation.api import _job_store as api_store
            _job_store = api_store  # type: ignore
        except ImportError:
            pass
            
        if _job_store is None:
            settings = get_settings()
            if settings.azure_table_connection_str:
                from src.infrastructure.repositories.job_store import AzureTableJobStore
                _job_store = AzureTableJobStore(settings.azure_table_connection_str)
            else:
                from src.infrastructure.repositories.job_store import InMemoryJobStore
                _job_store = InMemoryJobStore()
            await _job_store.initialize()
    return _job_store


async def _update_job(job_id: str, **fields: Any) -> None:
    store = await _get_job_store()
    await store.update_job(job_id, **fields)


async def _process_message(payload: dict[str, Any]) -> None:
    """Run the LangGraph agent for a single queued request."""
    job_id: str = payload["job_id"]
    work_item_id: int = payload["work_item_id"]
    request_type: str = payload.get("request_type", "investigation")
    context: str = payload.get("context", "")
    report_only: bool = payload.get("report_only", True)

    logger.info("Processing job %s — WI #%d (%s)", job_id, work_item_id, request_type)
    await _update_job(job_id, status="processing")

    settings = get_settings()
    devops = AzureDevOpsClient(settings)
    llm = get_chat_model(settings)

    try:
        graph = build_graph(settings, devops, llm)

        initial_state = AgentState(
            work_item_id=work_item_id,
            request_text=context,
            request_type=request_type,
        )
        if report_only:
            initial_state.max_iterations = 1

        # Wait up to 5 minutes (300 seconds) for the agent to complete
        result = await asyncio.wait_for(graph.ainvoke(initial_state), timeout=300.0)

        state = AgentState(**result) if isinstance(result, dict) else result

        await _update_job(
            job_id,
            status="completed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            result={
                "summary": state.output_summary,
                "branch": state.output_branch,
                "pr_url": state.output_pr_url,
                "files_reviewed": len(state.planned_files),
            },
        )
        logger.info("Job %s completed for WI #%d", job_id, work_item_id)

    except asyncio.TimeoutError:
        logger.error("Job %s timed out after 5 minutes", job_id)
        await _update_job(
            job_id,
            status="failed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            error="Agent invocation timed out after 5 minutes.",
        )
        raise
    except Exception as exc:
        logger.exception("Job %s failed: %s", job_id, exc)
        await _update_job(
            job_id,
            status="failed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            error=str(exc),
        )
        raise

    finally:
        await devops.close()


async def run_worker() -> None:
    """Long-running loop that consumes messages from Service Bus."""
    from azure.servicebus.aio import ServiceBusClient

    settings = get_settings()

    if not settings.service_bus_connection_str:
        raise RuntimeError("SERVICE_BUS_CONNECTION_STR must be set to run the worker")

    logger.info(
        "Starting Service Bus worker (queue=%s)",
        settings.service_bus_queue_name,
    )

    async with ServiceBusClient.from_connection_string(
        settings.service_bus_connection_str,
    ) as sb_client:
        receiver = sb_client.get_queue_receiver(
            queue_name=settings.service_bus_queue_name,
            max_wait_time=30,
        )

        async with receiver:
            while True:
                messages = await receiver.receive_messages(
                    max_message_count=1,
                    max_wait_time=30,
                )

                if not messages:
                    continue

                msg = messages[0]
                try:
                    payload = json.loads(str(msg))
                    await _process_message(payload)
                    await receiver.complete_message(msg)
                except Exception:
                    logger.exception("Message processing failed — abandoning for retry")
                    await receiver.abandon_message(msg)


def main() -> None:
    """Entry point for ``python -m src.worker``."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
