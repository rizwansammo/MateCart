from django.urls import path

from . import views

urlpatterns = [
    path("methods/", views.MethodsView.as_view(), name="account-methods"),
    path("register/", views.RegisterView.as_view(), name="account-register"),
    path("register/verify/", views.RegisterVerifyView.as_view(), name="account-register-verify"),
    path("code/resend/", views.ResendCodeView.as_view(), name="account-code-resend"),
    path("login/", views.LoginView.as_view(), name="account-login"),
    path("logout/", views.LogoutView.as_view(), name="account-logout"),
    path("password/forgot/", views.PasswordForgotView.as_view(), name="account-password-forgot"),
    path("password/reset/", views.PasswordResetView.as_view(), name="account-password-reset"),
    path("password/change/", views.PasswordChangeView.as_view(), name="account-password-change"),
    path("phone/start/", views.PhoneStartView.as_view(), name="account-phone-start"),
    path("phone/verify/", views.PhoneVerifyView.as_view(), name="account-phone-verify"),
    path("phone/complete/", views.PhoneCompleteView.as_view(), name="account-phone-complete"),
    path("google/start/", views.GoogleStartView.as_view(), name="account-google-start"),
    path("google/callback/", views.GoogleCallbackView.as_view(), name="account-google-callback"),
    path("me/", views.MeView.as_view(), name="account-me"),
    path("reauthenticate/", views.ReauthenticateView.as_view(), name="account-reauthenticate"),
    path(
        "reauthenticate/code/",
        views.ReauthenticateCodeView.as_view(),
        name="account-reauthenticate-code",
    ),
    path("link/email/start/", views.LinkEmailStartView.as_view(), name="account-link-email-start"),
    path(
        "link/email/verify/", views.LinkEmailVerifyView.as_view(), name="account-link-email-verify"
    ),
    path("link/phone/start/", views.LinkPhoneStartView.as_view(), name="account-link-phone-start"),
    path(
        "link/phone/verify/", views.LinkPhoneVerifyView.as_view(), name="account-link-phone-verify"
    ),
    path(
        "link/google/start/", views.LinkGoogleStartView.as_view(), name="account-link-google-start"
    ),
    path(
        "link/google/remove/", views.UnlinkGoogleView.as_view(), name="account-link-google-remove"
    ),
]
