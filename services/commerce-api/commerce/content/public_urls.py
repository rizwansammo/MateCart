from django.urls import path

from . import public

urlpatterns = [
    path("config/", public.StorefrontConfigView.as_view(), name="storefront-config"),
    path("home/", public.StorefrontHomeView.as_view(), name="storefront-home"),
    path("pages/<slug:slug>/", public.StorefrontPageView.as_view(), name="storefront-page"),
]
