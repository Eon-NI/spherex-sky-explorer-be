"""Allow configured local browser origins on public EON APIs only."""
import os

from django.conf import settings
from django.http import HttpResponse


class SpectralCorsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.allowed = {origin.strip() for origin in os.getenv(
            "EON_BROWSER_ORIGINS", os.getenv("EON_SPECTRAL_BROWSER_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080"
        )).split(",") if origin.strip()}

    def __call__(self, request):
        public = {"spectral": "/api/v1/spectral/", "observation": "/api/v1/observations/",
                  "image_processing": "/api/v1/cutouts/", "jobs": "/api/v1/timelapses/"}
        if not request.path.startswith(public.get(settings.SERVICE_NAME, "/__no_public_api__/")):
            return self.get_response(request)
        origin = request.headers.get("Origin")
        if request.method == "OPTIONS" and origin in self.allowed:
            response = HttpResponse(status=204)
        else:
            response = self.get_response(request)
        if origin in self.allowed:
            response["Access-Control-Allow-Origin"] = origin
            from django.utils.cache import patch_vary_headers
            patch_vary_headers(response, ["Origin"])
            response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response["Access-Control-Allow-Headers"] = "Content-Type"
            response["Access-Control-Expose-Headers"] = "Retry-After"
        return response
