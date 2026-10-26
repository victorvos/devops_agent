"""Unit tests for the Service Bus worker.

All external calls (DevOps API, LLM, Service Bus) are mocked.
Tests follow Arrange-Act-Assert.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infrastructure.repositories.job_store import InMemoryJobStore
from src.presentation.worker import _process_message, _update_job


@pytest.fixture(autouse=True)
def _patch_job_store():
    """Provide a clean in-process JobStore for each test."""
    store = InMemoryJobStore()
    with patch("src.presentation.worker._get_job_store", AsyncMock(return_value=store)):
        yield store


class TestUpdateJob:
    async def test_updates_existing_job(self, _patch_job_store: InMemoryJobStore) -> None:
        # Arrange
        await _patch_job_store.create_job("j1", {})

        # Act
        await _update_job("j1", status="processing")

        # Assert
        job = await _patch_job_store.get_job("j1")
        assert job is not None
        assert job["status"] == "processing"

    async def test_ignores_unknown_job(self, _patch_job_store: InMemoryJobStore) -> None:
        # Act — should not raise
        await _update_job("nonexistent", status="processing")

        # Assert
        assert await _patch_job_store.get_job("nonexistent") is None


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
        _patch_job_store: InMemoryJobStore,
        mock_graph: AsyncMock,
    ) -> None:
        # Arrange
        await _patch_job_store.create_job("job-abc", {})
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
            patch("src.presentation.worker.get_settings") as mock_get_settings,
            patch("src.presentation.worker.AzureDevOpsClient", return_value=mock_devops),
            patch("src.presentation.worker.get_chat_model", return_value=MagicMock()),
            patch("src.presentation.worker.build_graph", return_value=mock_graph),
        ):
            mock_get_settings.return_value = MagicMock()
            await _process_message(payload)

        # Assert
        job = await _patch_job_store.get_job("job-abc")
        assert job is not None
        assert job["status"] == "completed"
        assert job["result"]["summary"] == "Found a bug in auth module"
        mock_devops.close.assert_awaited_once()

    async def test_process_message_marks_failed_on_error(
        self,
        _patch_job_store: InMemoryJobStore,
    ) -> None:
        # Arrange
        await _patch_job_store.create_job("job-fail", {})
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
            patch("src.presentation.worker.get_settings", return_value=MagicMock()),
            patch("src.presentation.worker.AzureDevOpsClient", return_value=mock_devops),
            patch("src.presentation.worker.get_chat_model", return_value=MagicMock()),
            patch("src.presentation.worker.build_graph", return_value=mock_graph),
        ):
            with pytest.raises(RuntimeError, match="LLM unavailable"):
                await _process_message(payload)

        # Assert
        job = await _patch_job_store.get_job("job-fail")
        assert job is not None
        assert job["status"] == "failed"
        assert "LLM unavailable" in job["error"]
        mock_devops.close.assert_awaited_once()
