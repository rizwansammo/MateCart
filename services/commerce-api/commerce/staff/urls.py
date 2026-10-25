from django.urls import path

from . import views

urlpatterns = [
    path("auth/login/", views.LoginView.as_view(), name="manage-login"),
    path("auth/mfa/verify/", views.MfaVerifyView.as_view(), name="manage-mfa-verify"),
    path("auth/mfa/email/", views.MfaEmailView.as_view(), name="manage-mfa-email"),
    path("auth/logout/", views.LogoutView.as_view(), name="manage-logout"),
    path("auth/session/", views.SessionView.as_view(), name="manage-session"),
    path(
        "auth/password/forgot/", views.PasswordForgotView.as_view(), name="manage-password-forgot"
    ),
    path("auth/password/reset/", views.PasswordResetView.as_view(), name="manage-password-reset"),
    path(
        "invitation/inspect/",
        views.InvitationInspectView.as_view(),
        name="manage-invitation-inspect",
    ),
    path(
        "invitation/accept/", views.InvitationAcceptView.as_view(), name="manage-invitation-accept"
    ),
    path("team/", views.TeamView.as_view(), name="manage-team"),
    path(
        "team/invitations/", views.InvitationCreateView.as_view(), name="manage-invitation-create"
    ),
    path(
        "team/invitations/<uuid:invitation_id>/resend/",
        views.InvitationResendView.as_view(),
        name="manage-invitation-resend",
    ),
    path(
        "team/invitations/<uuid:invitation_id>/revoke/",
        views.InvitationRevokeView.as_view(),
        name="manage-invitation-revoke",
    ),
    path(
        "team/members/<uuid:membership_id>/role/",
        views.MemberRoleView.as_view(),
        name="manage-member-role",
    ),
    path(
        "team/members/<uuid:membership_id>/suspend/",
        views.MemberSuspendView.as_view(),
        name="manage-member-suspend",
    ),
    path(
        "team/members/<uuid:membership_id>/reactivate/",
        views.MemberReactivateView.as_view(),
        name="manage-member-reactivate",
    ),
    path("account/security/", views.SecurityView.as_view(), name="manage-security"),
    path(
        "account/reauthenticate/", views.ReauthenticateView.as_view(), name="manage-reauthenticate"
    ),
    path("account/mfa/totp/setup/", views.TotpSetupView.as_view(), name="manage-totp-setup"),
    path("account/mfa/totp/confirm/", views.TotpConfirmView.as_view(), name="manage-totp-confirm"),
    path("account/mfa/totp/disable/", views.TotpDisableView.as_view(), name="manage-totp-disable"),
    path(
        "account/mfa/email/start/", views.EmailMfaStartView.as_view(), name="manage-email-mfa-start"
    ),
    path(
        "account/mfa/email/confirm/",
        views.EmailMfaConfirmView.as_view(),
        name="manage-email-mfa-confirm",
    ),
    path(
        "account/mfa/email/disable/",
        views.EmailMfaDisableView.as_view(),
        name="manage-email-mfa-disable",
    ),
    path(
        "account/mfa/recovery-codes/",
        views.RecoveryCodesView.as_view(),
        name="manage-recovery-codes",
    ),
    path(
        "account/password/change/",
        views.PasswordChangeView.as_view(),
        name="manage-password-change",
    ),
]
