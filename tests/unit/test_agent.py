"""Unit tests for the LangGraph agent nodes.

All LLM calls are mocked. Tests follow Arrange-Act-Assert.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.infrastructure.agent.nodes import _extract_json, plan_files, reason
from src.core.agent.state import AgentAction, AgentState
from src.core.utils.tokens import build_context_block, count_tokens, truncate_to_budget


class TestExtractJSON:
    """JSON extraction from LLM output."""

    def test_plain_json(self) -> None:
        # Arrange
        text = '{"key": "value"}'

        # Act
        result = _extract_json(text)

        # Assert
        assert result == {"key": "value"}

    def test_markdown_fenced_json(self) -> None:
        # Arrange
        text = '```json\n{"key": "value"}\n```'

        # Act
        result = _extract_json(text)

        # Assert
        assert result == {"key": "value"}

    def test_json_with_surrounding_text(self) -> None:
        # Arrange
        text = 'Here is the result:\n{"key": "value"}\nEnd.'

        # Act
        result = _extract_json(text)

        # Assert
        assert result == {"key": "value"}

    def test_invalid_json_raises_value_error(self) -> None:
        # Act & Assert
        with pytest.raises(ValueError, match="Could not extract JSON"):
            _extract_json("not json at all")


class TestTokenUtils:
    """Token counting and budget management."""

    def test_count_tokens_returns_positive(self) -> None:
        # Act
        tokens = count_tokens("Hello world")

        # Assert
        assert 0 < tokens < 10

    def test_truncate_within_budget_returns_unchanged(self) -> None:
        # Arrange
        text = "Hello world"

        # Act
        result = truncate_to_budget(text, max_tokens=100)

        # Assert
        assert result == text

    def test_truncate_exceeding_budget_adds_marker(self) -> None:
        # Arrange
        text = "word " * 1000

        # Act
        result = truncate_to_budget(text, max_tokens=50)

        # Assert
        assert "truncated" in result.lower()
        assert count_tokens(result) <= 60

    def test_build_context_block_respects_token_budget(self) -> None:
        # Arrange
        files = [
            ("/file1.py", "x = 1\n" * 100),
            ("/file2.py", "y = 2\n" * 100),
            ("/file3.py", "z = 3\n" * 100),
        ]

        # Act
        result = build_context_block(files, max_tokens=200)

        # Assert
        assert count_tokens(result) <= 250


class TestPlanFiles:
    """LLM-based file planning node."""

    async def test_parses_valid_llm_response(self, mock_llm: AsyncMock) -> None:
        # Arrange
        mock_llm.ainvoke.return_value = MagicMock(
            content='{"reasoning": "Auth module is relevant", "files": [{"path": "/src/auth.py", "reason": "main auth logic"}]}'
        )
        state = AgentState(
            work_item_id=123,
            work_item_title="Fix auth bug",
            work_item_description="Login fails for SSO users",
            repo_tree_summary="  [dir]  /src\n  [file] /src/auth.py\n  [file] /src/main.py",
        )

        # Act
        result = await plan_files(state, llm=mock_llm)

        # Assert
        assert len(result["planned_files"]) == 1
        assert result["planned_files"][0].path == "/src/auth.py"
        assert "Auth" in result["plan_reasoning"]

    async def test_handles_unparseable_llm_response(self, mock_llm: AsyncMock) -> None:
        # Arrange
        mock_llm.ainvoke.return_value = MagicMock(content="Not valid JSON response")
        state = AgentState(
            work_item_id=123,
            repo_tree_summary="  [dir]  /src",
        )

        # Act
        result = await plan_files(state, llm=mock_llm)

        # Assert
        assert result["planned_files"] == []
        assert "Parse error" in result["plan_reasoning"]


class TestReason:
    """LLM-based reasoning node."""

    async def test_produces_bug_analysis(self, mock_llm: AsyncMock) -> None:
        # Arrange
        mock_llm.ainvoke.return_value = MagicMock(
            content='{"analysis": "The auth module has a bug in line 42.", "recommended_action": "bug_analysis", "suggested_file_changes": {}}'
        )
        state = AgentState(
            work_item_id=123,
            work_item_title="Auth bug",
            work_item_description="SSO login fails",
            request_type="bug",
            fetched_files=[("/src/auth.py", "def login():\n    pass")],
        )

        # Act
        result = await reason(state, llm=mock_llm, max_context_tokens=5000)

        # Assert
        assert "bug" in result["analysis"].lower()
        assert result["recommended_action"] == AgentAction.BUG_ANALYSIS

    async def test_falls_back_on_unparseable_response(self, mock_llm: AsyncMock) -> None:
        # Arrange
        mock_llm.ainvoke.return_value = MagicMock(
            content="Here is my free-form analysis of the code..."
        )
        state = AgentState(
            work_item_id=123,
            fetched_files=[("/src/main.py", "print('hello')")],
        )

        # Act
        result = await reason(state, llm=mock_llm, max_context_tokens=5000)

        # Assert
        assert result["analysis"] == "Here is my free-form analysis of the code..."
        assert result["recommended_action"] == AgentAction.INVESTIGATION_REPORT
