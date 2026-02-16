"""Webhook receiver for @agent triggers.

A lightweight FastAPI app that receives webhooks from:
  1. Azure DevOps service hooks (work item comment with @agent)
  2. Microsoft Teams bot / incoming webhook (@agent mention)
  3. Generic HTTP POST (any external caller)

On receiving a valid trigger, it calls the Azure DevOps Pipeline
REST API to queue a run of the webhook-trigger pipeline.

Deployment options:
  - Azure Container App (recommended — serverless, scales to zero)
  - Azure Functions (via ASGI adapter)
  - Any container host
"""

from __future__ import annotations

import html
import json
import logging
import re
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from src.config import get_settings

logger = logging.getLogger(__name__)

app = FastAPI(
    title="DevOps Agent Webhook Receiver",
    description="Receives @agent mentions from Azure DevOps and MS Teams, triggers the agent pipeline.",
    version="0.1.0",
)

# ── Models ───────────────────────────────────────────────────────


class TriggerResponse(BaseModel):
    status: str = "accepted"
    pipeline_run_id: int | None = None
    work_item_id: int | None = None
    trigger_source: str = ""
    message: str = ""


class ManualTriggerRequest(BaseModel):
    """Manual trigger via POST body."""

    work_item_id: int
    request_type: str = Field(default="investigation")
    additional_context: str = Field(default="")
    report_only: bool = Field(default=False)


# ── Pipeline trigger ─────────────────────────────────────────────

AGENT_MENTION_PATTERN = re.compile(r"@agent\b", re.IGNORECASE)


async def _trigger_pipeline(
    work_item_id: int,
    request_type: str = "investigation",
    additional_context: str = "",
    trigger_source: str = "webhook",
    report_only: bool = False,
) -> int | None:
    """Queue a run of the webhook-trigger pipeline via Azure DevOps REST API.

    Returns the pipeline run ID, or None on failure.
    """
    settings = get_settings()
    base = settings.azure_devops_org_url.rstrip("/")
    project = settings.azure_devops_project

    # Pipeline ID must be set in env (the ID of webhook-trigger.yml pipeline)
    import os

    pipeline_id = os.environ.get("AGENT_PIPELINE_ID")
    if not pipeline_id:
        logger.error("AGENT_PIPELINE_ID env var not set — cannot trigger pipeline")
        return None

    url = f"{base}/{project}/_apis/pipelines/{pipeline_id}/runs?api-version=7.1"

    import base64 as b64

    token = b64.b64encode(f":{settings.azure_devops_pat}".encode()).decode()

    body = {
        "templateParameters": {
            "workItemId": str(work_item_id),
            "requestType": request_type,
            "additionalContext": additional_context,
            "triggerSource": trigger_source,
            "reportOnly": str(report_only).lower(),
        },
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            json=body,
            headers={
                "Authorization": f"Basic {token}",
                "Content-Type": "application/json",
            },
        )

    if resp.status_code in (200, 201):
        run_id = resp.json().get("id")
        logger.info("Triggered pipeline run #%s for WI #%d", run_id, work_item_id)
        return run_id
    else:
        logger.error("Pipeline trigger failed: %s %s", resp.status_code, resp.text[:300])
        return None


# ── Azure DevOps Service Hook ────────────────────────────────────


def _parse_devops_webhook(payload: dict[str, Any]) -> tuple[int | None, str]:
    """Extract work item ID and @agent context from a DevOps service hook payload.

    Azure DevOps sends service hook events for work item comments.
    The event type is "workitem.commented" and the payload contains
    the comment text and work item ID.
    """
    resource = payload.get("resource", {})

    # workitem.commented event
    work_item_id = resource.get("id") or resource.get("workItemId")

    # Try to get comment text
    comment_text = ""
    revision = resource.get("revision", {})
    fields = revision.get("fields", {}) if revision else resource.get("fields", {})
    comment_text = fields.get("System.History", "")

    if not comment_text:
        # Fallback: check in the resource directly
        comment_text = resource.get("text", "")

    # Strip HTML if present
    comment_text = re.sub(r"<[^>]+>", " ", html.unescape(comment_text)).strip()

    # Extract context after @agent
    context = ""
    match = AGENT_MENTION_PATTERN.search(comment_text)
    if match:
        context = comment_text[match.end() :].strip()

    return work_item_id, context


