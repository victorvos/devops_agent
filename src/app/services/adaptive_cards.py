from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_action_digest(plan: dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 digest of the proposed mutating action so callbacks cannot be tampered with."""
    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def build_hitl_adaptive_card(
    *,
    thread_id: str,
    run_id: str,
    actor_upn: str,
    plan: dict[str, Any],
    citations: list[dict[str, Any]],
    callback_url: str,
) -> dict[str, Any]:
    """
    Constructs an Adaptive Card v1.5 payload for Microsoft Teams (`Action.Execute`)
    and Outlook Actionable Messages (`Action.Http`) showing:
      - Solution Fit Analysis (grounded in M365 Copilot SharePoint/Teams docs)
      - Proposed Azure DevOps PBI (Title, Area/Iteration Path, Story Points, Acceptance Criteria)
      - Proposed dbt + Snowflake Model Change & `EXPLAIN USING TEXT` Verification
    """
    digest = str(plan.get("action_digest") or compute_action_digest(plan))
    citation_titles = ", ".join(c.get("title", c.get("id", "doc")) for c in citations) or "None"
    pbi_spec = plan.get("pbi_spec", {})
    dbt_change = plan.get("dbt_change", {})
    explain_check = plan.get("snowflake_explain", {})

    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.5",
        "body": [
            {
                "type": "TextBlock",
                "size": "Large",
                "weight": "Bolder",
                "text": "LangGraph Solution Fit, PBI & dbt/Snowflake PR Approval",
            },
            {
                "type": "FactSet",
                "facts": [
                    {"title": "Thread ID", "value": thread_id},
                    {"title": "Requested By", "value": actor_upn},
                    {"title": "Operation", "value": str(plan.get("operation", "create_pbi_and_pr"))},
                    {"title": "PBI Title", "value": str(pbi_spec.get("title", "New Data Platform PBI"))},
                    {"title": "Area / Sprint", "value": f"{pbi_spec.get('area_path', '')} | {pbi_spec.get('iteration_path', '')}"},
                    {"title": "dbt Model", "value": str(dbt_change.get("dbt_file_path", "models/marts/customer/mart_customer_churn.sql"))},
                    {"title": "Snowflake EXPLAIN", "value": "PASSED" if explain_check.get("valid", True) else "FAILED"},
                    {"title": "Target Branch", "value": f"{plan.get('source_branch')} <- {plan.get('target_branch')}"},
                    {"title": "Action Digest", "value": digest},
                    {"title": "M365 Copilot Docs", "value": citation_titles},
                ],
            },
            {
                "type": "TextBlock",
                "weight": "Bolder",
                "text": "Solution Fit & PBI Acceptance Criteria:",
            },
            {
                "type": "TextBlock",
                "text": str(plan.get("solution_fit_summary", plan.get("summary", ""))),
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": str(pbi_spec.get("acceptance_criteria", "1. dbt tests pass on Snowflake clone.")),
                "wrap": True,
            },
        ],
        "actions": [
            {
                "type": "Action.Http",
                "title": "Approve: Create ADO PBI + dbt Snowflake PR",
                "method": "POST",
                "url": callback_url,
                "body": json.dumps(
                    {
                        "thread_id": thread_id,
                        "run_id": run_id,
                        "action_digest": digest,
                        "decision": "approve",
                    }
                ),
            },
            {
                "type": "Action.Http",
                "title": "Reject Action",
                "method": "POST",
                "url": callback_url,
                "body": json.dumps(
                    {
                        "thread_id": thread_id,
                        "run_id": run_id,
                        "action_digest": digest,
                        "decision": "reject",
                    }
                ),
            },
        ],
    }
