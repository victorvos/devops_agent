"""Unit tests for the Azure DevOps REST API client.

All external HTTP calls are mocked. Tests follow Arrange-Act-Assert.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.clients.devops import AzureDevOpsClient
from src.config import Settings


class TestRepoTree:
    async def test_get_repo_tree_parses_folders_and_files(
        self, devops_client: AzureDevOpsClient
    ) -> None:
        # Arrange
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

        # Act
        with patch.object(devops_client._client, "get", new_callable=AsyncMock, return_value=mock_response):
            tree = await devops_client.get_repo_tree("/")

        # Assert
        assert "/src" in tree.folders
        assert "/tests" in tree.folders
        assert "/src/main.py" in tree.files
        assert "/README.md" in tree.files

    async def test_get_repo_tree_handles_empty_response(
        self, devops_client: AzureDevOpsClient
    ) -> None:
        # Arrange
        mock_response = httpx.Response(
            200,
            json={"value": []},
            request=httpx.Request("GET", "https://example.com"),
        )

        # Act
        with patch.object(devops_client._client, "get", new_callable=AsyncMock, return_value=mock_response):
            tree = await devops_client.get_repo_tree("/")

        # Assert
        assert tree.folders == []
        assert tree.files == []


class TestFileContent:
    async def test_get_file_content_returns_item(
        self, devops_client: AzureDevOpsClient
    ) -> None:
        # Arrange
        mock_response = httpx.Response(
            200,
            json={"content": "print('hello')\n", "commitId": "abc123"},
            request=httpx.Request("GET", "https://example.com"),
        )

        # Act
        with patch.object(devops_client._client, "get", new_callable=AsyncMock, return_value=mock_response):
            item = await devops_client.get_file_content("/src/main.py")

        # Assert
        assert item.path == "/src/main.py"
        assert item.content == "print('hello')\n"
        assert item.commit_id == "abc123"

    async def test_get_files_batch_respects_max_limit(self, settings: Settings) -> None:
        # Arrange
        settings.max_files_per_request = 2
        client = AzureDevOpsClient(settings)
        mock_response = httpx.Response(
            200,
            json={"content": "x", "commitId": "abc"},
            request=httpx.Request("GET", "https://example.com"),
        )

        # Act
        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_response):
            items = await client.get_files_batch(["/a.py", "/b.py", "/c.py", "/d.py"])

        # Assert
        assert len(items) == 2


class TestWorkItems:
    async def test_get_work_item_returns_fields(
        self, devops_client: AzureDevOpsClient
    ) -> None:
        # Arrange
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

        # Act
        with patch.object(devops_client._client, "get", new_callable=AsyncMock, return_value=mock_response):
            wi = await devops_client.get_work_item(123)

        # Assert
        assert wi["fields"]["System.Title"] == "Test Feature"


class TestBranchOperations:
    async def test_create_branch_from_default(
        self, devops_client: AzureDevOpsClient
    ) -> None:
        # Arrange
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

        # Act
        with (
            patch.object(devops_client._client, "get", new_callable=AsyncMock, return_value=ref_response),
            patch.object(devops_client._client, "post", new_callable=AsyncMock, return_value=create_response),
        ):
            result = await devops_client.create_branch("feature/test")

        # Assert
        assert result["value"][0]["name"] == "refs/heads/feature/test"
