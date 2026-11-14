"""Observation-only fail-closed throttling; other services keep their policy."""
import logging

from redis.exceptions import RedisError
from rest_framework.exceptions import APIException
from rest_framework.throttling import ScopedRateThrottle

logger = logging.getLogger(__name__)


class RateLimitUnavailable(APIException):
    status_code = 503
    default_detail = "Rate limiting temporarily unavailable. Retry later."
    default_code = "rate_limit_unavailable"


class ObservationRateThrottle(ScopedRateThrottle):
    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except (RedisError, OSError):
            logger.warning("Observation rate limiter unavailable")
            raise RateLimitUnavailable() from None
