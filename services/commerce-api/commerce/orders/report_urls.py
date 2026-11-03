from django.urls import path

from . import report_views

urlpatterns = [
    path("dashboard/", report_views.DashboardView.as_view(), name="manage-dashboard"),
    path("reports/sales/", report_views.SalesReportView.as_view(), name="manage-sales-report"),
]
