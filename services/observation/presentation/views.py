from math import isfinite
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView
from services.observation.application.use_cases import SearchObservations
from services.observation.domain.models import SkySearch
from services.observation.infrastructure.irsa import IrsaSiaCatalog


class SearchSerializer(serializers.Serializer):
    def validate(self, attrs):
        if not all(isfinite(value) for value in attrs.values()):
            raise serializers.ValidationError("Coordinates and radius must be finite")
        return attrs

    ra_deg = serializers.FloatField(min_value=0, max_value=359.999999)
    dec_deg = serializers.FloatField(min_value=-90, max_value=90)
    radius_deg = serializers.FloatField(min_value=0.000001, max_value=1)


class ObservationSerializer(serializers.Serializer):
    publisher_did = serializers.CharField()
    collection = serializers.CharField()
    access_url = serializers.URLField()
    ra_deg = serializers.FloatField(allow_null=True)
    dec_deg = serializers.FloatField(allow_null=True)


class ObservationSearchView(APIView):
    def get(self, request):
        params = SearchSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        query = SkySearch(**params.validated_data)
        try:
            results = SearchObservations(IrsaSiaCatalog()).execute(query)
        except Exception:
            return Response({"detail": "IRSA query unavailable"}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"count": len(results), "results": ObservationSerializer(results, many=True).data})
