"""Shared test fixtures.

All fixtures defined here are available to every test module
automatically via pytest's conftest discovery.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.state import AgentState
from src.clients.devops import AzureDevOpsClient
from src.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Default test settings with fake credentials."""
    return Settings(
        azure_devops_org_url="https://dev.azure.com/test-org",
        azure_devops_pat="fake-pat-token",
        azure_devops_project="test-project",
        azure_devops_repository="test-repo",
    )


@pytest.fixture
def devops_client(settings: Settings) -> AzureDevOpsClient:
    """Azure DevOps client initialized with test settings."""
    return AzureDevOpsClient(settings)


@pytest.fixture
def mock_llm() -> AsyncMock:
    """Mock LLM that returns empty content by default.

    Override `mock_llm.ainvoke.return_value` in individual tests.
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
