from django.urls import path
from .views import ChannelsView, CubeRequestView, PreviewView, PreviewPointView, SpectralJobView, SpectrumRequestView

urlpatterns = [
    path("spectral/channels/", ChannelsView.as_view()),
    path("spectral/cubes/", CubeRequestView.as_view()),
    path("spectral/spectra/", SpectrumRequestView.as_view()),
    path("spectral/jobs/<uuid:job_id>/", SpectralJobView.as_view()),
    path("spectral/cubes/<int:tile_id>/channels/<int:channel_index>/preview/", PreviewView.as_view()),
    path("spectral/cubes/<int:tile_id>/point/", PreviewPointView.as_view()),
]
