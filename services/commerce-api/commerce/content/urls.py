from django.urls import path

from . import views

urlpatterns = [
    path("pages/", views.PageListView.as_view(), name="content-pages"),
    path("pages/<uuid:page_id>/", views.PageDetailView.as_view(), name="content-page"),
    path(
        "pages/<uuid:page_id>/status/", views.PageStatusView.as_view(), name="content-page-status"
    ),
    path("sections/", views.SectionListView.as_view(), name="content-sections"),
    path("sections/order/", views.SectionOrderView.as_view(), name="content-sections-order"),
    path(
        "sections/defaults/", views.SectionDefaultsView.as_view(), name="content-sections-defaults"
    ),
    path("sections/<uuid:section_id>/", views.SectionDetailView.as_view(), name="content-section"),
]
