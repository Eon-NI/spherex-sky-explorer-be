"""Job Service adapter over the existing Celery/Redis broker and result backend."""
import uuid

from celery.result import AsyncResult
from django.conf import settings
from redis import Redis

from eon_common.celery import app
from services.jobs.domain.models import Job, JobState


class CeleryJobQueue:
    def __init__(self) -> None:
        self.redis = Redis.from_url(settings.CELERY_BROKER_URL)

    def submit(self, task_name: str, payload: dict[str, object], idempotency_key: str) -> str:
        lock_key = f"eon:job:{task_name}:{idempotency_key}"
        job_id = str(uuid.uuid4())
        if not self.redis.set(lock_key, job_id, nx=True, ex=7 * 24 * 3600):
            existing = self.redis.get(lock_key)
            if existing:
                existing_id = existing.decode()
                if app.AsyncResult(existing_id).state != "FAILURE":
                    return existing_id
                self.redis.delete(lock_key)
            return self.submit(task_name, payload, idempotency_key)
        try:
            self.redis.setex(f"eon:job:id:{job_id}", 7 * 24 * 3600, "1")
            queue = "spectral" if task_name.startswith("eon.spectral.") else "image_processing"
            task_payload = payload
            if task_name == "eon.image_processing.render_timelapse":
                task_payload = {**payload, "job_id": job_id}
                self.redis.setex(f"eon:job:task:{job_id}", 7 * 24 * 3600, task_name)
            app.send_task(task_name, kwargs=task_payload, task_id=job_id, queue=queue)
        except Exception:
            self.redis.delete(lock_key)
            self.redis.delete(f"eon:job:id:{job_id}")
            self.redis.delete(f"eon:job:task:{job_id}")
            raise
        return job_id


class CeleryJobRepository:
    def task_name(self, job_id: str) -> str | None:
        value = Redis.from_url(settings.CELERY_BROKER_URL).get(f"eon:job:task:{job_id}")
        return value.decode() if value else None

    def get(self, job_id: str) -> Job | None:
        try:
            uuid.UUID(job_id)
        except ValueError:
            return None
        if not Redis.from_url(settings.CELERY_BROKER_URL).exists(f"eon:job:id:{job_id}"):
            return None
        result: AsyncResult = app.AsyncResult(job_id)
        state = {
            "PENDING": JobState.PENDING,
            "RECEIVED": JobState.PENDING,
            "STARTED": JobState.RUNNING,
            "RETRY": JobState.RUNNING,
            "PROGRESS": JobState.RUNNING,
            "SUCCESS": JobState.SUCCEEDED,
            "FAILURE": JobState.FAILED,
            "REVOKED": JobState.FAILED,
        }.get(result.state, JobState.PENDING)
        data = result.result if state == JobState.SUCCEEDED and isinstance(result.result, dict) else {}
        progress = (result.info.get("progress") if state == JobState.RUNNING
                    and isinstance(result.info, dict) else None)
        return Job(id=job_id, state=state, result_key=data.get("object_key"),
                   error="Processing failed" if state == JobState.FAILED else None,
                   result=data if data else None, progress=100 if state == JobState.SUCCEEDED else progress)
