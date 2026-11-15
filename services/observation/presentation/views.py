import logging
import re
from math import isfinite
from xml.etree.ElementTree import ParseError

from django.conf import settings
from django.db import DatabaseError
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from redis.exceptions import RedisError
from requests.exceptions import RequestException
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from services.observation.application.use_cases import (
    GetTemporalProduct,
    SearchObservations,
    SearchTemporalObservations,
)
from services.observation.domain.models import COLLECTIONS, DETECTORS, SkySearch, TemporalSearch
from services.observation.infrastructure.cache import ObservationSearchCache, TemporalSearchCache
from services.observation.infrastructure.irsa import IrsaSiaCatalog
from services.observation.infrastructure.repository import (
    DjangoObservationRepository,
    DjangoTemporalObservationRepository,
)
from services.observation.infrastructure.tap import IrsaTapCatalog
from services.observation.infrastructure.temporal import IrsaTemporalCatalog
from services.observation.presentation.throttling import ObservationRateThrottle

logger = logging.getLogger(__name__)


class FiniteSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        unknown = set(data.keys()) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({name: ["Unsupported query parameter."] for name in sorted(unknown)})
        if hasattr(data, "getlist"):
            repeated = [name for name in data if len(data.getlist(name)) > 1
                        and not isinstance(self.fields[name], serializers.ListField)]
            if repeated:
                raise serializers.ValidationError({name: ["Provide one value."] for name in repeated})
        return super().to_internal_value(data)

    def validate(self, attrs):
        if not all(isfinite(value) for value in attrs.values() if isinstance(value, float)):
            raise serializers.ValidationError("Numeric parameters must be finite")
        if "ra_deg" in attrs and not 0 <= attrs["ra_deg"] < 360:
            raise serializers.ValidationError({"ra_deg": "Must be in [0, 360)."})
        return attrs


class SearchSerializer(FiniteSerializer):
    ra_deg = serializers.FloatField(min_value=0, max_value=360, help_text="ICRS degrees, 0 <= RA < 360.")
    dec_deg = serializers.FloatField(min_value=-90, max_value=90, help_text="ICRS degrees.")
    radius_deg = serializers.FloatField(min_value=0.000001, max_value=1, required=False,
                                       help_text="Circular radius in degrees; exclusive with fov_deg.")
    fov_deg = serializers.FloatField(min_value=0.000002, max_value=2, required=False,
                                    help_text="Circular diameter in degrees; exclusive with radius_deg.")
    collections = serializers.ListField(child=serializers.ChoiceField(choices=COLLECTIONS),
                                       required=False, allow_empty=False,
                                       help_text="Repeat collections=... for multiple collections; default QR3 then DR1.")

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if ("radius_deg" in attrs) == ("fov_deg" in attrs):
            raise serializers.ValidationError("Provide exactly one of radius_deg or fov_deg")
        fov = attrs.get("fov_deg", attrs.get("radius_deg", 0) * 2)
        if fov > settings.EON_MAX_FOV_DEG:
            raise serializers.ValidationError("fov_deg exceeds server maximum")
        return attrs


class ObservationSerializer(serializers.Serializer):
    publisher_did = serializers.CharField()
    collection = serializers.CharField()
    access_url = serializers.CharField()
    ra_deg = serializers.FloatField(allow_null=True)
    dec_deg = serializers.FloatField(allow_null=True)
    obs_id = serializers.CharField(allow_null=True)
    access_format = serializers.CharField(allow_null=True)
    s_region = serializers.CharField(allow_null=True, help_text="Image footprint, not its center.")
    energy_bandpassname = serializers.CharField(allow_null=True)
    t_min_mjd = serializers.FloatField(allow_null=True, help_text="Observation start, MJD days (SIA2 UTC).")
    t_max_mjd = serializers.FloatField(allow_null=True, help_text="Observation end, MJD days (SIA2 UTC).")
    em_min_m = serializers.FloatField(allow_null=True, help_text="Lower wavelength bound in metres.")
    em_max_m = serializers.FloatField(allow_null=True, help_text="Upper wavelength bound in metres.")
    missing_fields = serializers.ListField(child=serializers.CharField())
    processing_version = serializers.CharField(allow_null=True)


class CircleGeometrySerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["circle"])
    radius_deg = serializers.FloatField()


class ObservationSearchResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField(min_value=0)
    geometry = CircleGeometrySerializer()
    results = ObservationSerializer(many=True)


class TemporalSearchSerializer(FiniteSerializer):
    ra_deg = serializers.FloatField(min_value=0, max_value=360, help_text="ICRS degrees, 0 <= RA < 360.")
    dec_deg = serializers.FloatField(min_value=-90, max_value=90)
    size_deg = serializers.FloatField(min_value=0.000001, max_value=1,
                                     help_text="Side of the requested square cutout in degrees.")
    detector = serializers.ChoiceField(choices=DETECTORS)
    limit = serializers.IntegerField(min_value=1, max_value=500, required=False,
                                     default=settings.EON_TAP_MAX_RESULTS,
                                     help_text="Chronological prefix, not pagination. Server cap EON_TAP_MAX_RESULTS.")

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["size_deg"] > settings.EON_MAX_CUTOUT_DEG:
            raise serializers.ValidationError("size_deg exceeds server maximum")
        if attrs["limit"] > settings.EON_TAP_MAX_RESULTS:
            raise serializers.ValidationError({"limit": "Exceeds server maximum."})
        return attrs


