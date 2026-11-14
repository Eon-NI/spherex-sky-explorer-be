from services.image_processing.domain.models import ImageReference
from services.image_processing.models import CutoutRecord


class DjangoCutoutRepository:
    def get(self, key: str) -> ImageReference | None:
        row = CutoutRecord.objects.filter(idempotency_key=key).first()
        return ImageReference(row.object_key) if row else None

    def get_record(self, key: str) -> CutoutRecord | None:
        return CutoutRecord.objects.filter(idempotency_key=key).first()

    def save(self, request, product, reference, metadata) -> None:
        CutoutRecord.objects.update_or_create(
            idempotency_key=request.idempotency_key,
            defaults={"product_id": request.product_id, "artifact_uri": product.artifact_uri,
                      "source_url": product.cutout_url, "object_key": reference.object_key,
                      "ra_deg": request.ra_deg, "dec_deg": request.dec_deg,
                      "size_deg": request.size_deg, "detector": product.detector,
                      "time_bounds_lower": product.time_bounds_lower,
                      "fits_version": metadata.version},
        )
