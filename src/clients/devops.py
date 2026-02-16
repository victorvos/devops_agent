"""Azure DevOps REST API client.

Provides targeted, cost-efficient access to:
  - Git items (file tree + contents)
  - Work items
  - Branch creation
  - Pull request creation

Uses httpx for async HTTP instead of cloning the full repo.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.config import Settings

logger = logging.getLogger(__name__)

# Azure DevOps REST API version used across all calls
API_VERSION = "7.1"


@dataclass
class FileItem:
    """A single file retrieved from Azure DevOps Git."""

    path: str
    content: str
    size: int = 0
    commit_id: str = ""


@dataclass
class RepoTree:
    """Lightweight representation of the repository file tree."""

    folders: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


class AzureDevOpsClient:
    """Async client for Azure DevOps Git & Work Item REST APIs.

    Designed for *targeted retrieval* — fetches only the files the agent
    needs rather than cloning the entire repository.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base = settings.azure_devops_org_url.rstrip("/")
        self._project = settings.azure_devops_project
        self._repo = settings.azure_devops_repository
        self._branch = settings.default_branch

        # PAT auth: base64-encode ":{pat}"
        token = base64.b64encode(f":{settings.azure_devops_pat}".encode()).decode()
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Basic {token}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    # ── URL builders ─────────────────────────────────────────────

    def _git_url(self, path: str = "") -> str:
        return f"{self._base}/{self._project}/_apis/git/repositories/{self._repo}{path}"

    def _wit_url(self, path: str = "") -> str:
        return f"{self._base}/{self._project}/_apis/wit{path}"

    # ── Repository tree ──────────────────────────────────────────

    async def get_repo_tree(self, scope_path: str = "/", depth: int = 2) -> RepoTree:
        """Fetch the repository file/folder tree at *scope_path*.

        Uses the Items endpoint with recursion to get structure without
        downloading file contents — very cheap.

        Args:
            scope_path: Root path to enumerate (e.g. "/src").
            depth: 1 = immediate children, 2 = two levels deep.
        """
        recursion = "OneLevel" if depth <= 1 else "Full"
        url = self._git_url("/items")
        params: dict[str, Any] = {
            "scopePath": scope_path,
            "recursionLevel": recursion,
            "api-version": API_VERSION,
        }
        if self._branch:
            params["versionDescriptor.version"] = self._branch
            params["versionDescriptor.versionType"] = "branch"

        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

        tree = RepoTree()
        for item in data.get("value", []):
            if item.get("isFolder"):
                tree.folders.append(item["path"])
            else:
                tree.files.append(item["path"])

        logger.info("Tree at %s: %d folders, %d files", scope_path, len(tree.folders), len(tree.files))
        return tree

    # ── File contents ────────────────────────────────────────────

    async def get_file_content(self, path: str, branch: str | None = None) -> FileItem:
        """Download a single file's content via the Items endpoint.

        Args:
            path: Repo-relative path, e.g. "/src/main.py".
            branch: Override branch (defaults to configured default_branch).
        """
        url = self._git_url("/items")
        params: dict[str, Any] = {
            "path": path,
            "includeContent": "true",
            "api-version": API_VERSION,
        }
        version = branch or self._branch
        if version:
            params["versionDescriptor.version"] = version
            params["versionDescriptor.versionType"] = "branch"

        resp = await self._client.get(url, params=params)
        resp.raise_for_status()

        data = resp.json()
        content = data.get("content", "")
        return FileItem(
            path=path,
            content=content,
            size=len(content),
            commit_id=data.get("commitId", ""),
        )

    async def get_files_batch(self, paths: list[str], branch: str | None = None) -> list[FileItem]:
        """Fetch multiple files concurrently.

        Respects max_files_per_request from settings to avoid rate limiting.
        """
        limited = paths[: self._settings.max_files_per_request]
        if len(paths) > len(limited):
            logger.warning(
                "Truncated file list from %d to %d (max_files_per_request)",
                len(paths),
                len(limited),
            )

        results: list[FileItem] = []
        for path in limited:
            try:
                item = await self.get_file_content(path, branch)
                results.append(item)
            except httpx.HTTPStatusError as exc:
                logger.warning("Failed to fetch %s: %s", path, exc.response.status_code)

        logger.info("Fetched %d/%d files", len(results), len(limited))
        return results

    # ── Work items ───────────────────────────────────────────────

    async def get_work_item(self, work_item_id: int) -> dict[str, Any]:
        """Fetch a single work item by ID with all fields."""
        url = self._wit_url(f"/workitems/{work_item_id}")
        params: dict[str, Any] = {
            "$expand": "all",
            "api-version": API_VERSION,
        }
        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_work_item_comments(self, work_item_id: int) -> list[dict[str, Any]]:
        """Fetch comments on a work item."""
        url = self._wit_url(f"/workitems/{work_item_id}/comments")
        params: dict[str, Any] = {"api-version": f"{API_VERSION}-preview"}
        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.json().get("comments", [])

    async def update_work_item(self, work_item_id: int, operations: list[dict[str, Any]]) -> dict[str, Any]:
        """Update a work item using JSON Patch operations."""
        url = self._wit_url(f"/workitems/{work_item_id}")
        params = {"api-version": API_VERSION}
        resp = await self._client.patch(
            url,
            json=operations,
            params=params,
            headers={"Content-Type": "application/json-patch+json"},
        )
        resp.raise_for_status()
        return resp.json()

    # ── Branch operations ────────────────────────────────────────

    async def get_default_branch_ref(self) -> str:
        """Get the latest commit (objectId) of the default branch."""
        url = self._git_url("/refs")
        params: dict[str, Any] = {
            "filter": f"heads/{self._branch}",
            "api-version": API_VERSION,
        }
        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        refs = resp.json().get("value", [])
        if not refs:
            raise ValueError(f"Branch '{self._branch}' not found")
        return refs[0]["objectId"]

    async def create_branch(self, branch_name: str) -> dict[str, Any]:
        """Create a new branch from the tip of the default branch.

        Args:
            branch_name: Short name, e.g. "feature/agent-investigation-123".
        """
        source_commit = await self.get_default_branch_ref()
        url = self._git_url("/refs")
        params = {"api-version": API_VERSION}
        body = [
            {
                "name": f"refs/heads/{branch_name}",
                "oldObjectId": "0000000000000000000000000000000000000000",
                "newObjectId": source_commit,
            }
        ]
        resp = await self._client.post(url, json=body, params=params)
        resp.raise_for_status()
        result = resp.json()
        logger.info("Created branch %s from %s", branch_name, source_commit[:8])
        return result

    # ── Push (create/update files) ───────────────────────────────

    async def push_files(
        self,
        branch_name: str,
        files: dict[str, str],
        commit_message: str,
    ) -> dict[str, Any]:
        """Push one or more files as a single commit to an existing branch.

        Args:
            branch_name: Target branch, e.g. "feature/agent-123".
            files: Mapping of {path: content} to add/update.
            commit_message: Commit message.
        """
        # Resolve the branch tip
        url_refs = self._git_url("/refs")
        resp = await self._client.get(
            url_refs,
            params={"filter": f"heads/{branch_name}", "api-version": API_VERSION},
        )
        resp.raise_for_status()
        refs = resp.json().get("value", [])
        if not refs:
            raise ValueError(f"Branch '{branch_name}' not found — create it first")
        old_object_id = refs[0]["objectId"]

        changes = []
        for path, content in files.items():
            changes.append(
                {
                    "changeType": "add",
                    "item": {"path": path},
                    "newContent": {
                        "content": content,
                        "contentType": "rawtext",
                    },
                }
            )

        body = {
            "refUpdates": [
                {
                    "name": f"refs/heads/{branch_name}",
                    "oldObjectId": old_object_id,
                }
            ],
            "commits": [
                {
                    "comment": commit_message,
                    "changes": changes,
                }
            ],
        }

        url_push = self._git_url("/pushes")
        resp = await self._client.post(url_push, json=body, params={"api-version": API_VERSION})
        resp.raise_for_status()
        result = resp.json()
        logger.info("Pushed %d files to %s", len(files), branch_name)
        return result

    # ── Pull requests ────────────────────────────────────────────

    async def create_pull_request(
        self,
        source_branch: str,
        title: str,
        description: str,
        target_branch: str | None = None,
        work_item_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Create a pull request.

        Args:
            source_branch: e.g. "feature/agent-123".
            title: PR title.
            description: PR description (Markdown).
            target_branch: Defaults to the configured default branch.
            work_item_ids: Optional work item IDs to link.
        """
        target = target_branch or self._branch
        body: dict[str, Any] = {
            "sourceRefName": f"refs/heads/{source_branch}",
            "targetRefName": f"refs/heads/{target}",
            "title": title,
            "description": description,
        }

        if work_item_ids:
            body["workItemRefs"] = [{"id": str(wid)} for wid in work_item_ids]

        url = self._git_url("/pullrequests")
        resp = await self._client.post(url, json=body, params={"api-version": API_VERSION})
        resp.raise_for_status()
        result = resp.json()
        logger.info("Created PR #%s: %s", result.get("pullRequestId"), title)
        return result

    # ── Cleanup ──────────────────────────────────────────────────

    async def close(self) -> None:
        await self._client.aclose()
