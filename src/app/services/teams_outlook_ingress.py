from __future__ import annotations

import hashlib
from typing import Any

from app.models.schemas import BotActivityPayload, GraphNotificationItem


def normalize_graph_notification_to_invocation(item: GraphNotificationItem) -> dict[str, Any]:
    """
    Normalizes a Microsoft Graph Change Notification from either MS Teams (`/teams/.../messages`)
    or Outlook (`/users/.../messages`) into a LangGraph invocation payload.
    """
    resource_lower = item.resource.lower()
    is_teams = "teams/" in resource_lower or "chats/" in resource_lower
    channel_origin = "ms_teams" if is_teams else "outlook"

    raw_id = f"{item.subscriptionId}:{item.resource}:{item.changeType}"
    event_key = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16]
    thread_id = f"{channel_origin}-{hashlib.sha256(item.resource.encode('utf-8')).hexdigest()[:12]}"

    subject_or_text = (
        item.resourceData.get("subject")
        or item.resourceData.get("bodyPreview")
        or f"Process {channel_origin} request on resource {item.resource}"
    )
    actor = item.resourceData.get("sender") or f"{channel_origin}-trigger@contoso.com"

    return {
        "thread_id": thread_id,
        "event_key": event_key,
        "channel_origin": channel_origin,
        "actor_upn": actor,
        "prompt": str(subject_or_text),
        "repository": "dbt-snowflake-analytics",
        "dbt_model_name": "mart_customer_churn",
        "source_branch": "refs/heads/main",
        "target_branch": "refs/heads/feat/dbt-customer-churn",
        "operation": "create_pbi_and_pr",
    }


def parse_bot_activity(activity: BotActivityPayload) -> dict[str, Any]:
    """
    Normalizes an Azure Bot Service (`MsTeamsChannel` / `OutlookChannel`) Activity:
      - If `activity.type == "invoke"` or `activity.value` contains a HITL `decision`
        (`approve` / `reject`), returns `kind="hitl_callback"` so FastAPI resumes the graph.
      - Otherwise returns `kind="new_message"` to start/continue a LangGraph thread.
    """
    conv_id = activity.conversation.get("id", "teams-conv-default")
    thread_id = f"teams-{hashlib.sha256(conv_id.encode('utf-8')).hexdigest()[:12]}"
    actor_upn = (
        activity.from_property.get("userPrincipalName")
        or activity.from_property.get("name")
        or "victor.vos@contoso.com"
    )

    if activity.value and "decision" in activity.value:
        return {
            "kind": "hitl_callback",
            "thread_id": activity.value.get("thread_id", thread_id),
            "run_id": activity.value.get("run_id", "run-bot"),
            "action_digest": activity.value.get("action_digest", ""),
            "decision": activity.value["decision"],
            "actor_upn": actor_upn,
            "channel_origin": "ms_teams",
        }

    prompt = (activity.text or "Add churn risk KPI to mart_customer_churn dbt model").strip()
    event_key = hashlib.sha256(f"{activity.id}:{conv_id}".encode("utf-8")).hexdigest()[:16]
    return {
        "kind": "new_message",
        "thread_id": thread_id,
        "event_key": event_key,
        "channel_origin": "ms_teams",
        "actor_upn": actor_upn,
        "prompt": prompt,
        "repository": "dbt-snowflake-analytics",
        "dbt_model_name": "mart_customer_churn",
        "source_branch": "refs/heads/main",
        "target_branch": "refs/heads/feat/dbt-customer-churn",
        "operation": "create_pbi_and_pr",
    }
