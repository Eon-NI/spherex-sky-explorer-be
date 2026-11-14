import logging
import os
from celery import shared_task
from services.observation.application.use_cases import SyncObservations
from services.observation.domain.models import SkySearch
from services.observation.infrastructure.irsa import IrsaSiaCatalog
from services.observation.infrastructure.repository import DjangoObservationRepository

logger = logging.getLogger(__name__)


@shared_task(name="services.observation.infrastructure.tasks.sync_irsa", autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def sync_irsa() -> int:
    required = ("IRSA_SYNC_RA_DEG", "IRSA_SYNC_DEC_DEG", "IRSA_SYNC_RADIUS_DEG")
    if not all(os.getenv(key) for key in required):
        logger.info("IRSA scheduled sync skipped: region is not configured")
        return 0
    query = SkySearch(*(float(os.environ[key]) for key in required))
    return SyncObservations(IrsaSiaCatalog(), DjangoObservationRepository()).execute(query)
