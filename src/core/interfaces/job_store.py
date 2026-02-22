"""Job store interface."""

from typing import Any, Protocol


class JobStore(Protocol):
    """Protocol for storing and retrieving job status."""

    async def initialize(self) -> None:
        """Initialize the store (e.g. create tables/collections)."""
        ...

    async def create_job(self, job_id: str, payload: dict[str, Any]) -> None:
        """Initialize a new job with 'queued' status."""
        ...

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Retrieve job status."""
        ...

    async def update_job(
        self,
        job_id: str,
        status: str,
        completed_at: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Update job status."""
        ...
