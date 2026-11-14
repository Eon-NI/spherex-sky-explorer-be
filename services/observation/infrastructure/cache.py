"""Redis hot cache with PostgreSQL snapshot fallback; both contain IDs only."""
import hashlib
import json
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from redis import Redis, RedisError
from services.observation.domain.models import Observation, SkySearch
from services.observation.infrastructure.repository import DjangoObservationRepository
from services.observation.models import ObservationSearchSnapshot, CatalogGeneration


class ObservationSearchCache:
    def __init__(self, repository: DjangoObservationRepository):
        self.repository = repository
        self.redis = Redis.from_url(settings.CELERY_BROKER_URL)
        self.ttl = settings.EON_OBSERVATION_CACHE_TTL_SECONDS

    @staticmethod
    def query_key(query: SkySearch) -> str:
        payload = (f"{query.ra_deg:.12g}:{query.dec_deg:.12g}:{query.radius_deg:.12g}:"
                   f"{','.join(query.collections)}")
        return hashlib.sha256(payload.encode()).hexdigest()

    def _redis_key(self, key: str) -> str:
        generation, _ = CatalogGeneration.objects.get_or_create(id=1)
        return f"eon:sia:{generation.version}:{key}"

    def get(self, query: SkySearch) -> list[Observation] | None:
        key = self.query_key(query)
        ids = None
        try:
            raw = self.redis.get(self._redis_key(key))
            if raw is not None:
                ids = json.loads(raw)
        except (RedisError, ValueError):
            pass
        if ids is None:
            snapshot = ObservationSearchSnapshot.objects.filter(query_key=key).first()
            if snapshot is None or snapshot.created_at < timezone.now() - timedelta(seconds=self.ttl):
                return None
            ids = snapshot.observation_ids
        result = self.repository.get_many(ids)
        return result

    def put(self, query: SkySearch, observations: list[Observation]) -> None:
        if any(not item.publisher_did for item in observations):
            return
        key = self.query_key(query)
        ids = list(dict.fromkeys(item.publisher_did for item in observations))
        ObservationSearchSnapshot.objects.update_or_create(
            query_key=key, defaults={"observation_ids": ids, "created_at": timezone.now()}
        )
        try:
            self.redis.setex(self._redis_key(key), self.ttl, json.dumps(ids))
        except RedisError:
            pass

    @transaction.atomic
    def invalidate_all(self) -> None:
        generation, _ = CatalogGeneration.objects.get_or_create(id=1)
        CatalogGeneration.objects.filter(pk=generation.pk).update(version=F("version") + 1)
        ObservationSearchSnapshot.objects.all().delete()
        # Old Redis entries use the previous durable generation and expire by TTL.



class TemporalSearchCache:
    """Exact TAP-query cache backed by the same PostgreSQL snapshot table."""
    def __init__(self, repository):
        self.repository = repository
        self.redis = Redis.from_url(settings.CELERY_BROKER_URL)
        self.ttl = settings.EON_OBSERVATION_CACHE_TTL_SECONDS

    @staticmethod
    def query_key(query):
        payload = (f"tap:{query.ra_deg:.12g}:{query.dec_deg:.12g}:"
                   f"{query.size_deg:.12g}:{query.detector}:{query.limit}")
        return hashlib.sha256(payload.encode()).hexdigest()

    def _redis_key(self, key):
        generation, _ = CatalogGeneration.objects.get_or_create(id=1)
        return f"eon:tap:{generation.version}:{key}"

    def get(self, query):
        key = self.query_key(query)
        ids = None
        try:
            raw = self.redis.get(self._redis_key(key))
            if raw is not None:
                ids = json.loads(raw)
        except (RedisError, ValueError):
            pass
        if ids is None:
            snapshot = ObservationSearchSnapshot.objects.filter(query_key=key).first()
            if snapshot is None or snapshot.created_at < timezone.now() - timedelta(seconds=self.ttl):
                return None
            ids = snapshot.observation_ids
        return self.repository.get_many(ids)

    def put(self, query, observations):
        key = self.query_key(query)
        ids = [item.product_id for item in observations]
        ObservationSearchSnapshot.objects.update_or_create(
            query_key=key, defaults={"observation_ids": ids, "created_at": timezone.now()}
        )
        try:
            self.redis.setex(self._redis_key(key), self.ttl, json.dumps(ids))
        except RedisError:
            pass
