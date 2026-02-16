"""Tests for the LangGraph agent nodes and graph."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.nodes import _extract_json, plan_files, reason
from src.agent.state import AgentAction, AgentState, PlannedFile
from src.utils.tokens import build_context_block, count_tokens, truncate_to_budget


# ── JSON extraction ──────────────────────────────────────────────


class TestExtractJSON:
    def test_plain_json(self) -> None:
        result = _extract_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_markdown_fenced_json(self) -> None:
        text = '```json\n{"key": "value"}\n```'
        result = _extract_json(text)
        assert result == {"key": "value"}

    def test_json_with_surrounding_text(self) -> None:
        text = 'Here is the result:\n{"key": "value"}\nEnd.'
        result = _extract_json(text)
        assert result == {"key": "value"}

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not extract JSON"):
            _extract_json("not json at all")


# ── Token utilities ──────────────────────────────────────────────


class TestTokenUtils:
    def test_count_tokens_basic(self) -> None:
        tokens = count_tokens("Hello world")
        assert tokens > 0
        assert tokens < 10

    def test_truncate_within_budget(self) -> None:
        text = "Hello world"
        result = truncate_to_budget(text, max_tokens=100)
        assert result == text  # no truncation needed

    def test_truncate_exceeds_budget(self) -> None:
        text = "word " * 1000  # ~1000 tokens
        result = truncate_to_budget(text, max_tokens=50)
        assert "truncated" in result.lower()
        assert count_tokens(result) <= 60  # small buffer for truncation message

    def test_build_context_block_respects_budget(self) -> None:
        files = [
            ("/file1.py", "x = 1\n" * 100),
            ("/file2.py", "y = 2\n" * 100),
            ("/file3.py", "z = 3\n" * 100),
        ]
        result = build_context_block(files, max_tokens=200)
        total = count_tokens(result)
        assert total <= 250  # allow small buffer


# ── Plan files node ──────────────────────────────────────────────


class TestPlanFiles:
    async def test_plan_files_parses_llm_response(self) -> None:
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content='{"reasoning": "Auth module is relevant", "files": [{"path": "/src/auth.py", "reason": "main auth logic"}]}'
        )

        state = AgentState(
            work_item_id=123,
            work_item_title="Fix auth bug",
            work_item_description="Login fails for SSO users",
            repo_tree_summary="  [dir]  /src\n  [file] /src/auth.py\n  [file] /src/main.py",
        )

        result = await plan_files(state, llm=mock_llm)

        assert len(result["planned_files"]) == 1
        assert result["planned_files"][0].path == "/src/auth.py"
        assert "Auth" in result["plan_reasoning"]

    async def test_plan_files_handles_parse_error(self) -> None:
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content="Not valid JSON response")

        state = AgentState(
            work_item_id=123,
            repo_tree_summary="  [dir]  /src",
        )

        result = await plan_files(state, llm=mock_llm)
        assert result["planned_files"] == []
        assert "Parse error" in result["plan_reasoning"]


# ── Reason node ──────────────────────────────────────────────────


class TestReason:
    async def test_reason_produces_analysis(self) -> None:
        mock_llm = AsyncMock()
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

        result = await reason(state, llm=mock_llm, max_context_tokens=5000)

        assert "bug" in result["analysis"].lower()
        assert result["recommended_action"] == AgentAction.BUG_ANALYSIS

    async def test_reason_handles_unparseable_response(self) -> None:
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content="Here is my free-form analysis of the code..."
        )

        state = AgentState(
            work_item_id=123,
            fetched_files=[("/src/main.py", "print('hello')")],
        )

        result = await reason(state, llm=mock_llm, max_context_tokens=5000)

        assert result["analysis"] == "Here is my free-form analysis of the code..."
        assert result["recommended_action"] == AgentAction.INVESTIGATION_REPORT
