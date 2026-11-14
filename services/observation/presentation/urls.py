from django.urls import path
from .views import ObservationSearchView, TemporalProductView, TemporalSearchView

urlpatterns = [
    path("observations/search/", ObservationSearchView.as_view(), name="observation-search"),
    path("observations/temporal/", TemporalSearchView.as_view(), name="temporal-search"),
    path("observations/temporal/<str:product_id>/", TemporalProductView.as_view(), name="temporal-product"),
]
