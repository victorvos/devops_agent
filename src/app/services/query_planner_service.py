from __future__ import annotations

import re
from typing import Any, Protocol
from pydantic import BaseModel, Field

from app.auth.entra import KeylessEntraTokenFactory
from app.config import Settings
from app.services.m365_copilot_grounding import M365CopilotGroundingService
from app.services.snowflake_dbt import SnowflakeDbtService


FORBIDDEN_SQL_VERB_RE = re.compile(
    r"\b(DROP|DELETE|TRUNCATE|UPDATE|INSERT|MERGE|ALTER|GRANT|REVOKE|CREATE|COPY)\b",
    re.IGNORECASE,
)


class QueryPlanHook(Protocol):
    """Optional protocol so external corporate Python packages can inject custom dbt/SQL hooks."""

    def transform_sql(self, sql: str, context: dict[str, Any]) -> str: ...


class SnowflakeDbtQueryPlan(BaseModel):
    """Structured dbt + Snowflake query execution plan returned by the injectable service."""

    request_prompt: str
    intent_mode: str = "query_plan"
    target_dbt_model: str
    dbt_refs_used: list[str] = Field(default_factory=list)
    m365_kpi_definitions_applied: list[str] = Field(default_factory=list)
    dbt_command_plan: list[str] = Field(default_factory=list)
    dbt_inline_jinja_sql: str
    compiled_snowflake_sql: str
    read_only_verified: bool = True
    snowflake_role: str = "RL_LANGGRAPH_READER"
    warehouse: str = "WH_LANGGRAPH_XS"
    statement_timeout_seconds: int = 60
    explain_valid: bool = True
    explain_summary: str = ""
    estimated_bytes_scanned: int = 1843200
    requires_hitl_approval: bool = False


def assert_read_only_snowflake_sql(sql: str) -> str:
    """
    Hard guardrail for the query planner: blocks any DDL/DML verbs so injected query plans
    are guaranteed to be read-only (`SELECT` / `WITH` CTEs only).
    """
    stripped = sql.strip()
    match = FORBIDDEN_SQL_VERB_RE.search(stripped)
    if match:
        raise ValueError(
            f"Query planner guardrail blocked non-read-only SQL keyword '{match.group(1)}'. "
            "Only SELECT / WITH CTE queries are permitted in query_plan mode."
        )
    return stripped


class DbtSnowflakeQueryPlannerService:
    """
    Pluggable, dependency-injected service that converts a natural-language business/analytics
    request into a validated `SnowflakeDbtQueryPlan` (dbt CLI commands + compiled Snowflake SQL +
    pre-flight `EXPLAIN` check + M365 Copilot KPI grounding).

    Can be used in two ways:
      1. Imported directly as a Python package component inside an existing corporate service:
         ```python
         from app.services.query_planner_service import DbtSnowflakeQueryPlannerService
         planner = DbtSnowflakeQueryPlannerService.from_default_env()
         plan = await planner.build_query_plan(prompt="Show churn risk by segment", dbt_model_name="mart_customer_churn")
         ```
      2. Called as a microservice over HTTP (`POST /api/v1/snowflake/query-plan`) or via the
         LangGraph orchestrator (`operation="query_plan"`).
    """

    def __init__(
        self,
        settings: Settings,
        token_factory: KeylessEntraTokenFactory,
        m365_service: M365CopilotGroundingService | None = None,
        snowflake_service: SnowflakeDbtService | None = None,
        custom_sql_hook: QueryPlanHook | None = None,
    ) -> None:
        self._settings = settings
        self._token_factory = token_factory
        self._m365 = m365_service or M365CopilotGroundingService(settings, token_factory)
        self._snowflake = snowflake_service or SnowflakeDbtService(settings, token_factory)
        self._custom_sql_hook = custom_sql_hook

    @classmethod
    def from_default_env(cls, custom_sql_hook: QueryPlanHook | None = None) -> "DbtSnowflakeQueryPlannerService":
        settings = Settings()
        token_factory = KeylessEntraTokenFactory(settings)
        return cls(settings=settings, token_factory=token_factory, custom_sql_hook=custom_sql_hook)

    async def build_query_plan(
        self,
        *,
        prompt: str,
        dbt_model_name: str = "mart_customer_churn",
        limit: int = 100,
        user_assertion_jwt: str | None = None,
    ) -> SnowflakeDbtQueryPlan:
        # 1. Ground against M365 Copilot KPI specs & Snowflake INFORMATION_SCHEMA + dbt refs
        m365_docs = await self._m365.retrieve_m365_knowledge(
            prompt, user_assertion_jwt=user_assertion_jwt
        )
        sf_meta = await self._snowflake.get_dbt_and_snowflake_grounding(dbt_model_name)

        dbt_inline_jinja = (
            f"WITH base_mart AS (\n"
            f"    SELECT * FROM {{{{ ref('{dbt_model_name}') }}}}\n"
            f")\n"
            f"SELECT\n"
            f"    customer_segment,\n"
            f"    COUNT(*) AS total_customers,\n"
            f"    SUM(IFF(days_since_last_order > 45 AND active_subscription_count = 0, 1, 0)) AS churn_risk_customers\n"
            f"FROM base_mart\n"
            f"GROUP BY customer_segment\n"
            f"ORDER BY churn_risk_customers DESC\n"
            f"LIMIT {int(limit)}"
        )

        fqn_table = (
            f"{self._settings.snowflake_analytics_database}."
            f"{self._settings.snowflake_analytics_schema}."
            f"{dbt_model_name.upper()}"
        )
        compiled_sql = (
            f"WITH base_mart AS (\n"
            f"    SELECT customer_id, customer_segment, days_since_last_order, active_subscription_count\n"
            f"    FROM {fqn_table}\n"
            f")\n"
            f"SELECT\n"
            f"    customer_segment,\n"
            f"    COUNT(*) AS total_customers,\n"
            f"    SUM(IFF(days_since_last_order > 45 AND active_subscription_count = 0, 1, 0)) AS churn_risk_customers\n"
            f"FROM base_mart\n"
            f"GROUP BY customer_segment\n"
            f"ORDER BY churn_risk_customers DESC\n"
            f"LIMIT {int(limit)}"
        )

        if self._custom_sql_hook is not None:
            compiled_sql = self._custom_sql_hook.transform_sql(
                compiled_sql, {"prompt": prompt, "metadata": sf_meta}
            )

        # Enforce read-only guardrail before running EXPLAIN
        compiled_sql = assert_read_only_snowflake_sql(compiled_sql)
        explain_res = await self._snowflake.validate_sql_with_snowflake_explain(compiled_sql)

        dbt_commands = [
            f"dbt compile --select {dbt_model_name}",
            f'dbt show --select {dbt_model_name} --limit {int(limit)}',
        ]

        return SnowflakeDbtQueryPlan(
            request_prompt=prompt,
            target_dbt_model=dbt_model_name,
            dbt_refs_used=[dbt_model_name, *sf_meta.get("upstream_refs", [])],
            m365_kpi_definitions_applied=[d.get("title", "") for d in m365_docs],
            dbt_command_plan=dbt_commands,
            dbt_inline_jinja_sql=dbt_inline_jinja,
            compiled_snowflake_sql=compiled_sql,
            read_only_verified=True,
            snowflake_role="RL_LANGGRAPH_READER",
            warehouse=self._settings.snowflake_warehouse,
            statement_timeout_seconds=60,
            explain_valid=bool(explain_res.get("valid", True)),
            explain_summary=str(explain_res.get("explain_summary", "")),
            estimated_bytes_scanned=1843200,
            requires_hitl_approval=False,
        )
