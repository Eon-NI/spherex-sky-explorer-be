"""Daily metadata-only regional synchronization across current SPHEREx collections."""
import json
import logging
import os
from celery import shared_task
from django.conf import settings
from redis import Redis
from services.observation.application.use_cases import SyncObservations
from services.observation.domain.models import COLLECTIONS, SkySearch
from services.observation.infrastructure.cache import ObservationSearchCache
from services.observation.infrastructure.irsa import IrsaSiaCatalog
from services.observation.infrastructure.repository import DjangoObservationRepository

logger = logging.getLogger(__name__)


def configured_regions() -> list[SkySearch]:
    raw = os.getenv("IRSA_SYNC_REGIONS", "").strip()
    if raw:
        regions = json.loads(raw)
        if not isinstance(regions, list) or len(regions) > 20:
            raise ValueError("IRSA_SYNC_REGIONS must contain at most 20 regions")
        return [SkySearch(float(item["ra_deg"]), float(item["dec_deg"]),
                          float(item["radius_deg"]), COLLECTIONS) for item in regions]
    legacy = ("IRSA_SYNC_RA_DEG", "IRSA_SYNC_DEC_DEG", "IRSA_SYNC_RADIUS_DEG")
    if all(os.getenv(key) for key in legacy):
        return [SkySearch(*(float(os.environ[key]) for key in legacy), collections=COLLECTIONS)]
    return []


@shared_task(name="services.observation.infrastructure.tasks.sync_irsa",
             autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def sync_irsa() -> int:
    regions = configured_regions()
    if not regions:
        logger.info("IRSA sync skipped: no regions configured")
        return 0
    lock = Redis.from_url(settings.CELERY_BROKER_URL).lock(
        "eon:observation:sync-lock", timeout=3600, blocking=False
    )
    if not lock.acquire(blocking=False):
        logger.info("IRSA sync skipped: prior run active")
        return 0
    try:
        repository = DjangoObservationRepository()
        catalog = IrsaSiaCatalog()
        changed = 0
        cache = ObservationSearchCache(repository)
        for region in regions:
            affected = SyncObservations(catalog, repository).execute(region)
            changed += affected
            if affected:
                cache.invalidate_all()
        logger.info("IRSA metadata sync finished", extra={"changed_count": changed,
                                                          "region_count": len(regions)})
        return changed
    finally:
        if lock.owned():
            lock.release()
