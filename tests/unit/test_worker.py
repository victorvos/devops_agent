"""Unit tests for the Service Bus worker.

All external calls (DevOps API, LLM, Service Bus) are mocked.
Tests follow Arrange-Act-Assert.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.worker import _process_message, _update_job


@pytest.fixture(autouse=True)
def _patch_job_store():
    """Provide a clean in-process job store for each test."""
    store: dict = {}
    with patch("src.worker._job_store", store):
        with patch("src.worker._get_job_store", return_value=store):
            yield store


class TestUpdateJob:
    def test_updates_existing_job(self, _patch_job_store: dict) -> None:
        # Arrange
        _patch_job_store["j1"] = {"status": "queued"}

        # Act
        _update_job("j1", status="processing")

        # Assert
        assert _patch_job_store["j1"]["status"] == "processing"

    def test_ignores_unknown_job(self, _patch_job_store: dict) -> None:
        # Act — should not raise
        _update_job("nonexistent", status="processing")

        # Assert
        assert "nonexistent" not in _patch_job_store


class TestProcessMessage:
    @pytest.fixture
    def mock_graph(self) -> AsyncMock:
        graph = AsyncMock()
        graph.ainvoke.return_value = {
            "output_summary": "Found a bug in auth module",
            "output_branch": "",
            "output_pr_url": "",
            "planned_files": [],
            "work_item_id": 999,
            "request_text": "",
            "request_type": "investigation",
            "work_item_title": "",
            "work_item_description": "",
            "work_item_tags": [],
            "work_item_comments": [],
            "repo_tree_summary": "",
            "plan_reasoning": "",
            "fetched_files": [],
            "fetch_errors": [],
            "analysis": "",
            "recommended_action": "investigation_report",
            "suggested_file_changes": {},
            "messages": [],
            "error": "",
            "needs_more_context": False,
            "iteration": 1,
            "max_iterations": 3,
        }
        return graph

    async def test_process_message_runs_agent_and_marks_complete(
        self,
        _patch_job_store: dict,
        mock_graph: AsyncMock,
    ) -> None:
        # Arrange
        _patch_job_store["job-abc"] = {"status": "queued"}
        payload = {
            "job_id": "job-abc",
            "work_item_id": 999,
            "request_type": "investigation",
            "context": "",
            "report_only": True,
        }

        mock_devops = MagicMock()
        mock_devops.close = AsyncMock()

        # Act
        with (
            patch("src.worker.get_settings") as mock_get_settings,
            patch("src.worker.AzureDevOpsClient", return_value=mock_devops),
            patch("src.worker.get_chat_model", return_value=MagicMock()),
            patch("src.worker.build_graph", return_value=mock_graph),
        ):
            mock_get_settings.return_value = MagicMock()
            await _process_message(payload)

        # Assert
        assert _patch_job_store["job-abc"]["status"] == "completed"
        assert _patch_job_store["job-abc"]["result"]["summary"] == "Found a bug in auth module"
        mock_devops.close.assert_awaited_once()

    async def test_process_message_marks_failed_on_error(
        self,
        _patch_job_store: dict,
    ) -> None:
        # Arrange
        _patch_job_store["job-fail"] = {"status": "queued"}
        payload = {
            "job_id": "job-fail",
            "work_item_id": 111,
            "request_type": "bug",
            "context": "",
            "report_only": True,
        }

        mock_devops = MagicMock()
        mock_devops.close = AsyncMock()
        mock_graph = AsyncMock()
        mock_graph.ainvoke.side_effect = RuntimeError("LLM unavailable")

        # Act & Assert
        with (
            patch("src.worker.get_settings", return_value=MagicMock()),
            patch("src.worker.AzureDevOpsClient", return_value=mock_devops),
            patch("src.worker.get_chat_model", return_value=MagicMock()),
            patch("src.worker.build_graph", return_value=mock_graph),
        ):
            with pytest.raises(RuntimeError, match="LLM unavailable"):
                await _process_message(payload)

        # Assert
        assert _patch_job_store["job-fail"]["status"] == "failed"
        assert "LLM unavailable" in _patch_job_store["job-fail"]["error"]
        mock_devops.close.assert_awaited_once()
