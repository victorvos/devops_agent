from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class GraphNotificationItem(BaseModel):
    subscriptionId: str
    changeType: str = "created"
    resource: str
    clientState: str | None = None
    lifecycleEvent: str | None = None
    resourceData: dict[str, Any] = Field(default_factory=dict)
    encryptedContent: dict[str, Any] | None = None
    tenantId: str | None = None


class GraphWebhookPayload(BaseModel):
    value: list[GraphNotificationItem] = Field(default_factory=list)
    validationTokens: list[str] | None = None


class BotActivityPayload(BaseModel):
    """
    Azure Bot Framework Activity payload sent when a user @mentions the bot in MS Teams
    or clicks an Adaptive Card `Action.Execute` button in Teams.
    """

    type: str = "message"  # "message" | "invoke"
    id: str = "act-001"
    name: str | None = None  # e.g., "adaptiveCard/action"
    text: str | None = None
    value: dict[str, Any] | None = None
    from_property: dict[str, Any] = Field(default_factory=dict, alias="from")
    conversation: dict[str, Any] = Field(default_factory=dict)
    serviceUrl: str | None = None
    channelId: str = "msteams"


class DevInvokeRequest(BaseModel):
    """Payload used from a whitelisted corporate DevBox to trigger or inspect a LangGraph thread."""

    thread_id: str = Field(..., description="Conversation or mail thread identifier.")
    channel_origin: Literal["ms_teams", "outlook", "devbox"] = "devbox"
    actor_upn: str = Field(default="victor.vos@contoso.com")
    prompt: str = Field(..., description="Engineering instruction, business problem, or analytics query.")
    repository: str = Field(default="dbt-snowflake-analytics")
    dbt_model_name: str = Field(default="mart_customer_churn")
    source_branch: str = Field(default="refs/heads/main")
    target_branch: str = Field(default="refs/heads/feat/dbt-customer-churn")
    operation: Literal[
        "create_pbi_and_pr",
        "query_plan",
        "create_pr",
        "create_branch",
        "trigger_pipeline",
        "read_only",
    ] = "create_pbi_and_pr"


class QueryPlanRequest(BaseModel):
    """
    Request schema for converting a natural-language question into a governed
    dbt + Snowflake query plan (`POST /api/v1/snowflake/query-plan`).
    """

    prompt: str = Field(..., description="Natural-language analytics or data question.")
    dbt_model_name: str = Field(default="mart_customer_churn", description="Target dbt model or mart.")
    limit: int = Field(default=100, ge=1, le=10000)


class HitlCallbackRequest(BaseModel):
    """Callback payload returned when an engineer clicks Approve or Reject on the Teams or Outlook Adaptive Card."""

    thread_id: str
    run_id: str
    action_digest: str
    decision: Literal["approve", "reject"]
    channel_origin: Literal["ms_teams", "outlook", "devbox"] = "ms_teams"
    reviewer_comment: str | None = None
