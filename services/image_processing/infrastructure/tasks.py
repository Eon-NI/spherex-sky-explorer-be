import os
from celery import shared_task
from django.conf import settings
from services.image_processing.application.use_cases import CreateFitsCutout
from services.image_processing.domain.models import CutoutRequest
from services.image_processing.infrastructure.clients import ObservationApiResolver
from services.image_processing.infrastructure.downloader import IrsaCutoutDownloader
from services.image_processing.infrastructure.fits_reader import AstropyFitsReader
from services.image_processing.infrastructure.repository import DjangoCutoutRepository
from services.image_processing.infrastructure.storage import S3ScientificObjectStore


@shared_task(name="eon.image_processing.create_cutout", bind=True,
             autoretry_for=(OSError, TimeoutError, ConnectionError),
             retry_backoff=True, retry_kwargs={"max_retries": 3})
def create_cutout(self, product_id: str, ra_deg: float, dec_deg: float, size_deg: float) -> dict:
    request = CutoutRequest(product_id, ra_deg, dec_deg, size_deg)
    reference = CreateFitsCutout(
        ObservationApiResolver(), IrsaCutoutDownloader(), S3ScientificObjectStore(),
        AstropyFitsReader(), DjangoCutoutRepository(), settings.EON_MAX_CUTOUT_BYTES,
    ).execute(request)
    return {"cutout_id": request.idempotency_key, "object_key": reference.object_key,
            "metadata_url": f"/api/v1/cutouts/{request.idempotency_key}/metadata/"}
