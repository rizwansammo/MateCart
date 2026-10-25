from django.urls import path

from . import views

urlpatterns = [
    path("auth/login/", views.LoginView.as_view(), name="console-login"),
    path("auth/mfa/verify/", views.SecondStepView.as_view(), name="console-mfa-verify"),
    path("auth/mfa/email/", views.SecondStepEmailView.as_view(), name="console-mfa-email"),
    path("auth/logout/", views.LogoutView.as_view(), name="console-logout"),
    path("auth/session/", views.SessionView.as_view(), name="console-session"),
    path(
        "auth/activate/inspect/",
        views.ActivationInspectView.as_view(),
        name="console-activate-inspect",
    ),
    path("auth/activate/", views.ActivationView.as_view(), name="console-activate"),
    path(
        "auth/password/forgot/", views.PasswordForgotView.as_view(), name="console-password-forgot"
    ),
    path("auth/password/reset/", views.PasswordResetView.as_view(), name="console-password-reset"),
    path(
        "enroll/totp/setup/", views.EnrollTotpSetupView.as_view(), name="console-enroll-totp-setup"
    ),
    path(
        "enroll/totp/confirm/",
        views.EnrollTotpConfirmView.as_view(),
        name="console-enroll-totp-confirm",
    ),
    path(
        "enroll/email/start/",
        views.EnrollEmailStartView.as_view(),
        name="console-enroll-email-start",
    ),
    path(
        "enroll/email/confirm/",
        views.EnrollEmailConfirmView.as_view(),
        name="console-enroll-email-confirm",
    ),
    path("account/security/", views.SecurityView.as_view(), name="console-security"),
    path(
        "account/reauthenticate/", views.ReauthenticateView.as_view(), name="console-reauthenticate"
    ),
    path("account/mfa/totp/setup/", views.TotpSetupView.as_view(), name="console-totp-setup"),
    path("account/mfa/totp/confirm/", views.TotpConfirmView.as_view(), name="console-totp-confirm"),
    path("account/mfa/totp/disable/", views.TotpDisableView.as_view(), name="console-totp-disable"),
    path(
        "account/mfa/email/start/", views.EmailCodesStartView.as_view(), name="console-email-start"
    ),
    path(
        "account/mfa/email/confirm/",
        views.EmailCodesConfirmView.as_view(),
        name="console-email-confirm",
    ),
    path(
        "account/mfa/email/disable/",
        views.EmailCodesDisableView.as_view(),
        name="console-email-disable",
    ),
    path(
        "account/mfa/recovery-codes/",
        views.RecoveryCodesView.as_view(),
        name="console-recovery-codes",
    ),
    path(
        "account/password/change/",
        views.PasswordChangeView.as_view(),
        name="console-password-change",
    ),
]
