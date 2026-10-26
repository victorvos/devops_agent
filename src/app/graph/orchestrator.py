from __future__ import annotations

import logging
import uuid
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.auth.entra import KeylessEntraTokenFactory
from app.config import Settings
from app.graph.state import OrchestratorState
from app.services.adaptive_cards import build_hitl_adaptive_card, compute_action_digest
from app.services.ado_client import AzureDevOpsKeylessClient, assert_safe_branch_target
from app.services.ai_search import EnterpriseGroundingService
from app.services.m365_copilot_grounding import M365CopilotGroundingService
from app.services.query_planner_service import DbtSnowflakeQueryPlannerService
from app.services.snowflake_dbt import SnowflakeDbtService

logger = logging.getLogger(__name__)


class EnterpriseLangGraphOrchestrator:
    """
    Builds and manages the stateful LangGraph workflow supporting TWO primary execution modes:
      - Mode A (`create_pbi_and_pr` / `create_pr`):
          1. ingest_event        -> Idempotency check & Teams/Outlook/DevBox normalization
          2. ground_context      -> M365 Copilot Retrieval API + Snowflake schema + AI Search
          3. plan_action         -> Solution Fitting + Azure DevOps PBI Spec + dbt SQL/schema.yml
                                    + Snowflake `EXPLAIN USING TEXT` pre-flight check
          4. human_approval_gate -> LangGraph `interrupt()` + Adaptive Card v1.5
          5. execute_ado_action  -> Creates ADO PBI + feature branch + linked dbt PR
      - Mode B (`query_plan` — Injectable dbt/Snowflake Query Planner):
          Converts natural-language requests into a read-only `SnowflakeDbtQueryPlan`
          (compiled Snowflake SQL, `dbt show`/`dbt compile` commands, M365 KPI rules,
          and pre-flight `EXPLAIN` verification) without mutating repositories.
    """

    def __init__(
        self,
        settings: Settings,
        token_factory: KeylessEntraTokenFactory,
        checkpointer: Any | None = None,
        query_planner: DbtSnowflakeQueryPlannerService | None = None,
    ) -> None:
        self._settings = settings
        self._token_factory = token_factory
        self._ai_search = EnterpriseGroundingService(settings, token_factory)
        self._m365_copilot = M365CopilotGroundingService(settings, token_factory)
        self._snowflake_dbt = SnowflakeDbtService(settings, token_factory)
        self._query_planner = query_planner or DbtSnowflakeQueryPlannerService(
            settings=settings,
            token_factory=token_factory,
            m365_service=self._m365_copilot,
            snowflake_service=self._snowflake_dbt,
        )
        self._ado = AzureDevOpsKeylessClient(settings, token_factory)
        self._checkpointer = checkpointer or MemorySaver()
        self.graph = self._build_graph()

    @property
    def query_planner(self) -> DbtSnowflakeQueryPlannerService:
        return self._query_planner

    async def ingest_event(self, state: OrchestratorState) -> OrchestratorState:
        processed = list(state.get("processed_event_keys", []))
        event_key = state.get("event_key") or f"evt-{uuid.uuid4().hex[:10]}"

        if event_key in processed:
            logger.info("Skipping duplicate Graph/Teams webhook delivery: %s", event_key)
            return {
                "is_duplicate_event": True,
                "status": "duplicate_ignored",
            }

        processed.append(event_key)
        return {
            "run_id": state.get("run_id") or f"run-{uuid.uuid4().hex[:8]}",
            "channel_origin": state.get("channel_origin", "devbox"),
            "event_key": event_key,
            "processed_event_keys": processed,
            "is_duplicate_event": False,
            "status": "ingested",
        }

    async def ground_context(self, state: OrchestratorState) -> OrchestratorState:
        prompt = state.get("prompt", "")
        dbt_model_name = state.get("dbt_model_name") or "mart_customer_churn"

        m365_docs = await self._m365_copilot.retrieve_m365_knowledge(prompt)
        search_docs = await self._ai_search.retrieve_context(prompt)
        sf_metadata = await self._snowflake_dbt.get_dbt_and_snowflake_grounding(dbt_model_name)

        combined_citations = m365_docs + search_docs
        return {
            "m365_copilot_citations": m365_docs,
            "grounded_citations": combined_citations,
            "snowflake_dbt_metadata": sf_metadata,
            "status": "grounded",
        }

    async def plan_action(self, state: OrchestratorState) -> OrchestratorState:
        operation = state.get("operation", "create_pbi_and_pr")
        repo = state.get("repository") or self._settings.ado_dbt_repo_name
        dbt_model_name = state.get("dbt_model_name") or "mart_customer_churn"
        source_branch = state.get("source_branch", "refs/heads/main")
        target_branch = state.get("target_branch", "refs/heads/feat/dbt-customer-churn")
        prompt = state.get("prompt", "")

        # Mode B: Injectable dbt / Snowflake Query Planner mode (no branch mutation or HITL required)
        if operation == "query_plan":
            q_plan = await self._query_planner.build_query_plan(
                prompt=prompt,
                dbt_model_name=dbt_model_name,
            )
            q_dict = q_plan.model_dump()
            return {
                "query_plan_result": q_dict,
                "proposed_plan": {
                    "operation": "query_plan",
                    "dbt_model_name": dbt_model_name,
                    "query_plan": q_dict,
                    "requires_hitl": False,
                },
                "approval_decision": "not_required",
                "status": "planned_query_plan",
            }

        # Validate branch guardrail early during planning if operation mutates a branch
        if operation in ("create_pbi_and_pr", "create_pr", "create_branch"):
            target_branch = assert_safe_branch_target(target_branch)

        dbt_change = self._snowflake_dbt.generate_dbt_sql_and_tests(dbt_model_name, prompt)
        snowflake_explain = await self._snowflake_dbt.validate_sql_with_snowflake_explain(
            dbt_change["compiled_sql"]
        )

        m365_citations = state.get("m365_copilot_citations", [])
        m365_links = [c.get("url", "") for c in m365_citations if c.get("url")]

        if not self._settings.dry_run_ai:
            from langchain_openai import AzureChatOpenAI

            llm = AzureChatOpenAI(
                azure_endpoint=self._settings.azure_openai_endpoint,
                azure_deployment=self._settings.azure_openai_deployment,
                api_version=self._settings.azure_openai_api_version,
                azure_ad_token_provider=self._token_factory.get_openai_token_provider(),
                temperature=0.0,
            )
            response = await llm.ainvoke(
                f"Perform request-to-solution fitting and draft an Azure DevOps PBI + dbt Snowflake plan for: {prompt}\n"
                f"M365 Copilot Context: {m365_citations}\n"
                f"Snowflake Metadata: {state.get('snowflake_dbt_metadata')}"
            )
            solution_fit_summary = str(response.content)
        else:
            solution_fit_summary = (
                f"Solution Fit: Aligns with M365 SharePoint KPI Standard (sp-doc-churn-kpi-v3: "
                f"days_since_last_order > 45 AND active_subscription_count = 0) and Teams Architecture "
                f"Board incremental mart rule. Target dbt model '{dbt_change['dbt_file_path']}' "
                f"verified against Snowflake ({self._settings.snowflake_analytics_database}.{self._settings.snowflake_analytics_schema}) "
                f"via EXPLAIN USING TEXT."
            )

        pbi_spec = {
            "title": f"[DataPlatform][dbt] {dbt_model_name}: {prompt[:64]}",
            "area_path": self._settings.ado_default_area_path,
            "iteration_path": self._settings.ado_default_iteration_path,
            "story_points": 3,
            "tags": ["langgraph", "dbt", "snowflake", "m365-copilot-grounded"],
            "m365_source_links": m365_links,
            "acceptance_criteria": (
                "1. Incremental dbt model compiles and passes EXPLAIN on Snowflake.\n"
                "2. schema.yml enforces unique(customer_id), not_null, and accepted_values(is_churn_risk).\n"
                "3. Slim CI (`dbt build --select state:modified+`) passes on Snowflake zero-copy clone."
            ),
            "description_html": (
                f"<h3>Solution Fit (Grounded via Microsoft 365 Copilot &amp; Snowflake)</h3>"
                f"<p>{solution_fit_summary}</p>"
                f"<p><b>Target dbt Model:</b> <code>{dbt_change['dbt_file_path']}</code></p>"
            ),
            "acceptance_criteria_html": (
                "<ul>"
                "<li>Incremental dbt model compiles and passes EXPLAIN on Snowflake.</li>"
                "<li><code>schema.yml</code> enforces <code>unique(customer_id)</code>, "
                "<code>not_null</code>, and <code>accepted_values(is_churn_risk)</code>.</li>"
                "<li>Azure DevOps Slim CI (<code>dbt build --select state:modified+</code>) succeeds "
                "against Snowflake zero-copy clone.</li>"
                "</ul>"
            ),
        }

        plan: dict[str, Any] = {
            "operation": operation,
            "repository": repo,
            "dbt_model_name": dbt_model_name,
            "source_branch": source_branch,
            "target_branch": target_branch,
            "summary": solution_fit_summary,
            "solution_fit_summary": solution_fit_summary,
            "pbi_spec": pbi_spec,
            "dbt_change": dbt_change,
            "snowflake_explain": snowflake_explain,
            "requires_hitl": operation not in ("read_only", "query_plan"),
        }
        plan["action_digest"] = compute_action_digest(plan)

        callback_url = f"{self._settings.public_base_url.rstrip('/')}/api/v1/hitl/callback"
        card = build_hitl_adaptive_card(
            thread_id=state.get("thread_id", "thread-default"),
            run_id=state.get("run_id", "run-default"),
            actor_upn=state.get("actor_upn", "victor.vos@contoso.com"),
            plan=plan,
            citations=state.get("grounded_citations", []),
            callback_url=callback_url,
        )

        await self._snowflake_dbt.record_snowflake_checkpoint_and_audit(
            thread_id=state.get("thread_id", "thread-default"),
            run_id=state.get("run_id", "run-default"),
            event_key=state.get("event_key", "evt-default"),
            status="awaiting_human_approval" if plan["requires_hitl"] else "planned_read_only",
            actor_upn=state.get("actor_upn", "victor.vos@contoso.com"),
            state_payload={"proposed_plan": plan},
        )

        return {
            "proposed_plan": plan,
            "adaptive_card_payload": card,
            "approval_decision": "pending" if plan["requires_hitl"] else "not_required",
            "status": "awaiting_human_approval" if plan["requires_hitl"] else "planned_read_only",
        }

    async def human_approval_gate(self, state: OrchestratorState) -> OrchestratorState:
        plan = state.get("proposed_plan", {})
        if not plan.get("requires_hitl", True):
            return {"approval_decision": "not_required", "status": "approved_read_only"}

        resume_payload: dict[str, Any] = interrupt(
            {
                "thread_id": state.get("thread_id"),
                "run_id": state.get("run_id"),
                "action_digest": plan.get("action_digest"),
                "adaptive_card": state.get("adaptive_card_payload"),
            }
        )

        decision = resume_payload.get("decision", "reject")
        approver = resume_payload.get("approver", "unknown@contoso.com")
        submitted_digest = resume_payload.get("action_digest")

        if submitted_digest and submitted_digest != plan.get("action_digest"):
            return {
                "approval_decision": "reject",
                "approved_by": approver,
                "status": "rejected_digest_mismatch",
            }

        return {
            "approval_decision": "approve" if decision == "approve" else "reject",
            "approved_by": approver,
            "status": "approved" if decision == "approve" else "rejected_by_human",
        }

    async def execute_ado_action(self, state: OrchestratorState) -> OrchestratorState:
        decision = state.get("approval_decision")
        if decision not in ("approve", "not_required"):
            return {
                "execution_result": {"status": "skipped", "reason": f"Approval state: {decision}"},
                "status": "halted_without_execution",
            }

        plan = state.get("proposed_plan", {})
        if plan.get("operation") == "query_plan":
            return {
                "execution_result": {
                    "status": "query_plan_ready",
                    "query_plan": state.get("query_plan_result") or plan.get("query_plan"),
                },
                "status": "completed",
            }

        if plan.get("operation") == "read_only":
            return {
                "execution_result": {
                    "status": "read_only_completed",
                    "citations": state.get("grounded_citations", []),
                    "snowflake_metadata": state.get("snowflake_dbt_metadata", {}),
                },
                "status": "completed",
            }

        pbi_result = await self._ado.create_pbi_work_item(pbi_spec=plan.get("pbi_spec", {}))
        pbi_id = pbi_result.get("pbi_id")

        dbt_change = plan.get("dbt_change", {})
        dbt_files = [
            {"path": dbt_change.get("dbt_file_path", ""), "content": dbt_change.get("dbt_sql", "")},
            {"path": dbt_change.get("dbt_schema_yml_path", ""), "content": dbt_change.get("schema_yml_snippet", "")},
        ]
        ado_pr = await self._ado.create_feature_branch_and_pr(
            repository=plan["repository"],
            source_branch=plan["source_branch"],
            target_feature_branch=plan["target_branch"],
            title=f"[LangGraph][PBI #{pbi_id}] {state.get('prompt', 'Automated dbt Snowflake PR')[:60]}",
            description=(
                f"Automated dbt + Snowflake Pull Request requested by `{state.get('actor_upn')}` "
                f"via `{state.get('channel_origin', 'devbox')}` and approved via Adaptive Card by `{state.get('approved_by')}`.\n\n"
                f"- **Linked Azure Boards PBI:** `#{pbi_id}` ({pbi_result.get('url')})\n"
                f"- **Action Digest:** `{plan.get('action_digest')}`\n"
                f"- **Snowflake EXPLAIN Pre-Flight:** `{plan.get('snowflake_explain', {}).get('explain_summary', 'VALID')}`"
            ),
            linked_pbi_id=pbi_id,
            dbt_files=dbt_files,
        )

        combined_result = {
            **ado_pr,
            "pbi": pbi_result,
            "dbt_model_name": plan.get("dbt_model_name"),
            "snowflake_explain_valid": plan.get("snowflake_explain", {}).get("valid", True),
        }

        await self._snowflake_dbt.record_snowflake_checkpoint_and_audit(
            thread_id=state.get("thread_id", "thread-default"),
            run_id=state.get("run_id", "run-default"),
            event_key=state.get("event_key", "evt-default"),
            status="completed",
            actor_upn=state.get("actor_upn", "victor.vos@contoso.com"),
            state_payload={"execution_result": combined_result},
        )

        return {
            "execution_result": combined_result,
            "status": "completed",
        }

    def _route_after_ingest(self, state: OrchestratorState) -> str:
        if state.get("is_duplicate_event"):
            return END
        return "ground_context"

    def _route_after_approval(self, state: OrchestratorState) -> str:
        if state.get("approval_decision") in ("approve", "not_required"):
            return "execute_ado_action"
        return END

    def _build_graph(self):
        builder = StateGraph(OrchestratorState)
        builder.add_node("ingest_event", self.ingest_event)
        builder.add_node("ground_context", self.ground_context)
        builder.add_node("plan_action", self.plan_action)
        builder.add_node("human_approval_gate", self.human_approval_gate)
        builder.add_node("execute_ado_action", self.execute_ado_action)

        builder.add_edge(START, "ingest_event")
        builder.add_conditional_edges("ingest_event", self._route_after_ingest)
        builder.add_edge("ground_context", "plan_action")
        builder.add_edge("plan_action", "human_approval_gate")
        builder.add_conditional_edges("human_approval_gate", self._route_after_approval)
        builder.add_edge("execute_ado_action", END)

        return builder.compile(checkpointer=self._checkpointer)
