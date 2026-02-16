"""Pipeline trigger client.

Provides helpers to trigger the agent pipeline via the Azure DevOps
Pipelines REST API. Used by:
  - The CLI (`devops-agent trigger` command)
  - Azure DevOps service hooks (parsed in the pipeline itself)
  - MS Teams Power Automate / Logic App flows

This is just an API call with credentials — no server needed.
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Any

import httpx

from src.config import Settings

logger = logging.getLogger(__name__)

AGENT_MENTION_PATTERN = re.compile(r"@agent\b", re.IGNORECASE)


class PipelineTrigger:
    """Trigger the agent pipeline via Azure DevOps REST API."""

    def __init__(self, settings: Settings, pipeline_id: int) -> None:
        self._settings = settings
        self._pipeline_id = pipeline_id
        self._base = settings.azure_devops_org_url.rstrip("/")
        self._project = settings.azure_devops_project

        token = base64.b64encode(f":{settings.azure_devops_pat}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }

    async def trigger(
        self,
        work_item_id: int,
        request_type: str = "investigation",
        additional_context: str = "",
        trigger_source: str = "api",
        report_only: bool = False,
    ) -> dict[str, Any]:
        """Queue a pipeline run with the given parameters.

        Returns the pipeline run response dict (contains 'id', 'url', etc.).
        """
        url = (
            f"{self._base}/{self._project}"
            f"/_apis/pipelines/{self._pipeline_id}/runs"
            f"?api-version=7.1"
        )

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
            resp = await client.post(url, json=body, headers=self._headers)

        resp.raise_for_status()
        result = resp.json()
        logger.info("Triggered pipeline run #%s for WI #%d", result.get("id"), work_item_id)
        return result


def parse_agent_mention(text: str) -> str | None:
    """Extract the context text after an @agent mention.

    Returns the text after @agent, or None if no mention found.
    """
    match = AGENT_MENTION_PATTERN.search(text)
    if not match:
        return None
    return text[match.end():].strip()


def extract_work_item_id(text: str) -> int | None:
    """Extract a work item ID from text.

    Supports: #1234, WI-1234, WI 1234, "work item 1234".
    """
    match = re.search(r"#(\d+)|WI[- ]?(\d+)|work\s*item\s*(\d+)", text, re.IGNORECASE)
    if not match:
        return None
    return int(next(g for g in match.groups() if g))
