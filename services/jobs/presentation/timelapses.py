"""Public bounded orchestration for Image, Blink and Timelapse using BE-02."""
import hashlib
import json
import logging
from math import isfinite

from django.http import StreamingHttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from services.jobs.domain.models import JobState
from services.jobs.infrastructure.celery_jobs import CeleryJobQueue, CeleryJobRepository
from services.jobs.infrastructure.timelapses import frame_key, open_png, resolve_product

logger = logging.getLogger(__name__)
TASK = 'eon.image_processing.render_timelapse'


class TimelapsePayload(serializers.Serializer):
    product_ids = serializers.ListField(child=serializers.RegexField(r'^[0-9a-f]{64}$'), min_length=1, max_length=5)
    ra_deg = serializers.FloatField(min_value=0, max_value=359.999999)
    dec_deg = serializers.FloatField(min_value=-90, max_value=90)
    size_deg = serializers.FloatField(min_value=0.000001, max_value=0.2)
    detector = serializers.ChoiceField(choices=[f'SPHEREx-D{i}' for i in range(1, 7)], default='SPHEREx-D2')

    def validate(self, values):
        if not all(isfinite(values[k]) for k in ['ra_deg', 'dec_deg', 'size_deg']):
            raise serializers.ValidationError('Coordinates and size must be finite')
        values['product_ids'] = sorted(set(values['product_ids']))
        return values


class Accepted(serializers.Serializer):
    job_id = serializers.UUIDField()
    status = serializers.CharField()
    status_url = serializers.CharField()


class Delivery(serializers.Serializer):
    index = serializers.IntegerField(min_value=0, max_value=4)
    url = serializers.URLField()


class PublicStatus(serializers.Serializer):
    job_id = serializers.UUIDField()
    status = serializers.CharField()
    progress = serializers.IntegerField(allow_null=True)
    error = serializers.CharField(allow_null=True)
    result = serializers.DictField(allow_null=True)
    delivery = Delivery(many=True)


class TimelapseCollectionView(APIView):
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = 'timelapse_submit'

    @extend_schema(request=TimelapsePayload, responses={202: Accepted, 400: OpenApiTypes.OBJECT,
                                                     404: OpenApiTypes.OBJECT, 429: OpenApiTypes.OBJECT, 502: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = TimelapsePayload(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        try:
            for pid in payload['product_ids']:
                product = resolve_product(pid)
                if product is None:
                    return Response({'detail': 'Observation product not found'}, status=404)
                if product.get('product_id') != pid or any(product.get(k) != payload[k] for k in
                                                          ['ra_deg', 'dec_deg', 'size_deg', 'detector']):
                    return Response({'detail': 'Product geometry or detector mismatch'}, status=400)
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            job_id = CeleryJobQueue().submit(TASK, payload, digest)
        except Exception:
            logger.exception('Unable to resolve or enqueue timelapse')
            return Response({'detail': 'Timelapse dependencies unavailable'}, status=502)
        return Response({'job_id': job_id, 'status': 'pending',
                         'status_url': f'/api/v1/timelapses/{job_id}/'}, status=202)


def find_job(job_id):
    repository = CeleryJobRepository()
    if repository.task_name(str(job_id)) != TASK:
        return None
    return repository.get(str(job_id))


class TimelapseDetailView(APIView):
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = 'job_status'

    @extend_schema(responses={200: PublicStatus, 404: OpenApiTypes.OBJECT, 429: OpenApiTypes.OBJECT, 502: OpenApiTypes.OBJECT})
    def get(self, request, job_id):
        try:
            job = find_job(job_id)
            if job is None:
                return Response({'detail': 'Timelapse job not found'}, status=404)
            delivery = []
            if job.state == JobState.SUCCEEDED:
                for index in range(len(job.result.get('frames', []))):
                    frame_key(job.result, job_id, index)
                    delivery.append({'index': index, 'url': request.build_absolute_uri(
                        f'/api/v1/timelapses/{job_id}/frames/{index}/')})
            return Response({'job_id': job.id, 'status': 'completed' if job.state == JobState.SUCCEEDED else job.state,
                             'progress': job.progress, 'error': job.error, 'result': job.result, 'delivery': delivery})
        except Exception:
            logger.exception('Unable to read timelapse')
            return Response({'detail': 'Timelapse status unavailable'}, status=502)


class FrameView(APIView):
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = 'frame_delivery'

    @extend_schema(responses={(200, 'image/png'): OpenApiTypes.BINARY, 404: OpenApiTypes.OBJECT,
                             409: OpenApiTypes.OBJECT, 429: OpenApiTypes.OBJECT, 502: OpenApiTypes.OBJECT})
    def get(self, request, job_id, index):
        try:
            job = find_job(job_id)
            if job is None:
                return Response({'detail': 'Timelapse job not found'}, status=404)
            if job.state != JobState.SUCCEEDED:
                return Response({'detail': 'Timelapse is not completed'}, status=409)
            key = frame_key(job.result, job_id, index)
            obj = open_png(key)
        except IndexError:
            return Response({'detail': 'Frame not found'}, status=404)
        except Exception:
            logger.exception('Unable to deliver PNG')
            return Response({'detail': 'Frame unavailable'}, status=502)
        body = obj['Body']
        def chunks():
            try:
                while block := body.read(65536):
                    yield block
            finally:
                body.close()
        response = StreamingHttpResponse(chunks(), content_type='image/png')
        response['Content-Length'] = str(obj['ContentLength'])
        response['Cache-Control'] = 'private, max-age=300'
        return response
