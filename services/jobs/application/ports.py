from typing import Protocol
from services.jobs.domain.models import Job


class JobRepository(Protocol):
    def get(self, job_id: str) -> Job | None: ...


class JobQueue(Protocol):
    def submit(self, task_name: str, payload: dict[str, object], idempotency_key: str) -> str: ...
