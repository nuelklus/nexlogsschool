from django.urls import path

from .auth_views import (
    CurrentUserView,
    LoginView,
    LogoutView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    RefreshView,
    SendVerificationEmailView,
    VerifyEmailView,
)
from .billing_views import SubscriptionStatusView
from .enrollment import (
    AdmissionSettingsView,
    GuardianDetailView,
    GuardianInviteView,
    MyChildrenView,
    MyStudentProfileView,
    StudentDetailView,
    StudentListCreateView,
    StudentPortalInviteView,
)
from .views import (
    AcceptStaffInvitationView,
    InitialSchoolSetupView,
    RevokeStaffInvitationView,
    StaffInvitationView,
)

urlpatterns = [
    path("setup/", InitialSchoolSetupView.as_view(), name="initial-school-setup"),
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/refresh/", RefreshView.as_view(), name="auth-refresh"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path("auth/me/", CurrentUserView.as_view(), name="auth-current-user"),
    path(
        "auth/email/verify/",
        VerifyEmailView.as_view(),
        name="auth-email-verify",
    ),
    path(
        "auth/email/verification-request/",
        SendVerificationEmailView.as_view(),
        name="auth-email-verification-request",
    ),
    path(
        "auth/password-reset/",
        PasswordResetRequestView.as_view(),
        name="auth-password-reset-request",
    ),
    path(
        "auth/password-reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="auth-password-reset-confirm",
    ),
    path(
        "tenants/<uuid:tenant_id>/invitations/",
        StaffInvitationView.as_view(),
        name="staff-invitation-create",
    ),
    path(
        "tenants/<uuid:tenant_id>/invitations/<uuid:invitation_id>/",
        RevokeStaffInvitationView.as_view(),
        name="staff-invitation-revoke",
    ),
    path(
        "invitations/accept/",
        AcceptStaffInvitationView.as_view(),
        name="staff-invitation-accept",
    ),
    path(
        "tenants/<uuid:tenant_id>/subscription/",
        SubscriptionStatusView.as_view(),
        name="tenant-subscription",
    ),
    path(
        "tenants/<uuid:tenant_id>/admission-settings/",
        AdmissionSettingsView.as_view(),
        name="admission-settings",
    ),
    path("tenants/<uuid:tenant_id>/students/", StudentListCreateView.as_view(), name="student-list"),
    path("tenants/<uuid:tenant_id>/students/<uuid:student_id>/", StudentDetailView.as_view(), name="student-detail"),
    path(
        "tenants/<uuid:tenant_id>/students/<uuid:student_id>/guardians/",
        GuardianInviteView.as_view(),
        name="student-guardian-invite",
    ),
    path(
        "tenants/<uuid:tenant_id>/students/<uuid:student_id>/guardians/<uuid:link_id>/",
        GuardianDetailView.as_view(),
        name="student-guardian-detail",
    ),
    path(
        "tenants/<uuid:tenant_id>/students/<uuid:student_id>/portal-invitation/",
        StudentPortalInviteView.as_view(),
        name="student-portal-invite",
    ),
    path("tenants/<uuid:tenant_id>/me/children/", MyChildrenView.as_view(), name="my-children"),
    path("tenants/<uuid:tenant_id>/me/student/", MyStudentProfileView.as_view(), name="my-student-profile"),
]
