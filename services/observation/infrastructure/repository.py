from django.db import transaction
from django.utils import timezone
from services.observation.domain.models import Observation, TemporalObservation
from services.observation.models import ObservationRecord, TemporalObservationRecord

FIELDS = ("collection", "access_url", "ra_deg", "dec_deg", "obs_id", "access_format",
          "s_region", "energy_bandpassname", "t_min_mjd", "t_max_mjd", "em_min_m",
          "em_max_m", "processing_version", "missing_fields")


def _to_observation(row: ObservationRecord) -> Observation:
    return Observation(
        row.publisher_did, row.collection, row.access_url, row.ra_deg, row.dec_deg,
        row.obs_id, row.access_format, row.s_region, row.energy_bandpassname,
        row.t_min_mjd, row.t_max_mjd, row.em_min_m, row.em_max_m,
        tuple(row.missing_fields), row.processing_version,
    )


class DjangoObservationRepository:
    @transaction.atomic
    def upsert(self, observations: list[Observation]) -> int:
        identified = {item.publisher_did: item for item in observations if item.publisher_did}
        existing = ObservationRecord.objects.in_bulk(identified.keys())
        new_rows, changed_rows = [], []
        now = timezone.now()
        for did, item in identified.items():
            values = {field: list(item.missing_fields) if field == "missing_fields"
                      else getattr(item, field) for field in FIELDS}
            row = existing.get(did)
            if row is None:
                new_rows.append(ObservationRecord(publisher_did=did, last_seen_at=now, **values))
            elif any(getattr(row, field) != value for field, value in values.items()):
                for field, value in values.items():
                    setattr(row, field, value)
                row.last_seen_at = now
                changed_rows.append(row)
        if new_rows:
            ObservationRecord.objects.bulk_create(new_rows)
        if changed_rows:
            ObservationRecord.objects.bulk_update(changed_rows, [*FIELDS, "last_seen_at"])
        return len(new_rows) + len(changed_rows)

    def get_many(self, ids: list[str]) -> list[Observation] | None:
        records = ObservationRecord.objects.in_bulk(ids)
        if len(records) != len(ids):
            return None
        return [_to_observation(records[item]) for item in ids]


class DjangoTemporalObservationRepository:
    @transaction.atomic
    def upsert(self, observations: list[TemporalObservation]) -> None:
        for item in observations:
            TemporalObservationRecord.objects.update_or_create(
                product_id=item.product_id,
                defaults={"artifact_uri": item.artifact_uri, "cutout_url": item.cutout_url,
                          "time_bounds_lower": item.time_bounds_lower,
                          "ra_deg": item.ra_deg, "dec_deg": item.dec_deg,
                          "size_deg": item.size_deg, "detector": item.detector},
            )

    def get_many(self, ids: list[str]) -> list[TemporalObservation] | None:
        records = TemporalObservationRecord.objects.in_bulk(ids)
        if len(records) != len(ids):
            return None
        return [self._to_domain(records[item]) for item in ids]

    @staticmethod
    def _to_domain(item: TemporalObservationRecord) -> TemporalObservation:
        return TemporalObservation(
            item.product_id, item.artifact_uri, item.cutout_url,
            item.time_bounds_lower, item.ra_deg, item.dec_deg,
            item.size_deg, item.detector,
        )

    def get(self, product_id: str) -> TemporalObservation | None:
        item = TemporalObservationRecord.objects.filter(product_id=product_id).first()
        if item is None:
            return None
        return self._to_domain(item)