class TemporalObservationSerializer(serializers.Serializer):
    product_id = serializers.CharField(help_text="SHA-256 reference for artifact + query geometry/detector/time; not a universal exposure ID.")
    artifact_uri = serializers.CharField()
    cutout_url = serializers.URLField(help_text="IRSA reference only; Observation never downloads FITS.")
    time_bounds_lower = serializers.FloatField(help_text="Original observation lower bound in MJD days: native TAP or documented SIA2 UTC fallback. Verify acquisition UTC in FITS.")
    ra_deg = serializers.FloatField()
    dec_deg = serializers.FloatField()
    size_deg = serializers.FloatField()
    detector = serializers.CharField()


class TemporalSearchResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField(min_value=0)
    results = TemporalObservationSerializer(many=True)


class ErrorSerializer(serializers.Serializer):
    detail = serializers.CharField()


ERRORS = {
    400: OpenApiResponse(response=OpenApiTypes.OBJECT, description="Invalid, repeated or unsupported query parameters; field errors or detail."),
    429: OpenApiResponse(response=ErrorSerializer, description="Rate limit exceeded; Retry-After header."),
    503: OpenApiResponse(response=ErrorSerializer, description="Rate limiting or local metadata infrastructure unavailable."),
}


class ObservationSearchView(APIView):
    throttle_classes = (ObservationRateThrottle,)
    throttle_scope = "sia_search"

    @extend_schema(operation_id="observations_search", parameters=[SearchSerializer], responses={
        200: ObservationSearchResponseSerializer, **ERRORS,
        502: OpenApiResponse(response=ErrorSerializer, description="IRSA SIA request or metadata invalid/unavailable."),
    }, description="SIA2 circular intersection. Supports only the listed parameters; date and detector filters are rejected. No pagination or FITS download.")
    def get(self, request):
        params = SearchSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        data = dict(params.validated_data)
        if "collections" in data:
            data["collections"] = tuple(data["collections"])
        if "fov_deg" in data:
            data["radius_deg"] = data.pop("fov_deg") / 2
        query = SkySearch(**data)
        try:
            repository = DjangoObservationRepository()
            results = SearchObservations(
                IrsaSiaCatalog(), repository, ObservationSearchCache(repository)
            ).execute(query)
        except (DatabaseError, RedisError):
            logger.warning("Observation metadata infrastructure unavailable")
            return Response({"detail": "Observation metadata service unavailable"}, status=503)
        except (RequestException, OSError, ValueError, RuntimeError, ParseError):
            logger.warning("SIA2 search unavailable")
            return Response({"detail": "IRSA SIA query unavailable"}, status=502)
        return Response({"count": len(results), "geometry": {"type": "circle", "radius_deg": query.radius_deg},
                         "results": ObservationSerializer(results, many=True).data})


class TemporalSearchView(APIView):
    throttle_classes = (ObservationRateThrottle,)
    throttle_scope = "tap_search"

    @extend_schema(operation_id="observations_temporal_search", parameters=[TemporalSearchSerializer], responses={
        200: TemporalSearchResponseSerializer, **ERRORS,
        502: OpenApiResponse(response=ErrorSerializer, description="Native TAP and bounded SIA2 fallback unavailable, or invalid scientific reference."),
    }, description="Bounded chronological prefix: native TAP TOP/maxrec, or explicit SIA2 point fallback on transport failure. SIA2 t_min is MJD UTC; raw TAP values remain unconverted. Possibly incomplete when count reaches limit. Not pagination or full-interval sampling. Date/collection filters are unsupported and rejected.")
    def get(self, request):
        params = TemporalSearchSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        query = TemporalSearch(**params.validated_data)
        try:
            repository = DjangoTemporalObservationRepository()
            results = SearchTemporalObservations(
                IrsaTemporalCatalog(primary=IrsaTapCatalog(read_timeout=10)), repository, TemporalSearchCache(repository)
            ).execute(query)
        except (DatabaseError, RedisError):
            logger.warning("Observation metadata infrastructure unavailable")
            return Response({"detail": "Observation metadata service unavailable"}, status=503)
        except (RequestException, OSError, ValueError, RuntimeError, ParseError):
            logger.warning("Temporal search unavailable")
            return Response({"detail": "IRSA TAP query unavailable"}, status=502)
        return Response({"count": len(results), "results": TemporalObservationSerializer(results, many=True).data})


class TemporalProductView(APIView):
    throttle_classes = (ObservationRateThrottle,)
    throttle_scope = "metadata_read"

    @extend_schema(operation_id="observations_temporal_detail", responses={200: TemporalObservationSerializer, **ERRORS,
                              404: OpenApiResponse(response=ErrorSerializer, description="Product not previously discovered/stored.")},
                   description="Resolve a stored query-associated TAP reference, unchanged for BE-02 SourceProduct. No external discovery or FITS download.")
    def get(self, request, product_id):
        params = FiniteSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        if not re.fullmatch(r"[0-9a-f]{64}", product_id):
            return Response({"detail": "Invalid product_id"}, status=400)
        try:
            item = GetTemporalProduct(DjangoTemporalObservationRepository()).execute(product_id)
        except DatabaseError:
            return Response({"detail": "Observation metadata service unavailable"}, status=503)
        if item is None:
            return Response({"detail": "Product not found"}, status=404)
        return Response(TemporalObservationSerializer(item).data)
