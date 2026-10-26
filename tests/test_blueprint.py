from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.services.ado_client import PolicyViolationError, assert_safe_branch_target


client = TestClient(app)


def test_health_and_ready_probes() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"

    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["keyless_auth"] is True
    assert ready.json()["primary_database"] == "snowflake_entra_external_oauth"
    assert ready.json()["m365_copilot_grounding"] is True


def test_graph_webhook_and_lifecycle_handshakes() -> None:
    resp = client.post("/api/v1/webhooks/graph?validationToken=handshake-token-123")
    assert resp.status_code == 200
    assert resp.text == "handshake-token-123"

    life = client.post("/api/v1/webhooks/graph/lifecycle?validationToken=life-token-456")
    assert life.status_code == 200
    assert life.text == "life-token-456"


def test_graph_webhook_accepts_teams_and_outlook_notifications() -> None:
    payload = {
        "value": [
            {
                "subscriptionId": "sub-teams-001",
                "changeType": "created",
                "resource": "teams/000/channels/19:general@thread.tacv2/messages/1",
                "clientState": "enterprise-lg-webhook-state-v1",
                "resourceData": {"bodyPreview": "Add churn KPI to mart_customer_churn"},
            },
            {
                "subscriptionId": "sub-outlook-002",
                "changeType": "created",
                "resource": "users/ai-orchestrator@contoso.com/mailFolders('Inbox')/messages/99",
                "clientState": "enterprise-lg-webhook-state-v1",
                "resourceData": {"subject": "New PBI & dbt model for customer churn"},
            },
        ]
    }
    resp = client.post("/api/v1/webhooks/graph", json=payload)
    assert resp.status_code == 202


def test_branch_guardrail_blocks_main_and_release() -> None:
    with pytest.raises(PolicyViolationError):
        assert_safe_branch_target("main")
    with pytest.raises(PolicyViolationError):
        assert_safe_branch_target("refs/heads/main")
    with pytest.raises(PolicyViolationError):
        assert_safe_branch_target("release/2026.10")

    assert assert_safe_branch_target("feat/dbt-churn-mart") == "refs/heads/feat/dbt-churn-mart"


def test_m365_copilot_grounding_pbi_and_dbt_snowflake_pr_flow() -> None:
    # 1. Trigger a request from DevBox/Teams -> grounds in M365 Copilot & Snowflake,
    #    runs Snowflake EXPLAIN USING TEXT, drafts PBI + dbt SQL, and pauses at human_approval_gate
    invoke_resp = client.post(
        "/api/v1/dev/invoke",
        json={
            "thread_id": "test-thread-dbt-pbi-1",
            "channel_origin": "ms_teams",
            "actor_upn": "victor.vos@contoso.com",
            "prompt": "Add customer churn risk flag to mart_customer_churn and create PBI + PR",
            "repository": "dbt-snowflake-analytics",
            "dbt_model_name": "mart_customer_churn",
            "source_branch": "refs/heads/main",
            "target_branch": "refs/heads/feat/dbt-customer-churn",
            "operation": "create_pbi_and_pr",
        },
    )
    assert invoke_resp.status_code == 200
    body = invoke_resp.json()
    assert body["status"] == "paused_for_hitl_approval"
    assert "human_approval_gate" in body["next_nodes"]
    assert len(body["m365_copilot_citations"]) >= 2
    assert body["snowflake_dbt_metadata"]["target_dbt_model"] == "mart_customer_churn"
    assert body["proposed_plan"]["snowflake_explain"]["valid"] is True
    assert "pbi_spec" in body["proposed_plan"]
    digest = body["proposed_plan"]["action_digest"]

    # 2. Resume via Outlook Actionable Message / Teams HITL callback with 'approve'
    callback_resp = client.post(
        "/api/v1/outlook/action",
        json={
            "thread_id": "test-thread-dbt-pbi-1",
            "run_id": "run-1",
            "action_digest": digest,
            "decision": "approve",
        },
    )
    assert callback_resp.status_code == 200
    resumed = callback_resp.json()
    assert resumed["status"] == "completed"
    assert resumed["approval_decision"] == "approve"
    assert resumed["execution_result"]["pbi"]["pbi_id"] == 9104
    assert resumed["execution_result"]["pull_request_id"] == 4201
    assert resumed["execution_result"]["linked_pbi_id"] == 9104


def test_teams_bot_service_activity_and_adaptive_card_action() -> None:
    # 1. Teams @mention message arrives via Azure Bot Service
    msg_resp = client.post(
        "/api/v1/bot/messages",
        json={
            "type": "message",
            "id": "act-teams-100",
            "text": "Create PBI and dbt Snowflake PR for customer churn mart",
            "from": {"userPrincipalName": "victor.vos@contoso.com"},
            "conversation": {"id": "19:teams-channel-thread@thread.tacv2"},
        },
    )
    assert msg_resp.status_code == 200
    msg_body = msg_resp.json()
    assert msg_body["status"] == "paused_for_hitl_approval"
    thread_id = msg_body["thread_id"]
    digest = msg_body["proposed_plan"]["action_digest"]

    # 2. Engineer clicks Approve on the Adaptive Card inside Teams (`Action.Execute`)
    card_resp = client.post(
        "/api/v1/bot/messages",
        json={
            "type": "invoke",
            "name": "adaptiveCard/action",
            "from": {"userPrincipalName": "victor.vos@contoso.com"},
            "conversation": {"id": "19:teams-channel-thread@thread.tacv2"},
            "value": {
                "thread_id": thread_id,
                "run_id": "run-teams-100",
                "action_digest": digest,
                "decision": "approve",
            },
        },
    )
    assert card_resp.status_code == 200
    card_body = card_resp.json()
    assert card_body["status"] == "completed"
    assert card_body["execution_result"]["pbi"]["pbi_id"] == 9104
    assert card_body["execution_result"]["pull_request_id"] == 4201


def test_injectable_dbt_snowflake_query_planner_endpoint_and_guardrail() -> None:
    from app.services.query_planner_service import assert_read_only_snowflake_sql

    with pytest.raises(ValueError):
        assert_read_only_snowflake_sql("DROP TABLE ANALYTICS_PROD.MARTS.MART_CUSTOMER_CHURN")

    resp = client.post(
        "/api/v1/snowflake/query-plan",
        json={
            "prompt": "Show churn risk customer counts grouped by segment",
            "dbt_model_name": "mart_customer_churn",
            "limit": 50,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    q_plan = data["query_plan"]
    assert q_plan["target_dbt_model"] == "mart_customer_churn"
    assert q_plan["read_only_verified"] is True
    assert q_plan["explain_valid"] is True
    assert "ANALYTICS_PROD.MARTS.MART_CUSTOMER_CHURN" in q_plan["compiled_snowflake_sql"]
    assert any("dbt show" in cmd for cmd in q_plan["dbt_command_plan"])
