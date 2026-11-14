from django.conf import settings
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView


def health(_request):
    return JsonResponse({"status": "ok", "service": settings.SERVICE_NAME})


urlpatterns = [
    path("health/", health),
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/v1/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]
if settings.SERVICE_NAME == "observation":
    urlpatterns.append(path("api/v1/", include("services.observation.presentation.urls")))
