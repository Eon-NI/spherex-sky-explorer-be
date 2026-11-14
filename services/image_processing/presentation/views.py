import logging
import re
from dataclasses import asdict
from urllib.error import HTTPError
from math import isfinite
from drf_spectacular.utils import extend_schema
from django.conf import settings
from django.http import StreamingHttpResponse
from drf_spectacular.types import OpenApiTypes
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import ScopedRateThrottle
from services.image_processing.application.use_cases import InspectFitsMetadata, RequestFitsCutout
from services.image_processing.domain.models import CutoutRequest
from services.image_processing.infrastructure.clients import JobApiReader, JobApiSubmitter, ObservationApiResolver
from services.image_processing.infrastructure.fits_reader import AstropyFitsReader
from services.image_processing.infrastructure.repository import DjangoCutoutRepository
from services.image_processing.infrastructure.storage import S3ScientificObjectStore
from services.image_processing.domain.security import validate_cutout_url

logger = logging.getLogger(__name__)


class CutoutRequestSerializer(serializers.Serializer):
    product_id = serializers.RegexField(r"^[0-9a-f]{64}$")
    ra_deg = serializers.FloatField(min_value=0, max_value=359.999999)
    dec_deg = serializers.FloatField(min_value=-90, max_value=90)
    size_deg = serializers.FloatField(min_value=0.000001, max_value=1)

    def validate(self, attrs):
        if not all(isfinite(attrs[name]) for name in ("ra_deg", "dec_deg", "size_deg")):
            raise serializers.ValidationError("Coordinates and size must be finite")
        if attrs["size_deg"] > settings.EON_MAX_CUTOUT_DEG:
            raise serializers.ValidationError("size_deg exceeds server maximum")
        return attrs


class CutoutJobSerializer(serializers.Serializer):
    job_id = serializers.UUIDField()
    status_url = serializers.CharField()


class CutoutJobStatusSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    state = serializers.CharField()
    result_key = serializers.CharField(allow_null=True)
    error = serializers.CharField(allow_null=True)
    result = serializers.DictField(allow_null=True)


class FitsMetadataSerializer(serializers.Serializer):
    cutout_id = serializers.CharField()
    object_key = serializers.CharField()
    source_url = serializers.URLField()
    time_bounds_lower = serializers.FloatField()
    fits = serializers.DictField()


class CutoutRequestView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "cutout_submit"
    @extend_schema(request=CutoutRequestSerializer, responses={202: CutoutJobSerializer})
    def post(self, request):
        params = CutoutRequestSerializer(data=request.data)
        params.is_valid(raise_exception=True)
        cutout = CutoutRequest(**params.validated_data)
        try:
            product = ObservationApiResolver().get(cutout.product_id)
            if product is None:
                return Response({"detail": "Product not found"}, status=404)
            if (product.ra_deg, product.dec_deg, product.size_deg) != (cutout.ra_deg, cutout.dec_deg, cutout.size_deg):
                return Response({"detail": "Cutout parameters differ from TAP search"}, status=400)
            validate_cutout_url(product.cutout_url, product.artifact_uri, cutout.ra_deg, cutout.dec_deg, cutout.size_deg)
            job_id = RequestFitsCutout(JobApiSubmitter()).execute(cutout)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        except Exception:
            logger.exception("Unable to submit cutout job")
            return Response({"detail": "Job service unavailable"}, status=502)
        return Response({"job_id": job_id, "status_url": f"/api/v1/cutouts/jobs/{job_id}/"}, status=202)


class FitsMetadataView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "metadata_read"
    @extend_schema(responses=FitsMetadataSerializer)
    def get(self, request, cutout_id):
        if not re.fullmatch(r"[0-9a-f]{64}", cutout_id):
            return Response({"detail": "Invalid cutout_id"}, status=400)
        repository = DjangoCutoutRepository()
        record = repository.get_record(cutout_id)
        if record is None:
            return Response({"detail": "Cutout not found"}, status=404)
        try:
            metadata = InspectFitsMetadata(
                S3ScientificObjectStore(), AstropyFitsReader(), repository
            ).execute(cutout_id, record.ra_deg, record.dec_deg)
        except Exception:
            logger.exception("Unable to inspect FITS")
            return Response({"detail": "FITS inspection unavailable"}, status=502)
        return Response({"cutout_id": cutout_id, "object_key": record.object_key,
                         "source_url": record.source_url,
                         "time_bounds_lower": record.time_bounds_lower,
                         "fits": asdict(metadata)})


class CutoutJobStatusView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "job_status"
    @extend_schema(responses=CutoutJobStatusSerializer)
    def get(self, request, job_id):
        try:
            return Response(JobApiReader().get(str(job_id)))
        except HTTPError as exc:
            if exc.code == 404:
                return Response({"detail": "Job not found"}, status=404)
            logger.exception("Unable to read cutout job")
            return Response({"detail": "Job service unavailable"}, status=502)
        except Exception:
            logger.exception("Unable to read cutout job")
            return Response({"detail": "Job service unavailable"}, status=502)


class FitsContentView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "fits_stream"

    @extend_schema(responses={(200, "application/fits"): OpenApiTypes.BINARY})
    def get(self, request, cutout_id):
        if not re.fullmatch(r"[0-9a-f]{64}", cutout_id):
            return Response({"detail": "Invalid cutout_id"}, status=400)
        reference = DjangoCutoutRepository().get(cutout_id)
        if reference is None:
            return Response({"detail": "Cutout not found"}, status=404)
        try:
            body, length = S3ScientificObjectStore().open_stream(reference)
        except Exception:
            logger.exception("Unable to open FITS object")
            return Response({"detail": "FITS unavailable"}, status=502)

        def chunks():
            try:
                while data := body.read(1024 * 1024):
                    yield data
            finally:
                body.close()

        response = StreamingHttpResponse(chunks(), content_type="application/fits")
        if length is not None:
            response["Content-Length"] = str(length)
        response["Content-Disposition"] = 'inline; filename="cutout.fits"'
        return response
