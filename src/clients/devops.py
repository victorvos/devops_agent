"""Azure DevOps REST API client.

Provides targeted, cost-efficient access to:
  - Git items (file tree + contents)
  - Work items
  - Branch creation
  - Pull request creation

Uses httpx for async HTTP instead of cloning the full repo.

Auth modes:
  - managed_identity: Bearer auth via azure-identity — preferred in Container Apps.
  - system_token: Bearer auth with $(System.AccessToken) — for Azure Pipelines / CI.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.config import DevOpsAuthMode, Settings

logger = logging.getLogger(__name__)

API_VERSION = "7.1"

DEVOPS_API_SCOPE = "499b84ac-1321-427f-aa17-267ca6975798/.default"

# Relation type for "parent" link (child → parent in hierarchy).
PARENT_RELATION_TYPE = "System.LinkTypes.Hierarchy-Reverse"

# URL pattern to extract work item id: .../workItems/123 or .../workItems/123?...
_WIT_ID_RE = re.compile(r"/workitems/(\d+)(?:\?|$)", re.IGNORECASE)


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


def _build_auth_header(settings: Settings) -> str:
    """Build the Authorization header value based on auth mode.

    For managed_identity, acquires a token synchronously at startup.
    The token is cached by azure-identity and refreshed automatically.
    """
    if settings.devops_auth_mode == DevOpsAuthMode.MANAGED_IDENTITY:
        from azure.identity import ManagedIdentityCredential

        logger.info("Using Managed Identity for DevOps API auth")
        credential = ManagedIdentityCredential(
            client_id=settings.managed_identity_client_id or None
        )
        token = credential.get_token(DEVOPS_API_SCOPE)
        return f"Bearer {token.token}"

    logger.info("Using System.AccessToken (Bearer) for DevOps API auth")
    return f"Bearer {settings.system_access_token}"


class AzureDevOpsClient:
    """Async client for Azure DevOps Git & Work Item REST APIs.

    Designed for *targeted retrieval* — fetches only the files the agent
    needs rather than cloning the entire repository.

    Auth modes:
      - managed_identity: User-assigned Managed Identity (Container App).
      - system_token: $(System.AccessToken) from the pipeline (Bearer).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base = settings.azure_devops_org_url.rstrip("/")
        self._project = settings.azure_devops_project
        self._repo = settings.azure_devops_repository
        self._branch = settings.default_branch

        self._client = httpx.AsyncClient(
            headers={
                "Authorization": _build_auth_header(settings),
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
        """Download a single file's content via the Items endpoint."""
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
        """Fetch a single work item by ID with all fields (includes relations when $expand=all)."""
        url = self._wit_url(f"/workitems/{work_item_id}")
        params: dict[str, Any] = {
            "$expand": "all",
            "api-version": API_VERSION,
        }
        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def _parent_work_item_id_from_relations(self, work_item: dict[str, Any]) -> int | None:
        """Extract parent work item ID from a work item's relations, if present."""
        relations = work_item.get("relations") or []
        for rel in relations:
            if rel.get("rel") != PARENT_RELATION_TYPE:
                continue
            url = rel.get("url") or ""
            match = _WIT_ID_RE.search(url)
            if match:
                return int(match.group(1))
        return None

    async def get_parent_work_item(self, work_item_id: int) -> dict[str, Any] | None:
        """Fetch the parent work item of the given work item, if it has one.

        Uses the work item's relations (Hierarchy-Reverse = parent). Returns None
        if there is no parent or the parent cannot be fetched.
        """
        try:
            wi = await self.get_work_item(work_item_id)
            parent_id = self._parent_work_item_id_from_relations(wi)
            if parent_id is None:
                return None
            parent = await self.get_work_item(parent_id)
            logger.info("Resolved parent work item #%d for WI #%d", parent_id, work_item_id)
            return parent
        except Exception as exc:
            logger.warning("Could not fetch parent for work item #%d: %s", work_item_id, exc)
            return None

    async def get_work_item_comments(self, work_item_id: int) -> list[dict[str, Any]]:
        """Fetch comments on a work item."""
        url = self._wit_url(f"/workitems/{work_item_id}/comments")
        params: dict[str, Any] = {"api-version": f"{API_VERSION}-preview"}
        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.json().get("comments", [])

    async def add_work_item_comment(self, work_item_id: int, text: str) -> dict[str, Any]:
        """Append a comment to a work item. Never removes existing content.

        This is the preferred way to post agent results — comments are
        immutable entries in the work item history and cannot overwrite
        or delete existing information.
        """
        url = self._wit_url(f"/workitems/{work_item_id}/comments")
        params: dict[str, Any] = {"api-version": f"{API_VERSION}-preview"}
        resp = await self._client.post(
            url,
            json={"text": text},
            params=params,
        )
        resp.raise_for_status()
        logger.info("Added comment to work item #%d", work_item_id)
        return resp.json()

    async def update_work_item(self, work_item_id: int, operations: list[dict[str, Any]]) -> dict[str, Any]:
        """Update a work item using JSON Patch operations.

        SAFETY: Only 'add' operations are allowed. The 'replace' and
        'remove' ops are rejected to prevent accidental data loss.
        """
        for op in operations:
            op_type = op.get("op", "").lower()
            if op_type in ("replace", "remove"):
                raise ValueError(
                    f"Unsafe operation '{op_type}' blocked on work item #{work_item_id}. "
                    f"Only 'add' operations are allowed to prevent data loss. "
                    f"Path: {op.get('path', '?')}"
                )

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
        """Create a new branch from the tip of the default branch."""
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
        """Push one or more files as a single commit to an existing branch."""
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
        """Create a pull request."""
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
