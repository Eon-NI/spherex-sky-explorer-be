from django.urls import path
from .views import JobCollectionView, JobDetailView

urlpatterns = [
    path("jobs/", JobCollectionView.as_view(), name="jobs"),
    path("jobs/<uuid:job_id>/", JobDetailView.as_view(), name="job-detail"),
]
