from django.urls import path
from . import views

urlpatterns = [
    path("reviews/", views.ManageReviewView.as_view(), name="manage-reviews"),
    path("reviews/<uuid:review_id>/", views.ManageReviewDecisionView.as_view(), name="manage-review"),
    path("cases/", views.ManageCaseListView.as_view(), name="manage-cases"),
    path("cases/<uuid:case_id>/", views.ManageCaseDetailView.as_view(), name="manage-case"),
]
