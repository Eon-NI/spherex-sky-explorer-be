from django.db import transaction
from services.observation.domain.models import Observation
from services.observation.models import ObservationRecord


class DjangoObservationRepository:
    @transaction.atomic
    def upsert(self, observations: list[Observation]) -> int:
        for item in observations:
            ObservationRecord.objects.update_or_create(
                publisher_did=item.publisher_did,
                defaults={"collection": item.collection, "access_url": item.access_url,
                          "ra_deg": item.ra_deg, "dec_deg": item.dec_deg},
            )
        return len(observations)
