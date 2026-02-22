"""Job store implementations (in-memory and Azure Table Storage)."""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.core.interfaces.job_store import JobStore

logger = logging.getLogger(__name__)


class InMemoryJobStore:
    """Fallback in-memory job store for local testing."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    async def initialize(self) -> None:
        pass

    async def create_job(self, job_id: str, payload: dict[str, Any]) -> None:
        self._store[job_id] = {
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "result": None,
            "error": None,
            "payload": payload,
        }

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self._store.get(job_id)

    async def update_job(
        self,
        job_id: str,
        status: str,
        completed_at: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        job = self._store.get(job_id)
        if not job:
            return
        job["status"] = status
        if completed_at:
            job["completed_at"] = completed_at
        if result is not None:
            job["result"] = result
        if error is not None:
            job["error"] = error

    async def close(self) -> None:
        pass


class AzureTableJobStore:
    """Azure Table Storage implementation for distributed job state."""

    def __init__(self, connection_str: str, table_name: str = "agentjobs") -> None:
        from azure.data.tables.aio import TableServiceClient

        self._service_client = TableServiceClient.from_connection_string(conn_str=connection_str)
        self._table_name = table_name
        self._table_client = self._service_client.get_table_client(table_name)

    async def initialize(self) -> None:
        """Ensure the table exists."""
        try:
            await self._service_client.create_table_if_not_exists(self._table_name)
        except Exception as exc:
            logger.warning("Failed to create table %s: %s", self._table_name, exc)

    async def create_job(self, job_id: str, payload: dict[str, Any]) -> None:
        entity = {
            "PartitionKey": "jobs",
            "RowKey": job_id,
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": json.dumps(payload),
        }
        await self._table_client.create_entity(entity)

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            entity = await self._table_client.get_entity(partition_key="jobs", row_key=job_id)
            result = {
                "status": entity.get("status"),
                "created_at": entity.get("created_at"),
                "completed_at": entity.get("completed_at"),
                "error": entity.get("error"),
            }
            if "result" in entity and entity["result"]:
                result["result"] = json.loads(entity["result"])
            else:
                result["result"] = None
            return result
        except ResourceNotFoundError:
            return None

    async def update_job(
        self,
        job_id: str,
        status: str,
        completed_at: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        from azure.core.exceptions import ResourceNotFoundError
        from azure.data.tables import UpdateMode

        try:
            entity = await self._table_client.get_entity(partition_key="jobs", row_key=job_id)
            entity["status"] = status
            if completed_at:
                entity["completed_at"] = completed_at
            if result is not None:
                entity["result"] = json.dumps(result)
            if error is not None:
                entity["error"] = error
            
            await self._table_client.update_entity(entity, mode=UpdateMode.REPLACE)
        except ResourceNotFoundError:
            logger.warning("Tried to update non-existent job %s", job_id)

    async def close(self) -> None:
        """Close the table service client."""
        await self._service_client.close()
