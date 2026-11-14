from django.urls import path
from .views import ObservationSearchView

urlpatterns = [path("observations/search/", ObservationSearchView.as_view(), name="observation-search")]
