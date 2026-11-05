from django.urls import path

from . import views

urlpatterns = [
    path("", views.OverviewView.as_view(), name="data-overview"),
    path("template/", views.TemplateView.as_view(), name="data-template"),
    path("imports/", views.ImportCreateView.as_view(), name="data-import"),
    path("migrations/", views.MigrationCreateView.as_view(), name="data-migration"),
    path("exports/", views.ExportCreateView.as_view(), name="data-export"),
    path("jobs/", views.JobListView.as_view(), name="data-jobs"),
    path("jobs/<uuid:job_id>/", views.JobDetailView.as_view(), name="data-job"),
    path("jobs/<uuid:job_id>/items/", views.JobItemsView.as_view(), name="data-job-items"),
    path(
        "jobs/<uuid:job_id>/decisions/", views.JobDecisionsView.as_view(), name="data-job-decisions"
    ),
    path("jobs/<uuid:job_id>/approve/", views.JobApproveView.as_view(), name="data-job-approve"),
    path("jobs/<uuid:job_id>/cancel/", views.JobCancelView.as_view(), name="data-job-cancel"),
    path("jobs/<uuid:job_id>/resume/", views.JobResumeView.as_view(), name="data-job-resume"),
    path(
        "jobs/<uuid:job_id>/compensate/",
        views.JobCompensateView.as_view(),
        name="data-job-compensate",
    ),
    path("files/<uuid:file_id>/download/", views.FileDownloadView.as_view(), name="data-file"),
    path("sources/", views.SourceListView.as_view(), name="data-sources"),
    path("sources/<uuid:source_id>/", views.SourceDetailView.as_view(), name="data-source"),
    path(
        "sources/<uuid:source_id>/verify/",
        views.SourceVerifyView.as_view(),
        name="data-source-verify",
    ),
    path(
        "history/customers/",
        views.HistoricalCustomerListView.as_view(),
        name="data-history-customers",
    ),
    path("history/orders/", views.HistoricalOrderListView.as_view(), name="data-history-orders"),
]
