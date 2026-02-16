"""Tests for the Azure DevOps REST API client."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.clients.devops import AzureDevOpsClient, FileItem, RepoTree
from src.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        azure_devops_org_url="https://dev.azure.com/test-org",
        azure_devops_pat="fake-pat-token",
        azure_devops_project="test-project",
        azure_devops_repository="test-repo",
    )


@pytest.fixture
def client(settings: Settings) -> AzureDevOpsClient:
    return AzureDevOpsClient(settings)


class TestRepoTree:
    async def test_get_repo_tree_parses_response(self, client: AzureDevOpsClient) -> None:
        mock_response = httpx.Response(
            200,
            json={
                "value": [
                    {"path": "/src", "isFolder": True},
                    {"path": "/src/main.py", "isFolder": False},
                    {"path": "/tests", "isFolder": True},
                    {"path": "/README.md", "isFolder": False},
                ]
            },
            request=httpx.Request("GET", "https://example.com"),
        )

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_response):
            tree = await client.get_repo_tree("/")

        assert "/src" in tree.folders
        assert "/tests" in tree.folders
        assert "/src/main.py" in tree.files
        assert "/README.md" in tree.files

    async def test_get_repo_tree_handles_empty(self, client: AzureDevOpsClient) -> None:
        mock_response = httpx.Response(
            200,
            json={"value": []},
            request=httpx.Request("GET", "https://example.com"),
        )

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_response):
            tree = await client.get_repo_tree("/")

        assert tree.folders == []
        assert tree.files == []


class TestFileContent:
    async def test_get_file_content(self, client: AzureDevOpsClient) -> None:
        mock_response = httpx.Response(
            200,
            json={
                "content": "print('hello')\n",
                "commitId": "abc123",
            },
            request=httpx.Request("GET", "https://example.com"),
        )

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_response):
            item = await client.get_file_content("/src/main.py")

        assert item.path == "/src/main.py"
        assert item.content == "print('hello')\n"
        assert item.commit_id == "abc123"

    async def test_get_files_batch_respects_limit(self, settings: Settings) -> None:
        settings.max_files_per_request = 2
        client = AzureDevOpsClient(settings)

        mock_response = httpx.Response(
            200,
            json={"content": "x", "commitId": "abc"},
            request=httpx.Request("GET", "https://example.com"),
        )

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_response):
            items = await client.get_files_batch(["/a.py", "/b.py", "/c.py", "/d.py"])

        # Should only fetch 2 (the limit)
        assert len(items) == 2


class TestWorkItems:
    async def test_get_work_item(self, client: AzureDevOpsClient) -> None:
        mock_response = httpx.Response(
            200,
            json={
                "id": 123,
                "fields": {
                    "System.Title": "Test Feature",
                    "System.Description": "Add a new widget",
                    "System.Tags": "feature;backend",
                },
            },
            request=httpx.Request("GET", "https://example.com"),
        )

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_response):
            wi = await client.get_work_item(123)

        assert wi["fields"]["System.Title"] == "Test Feature"


class TestBranchOperations:
    async def test_create_branch(self, client: AzureDevOpsClient) -> None:
        ref_response = httpx.Response(
            200,
            json={"value": [{"objectId": "abc123def456"}]},
            request=httpx.Request("GET", "https://example.com"),
        )
        create_response = httpx.Response(
            200,
            json={"value": [{"name": "refs/heads/feature/test"}]},
            request=httpx.Request("POST", "https://example.com"),
        )

        with (
            patch.object(client._client, "get", new_callable=AsyncMock, return_value=ref_response),
            patch.object(client._client, "post", new_callable=AsyncMock, return_value=create_response),
        ):
            result = await client.create_branch("feature/test")

        assert result["value"][0]["name"] == "refs/heads/feature/test"
