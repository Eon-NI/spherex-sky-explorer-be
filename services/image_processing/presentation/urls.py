from django.urls import path
from .views import CutoutRequestView, FitsMetadataView, CutoutJobStatusView, FitsContentView

urlpatterns = [
    path("cutouts/", CutoutRequestView.as_view(), name="cutout-request"),
    path("cutouts/jobs/<uuid:job_id>/", CutoutJobStatusView.as_view(), name="cutout-job-status"),
    path("cutouts/<str:cutout_id>/metadata/", FitsMetadataView.as_view(), name="fits-metadata"),
    path("cutouts/<str:cutout_id>/fits/", FitsContentView.as_view(), name="fits-content"),
]
