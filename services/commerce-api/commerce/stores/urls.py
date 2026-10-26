from django.urls import path

from . import views

urlpatterns = [
    path("appearance/", views.AppearanceView.as_view(), name="store-appearance"),
    path("appearance/reset/", views.AppearanceResetView.as_view(), name="store-appearance-reset"),
    path("modules/", views.ModuleListView.as_view(), name="store-modules"),
    path("modules/<slug:key>/", views.ModuleDetailView.as_view(), name="store-module"),
]
