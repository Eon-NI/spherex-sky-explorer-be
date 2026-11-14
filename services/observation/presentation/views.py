import logging
import re
from math import isfinite
from django.conf import settings
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import ScopedRateThrottle
from drf_spectacular.utils import extend_schema, OpenApiParameter
from services.observation.application.use_cases import SearchObservations, SearchTemporalObservations
from services.observation.domain.models import COLLECTIONS, DETECTORS, SkySearch, TemporalSearch
from services.observation.infrastructure.irsa import IrsaSiaCatalog
from services.observation.infrastructure.tap import IrsaTapCatalog
from services.observation.infrastructure.repository import DjangoObservationRepository, DjangoTemporalObservationRepository
from services.observation.infrastructure.cache import ObservationSearchCache, TemporalSearchCache

logger = logging.getLogger(__name__)


class FiniteSerializer(serializers.Serializer):
    def validate(self, attrs):
        if not all(isfinite(value) for value in attrs.values() if isinstance(value, float)):
            raise serializers.ValidationError("Numeric parameters must be finite")
        return attrs


class SearchSerializer(FiniteSerializer):
    def validate(self, attrs):
        attrs = super().validate(attrs)
        if ("radius_deg" in attrs) == ("fov_deg" in attrs):
            raise serializers.ValidationError("Provide exactly one of radius_deg or fov_deg")
        fov = attrs.get("fov_deg", attrs.get("radius_deg", 0) * 2)
        if fov > settings.EON_MAX_FOV_DEG:
            raise serializers.ValidationError("fov_deg exceeds server maximum")
        return attrs

    ra_deg = serializers.FloatField(min_value=0, max_value=359.999999)
    dec_deg = serializers.FloatField(min_value=-90, max_value=90)
    radius_deg = serializers.FloatField(min_value=0.000001, max_value=1, required=False)
    fov_deg = serializers.FloatField(min_value=0.000002, max_value=2, required=False)
    collections = serializers.ListField(
        child=serializers.ChoiceField(choices=COLLECTIONS), required=False,
        allow_empty=False,
    )


class ObservationSerializer(serializers.Serializer):
    publisher_did = serializers.CharField(allow_blank=True)
    collection = serializers.CharField(allow_blank=True)
    access_url = serializers.CharField(allow_blank=True)
    ra_deg = serializers.FloatField(allow_null=True)
    dec_deg = serializers.FloatField(allow_null=True)
    obs_id = serializers.CharField(allow_null=True)
    access_format = serializers.CharField(allow_null=True)
    s_region = serializers.CharField(allow_null=True)
    energy_bandpassname = serializers.CharField(allow_null=True)
    t_min_mjd = serializers.FloatField(allow_null=True)
    t_max_mjd = serializers.FloatField(allow_null=True)
    em_min_m = serializers.FloatField(allow_null=True)
    em_max_m = serializers.FloatField(allow_null=True)
    missing_fields = serializers.ListField(child=serializers.CharField())
    processing_version = serializers.CharField(allow_null=True)


class TemporalSearchSerializer(FiniteSerializer):
    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["size_deg"] > settings.EON_MAX_CUTOUT_DEG:
            raise serializers.ValidationError("size_deg exceeds server maximum")
        return attrs

    ra_deg = serializers.FloatField(min_value=0, max_value=359.999999)
    dec_deg = serializers.FloatField(min_value=-90, max_value=90)
    size_deg = serializers.FloatField(min_value=0.000001, max_value=1)
    detector = serializers.ChoiceField(choices=DETECTORS)
    limit = serializers.IntegerField(min_value=1, max_value=500, required=False,
                                     default=settings.EON_TAP_MAX_RESULTS)


class TemporalObservationSerializer(serializers.Serializer):
    product_id = serializers.CharField()
    artifact_uri = serializers.CharField()
    cutout_url = serializers.URLField()
    time_bounds_lower = serializers.FloatField()
    ra_deg = serializers.FloatField()
    dec_deg = serializers.FloatField()
    size_deg = serializers.FloatField()
    detector = serializers.CharField()


class ObservationSearchView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "sia_search"
    @extend_schema(parameters=[SearchSerializer], responses=ObservationSerializer(many=True))
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
        except Exception:
            logger.exception("SIA2 search failed")
            return Response({"detail": "IRSA SIA query unavailable"}, status=502)
        return Response({"count": len(results), "geometry": {"type": "circle",
                         "radius_deg": query.radius_deg},
                         "results": ObservationSerializer(results, many=True).data})


class TemporalSearchView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "tap_search"
    @extend_schema(parameters=[TemporalSearchSerializer], responses=TemporalObservationSerializer(many=True))
    def get(self, request):
        params = TemporalSearchSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        query = TemporalSearch(**params.validated_data)
        if query.limit > settings.EON_TAP_MAX_RESULTS:
            return Response({"detail": "limit exceeds server maximum"}, status=400)
        try:
            repository = DjangoTemporalObservationRepository()
            results = SearchTemporalObservations(
                IrsaTapCatalog(), repository, TemporalSearchCache(repository)
            ).execute(query)
        except Exception:
            logger.exception("Temporal search failed")
            return Response({"detail": "IRSA TAP query unavailable"}, status=502)
        return Response({"count": len(results), "results": TemporalObservationSerializer(results, many=True).data})


class TemporalProductView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "metadata_read"
    @extend_schema(responses=TemporalObservationSerializer)
    def get(self, request, product_id):
        if not re.fullmatch(r"[0-9a-f]{64}", product_id):
            return Response({"detail": "Invalid product_id"}, status=400)
        item = DjangoTemporalObservationRepository().get(product_id)
        if item is None:
            return Response({"detail": "Product not found"}, status=404)
        return Response(TemporalObservationSerializer(item).data)
