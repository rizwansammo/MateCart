from django.urls import path

from . import views

urlpatterns = [
    path("assets/", views.MediaListView.as_view(), name="media-assets"),
    path("assets/upload/", views.MediaUploadView.as_view(), name="media-upload"),
    path("assets/<uuid:asset_id>/", views.MediaDetailView.as_view(), name="media-asset"),
    path("assets/<uuid:asset_id>/file/", views.MediaFileView.as_view(), name="media-file"),
]
