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

from src.agent.graph import build_graph
from src.agent.state import AgentState
from src.clients.devops import AzureDevOpsClient
from src.clients.llm import get_chat_model
from src.config import get_settings

logger = logging.getLogger(__name__)

# In-process job store shared with the API (imported lazily to avoid
# circular imports when the worker is started independently).
_job_store: dict[str, dict[str, Any]] | None = None


def _get_job_store() -> dict[str, dict[str, Any]]:
    global _job_store
    if _job_store is None:
        try:
            from src.api import _job_store as api_store

            _job_store = api_store
        except ImportError:
            _job_store = {}
    return _job_store


def _update_job(job_id: str, **fields: Any) -> None:
    store = _get_job_store()
    if job_id in store:
        store[job_id].update(fields)


async def _process_message(payload: dict[str, Any]) -> None:
    """Run the LangGraph agent for a single queued request."""
    job_id: str = payload["job_id"]
    work_item_id: int = payload["work_item_id"]
    request_type: str = payload.get("request_type", "investigation")
    context: str = payload.get("context", "")
    report_only: bool = payload.get("report_only", True)

    logger.info("Processing job %s — WI #%d (%s)", job_id, work_item_id, request_type)
    _update_job(job_id, status="processing")

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

        result = await graph.ainvoke(initial_state)

        state = AgentState(**result) if isinstance(result, dict) else result

        _update_job(
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

    except Exception as exc:
        logger.exception("Job %s failed: %s", job_id, exc)
        _update_job(
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
