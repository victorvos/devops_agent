from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.responses import PlainTextResponse
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.auth.entra import (
    KeylessEntraTokenFactory,
    verify_inbound_entra_caller,
)
from app.config import Settings, get_settings
from app.graph.orchestrator import EnterpriseLangGraphOrchestrator
from app.models.schemas import (
    BotActivityPayload,
    DevInvokeRequest,
    GraphWebhookPayload,
    HitlCallbackRequest,
    QueryPlanRequest,
)
from app.services.ado_client import PolicyViolationError
from app.services.graph_subscriptions import GraphSubscriptionManager
from app.services.teams_outlook_ingress import (
    normalize_graph_notification_to_invocation,
    parse_bot_activity,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Initializes the LangGraph orchestrator:
    - Uses Snowflake (`AGENT_STATE_DB.LANGGRAPH.THREAD_CHECKPOINTS` & `HITL_AUDIT_LOG`) via
      Entra External OAuth for durable audit/state persistence alongside LangGraph's checkpointer.
    - Optionally connects `AsyncPostgresSaver` if `DRY_RUN_CHECKPOINTER=false`.
    """
    settings = get_settings()
    token_factory = KeylessEntraTokenFactory(settings)

    if settings.dry_run_checkpointer:
        logger.info("Starting orchestrator with in-memory checkpointer + Snowflake state sync")
        app.state.orchestrator = EnterpriseLangGraphOrchestrator(
            settings=settings,
            token_factory=token_factory,
            checkpointer=MemorySaver(),
        )
        yield
    else:
        from psycopg_pool import AsyncConnectionPool
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        conninfo = token_factory.build_postgres_conninfo()
        async with AsyncConnectionPool(
            conninfo=conninfo,
            max_size=10,
            kwargs={"autocommit": True, "prepare_threshold": 0},
        ) as pool:
            checkpointer = AsyncPostgresSaver(pool)
            await checkpointer.setup()
            app.state.orchestrator = EnterpriseLangGraphOrchestrator(
                settings=settings,
                token_factory=token_factory,
                checkpointer=checkpointer,
            )
            yield


app = FastAPI(
    title="Azure Container Apps + LangGraph + M365 Copilot + dbt/Snowflake Orchestrator",
    version="0.2.0",
    description=(
        "100% Keyless Entra ID authenticated FastAPI + LangGraph orchestrator triggered via "
        "corporate MS Teams, Outlook, or DevBox, grounding in M365 Copilot & Snowflake, "
        "and creating Azure DevOps PBIs + linked dbt Pull Requests."
    ),
    lifespan=lifespan,
)


def _get_orchestrator() -> EnterpriseLangGraphOrchestrator:
    if not hasattr(app.state, "orchestrator"):
        settings = get_settings()
        app.state.orchestrator = EnterpriseLangGraphOrchestrator(
            settings=settings,
            token_factory=KeylessEntraTokenFactory(settings),
        )
    return app.state.orchestrator


@app.get("/health", tags=["probes"])
async def health_probe() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/ready", tags=["probes"])
async def readiness_probe(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return {
        "status": "ready",
        "primary_database": "snowflake_entra_external_oauth",
        "snowflake_account": settings.snowflake_account,
        "m365_copilot_grounding": settings.m365_copilot_retrieval_enabled,
        "keyless_auth": True,
    }


@app.post("/api/v1/webhooks/graph", tags=["webhooks"])
async def microsoft_graph_webhook(
    background_tasks: BackgroundTasks,
    validationToken: str | None = Query(default=None),
    payload: GraphWebhookPayload | None = None,
    settings: Settings = Depends(get_settings),
) -> Response:
    """
    Phase 1 Ingress Endpoint for Microsoft Graph Change Notifications (MS Teams & Outlook):
    1. Immediately echoes `?validationToken=...` as `text/plain` `200 OK` during subscription creation.
    2. Verifies `clientState` on incoming notifications, normalizes Teams/Outlook payloads,
       schedules background LangGraph execution, and returns `HTTP 202 Accepted` within <200ms.
    """
    if validationToken is not None:
        return PlainTextResponse(content=validationToken, status_code=status.HTTP_200_OK)

    if payload is None or not payload.value:
        return Response(status_code=status.HTTP_202_ACCEPTED)

    for item in payload.value:
        if item.clientState != settings.graph_expected_client_state:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Microsoft Graph webhook clientState.",
            )

        invocation = normalize_graph_notification_to_invocation(item)
        background_tasks.add_task(_run_graph_background, invocation=invocation)

    return Response(status_code=status.HTTP_202_ACCEPTED)


@app.post("/api/v1/webhooks/graph/lifecycle", tags=["webhooks"])
async def microsoft_graph_lifecycle_webhook(
    background_tasks: BackgroundTasks,
    validationToken: str | None = Query(default=None),
    payload: GraphWebhookPayload | None = None,
    settings: Settings = Depends(get_settings),
) -> Response:
    """
    Handles Microsoft Graph `reauthorizationRequired` / `subscriptionRemoved` lifecycle webhooks
    so corporate MS Teams and Outlook subscriptions never silently expire.
    """
    if validationToken is not None:
        return PlainTextResponse(content=validationToken, status_code=status.HTTP_200_OK)

    if payload and payload.value:
        manager = GraphSubscriptionManager(settings, KeylessEntraTokenFactory(settings))
        for item in payload.value:
            if item.clientState != settings.graph_expected_client_state:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid Microsoft Graph lifecycle clientState.",
                )
            background_tasks.add_task(manager.renew_subscription, item.subscriptionId)

    return Response(status_code=status.HTTP_202_ACCEPTED)


@app.post("/api/v1/bot/messages", tags=["teams-bot"])
async def azure_bot_service_messages(
    activity: BotActivityPayload,
    caller: dict[str, str] = Depends(verify_inbound_entra_caller),
) -> dict[str, Any]:
    """
    Pattern C Endpoint for Keyless Azure Bot Service (`UserAssignedMSI` in `triggers.bicep`):
    - Handles `@LangGraph Architect` mentions in corporate MS Teams channels/chats.
    - Handles Adaptive Card `Action.Execute` button clicks directly inside MS Teams.
    """
    parsed = parse_bot_activity(activity)
    orchestrator = _get_orchestrator()
    config = {"configurable": {"thread_id": parsed["thread_id"]}}

    if parsed["kind"] == "hitl_callback":
        resumed = await orchestrator.graph.ainvoke(
            Command(
                resume={
                    "decision": parsed["decision"],
                    "action_digest": parsed["action_digest"],
                    "approver": caller.get("upn", parsed["actor_upn"]),
                }
            ),
            config=config,
        )
        return {
            "type": "message",
            "thread_id": parsed["thread_id"],
            "status": resumed.get("status"),
            "execution_result": resumed.get("execution_result"),
        }

    state_out = await orchestrator.graph.ainvoke(parsed, config=config)
    snapshot = await orchestrator.graph.aget_state(config)
    return {
        "type": "message",
        "thread_id": parsed["thread_id"],
        "status": "paused_for_hitl_approval" if snapshot.next else state_out.get("status"),
        "adaptive_card": state_out.get("adaptive_card_payload"),
        "proposed_plan": state_out.get("proposed_plan"),
    }


@app.post("/api/v1/outlook/action", tags=["outlook"])
async def outlook_actionable_message_callback(
    callback: HitlCallbackRequest,
    caller: dict[str, str] = Depends(verify_inbound_entra_caller),
) -> dict[str, Any]:
    """
    Handles Outlook Actionable Message (`Action.Http`) button clicks when an engineer or
    product owner approves a proposed PBI + dbt Snowflake PR directly from Outlook email.
    """
    callback.channel_origin = "outlook"
    return await hitl_adaptive_card_callback(callback=callback, caller=caller)


async def _run_graph_background(invocation: dict[str, Any]) -> None:
    orchestrator = _get_orchestrator()
    config = {"configurable": {"thread_id": invocation["thread_id"]}}
    await orchestrator.graph.ainvoke(invocation, config=config)


@app.post("/api/v1/dev/invoke", tags=["devbox"])
async def devbox_invoke_graph(
    req: DevInvokeRequest,
    caller: dict[str, str] = Depends(verify_inbound_entra_caller),
) -> dict[str, Any]:
    """
    Direct synchronous invocation endpoint for engineers testing from a whitelisted Azure DevBox.
    """
    orchestrator = _get_orchestrator()
    config = {"configurable": {"thread_id": req.thread_id}}

    try:
        state_out = await orchestrator.graph.ainvoke(
            {
                "thread_id": req.thread_id,
                "channel_origin": req.channel_origin,
                "actor_upn": caller.get("upn", req.actor_upn),
                "prompt": req.prompt,
                "repository": req.repository,
                "dbt_model_name": req.dbt_model_name,
                "source_branch": req.source_branch,
                "target_branch": req.target_branch,
                "operation": req.operation,
            },
            config=config,
        )
    except PolicyViolationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    snapshot = await orchestrator.graph.aget_state(config)
    interrupted = bool(snapshot.next)

    return {
        "thread_id": req.thread_id,
        "status": "paused_for_hitl_approval" if interrupted else state_out.get("status", "completed"),
        "next_nodes": list(snapshot.next),
        "m365_copilot_citations": state_out.get("m365_copilot_citations"),
        "snowflake_dbt_metadata": state_out.get("snowflake_dbt_metadata"),
        "proposed_plan": state_out.get("proposed_plan"),
        "adaptive_card": state_out.get("adaptive_card_payload"),
        "execution_result": state_out.get("execution_result"),
    }


@app.post("/api/v1/hitl/callback", tags=["hitl"])
async def hitl_adaptive_card_callback(
    callback: HitlCallbackRequest,
    caller: dict[str, str] = Depends(verify_inbound_entra_caller),
) -> dict[str, Any]:
    """
    Phase 3 Callback Endpoint: Resumes a paused LangGraph thread when an engineer clicks
    Approve or Reject on the Teams/Outlook Adaptive Card.
    """
    orchestrator = _get_orchestrator()
    config = {"configurable": {"thread_id": callback.thread_id}}

    resumed_state = await orchestrator.graph.ainvoke(
        Command(
            resume={
                "decision": callback.decision,
                "action_digest": callback.action_digest,
                "approver": caller.get("upn", "victor.vos@contoso.com"),
                "comment": callback.reviewer_comment,
            }
        ),
        config=config,
    )

    return {
        "thread_id": callback.thread_id,
        "status": resumed_state.get("status"),
        "approval_decision": resumed_state.get("approval_decision"),
        "approved_by": resumed_state.get("approved_by"),
        "execution_result": resumed_state.get("execution_result"),
    }


@app.post("/api/v1/snowflake/query-plan", tags=["snowflake-dbt-query-planner"])
async def generate_dbt_snowflake_query_plan(
    req: QueryPlanRequest,
    caller: dict[str, str] = Depends(verify_inbound_entra_caller),
) -> dict[str, Any]:
    """
    Injectable Service Endpoint (`POST /api/v1/snowflake/query-plan`):
    Converts a natural-language business/analytics question into a governed, read-only
    `SnowflakeDbtQueryPlan` (compiled Snowflake SQL + `dbt compile`/`dbt show` commands +
    M365 Copilot KPI citations + Snowflake `EXPLAIN` verification).
    """
    orchestrator = _get_orchestrator()
    plan = await orchestrator.query_planner.build_query_plan(
        prompt=req.prompt,
        dbt_model_name=req.dbt_model_name,
        limit=req.limit,
        user_assertion_jwt=caller.get("raw_token") or None,
    )
    return {
        "requested_by": caller.get("upn", "victor.vos@contoso.com"),
        "query_plan": plan.model_dump(),
    }
