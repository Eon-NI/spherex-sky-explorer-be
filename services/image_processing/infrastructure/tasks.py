from celery import shared_task
from django.conf import settings

from services.image_processing.application.use_cases import CreateFitsCutout
from services.image_processing.domain.models import CutoutRequest
from services.image_processing.infrastructure.clients import ObservationApiResolver
from services.image_processing.infrastructure.downloader import IrsaCutoutDownloader
from services.image_processing.infrastructure.fits_reader import AstropyFitsReader
from services.image_processing.infrastructure.storage import S3ScientificObjectStore


@shared_task(name="eon.image_processing.create_cutout", bind=True,
             autoretry_for=(OSError, TimeoutError, ConnectionError),
             retry_backoff=True, retry_kwargs={"max_retries": 3})
def create_cutout(self, product_id: str, ra_deg: float, dec_deg: float, size_deg: float) -> dict:
    from services.image_processing.infrastructure.repository import DjangoCutoutRepository
    request = CutoutRequest(product_id, ra_deg, dec_deg, size_deg)
    reference = CreateFitsCutout(
        ObservationApiResolver(), IrsaCutoutDownloader(), S3ScientificObjectStore(),
        AstropyFitsReader(), DjangoCutoutRepository(), settings.EON_MAX_CUTOUT_BYTES,
    ).execute(request)
    return {"cutout_id": request.idempotency_key, "object_key": reference.object_key,
            "metadata_url": f"/api/v1/cutouts/{request.idempotency_key}/metadata/"}


@shared_task(name="eon.image_processing.render_timelapse", time_limit=600)
def render_timelapse(job_id: str, product_ids: list[str], ra_deg: float,
                     dec_deg: float, size_deg: float, detector: str = "SPHEREx-D2") -> dict:
    """BE-03 supplies job_id/task_id and owns lifecycle; this task only returns outputs."""
    from services.image_processing.application.timelapse import ProcessTimelapse
    from services.image_processing.domain.timelapse import TimelapseRequest
    from services.image_processing.infrastructure.timelapse import FitsSequenceRenderer
    request = TimelapseRequest(job_id, product_ids, ra_deg, dec_deg, size_deg, detector)
    return ProcessTimelapse(ObservationApiResolver(), IrsaCutoutDownloader(),
                           S3ScientificObjectStore(), FitsSequenceRenderer()).execute(request)
