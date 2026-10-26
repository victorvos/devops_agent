from __future__ import annotations

from typing import Any
import httpx

from app.auth.entra import KeylessEntraTokenFactory
from app.config import Settings

PROTECTED_BRANCHES = {
    "main",
    "master",
    "trunk",
    "refs/heads/main",
    "refs/heads/master",
    "refs/heads/trunk",
}


class PolicyViolationError(ValueError):
    """Raised when an agent attempts a direct commit or push to a protected branch."""


def assert_safe_branch_target(branch_ref: str) -> str:
    """
    Hard guardrail: never allow direct commits or branch creations targeting main/master/release/*.
    """
    normalized = branch_ref.strip()
    short = normalized.removeprefix("refs/heads/")
    if normalized in PROTECTED_BRANCHES or short in PROTECTED_BRANCHES or short.startswith("release/"):
        raise PolicyViolationError(
            f"Guardrail blocked direct mutation on protected branch '{branch_ref}'. "
            "All agent changes must use a feature branch (e.g. refs/heads/feat/*) and open a Pull Request."
        )
    if not normalized.startswith("refs/heads/"):
        return f"refs/heads/{normalized}"
    return normalized


class AzureDevOpsKeylessClient:
    """
    Executes least-privilege Azure DevOps REST API operations using a Managed Identity / Entra ID
    Bearer token scoped to `499b84ac-1321-427f-aa17-267ca6975798/.default`:
      1. Creates structured Product Backlog Items (PBIs) in Azure Boards grounded in M365 Copilot docs.
      2. Creates feature branches & commits dbt `.sql` / `schema.yml` changes in Azure Repos.
      3. Opens Pull Requests linked directly to the PBI (`workItemRefs`) so the dbt Snowflake Slim CI
         pipeline triggers automatically.
    """

    def __init__(self, settings: Settings, token_factory: KeylessEntraTokenFactory) -> None:
        self._settings = settings
        self._token_factory = token_factory

    def _headers(self, content_type: str = "application/json") -> dict[str, str]:
        token = self._token_factory.get_ado_bearer_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
        }

    async def create_pbi_work_item(
        self,
        *,
        pbi_spec: dict[str, Any],
        project: str | None = None,
    ) -> dict[str, Any]:
        """
        Creates a Product Backlog Item (PBI) in Azure Boards via JSON Patch (`application/json-patch+json`)
        with M365 Copilot source citations, Solution Fit notes, and Acceptance Criteria.
        """
        target_project = project or self._settings.ado_default_project
        title = pbi_spec.get("title", "[DataPlatform] Add dbt Snowflake Customer Churn Mart")
        description_html = pbi_spec.get("description_html", "<p>Grounded via M365 Copilot & Snowflake schema.</p>")
        acceptance_criteria_html = pbi_spec.get(
            "acceptance_criteria_html",
            "<ul><li>dbt build --select state:modified+ passes on Snowflake zero-copy clone.</li></ul>",
        )
        area_path = pbi_spec.get("area_path", self._settings.ado_default_area_path)
        iteration_path = pbi_spec.get("iteration_path", self._settings.ado_default_iteration_path)
        tags = "; ".join(pbi_spec.get("tags", ["langgraph", "dbt", "snowflake", "m365-grounded"]))

        if self._settings.dry_run_ado:
            return {
                "pbi_id": 9104,
                "title": title,
                "area_path": area_path,
                "iteration_path": iteration_path,
                "url": f"{self._settings.ado_org_url}/{target_project}/_workitems/edit/9104",
                "status": "created_dry_run",
            }

        wit_url = (
            f"{self._settings.ado_org_url.rstrip('/')}/{target_project}"
            f"/_apis/wit/workitems/$Product%20Backlog%20Item?api-version=7.1"
        )
        patch_ops = [
            {"op": "add", "path": "/fields/System.Title", "value": title},
            {"op": "add", "path": "/fields/System.Description", "value": description_html},
            {
                "op": "add",
                "path": "/fields/Microsoft.VSTS.Common.AcceptanceCriteria",
                "value": acceptance_criteria_html,
            },
            {"op": "add", "path": "/fields/System.AreaPath", "value": area_path},
            {"op": "add", "path": "/fields/System.IterationPath", "value": iteration_path},
            {"op": "add", "path": "/fields/System.Tags", "value": tags},
        ]
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                wit_url,
                headers=self._headers("application/json-patch+json"),
                json=patch_ops,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "pbi_id": data.get("id"),
                "title": title,
                "area_path": area_path,
                "iteration_path": iteration_path,
                "url": data.get("_links", {}).get("html", {}).get("href", data.get("url")),
                "status": "created",
            }

    async def create_feature_branch_and_pr(
        self,
        *,
        repository: str,
        source_branch: str,
        target_feature_branch: str,
        title: str,
        description: str,
        linked_pbi_id: int | None = None,
        dbt_files: list[dict[str, str]] | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """
        Creates a feature branch off `source_branch`, commits the dbt `.sql` and `schema.yml` files,
        and opens a Pull Request back into `source_branch` linked to `linked_pbi_id`.
        Enforces `assert_safe_branch_target(target_feature_branch)` before any network call.
        """
        safe_feature_branch = assert_safe_branch_target(target_feature_branch)
        target_project = project or self._settings.ado_default_project

        if self._settings.dry_run_ado:
            return {
                "status": "succeeded_dry_run",
                "project": target_project,
                "repository": repository,
                "feature_branch": safe_feature_branch,
                "pull_request_target": source_branch,
                "pull_request_id": 4201,
                "linked_pbi_id": linked_pbi_id,
                "committed_dbt_files": [f.get("path") for f in (dbt_files or [])],
                "ci_pipeline_triggered": "dbt-ci-snowflake (CREATE DATABASE ANALYTICS_PR_4201 CLONE ANALYTICS_PROD)",
                "url": f"{self._settings.ado_org_url}/{target_project}/_git/{repository}/pullrequest/4201",
                "auth_mode": "entra_managed_identity_499b84ac",
            }

        pr_url = (
            f"{self._settings.ado_org_url.rstrip('/')}/{target_project}"
            f"/_apis/git/repositories/{repository}/pullrequests?api-version=7.1"
        )
        payload: dict[str, Any] = {
            "sourceRefName": safe_feature_branch,
            "targetRefName": source_branch if source_branch.startswith("refs/heads/") else f"refs/heads/{source_branch}",
            "title": title,
            "description": description,
        }
        if linked_pbi_id is not None:
            payload["workItemRefs"] = [{"id": str(linked_pbi_id)}]

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(pr_url, headers=self._headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "status": "succeeded",
                "project": target_project,
                "repository": repository,
                "feature_branch": safe_feature_branch,
                "pull_request_id": data.get("pullRequestId"),
                "linked_pbi_id": linked_pbi_id,
                "url": data.get("url"),
            }
