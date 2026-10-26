from __future__ import annotations

from typing import Any, Literal, TypedDict


class OrchestratorState(TypedDict, total=False):
    """
    Durable state persisted in Snowflake (`AGENT_STATE_DB.LANGGRAPH.THREAD_CHECKPOINTS`)
    or Azure PostgreSQL (`langgraph-checkpoint-postgres`).
    """

    thread_id: str
    run_id: str
    channel_origin: Literal["ms_teams", "outlook", "devbox"]
    actor_upn: str
    event_key: str
    processed_event_keys: list[str]
    is_duplicate_event: bool

    prompt: str
    repository: str
    dbt_model_name: str
    source_branch: str
    target_branch: str
    operation: Literal[
        "create_pbi_and_pr",
        "query_plan",
        "create_pr",
        "create_branch",
        "trigger_pipeline",
        "read_only",
    ]

    grounded_citations: list[dict[str, Any]]
    m365_copilot_citations: list[dict[str, Any]]
    snowflake_dbt_metadata: dict[str, Any]

    query_plan_result: dict[str, Any] | None
    proposed_plan: dict[str, Any]
    adaptive_card_payload: dict[str, Any]

    approval_decision: Literal["pending", "approve", "reject", "not_required"]
    approved_by: str | None
    execution_result: dict[str, Any] | None
    status: str
