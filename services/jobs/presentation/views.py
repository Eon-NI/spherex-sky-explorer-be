import hmac
from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import ScopedRateThrottle
from services.jobs.infrastructure.celery_jobs import CeleryJobQueue, CeleryJobRepository


class CutoutPayloadSerializer(serializers.Serializer):
    product_id = serializers.RegexField(r"^[0-9a-f]{64}$")
    ra_deg = serializers.FloatField(min_value=0, max_value=359.999999)
    dec_deg = serializers.FloatField(min_value=-90, max_value=90)
    size_deg = serializers.FloatField(min_value=0.000001, max_value=1)


class SubmitJobSerializer(serializers.Serializer):
    task = serializers.ChoiceField(choices=["eon.image_processing.create_cutout"])
    idempotency_key = serializers.RegexField(r"^[0-9a-f]{64}$")
    payload = CutoutPayloadSerializer()


class JobSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    state = serializers.CharField()
    result_key = serializers.CharField(allow_null=True)
    error = serializers.CharField(allow_null=True)
    result = serializers.DictField(allow_null=True)


class JobCollectionView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "cutout_submit"
    @extend_schema(request=SubmitJobSerializer, responses={202: JobSerializer})
    def post(self, request):
        supplied = request.headers.get("X-EON-Internal-Token", "")
        if not settings.EON_INTERNAL_TOKEN or not hmac.compare_digest(supplied, settings.EON_INTERNAL_TOKEN):
            return Response({"detail": "Unauthorized"}, status=401)
        body = SubmitJobSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        job_id = CeleryJobQueue().submit(data["task"], data["payload"], data["idempotency_key"])
        return Response({"id": job_id, "state": "pending", "result_key": None, "error": None, "result": None}, status=202)


class JobDetailView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "job_status"
    @extend_schema(responses=JobSerializer)
    def get(self, request, job_id):
        job = CeleryJobRepository().get(str(job_id))
        if job is None:
            return Response({"detail": "Job not found"}, status=404)
        return Response(JobSerializer(job).data)
