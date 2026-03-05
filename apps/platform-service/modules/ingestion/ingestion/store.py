from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Callable

from .errors import err_job_not_found
from .schemas import IngestionJobRecord


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, IngestionJobRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, job: IngestionJobRecord) -> IngestionJobRecord:
        async with self._lock:
            self._jobs[job.job_id] = job
            return job

    async def get(self, job_id: str) -> IngestionJobRecord:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise err_job_not_found()
            return job

    async def update(self, job_id: str, updater: Callable[[IngestionJobRecord], None]) -> IngestionJobRecord:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise err_job_not_found()
            updater(job)
            job.updated_at = datetime.now(tz=timezone.utc)
            self._jobs[job_id] = job
            return job

