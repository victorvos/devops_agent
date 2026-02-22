"""Shared test fixtures.

All fixtures defined here are available to every test module
automatically via pytest's conftest discovery.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.agent.state import AgentState
from src.infrastructure.clients.devops import AzureDevOpsClient
from src.core.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Default test settings with system_token auth."""
    return Settings(
        azure_devops_org_url="https://dev.azure.com/test-org",
        azure_devops_project="test-project",
        azure_devops_repository="test-repo",
        devops_auth_mode="system_token",
        system_access_token="fake-system-token",
    )


@pytest.fixture
def settings_managed_identity() -> Settings:
    """Test settings with Managed Identity auth (Container App mode)."""
    return Settings(
        azure_devops_org_url="https://dev.azure.com/test-org",
        azure_devops_project="test-project",
        azure_devops_repository="test-repo",
        devops_auth_mode="managed_identity",
        managed_identity_client_id="00000000-0000-0000-0000-000000000000",
    )


@pytest.fixture
def devops_client(settings: Settings) -> AzureDevOpsClient:
    """Azure DevOps client initialized with system_token auth."""
    return AzureDevOpsClient(settings)


@pytest.fixture
def mock_llm() -> AsyncMock:
    """Mock LLM that returns empty content by default.

    Override ``mock_llm.ainvoke.return_value`` in individual tests.
    """
    llm = AsyncMock()
    llm.ainvoke.return_value = MagicMock(content="{}")
    return llm


@pytest.fixture
def agent_state() -> AgentState:
    """Minimal agent state for testing."""
    return AgentState(
        work_item_id=123,
        work_item_title="Test Work Item",
        work_item_description="A test description",
        request_type="investigation",
        repo_tree_summary="  [dir]  /src\n  [file] /src/main.py",
    )
