from django.urls import path
from .views import JobCollectionView, JobDetailView
from .timelapses import TimelapseCollectionView, TimelapseDetailView, FrameView

urlpatterns = [
    path("timelapses/", TimelapseCollectionView.as_view()),
    path("timelapses/<uuid:job_id>/", TimelapseDetailView.as_view()),
    path("timelapses/<uuid:job_id>/frames/<int:index>/", FrameView.as_view()),
    path("jobs/", JobCollectionView.as_view(), name="jobs"),
    path("jobs/<uuid:job_id>/", JobDetailView.as_view(), name="job-detail"),
]
