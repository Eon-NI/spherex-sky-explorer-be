"""Narrow browser access to the Spectral API from the local React app."""
import os
from django.conf import settings
from django.http import HttpResponse


class SpectralCorsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.allowed = {origin.strip() for origin in os.getenv(
            "EON_SPECTRAL_BROWSER_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ).split(",") if origin.strip()}

    def __call__(self, request):
        if settings.SERVICE_NAME != "spectral" or not request.path.startswith("/api/v1/spectral/"):
            return self.get_response(request)
        origin = request.headers.get("Origin")
        if request.method == "OPTIONS" and origin in self.allowed:
            response = HttpResponse(status=204)
        else:
            response = self.get_response(request)
        if origin in self.allowed:
            response["Access-Control-Allow-Origin"] = origin
            response["Vary"] = "Origin"
            response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response["Access-Control-Allow-Headers"] = "Content-Type"
        return response
