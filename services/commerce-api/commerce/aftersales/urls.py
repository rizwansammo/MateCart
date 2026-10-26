from django.urls import path
from . import views

urlpatterns = [
    path("reviews/", views.CustomerReviewView.as_view(), name="customer-reviews"),
    path("cases/", views.CustomerCaseListView.as_view(), name="customer-cases"),
    path("cases/<uuid:case_id>/", views.CustomerCaseDetailView.as_view(), name="customer-case"),
    path("products/<uuid:product_id>/reviews/", views.ReviewPublicView.as_view(), name="public-reviews"),
]