@app.post("/webhooks/devops", response_model=TriggerResponse)
async def handle_devops_webhook(request: Request) -> TriggerResponse:
    """Handle Azure DevOps service hook (work item comment with @agent)."""
    payload = await request.json()
    event_type = payload.get("eventType", "")

    logger.info("DevOps webhook received: eventType=%s", event_type)

    # Only process comment events
    if "comment" not in event_type.lower() and "updated" not in event_type.lower():
        return TriggerResponse(
            status="ignored",
            message=f"Event type '{event_type}' not handled",
        )

    work_item_id, context = _parse_devops_webhook(payload)
    if not work_item_id:
        raise HTTPException(status_code=400, detail="Could not extract work item ID")

    # Check for @agent mention — only trigger if mentioned
    raw_text = json.dumps(payload.get("resource", {}))
    if not AGENT_MENTION_PATTERN.search(raw_text):
        return TriggerResponse(
            status="ignored",
            work_item_id=work_item_id,
            message="No @agent mention found in comment",
        )

    run_id = await _trigger_pipeline(
        work_item_id=work_item_id,
        additional_context=context,
        trigger_source="devops_mention",
    )

    return TriggerResponse(
        status="accepted" if run_id else "error",
        pipeline_run_id=run_id,
        work_item_id=work_item_id,
        trigger_source="devops_mention",
        message=f"Pipeline run #{run_id} queued" if run_id else "Failed to trigger pipeline",
    )


# ── MS Teams Webhook ─────────────────────────────────────────────


def _parse_teams_payload(payload: dict[str, Any]) -> tuple[int | None, str]:
    """Extract work item ID and context from an MS Teams bot / webhook payload.

    MS Teams sends an Activity object. The text field contains the
    message with @agent mention. We look for work item references
    like #1234 or WI-1234.
    """
    text = payload.get("text", "")

    # Strip the bot mention markup from Teams
    text = re.sub(r"<at>[^<]*</at>", "", text).strip()

    # Try to find a work item ID: #1234, WI-1234, or just a bare number
    wi_match = re.search(r"#(\d+)|WI[- ]?(\d+)|work\s*item\s*(\d+)", text, re.IGNORECASE)
    work_item_id = None
    if wi_match:
        work_item_id = int(next(g for g in wi_match.groups() if g))

    # Context is everything after @agent
    context = text
    agent_match = AGENT_MENTION_PATTERN.search(text)
    if agent_match:
        context = text[agent_match.end() :].strip()

    return work_item_id, context


@app.post("/webhooks/teams", response_model=TriggerResponse)
async def handle_teams_webhook(request: Request) -> TriggerResponse:
    """Handle MS Teams bot / incoming webhook (@agent mention in Teams)."""
    payload = await request.json()

    logger.info("Teams webhook received")

    work_item_id, context = _parse_teams_payload(payload)
    if not work_item_id:
        return TriggerResponse(
            status="error",
            trigger_source="teams_mention",
            message="Could not find a work item reference (use #1234 or WI-1234)",
        )

    run_id = await _trigger_pipeline(
        work_item_id=work_item_id,
        additional_context=context,
        trigger_source="teams_mention",
    )

    return TriggerResponse(
        status="accepted" if run_id else "error",
        pipeline_run_id=run_id,
        work_item_id=work_item_id,
        trigger_source="teams_mention",
        message=f"Pipeline run #{run_id} queued" if run_id else "Failed to trigger pipeline",
    )


# ── Manual / Generic Webhook ────────────────────────────────────


@app.post("/webhooks/trigger", response_model=TriggerResponse)
async def handle_manual_trigger(body: ManualTriggerRequest) -> TriggerResponse:
    """Manually trigger the agent via a simple POST request."""
    run_id = await _trigger_pipeline(
        work_item_id=body.work_item_id,
        request_type=body.request_type,
        additional_context=body.additional_context,
        trigger_source="webhook",
        report_only=body.report_only,
    )

    return TriggerResponse(
        status="accepted" if run_id else "error",
        pipeline_run_id=run_id,
        work_item_id=body.work_item_id,
        trigger_source="webhook",
        message=f"Pipeline run #{run_id} queued" if run_id else "Failed to trigger pipeline",
    )


# ── Health check ─────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "devops-agent-webhook-receiver"}
