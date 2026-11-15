import hashlib
import json
import logging
from dataclasses import asdict
from math import isfinite
from urllib.error import HTTPError

from astropy.io import fits
from django.core.cache import cache
from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from services.spectral.application.use_cases import preview_png, preview_point
from services.spectral.infrastructure.clients import JobsClient
from services.spectral.domain.models import BLANK_DICHROIC_CHANNELS, SpectrumRequest
from services.spectral.infrastructure.hips import CubeStore, channel_metadata, tile_number

logger = logging.getLogger(__name__)


class CoordinatesSerializer(serializers.Serializer):
    ra_deg = serializers.FloatField(min_value=0, max_value=359.999999)
    dec_deg = serializers.FloatField(min_value=-90, max_value=90)
    fov_deg = serializers.FloatField(min_value=0.01, max_value=1)

    def validate(self, attrs):
        if not all(isfinite(value) for value in attrs.values()):
            raise serializers.ValidationError("Coordinates and FOV must be finite")
        return attrs


class JobEnvelopeSerializer(serializers.Serializer):
    job_id = serializers.UUIDField()
    status = serializers.CharField()
    status_url = serializers.CharField()


class ChannelSerializer(serializers.Serializer):
    channel_index = serializers.IntegerField(min_value=0, max_value=101)
    band = serializers.IntegerField(min_value=1, max_value=6)
    band_channel = serializers.IntegerField(min_value=1, max_value=17)
    wavelength_um = serializers.FloatField()
    available = serializers.BooleanField()


class ChannelsResponseSerializer(serializers.Serializer):
    product_id = serializers.CharField()
    wavelength_unit = serializers.ChoiceField(choices=["um"])
    channels = ChannelSerializer(many=True)


class SpectralJobSerializer(serializers.Serializer):
    job_id = serializers.UUIDField()
    status = serializers.CharField()
    progress = serializers.IntegerField(allow_null=True)
    error = serializers.CharField(allow_null=True)
    result = serializers.DictField(allow_null=True)


class ChannelsView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "metadata_read"

    @extend_schema(responses={200: ChannelsResponseSerializer})
    def get(self, request):
        try:
            value = cache.get("eon:spectral:dr1:channels")
            if value is None:
                value = channel_metadata()
                cache.set("eon:spectral:dr1:channels", value, 3600)
            return Response(value)
        except Exception:
            logger.exception("Unable to read DR1 HiPS properties")
            return Response({"detail": "IRSA channel metadata unavailable"}, status=502)


class _SubmitView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "spectral_submit"
    task_name = ""

    @extend_schema(request=CoordinatesSerializer, responses={202: JobEnvelopeSerializer})
    def post(self, request):
        serializer = CoordinatesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = asdict(SpectrumRequest(**serializer.validated_data))
        digest = hashlib.sha256((self.task_name + ":" + json.dumps(payload, sort_keys=True)).encode()).hexdigest()
        try:
            job_id = JobsClient().submit(self.task_name, payload, digest)
        except HTTPError as exc:
            if exc.code == 429:
                response = Response({"detail": "Spectral job submission rate exceeded"}, status=429)
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                if retry_after and retry_after.isdigit():
                    response["Retry-After"] = retry_after
                return response
            logger.exception("Unable to submit spectral job")
            return Response({"detail": "Job service unavailable"}, status=502)
        except Exception:
            logger.exception("Unable to submit spectral job")
            return Response({"detail": "Job service unavailable"}, status=502)
        return Response({"job_id": job_id, "status": "pending",
                         "status_url": f"/api/v1/spectral/jobs/{job_id}/"}, status=202)


class CubeRequestView(_SubmitView):
    task_name = "eon.spectral.prepare_cube"


class SpectrumRequestView(_SubmitView):
    task_name = "eon.spectral.extract_spectrum"


class SpectralJobView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "job_status"

    @extend_schema(responses={200: SpectralJobSerializer})
    def get(self, request, job_id):
        try:
            job = JobsClient().get(str(job_id))
        except HTTPError as exc:
            if exc.code == 404:
                return Response({"detail": "Job not found"}, status=404)
            return Response({"detail": "Job service unavailable"}, status=502)
        except Exception:
            return Response({"detail": "Job service unavailable"}, status=502)
        state = job["state"]
        return Response({"job_id": job["id"],
                         "status": "completed" if state == "succeeded" else state,
                         "progress": job.get("progress"), "error": job.get("error"),
                         "result": job.get("result")})


class PreviewView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "spectral_preview"

    @extend_schema(parameters=[CoordinatesSerializer],
                   responses={(200, "image/png"): OpenApiTypes.BINARY})
    def get(self, request, tile_id, channel_index):
        if not 0 <= tile_id < 12288 or not 0 <= channel_index < 102:
            return Response({"detail": "Invalid tile or channel"}, status=400)
        if channel_index in BLANK_DICHROIC_CHANNELS:
            return Response({"detail": "Blank dichroic channel"}, status=404)
        serializer = CoordinatesSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        if tile_number(values["ra_deg"], values["dec_deg"]) != tile_id:
            return Response({"detail": "Coordinate does not match tile"}, status=400)
        store = CubeStore()
        if not store.exists(store.cube_key(tile_id)):
            return Response({"detail": "Cube not prepared"}, status=404)
        digest = hashlib.sha256(f"{tile_id}:{channel_index}:{values['ra_deg']:.10f}:"
                                f"{values['dec_deg']:.10f}:{values['fov_deg']:.10f}".encode()).hexdigest()
        preview_key = f"spectral/dr1/previews/{digest}.png"
        try:
            if store.exists(preview_key):
                png = store.client.get_object(Bucket=store.bucket, Key=preview_key)["Body"].read()
            else:
                directory, path = store.local_cube(tile_id)
                try:
                    with fits.open(path, memmap=True) as hdul:
                        png = preview_png(hdul, channel_index, **values)
                finally:
                    directory.cleanup()
                store.client.put_object(Bucket=store.bucket, Key=preview_key, Body=png,
                                        ContentType="image/png")
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        except Exception:
            logger.exception("Unable to render spectral preview")
            return Response({"detail": "Preview unavailable"}, status=502)
        response = HttpResponse(png, content_type="image/png")
        response["Cache-Control"] = "public, max-age=86400"
        return response


class PreviewPointSerializer(CoordinatesSerializer):
    u = serializers.FloatField(min_value=0, max_value=1)
    v = serializers.FloatField(min_value=0, max_value=1)


class PreviewPointResponseSerializer(serializers.Serializer):
    ra_deg = serializers.FloatField()
    dec_deg = serializers.FloatField()


class PreviewPointView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "spectral_preview"

    @extend_schema(request=PreviewPointSerializer, responses={200: PreviewPointResponseSerializer})
    def post(self, request, tile_id):
        serializer = PreviewPointSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        if not 0 <= tile_id < 12288 or tile_number(values["ra_deg"], values["dec_deg"]) != tile_id:
            return Response({"detail": "Coordinate does not match tile"}, status=400)
        store = CubeStore()
        if not store.exists(store.cube_key(tile_id)):
            return Response({"detail": "Cube not prepared"}, status=404)
        try:
            directory, path = store.local_cube(tile_id)
            try:
                with fits.open(path, memmap=True) as hdul:
                    point = preview_point(hdul, **values)
            finally:
                directory.cleanup()
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        except Exception:
            logger.exception("Unable to resolve spectral preview point")
            return Response({"detail": "Point unavailable"}, status=502)
        return Response(point)
