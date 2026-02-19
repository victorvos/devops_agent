"""Unit tests for the FastAPI application.

All Service Bus interactions are mocked. Tests follow Arrange-Act-Assert.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api import app, _job_store


def _ensure_azure_servicebus_mock():
    """Ensure azure.servicebus can be imported when the package is not installed (e.g. in tests)."""
    if "azure" not in sys.modules:
        sys.modules["azure"] = MagicMock()
    if "azure.servicebus" not in sys.modules:
        mod = MagicMock()
        mod.ServiceBusMessage = MagicMock()
        sys.modules["azure.servicebus"] = mod


@pytest.fixture(autouse=True)
def _clear_job_store():
    """Ensure the in-process job store is empty before each test."""
    _job_store.clear()
    yield
    _job_store.clear()


@pytest.fixture
def client():
    """FastAPI test client (sync, no real Service Bus)."""
    return TestClient(app, raise_server_exceptions=False)


class TestHealthEndpoint:
    def test_health_returns_200(self, client: TestClient) -> None:
        # Act
        response = client.get("/health")

        # Assert
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


class TestInvestigateEndpoint:
    def test_investigate_enqueues_and_returns_job_id(self, client: TestClient) -> None:
        # Arrange
        mock_sender = AsyncMock()
        mock_sender.__aenter__ = AsyncMock(return_value=mock_sender)
        mock_sender.__aexit__ = AsyncMock(return_value=False)
        mock_sender.send_messages = AsyncMock()

        mock_sb = MagicMock()
        mock_sb.get_queue_sender.return_value = mock_sender

        mock_settings = MagicMock()
        mock_settings.service_bus_queue_name = "agent-requests"

        _ensure_azure_servicebus_mock()

        # Act
        with (
            patch("src.api._sb_client", mock_sb),
            patch("src.api.get_settings", return_value=mock_settings),
        ):
            response = client.post(
                "/api/investigate",
                json={
                    "work_item_id": 1234,
                    "request_type": "bug",
                    "context": "test context",
                    "report_only": True,
                },
            )

        # Assert
        assert response.status_code == 200, f"Got {response.status_code}: {response.text}"
        data = response.json()
        assert data["status"] == "queued"
        assert "job_id" in data
        mock_sender.send_messages.assert_called_once()

    def test_investigate_runs_direct_mode_without_sb(self, client: TestClient) -> None:
        # Arrange — _sb_client is None → direct mode

        # Act
        with (
            patch("src.api._sb_client", None),
            patch("src.api._run_agent_direct", new_callable=AsyncMock) as mock_run,
        ):
            response = client.post(
                "/api/investigate",
                json={"work_item_id": 1234},
            )

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processing"
        assert "direct mode" in data["message"]

    def test_investigate_rejects_missing_work_item_id(self, client: TestClient) -> None:
        # Act
        response = client.post("/api/investigate", json={})

        # Assert
        assert response.status_code == 422


class TestStatusEndpoint:
    def test_status_returns_queued_job(self, client: TestClient) -> None:
        # Arrange
        _job_store["test-123"] = {
            "status": "queued",
            "created_at": "2026-02-16T12:00:00+00:00",
            "completed_at": None,
            "result": None,
            "error": None,
        }

        # Act
        response = client.get("/api/status/test-123")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "test-123"
        assert data["status"] == "queued"

    def test_status_returns_completed_job(self, client: TestClient) -> None:
        # Arrange
        _job_store["test-456"] = {
            "status": "completed",
            "created_at": "2026-02-16T12:00:00+00:00",
            "completed_at": "2026-02-16T12:01:30+00:00",
            "result": {"summary": "Investigation complete", "files_reviewed": 8},
            "error": None,
        }

        # Act
        response = client.get("/api/status/test-456")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["result"]["files_reviewed"] == 8

    def test_status_returns_404_for_unknown_job(self, client: TestClient) -> None:
        # Act
        response = client.get("/api/status/nonexistent")

        # Assert
        assert response.status_code == 404

    def test_status_returns_failed_job(self, client: TestClient) -> None:
        # Arrange
        _job_store["test-err"] = {
            "status": "failed",
            "created_at": "2026-02-16T12:00:00+00:00",
            "completed_at": "2026-02-16T12:00:05+00:00",
            "result": None,
            "error": "LLM timeout",
        }

        # Act
        response = client.get("/api/status/test-err")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert data["error"] == "LLM timeout"
