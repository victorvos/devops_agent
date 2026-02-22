"""FastAPI application for the Azure DevOps Agent.

Two operating modes:
  - **Direct** (no Service Bus): requests are processed in-process via
    a background task.  Lightweight, no extra infrastructure.
  - **Queued** (Service Bus configured): requests are enqueued and
    processed by the worker.  Better for high volume / reliability.

Endpoints:
  POST /api/investigate  — submit a work item investigation
  GET  /api/status/{id}  — poll for job result
  GET  /health           — liveness probe
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from pydantic import BaseModel, Field

from src.core.config import get_settings

from src.core.interfaces.job_store import JobStore

logger = logging.getLogger(__name__)

_job_store: JobStore | None = None

_sb_client: Any | None = None


def _configure_logging() -> None:
    """Configure logging from LOG_LEVEL so Container App and serve use it for errors and debug flow."""
    from src.core.config import get_settings

    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=level, format=fmt, datefmt="%Y-%m-%dT%H:%M:%SZ", force=True)
    # Ensure our log level is applied (basicConfig may have been set elsewhere)
    logging.getLogger("src").setLevel(level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _sb_client, _job_store
    _configure_logging()
    settings = get_settings()

    if settings.azure_table_connection_str:
        from src.infrastructure.repositories.job_store import AzureTableJobStore
        _job_store = AzureTableJobStore(settings.azure_table_connection_str)
        logger.info("Using Azure Table Storage for job state")
    else:
        from src.infrastructure.repositories.job_store import InMemoryJobStore
        _job_store = InMemoryJobStore()
        logger.info("Using in-memory store for job state")

    await _job_store.initialize()

    if settings.service_bus_connection_str:
        try:
            from azure.servicebus.aio import ServiceBusClient  # pyright: ignore[reportMissingImports]

            _sb_client = ServiceBusClient.from_connection_string(
                settings.service_bus_connection_str,
            )
            logger.info("Service Bus configured (queue=%s) — queued mode", settings.service_bus_queue_name)
        except ImportError:
            logger.warning("SERVICE_BUS_CONNECTION_STR set but azure-servicebus not installed — running in direct mode")
            _sb_client = None
    else:
        logger.info("No Service Bus configured — running in direct mode")

    yield

    if _sb_client:
        await _sb_client.close()
    
    if _job_store:
        await getattr(_job_store, "close", lambda: None)()


app = FastAPI(
    title="Azure DevOps Agent",
    description="Targeted-retrieval AI agent for Azure DevOps",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next: Any) -> Any:
    """Add standard security headers to all responses."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = "default-src 'self'"
    return response


# ── Request / Response models ────────────────────────────────────

class InvestigateRequest(BaseModel):
    work_item_id: int = Field(..., description="Azure DevOps work item ID")
    request_type: str = Field(default="investigation", description="investigation | feature_request | bug")
    context: str = Field(default="", description="Extra context for the agent")
    report_only: bool = Field(default=True, description="When true, only post a comment (no branch/PR)")


class InvestigateResponse(BaseModel):
    job_id: str
    status: str
    message: str


class JobStatus(BaseModel):
    job_id: str
    status: str
    created_at: str
    completed_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


# ── Direct processing (no Service Bus) ───────────────────────────

async def _run_agent_direct(job_id: str, payload: dict[str, Any]) -> None:
    """Run the agent in-process as a background task."""
    from src.presentation.worker import _process_message

    try:
        await _process_message(payload)
    except Exception:
        logger.exception("Direct processing failed for job %s", job_id)


# ── Endpoints ────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.post("/api/investigate", response_model=InvestigateResponse)
async def investigate(body: InvestigateRequest, background_tasks: BackgroundTasks) -> InvestigateResponse:
    """Accept an investigation request.

    If Service Bus is configured, enqueues the message for the worker.
    Otherwise, processes it directly in a background task.
    """
    job_id = uuid.uuid4().hex

    payload = {
        "job_id": job_id,
        "work_item_id": body.work_item_id,
        "request_type": body.request_type,
        "context": body.context,
        "report_only": body.report_only,
    }

    if _job_store:
        await _job_store.create_job(job_id, payload)

    if _sb_client:
        import json

        from azure.servicebus import ServiceBusMessage  # pyright: ignore[reportMissingImports]

        settings = get_settings()
        async with _sb_client.get_queue_sender(settings.service_bus_queue_name) as sender:
            msg = ServiceBusMessage(
                body=json.dumps(payload),
                content_type="application/json",
                subject=f"wi-{body.work_item_id}",
                application_properties={"job_id": job_id},
            )
            await sender.send_messages(msg)
        logger.info("Enqueued job %s for WI #%d", job_id, body.work_item_id)
        return InvestigateResponse(job_id=job_id, status="queued", message="Job enqueued for processing")

    background_tasks.add_task(_run_agent_direct, job_id, payload)
    logger.info("Started direct processing for job %s (WI #%d)", job_id, body.work_item_id)
    return InvestigateResponse(job_id=job_id, status="processing", message="Job started (direct mode)")


@app.get("/api/status/{job_id}", response_model=JobStatus)
async def job_status(job_id: str) -> JobStatus:
    """Poll for the status of a previously submitted job."""
    if not _job_store:
        raise HTTPException(status_code=500, detail="Job store not initialized")

    job = await _job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return JobStatus(
        job_id=job_id,
        status=job.get("status", "unknown"),
        created_at=job.get("created_at", ""),
        completed_at=job.get("completed_at"),
        result=job.get("result"),
        error=job.get("error"),
    )
