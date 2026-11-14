from dataclasses import dataclass
from enum import StrEnum


class JobState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    state: JobState
    result_key: str | None = None
    error: str | None = None
