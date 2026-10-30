import hashlib
import re
import time
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from io import StringIO
from threading import Barrier, Event, Thread
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.core import mail
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.db import DatabaseError, connection, connections, transaction
from django.db.transaction import TransactionManagementError
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    AuditEvent,
    Membership,
    MembershipInvitation,
    PlatformSetupState,
    Student,
    StudentGuardian,
    SubscriptionPlan,
    Tenant,
    TenantSubscription,
    User,
)
from .subscriptions import (
    SubscriptionError,
    activate_subscription,
    add_months,
    has_active_subscription,
)
from .views import start_trial_subscription


def create_tenant(**kwargs):
    tenant = Tenant.objects.create(**kwargs)
    start_trial_subscription(tenant)
    return tenant


@override_settings(
    DEBUG=True,
    NLSCHOOL_SETUP_TOKEN="",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class InitialSchoolSetupTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.payload = {
            "school_name": "North Star Academy",
            "school_email": "office@northstar.example",
            "school_phone": "+233 20 123 4567",
            "country": "Ghana",
            "region": "Greater Accra",
            "address": "1 School Road",
            "school_type": "Private",
            "owner_name": "Ama Mensah",
            "owner_email": "ama@northstar.example",
            "owner_password": "Taller-River-804",
        }

    def test_setup_status_and_creation(self):
        self.assertTrue(self.client.get("/api/setup/").data["available"])

        response = self.client.post("/api/setup/", self.payload, format="json")

        self.assertEqual(response.status_code, 201)
        tenant = Tenant.objects.get()
        owner = User.objects.get()
        self.assertEqual(tenant.name, self.payload["school_name"])
        self.assertEqual(owner.email, self.payload["owner_email"])
        self.assertTrue(owner.check_password(self.payload["owner_password"]))
        self.assertFalse(owner.email_verified)
        self.assertEqual(
            Membership.objects.get(tenant=tenant, user=owner).role,
            Membership.Role.OWNER,
        )
        self.assertTrue(PlatformSetupState.objects.get(pk=1).initial_setup_completed)
        subscription = TenantSubscription.objects.get(tenant=tenant)
        self.assertEqual(subscription.status, TenantSubscription.Status.TRIALING)
        self.assertEqual(subscription.plan.code, "trial")
        self.assertEqual(
            subscription.trial_ends_at - subscription.trial_started_at,
            timedelta(days=30),
        )
        self.assertEqual(AuditEvent.objects.filter(tenant=tenant).count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/account/verify?token=", mail.outbox[0].body)
        self.assertFalse(self.client.get("/api/setup/").data["available"])

    def test_setup_is_single_use(self):
        self.client.post("/api/setup/", self.payload, format="json")
        response = self.client.post("/api/setup/", self.payload, format="json")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(Tenant.objects.count(), 1)

    def test_invalid_owner_password_does_not_create_records(self):
        self.payload["owner_password"] = "password"

        response = self.client.post("/api/setup/", self.payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Tenant.objects.count(), 0)
        self.assertEqual(User.objects.count(), 0)

    def test_existing_owner_email_is_rejected(self):
        User.objects.create_user(
            email=self.payload["owner_email"],
            password="Taller-River-804",
            full_name=self.payload["owner_name"],
        )

        response = self.client.post("/api/setup/", self.payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("owner_email", response.data)
        self.assertEqual(Tenant.objects.count(), 0)

    @override_settings(DEBUG=False, NLSCHOOL_SETUP_TOKEN="")
    def test_setup_is_disabled_in_production_without_token(self):
        response = self.client.post("/api/setup/", self.payload, format="json")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Tenant.objects.count(), 0)

    @override_settings(DEBUG=False, NLSCHOOL_SETUP_TOKEN="one-time-secret")
    def test_production_setup_requires_configured_token(self):
        denied = self.client.post("/api/setup/", self.payload, format="json")
        allowed = self.client.post(
            "/api/setup/",
            self.payload,
            format="json",
            HTTP_X_NLSCHOOL_SETUP_TOKEN="one-time-secret",
        )

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 201)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class StaffInvitationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = create_tenant(
            name="North Star Academy",
            slug="north-star-academy",
            email="office@northstar.example",
            phone="+233201234567",
        )
        self.owner = User.objects.create_user(
            email="owner@northstar.example",
            password="Owner-Mango-5732",
            full_name="Ama Mensah",
        )
        Membership.objects.create(
            tenant=self.tenant,
            user=self.owner,
            role=Membership.Role.OWNER,
        )
        self.client.force_authenticate(user=self.owner)
        self.url = f"/api/tenants/{self.tenant.pk}/invitations/"

    def invite(self, email="staff@northstar.example", role="teacher"):
        return self.client.post(
            self.url,
            {"email": email, "role": role},
            format="json",
        )

    def email_token(self):
        match = re.search(r"token=([A-Za-z0-9_-]+)", mail.outbox[-1].body)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_owner_can_invite_staff_and_only_hash_is_stored(self):
        response = self.invite()

        self.assertEqual(response.status_code, 201)
        invitation = MembershipInvitation.objects.get()
        token = self.email_token()
        self.assertEqual(invitation.email, "staff@northstar.example")
        self.assertEqual(invitation.role, MembershipInvitation.Role.TEACHER)
        self.assertEqual(
            invitation.token_hash,
            hashlib.sha256(token.encode("utf-8")).hexdigest(),
        )
        self.assertNotIn(token, invitation.token_hash)
        self.assertNotIn("token", response.data)
        self.assertEqual(mail.outbox[-1].to, [invitation.email])
        self.assertEqual(
            AuditEvent.objects.get(action="membership.invitation_created").actor,
            self.owner,
        )

    def test_invitation_rejects_non_staff_roles(self):
        response = self.invite(role="parent")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(MembershipInvitation.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_only_owner_or_school_admin_can_invite_staff(self):
        teacher = User.objects.create_user(
            email="teacher@northstar.example",
            password="Teacher-Mango-5732",
            full_name="Kofi Owusu",
        )
        Membership.objects.create(
            tenant=self.tenant,
            user=teacher,
            role=Membership.Role.TEACHER,
        )
        self.client.force_authenticate(user=teacher)

        response = self.invite()

        self.assertEqual(response.status_code, 404)
        self.assertEqual(MembershipInvitation.objects.count(), 0)

    def test_school_admin_can_invite_staff(self):
        administrator = User.objects.create_user(
            email="admin@northstar.example",
            password="Admin-Mango-5732",
            full_name="Yaw Asante",
        )
        Membership.objects.create(
            tenant=self.tenant,
            user=administrator,
            role=Membership.Role.SCHOOL_ADMIN,
        )
        self.client.force_authenticate(user=administrator)

        response = self.invite()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(MembershipInvitation.objects.get().invited_by, administrator)

    def test_staff_invitation_is_tenant_scoped(self):
        other_tenant = Tenant.objects.create(
            name="East Ridge School",
            slug="east-ridge-school",
            email="office@eastridge.example",
            phone="+233201234568",
        )
        other_owner = User.objects.create_user(
            email="other-owner@eastridge.example",
            password="Owner-Mango-5732",
            full_name="Akosua Nkrumah",
        )
        Membership.objects.create(
            tenant=other_tenant,
            user=other_owner,
            role=Membership.Role.OWNER,
        )
        self.client.force_authenticate(user=other_owner)

        response = self.invite()

        self.assertEqual(response.status_code, 404)
        self.assertEqual(MembershipInvitation.objects.count(), 0)

    def test_acceptance_creates_verified_account_and_membership_once(self):
        self.assertEqual(self.invite().status_code, 201)
        token = self.email_token()

        response = self.client.post(
            "/api/invitations/accept/",
            {
                "token": token,
                "full_name": "Esi Boateng",
                "password": "Mango-Jungle-5842",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="staff@northstar.example")
        self.assertTrue(user.email_verified)
        self.assertTrue(user.check_password("Mango-Jungle-5842"))
        self.assertEqual(
            Membership.objects.get(tenant=self.tenant, user=user).role,
            Membership.Role.TEACHER,
        )
        self.assertIsNotNone(MembershipInvitation.objects.get().accepted_at)
        self.assertEqual(
            AuditEvent.objects.filter(action="membership.invitation_accepted").count(),
            1,
        )

        replay = self.client.post(
            "/api/invitations/accept/",
            {
                "token": token,
                "full_name": "Esi Boateng",
                "password": "Mango-Jungle-5842",
            },
            format="json",
        )
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(
            Membership.objects.filter(tenant=self.tenant, user=user).count(),
            1,
        )

    def test_existing_account_must_confirm_its_password(self):
        existing_user = User.objects.create_user(
            email="staff@northstar.example",
            password="Existing-Lion-5914",
            full_name="Kwame Antwi",
        )
        self.assertEqual(self.invite().status_code, 201)
        token = self.email_token()
        accept_url = "/api/invitations/accept/"

        denied = self.client.post(accept_url, {"token": token}, format="json")
        accepted = self.client.post(
            accept_url,
            {"token": token, "password": "Existing-Lion-5914"},
            format="json",
        )

        self.assertEqual(denied.status_code, 400)
        self.assertEqual(accepted.status_code, 201)
        existing_user.refresh_from_db()
        self.assertTrue(existing_user.email_verified)
        self.assertTrue(existing_user.check_password("Existing-Lion-5914"))
        self.assertEqual(Membership.objects.filter(user=existing_user).count(), 1)

    def test_expired_invitation_cannot_be_accepted(self):
        self.assertEqual(self.invite().status_code, 201)
        invitation = MembershipInvitation.objects.get()
        invitation.expires_at = timezone.now() - timedelta(seconds=1)
        invitation.save(update_fields=("expires_at",))

        response = self.client.post(
            "/api/invitations/accept/",
            {
                "token": self.email_token(),
                "full_name": "Esi Boateng",
                "password": "Mango-Jungle-5842",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(email="staff@northstar.example").exists())

    def test_revoked_invitation_cannot_be_accepted(self):
        response = self.invite()
        invitation_id = response.data["id"]
        token = self.email_token()

        revoked = self.client.delete(
            f"/api/tenants/{self.tenant.pk}/invitations/{invitation_id}/"
        )
        acceptance = self.client.post(
            "/api/invitations/accept/",
            {
                "token": token,
                "full_name": "Esi Boateng",
                "password": "Mango-Jungle-5842",
            },
            format="json",
        )

        self.assertEqual(revoked.status_code, 204)
        self.assertEqual(acceptance.status_code, 400)
        self.assertIsNotNone(MembershipInvitation.objects.get().revoked_at)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AccountAuthenticationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.tenant = Tenant.objects.create(
            name="North Star Academy",
            slug="north-star-auth",
            email="office@northstar.example",
            phone="+233201234567",
        )
        self.user = User.objects.create_user(
            email="owner@northstar.example",
            password="Owner-Orange-7851",
            full_name="Ama Mensah",
        )
        Membership.objects.create(
            tenant=self.tenant,
            user=self.user,
            role=Membership.Role.OWNER,
        )

    def verify_user(self):
        from urllib.parse import parse_qs, urlparse

        response = self.client.post(
            "/api/auth/email/verification-request/",
            {"email": self.user.email},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        query = parse_qs(urlparse(mail.outbox[-1].body.splitlines()[2]).query)
        token = query["token"][0]
        return self.client.post(
            "/api/auth/email/verify/",
            {"token": token},
            format="json",
        )

    def login(self):
        return self.client.post(
            "/api/auth/login/",
            {"email": self.user.email, "password": "Owner-Orange-7851"},
            format="json",
        )

    def test_unverified_user_cannot_log_in_until_email_is_verified(self):
        denied = self.login()
        verified = self.verify_user()
        allowed = self.login()

        self.assertEqual(denied.status_code, 400)
        self.assertIn("detail", denied.data)
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(allowed.status_code, 200)
        self.assertIn("access", allowed.data)
        self.assertNotIn("refresh", allowed.data)
        self.assertTrue(self.client.cookies["nlschool_refresh"]["httponly"])
        self.assertEqual(allowed.data["user"]["memberships"][0]["role"], "owner")

    def test_email_verification_token_is_single_use(self):
        self.assertEqual(self.verify_user().status_code, 200)
        action = self.user.email_action_tokens.get()
        token = mail.outbox[-1].body.split("token=", 1)[1].split()[0]

        repeated = self.client.post(
            "/api/auth/email/verify/",
            {"token": token},
            format="json",
        )

        self.assertEqual(repeated.status_code, 400)
        self.assertIsNotNone(action.used_at)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)
        self.assertTrue(
            AuditEvent.objects.filter(
                tenant=self.tenant,
                action="account.email_verified",
            ).exists()
        )

    def test_login_uses_one_account_for_owner_membership(self):
        self.user.email_verified = True
        self.user.save(update_fields=("email_verified",))

        response = self.login()
        authenticated = self.client.get(
            "/api/auth/me/",
            HTTP_AUTHORIZATION=f"Bearer {response.data['access']}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(authenticated.status_code, 200)
        self.assertEqual(authenticated.data["id"], str(self.user.pk))
        self.assertEqual(authenticated.data["memberships"][0]["role"], "owner")

    def test_login_rejects_untrusted_browser_origins(self):
        self.user.email_verified = True
        self.user.save(update_fields=("email_verified",))

        response = self.client.post(
            "/api/auth/login/",
            {"email": self.user.email, "password": "Owner-Orange-7851"},
            format="json",
            HTTP_ORIGIN="https://untrusted.example",
        )

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("nlschool_refresh", self.client.cookies)

    def test_shared_login_returns_all_active_system_roles(self):
        self.user.email_verified = True
        self.user.save(update_fields=("email_verified",))
        parent_school = create_tenant(
            name="East Ridge School",
            slug="east-ridge-auth",
            email="office@eastridge.example",
            phone="+233201234568",
        )
        student_school = create_tenant(
            name="West Ridge School",
            slug="west-ridge-auth",
            email="office@westridge.example",
            phone="+233201234569",
        )
        Membership.objects.create(
            tenant=parent_school,
            user=self.user,
            role=Membership.Role.PARENT,
        )
        Membership.objects.create(
            tenant=student_school,
            user=self.user,
            role=Membership.Role.STUDENT,
        )

        response = self.login()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {membership["role"] for membership in response.data["user"]["memberships"]},
            {"owner", "parent", "student"},
        )

    def test_refresh_rotates_cookie_and_logout_revokes_session(self):
        self.user.email_verified = True
        self.user.save(update_fields=("email_verified",))
        self.assertEqual(self.login().status_code, 200)
        first_refresh = self.client.cookies["nlschool_refresh"].value

        refreshed = self.client.post("/api/auth/refresh/", {}, format="json")
        second_refresh = self.client.cookies["nlschool_refresh"].value
        logged_out = self.client.post(
            "/api/auth/logout/",
            {},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {refreshed.data['access']}",
        )

        self.assertEqual(refreshed.status_code, 200)
        self.assertIn("access", refreshed.data)
        self.assertNotEqual(first_refresh, second_refresh)
        self.assertEqual(logged_out.status_code, 204)
        self.assertEqual(self.client.cookies["nlschool_refresh"].value, "")

        replay = self.client.post(
            "/api/auth/refresh/",
            {"refresh": second_refresh},
            format="json",
        )
        self.assertEqual(replay.status_code, 401)

    def test_password_reset_is_generic_and_revokes_existing_refresh_tokens(self):
        self.user.email_verified = True
        self.user.save(update_fields=("email_verified",))
        login = self.login()
        self.assertEqual(login.status_code, 200)
        refresh = self.client.cookies["nlschool_refresh"].value
        access = login.data["access"]

        response = self.client.post(
            "/api/auth/password-reset/",
            {"email": self.user.email},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("if an active account exists", response.data["detail"].lower())
        reset_link = next(
            line for line in mail.outbox[-1].body.splitlines()
            if "/password-reset/confirm?token=" in line
        )
        token = reset_link.split("token=", 1)[1]

        reset = self.client.post(
            "/api/auth/password-reset/confirm/",
            {
                "token": token,
                "new_password": "New-Orange-Password-7852",
            },
            format="json",
        )
        self.user.refresh_from_db()
        self.assertEqual(reset.status_code, 200)
        self.assertTrue(self.user.check_password("New-Orange-Password-7852"))

        revoked = self.client.post(
            "/api/auth/refresh/",
            {"refresh": refresh},
            format="json",
        )
        self.assertEqual(revoked.status_code, 401)
        revoked_access = self.client.get(
            "/api/auth/me/",
            HTTP_AUTHORIZATION=f"Bearer {access}",
        )
        self.assertEqual(revoked_access.status_code, 401)

        replay = self.client.post(
            "/api/auth/password-reset/confirm/",
            {
                "token": token,
                "new_password": "Another-Orange-Password-7853",
            },
            format="json",
        )
        self.assertEqual(replay.status_code, 400)

    def test_reset_request_does_not_disclose_unknown_email(self):
        known = self.client.post(
            "/api/auth/password-reset/",
            {"email": self.user.email},
            format="json",
        )
        unknown = self.client.post(
            "/api/auth/password-reset/",
            {"email": "nobody@northstar.example"},
            format="json",
        )

        self.assertEqual(known.status_code, 200)
        self.assertEqual(known.data, unknown.data)
        self.assertEqual(len(mail.outbox), 1)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EnrollmentAndGuardianTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = create_tenant(
            name="North Star Academy",
            slug="north-star-academy",
            email="office@northstar.example",
            phone="+233201234567",
        )
        self.owner = self.member("owner@northstar.example", Membership.Role.OWNER)
        self.teacher = self.member("teacher@northstar.example", Membership.Role.TEACHER)
        self.base = f"/api/tenants/{self.tenant.pk}"
        self.client.force_authenticate(user=self.owner)

    def member(self, email, role, tenant=None):
        user = User.objects.create_user(
            email=email, password="Taller-River-804", full_name=email.split("@")[0]
        )
        user.email_verified = True
        user.save(update_fields=("email_verified",))
        Membership.objects.create(tenant=tenant or self.tenant, user=user, role=role)
        return user

    def enroll(self, number=None, first="Esi", last="Boateng"):
        # `number` is accepted for old call sites but ignored: the server generates it.
        response = self.client.post(
            f"{self.base}/students/",
            {"first_name": first, "last_name": last},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["id"]

    def token(self):
        return re.search(r"token=([A-Za-z0-9_-]+)", mail.outbox[-1].body).group(1)

    def accept(self, token, **extra):
        return APIClient().post(
            "/api/invitations/accept/",
            {"token": token, "full_name": "Yaa Boateng", "password": "Quiet-Lagoon-731", **extra},
            format="json",
        )

    def test_admin_enrolls_students_and_a_supplied_number_is_ignored(self):
        self.enroll()
        second = self.client.post(
            f"{self.base}/students/",
            {"admission_number": "A-001", "first_name": "Kojo", "last_name": "Asare"},
            format="json",
        )
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(second.data["admission_number"], "A-001")
        self.assertEqual(Student.objects.count(), 2)
        self.assertTrue(AuditEvent.objects.filter(action="student.enrolled").exists())

    def test_teacher_can_read_but_not_enroll(self):
        student_id = self.enroll()
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(self.client.get(f"{self.base}/students/").status_code, 200)
        self.assertEqual(self.client.get(f"{self.base}/students/{student_id}/").status_code, 200)
        response = self.client.post(
            f"{self.base}/students/",
            {"admission_number": "A-002", "first_name": "A", "last_name": "B"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)

    def test_students_are_tenant_scoped(self):
        student_id = self.enroll()
        other = create_tenant(
            name="Other", slug="other", email="o@o.example", phone="1"
        )
        outsider = self.member("admin@other.example", Membership.Role.OWNER, other)
        self.client.force_authenticate(user=outsider)
        self.assertEqual(self.client.get(f"{self.base}/students/{student_id}/").status_code, 404)
        self.assertEqual(self.client.get(f"{self.base}/students/").status_code, 404)

    def test_new_parent_accepts_invitation_and_sees_only_linked_children(self):
        child = self.enroll()
        other_child = self.enroll("A-002", "Kojo", "Asare")
        response = self.client.post(
            f"{self.base}/students/{child}/guardians/",
            {"email": "Yaa@Example.com", "relationship": "Mother", "is_primary": True},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "invited")
        self.assertEqual(mail.outbox[-1].to, ["yaa@example.com"])

        token = self.token()
        accepted = self.accept(token)
        self.assertEqual(accepted.status_code, 201, accepted.data)
        parent = User.objects.get(email="yaa@example.com")
        self.assertTrue(parent.email_verified)
        link = StudentGuardian.objects.get()
        self.assertEqual((link.relationship, link.is_primary), ("Mother", True))
        self.assertEqual(self.accept(token).status_code, 400)

        self.client.force_authenticate(user=parent)
        children = self.client.get(f"{self.base}/me/children/")
        self.assertEqual([c["id"] for c in children.data], [child])
        self.assertEqual(self.client.get(f"{self.base}/students/{child}/").status_code, 200)
        self.assertEqual(self.client.get(f"{self.base}/students/{other_child}/").status_code, 404)
        self.assertEqual(self.client.get(f"{self.base}/students/").status_code, 404)

    def test_existing_parent_is_linked_to_second_child_directly(self):
        first = self.enroll()
        second = self.enroll("A-002", "Kojo", "Asare")
        self.client.post(
            f"{self.base}/students/{first}/guardians/", {"email": "yaa@example.com"}, format="json"
        )
        self.accept(self.token())
        sent = len(mail.outbox)
        response = self.client.post(
            f"{self.base}/students/{second}/guardians/", {"email": "yaa@example.com"}, format="json"
        )
        self.assertEqual(response.data["status"], "linked")
        self.assertEqual(len(mail.outbox), sent)
        self.assertEqual(StudentGuardian.objects.count(), 2)
        self.assertEqual(Membership.objects.filter(user__email="yaa@example.com").count(), 1)

    def test_parent_with_two_pending_invitations_can_accept_both(self):
        first = self.enroll()
        second = self.enroll("A-002", "Kojo", "Asare")
        self.client.post(
            f"{self.base}/students/{first}/guardians/", {"email": "yaa@example.com"}, format="json"
        )
        token_one = self.token()
        self.client.post(
            f"{self.base}/students/{second}/guardians/", {"email": "yaa@example.com"}, format="json"
        )
        token_two = self.token()
        self.assertEqual(self.accept(token_one).status_code, 201)
        self.assertEqual(self.accept(token_two).status_code, 201)
        self.assertEqual(StudentGuardian.objects.count(), 2)
        self.assertEqual(Membership.objects.filter(user__email="yaa@example.com").count(), 1)

    def test_staff_member_cannot_be_invited_as_guardian(self):
        student = self.enroll()
        response = self.client.post(
            f"{self.base}/students/{student}/guardians/",
            {"email": self.teacher.email},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(StudentGuardian.objects.count(), 0)

    def test_duplicate_pending_guardian_invitation_is_rejected(self):
        student = self.enroll()
        payload = {"email": "yaa@example.com"}
        self.client.post(f"{self.base}/students/{student}/guardians/", payload, format="json")
        again = self.client.post(f"{self.base}/students/{student}/guardians/", payload, format="json")
        self.assertEqual(again.status_code, 409)

    def test_student_portal_invitation_links_one_account(self):
        student = self.enroll()
        response = self.client.post(
            f"{self.base}/students/{student}/portal-invitation/",
            {"email": "esi@example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.accept(self.token()).status_code, 201)
        record = Student.objects.get()
        self.assertEqual(record.user.email, "esi@example.com")
        self.assertEqual(
            Membership.objects.get(user=record.user).role, Membership.Role.STUDENT
        )

        again = self.client.post(
            f"{self.base}/students/{student}/portal-invitation/",
            {"email": "other@example.com"},
            format="json",
        )
        self.assertEqual(again.status_code, 409)

        self.client.force_authenticate(user=record.user)
        self.assertEqual(self.client.get(f"{self.base}/me/student/").data["id"], student)
        other = self.client.post(
            f"{self.base}/students/", {"admission_number": "Z", "first_name": "a", "last_name": "b"}, format="json"
        )
        self.assertEqual(other.status_code, 404)

    def test_student_cannot_read_other_students(self):
        mine = self.enroll()
        theirs = self.enroll("A-002", "Kojo", "Asare")
        self.client.post(
            f"{self.base}/students/{mine}/portal-invitation/", {"email": "esi@example.com"}, format="json"
        )
        self.accept(self.token())
        self.client.force_authenticate(user=User.objects.get(email="esi@example.com"))
        self.assertEqual(self.client.get(f"{self.base}/students/{mine}/").status_code, 200)
        self.assertEqual(self.client.get(f"{self.base}/students/{theirs}/").status_code, 404)

    def test_revoked_guardian_invitation_cannot_be_accepted_and_unlink_works(self):
        student = self.enroll()
        self.client.post(
            f"{self.base}/students/{student}/guardians/", {"email": "yaa@example.com"}, format="json"
        )
        invitation = MembershipInvitation.objects.get()
        token = self.token()
        revoke = self.client.delete(f"{self.base}/invitations/{invitation.pk}/")
        self.assertEqual(revoke.status_code, 204)
        self.assertEqual(self.accept(token).status_code, 400)

        self.client.post(
            f"{self.base}/students/{student}/guardians/", {"email": "yaa@example.com"}, format="json"
        )
        self.accept(self.token())
        link = StudentGuardian.objects.get()
        removed = self.client.delete(f"{self.base}/students/{student}/guardians/{link.pk}/")
        self.assertEqual(removed.status_code, 204)
        self.assertEqual(StudentGuardian.objects.count(), 0)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SubscriptionExpiryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = create_tenant(
            name="North Star Academy",
            slug="north-star-expiry",
            email="office@northstar.example",
            phone="+233201234567",
        )
        self.owner = User.objects.create_user(
            email="owner@northstar.example",
            password="Taller-River-804",
            full_name="Ama Mensah",
        )
        Membership.objects.create(
            tenant=self.tenant, user=self.owner, role=Membership.Role.OWNER
        )
        self.client.force_authenticate(user=self.owner)
        self.url = f"/api/tenants/{self.tenant.pk}/students/"
        self.subscription = TenantSubscription.objects.get(tenant=self.tenant)

    def test_active_trial_has_access(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_ended_trial_is_expired_and_blocked(self):
        self.subscription.trial_ends_at = timezone.now() - timedelta(seconds=1)
        self.subscription.save()

        for response in (
            self.client.get(self.url),
            self.client.post(self.url, {}, format="json"),
            self.client.post(
                f"/api/tenants/{self.tenant.pk}/invitations/",
                {"email": "t@northstar.example", "role": "teacher"},
                format="json",
            ),
        ):
            self.assertEqual(response.status_code, 402)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, TenantSubscription.Status.EXPIRED)

    def test_renewed_subscription_restores_access(self):
        self.subscription.trial_ends_at = timezone.now() - timedelta(seconds=1)
        self.subscription.save()
        self.assertEqual(self.client.get(self.url).status_code, 402)

        TenantSubscription.objects.create(
            tenant=self.tenant,
            plan=self.subscription.plan,
            status=TenantSubscription.Status.ACTIVE,
            current_period_end=timezone.now() + timedelta(days=30),
        )
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_non_members_cannot_probe_subscription_state(self):
        outsider = User.objects.create_user(
            email="x@example.com", password="Taller-River-804", full_name="X"
        )
        self.subscription.status = TenantSubscription.Status.EXPIRED
        self.subscription.save()
        self.client.force_authenticate(user=outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_expire_command_marks_ended_subscriptions(self):
        self.subscription.trial_ends_at = timezone.now() - timedelta(days=1)
        self.subscription.save()
        call_command("expire_subscriptions", stdout=StringIO())
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, TenantSubscription.Status.EXPIRED)


def utc(year, month, day, hour=9):
    return datetime(year, month, day, hour, tzinfo=dt_timezone.utc)


class AddMonthsTests(SimpleTestCase):
    def test_calendar_months_not_fixed_day_counts(self):
        self.assertEqual(add_months(utc(2026, 1, 15), 1), utc(2026, 2, 15))
        self.assertEqual(add_months(utc(2026, 9, 1), 3), utc(2026, 12, 1))
        self.assertEqual(add_months(utc(2026, 11, 1), 3), utc(2027, 2, 1))
        self.assertEqual(add_months(utc(2026, 10, 26), 12), utc(2027, 10, 26))

    def test_month_end_is_clamped(self):
        self.assertEqual(add_months(utc(2026, 1, 31), 1), utc(2026, 2, 28))
        self.assertEqual(add_months(utc(2026, 11, 30), 3), utc(2027, 2, 28))
        self.assertEqual(add_months(utc(2026, 8, 31), 3), utc(2026, 11, 30))

    def test_leap_years(self):
        self.assertEqual(add_months(utc(2028, 1, 31), 1), utc(2028, 2, 29))
        self.assertEqual(add_months(utc(2027, 11, 30), 3), utc(2028, 2, 29))
        self.assertEqual(add_months(utc(2028, 2, 29), 12), utc(2029, 2, 28))
        self.assertEqual(add_months(utc(2028, 2, 29), 48), utc(2032, 2, 29))

    def test_anchor_day_prevents_drift_after_clamping(self):
        end = add_months(utc(2026, 11, 30), 3)
        self.assertEqual(end, utc(2027, 2, 28))
        self.assertEqual(add_months(end, 3, anchor_day=30), utc(2027, 5, 30))
        self.assertEqual(add_months(end, 3), utc(2027, 5, 28))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SubscriptionBillingTests(TestCase):
    def setUp(self):
        self.tenant = create_tenant(
            name="North Star Academy",
            slug="north-star-term",
            email="office@northstar.example",
            phone="+233201234567",
        )
        self.other = create_tenant(
            name="East Ridge School",
            slug="east-ridge",
            email="office@eastridge.example",
            phone="+233201234568",
        )
        self.termly = SubscriptionPlan.objects.create(
            name="Termly",
            code="termly",
            price=300,
            billing_interval=SubscriptionPlan.Interval.TERMLY,
        )
        self.monthly = SubscriptionPlan.objects.create(
            name="Monthly",
            code="monthly",
            price=120,
            billing_interval=SubscriptionPlan.Interval.MONTHLY,
        )
        self.yearly = SubscriptionPlan.objects.create(
            name="Yearly",
            code="yearly",
            price=1000,
            billing_interval=SubscriptionPlan.Interval.YEARLY,
        )

    def activate(self, plan, now, tenant=None, **kwargs):
        with patch("core.subscriptions.timezone.now", return_value=now):
            return activate_subscription(tenant or self.tenant, plan, **kwargs)

    def live(self, tenant=None):
        return TenantSubscription.objects.filter(
            tenant=tenant or self.tenant,
            status__in=(
                TenantSubscription.Status.TRIALING,
                TenantSubscription.Status.ACTIVE,
            ),
        )

    def test_period_lengths_by_interval(self):
        now = utc(2026, 11, 30)
        for plan, expected in (
            (self.monthly, utc(2026, 12, 30)),
            (self.termly, utc(2027, 2, 28)),
            (self.yearly, utc(2027, 11, 30)),
        ):
            tenant = create_tenant(
                name=plan.name, slug=f"s-{plan.code}", email="a@b.example", phone="1"
            )
            subscription = self.activate(plan, now, tenant=tenant)
            self.assertEqual(subscription.current_period_start, now)
            self.assertEqual(subscription.current_period_end, expected)

    def test_activation_ends_trial_and_leaves_one_live_subscription(self):
        subscription = self.activate(self.termly, utc(2026, 10, 26))

        self.assertEqual(subscription.status, TenantSubscription.Status.ACTIVE)
        self.assertEqual(subscription.started_at, utc(2026, 10, 26))
        trial = TenantSubscription.objects.get(tenant=self.tenant, plan__code="trial")
        self.assertEqual(trial.status, TenantSubscription.Status.CANCELLED)
        self.assertEqual(trial.cancelled_at, utc(2026, 10, 26))
        self.assertEqual(self.live().get(), subscription)

    def test_other_schools_are_not_modified(self):
        before = list(TenantSubscription.objects.filter(tenant=self.other).values())
        self.activate(self.termly, utc(2026, 10, 26))
        after = list(TenantSubscription.objects.filter(tenant=self.other).values())
        self.assertEqual(before, after)

    def test_trial_plan_cannot_be_activated(self):
        trial = SubscriptionPlan.objects.get(code="trial")
        with self.assertRaises(SubscriptionError):
            activate_subscription(self.tenant, trial)
        self.assertEqual(self.live().get().plan, trial)

    def test_inactive_plan_cannot_be_activated(self):
        self.termly.is_active = False
        self.termly.save()
        with self.assertRaises(SubscriptionError):
            activate_subscription(self.tenant, self.termly)
        self.assertEqual(self.live().get().plan.code, "trial")

    def test_early_renewal_extends_from_existing_expiry_not_today(self):
        first = self.activate(self.termly, utc(2026, 10, 26))
        self.assertEqual(first.current_period_end, utc(2027, 1, 26))

        renewed = self.activate(self.termly, utc(2026, 12, 20))

        self.assertEqual(renewed.pk, first.pk)
        self.assertEqual(renewed.current_period_start, utc(2026, 10, 26))
        self.assertEqual(renewed.current_period_end, utc(2027, 4, 26))
        self.assertEqual(TenantSubscription.objects.filter(plan=self.termly).count(), 1)

    def test_repeated_early_renewals_do_not_drift_at_month_end(self):
        self.activate(self.termly, utc(2026, 11, 30))
        for expected in (utc(2027, 5, 30), utc(2027, 8, 30), utc(2027, 11, 30)):
            renewed = self.activate(self.termly, utc(2026, 12, 1))
            self.assertEqual(renewed.current_period_end, expected)

    def test_renewal_after_expiry_starts_from_the_current_date(self):
        first = self.activate(self.termly, utc(2026, 10, 26))

        renewed = self.activate(self.termly, utc(2027, 2, 10))

        self.assertNotEqual(renewed.pk, first.pk)
        first.refresh_from_db()
        self.assertEqual(first.status, TenantSubscription.Status.EXPIRED)
        self.assertEqual(renewed.current_period_start, utc(2027, 2, 10))
        self.assertEqual(renewed.current_period_end, utc(2027, 5, 10))

    def test_ended_trial_is_expired_not_cancelled_when_paying(self):
        TenantSubscription.objects.filter(tenant=self.tenant).update(
            trial_ends_at=utc(2026, 11, 1)
        )
        self.activate(self.termly, utc(2026, 11, 5))
        trial = TenantSubscription.objects.get(tenant=self.tenant, plan__code="trial")
        self.assertEqual(trial.status, TenantSubscription.Status.EXPIRED)

    def test_changing_plan_mid_period_is_refused_and_keeps_paid_time(self):
        first = self.activate(self.termly, utc(2026, 10, 26))
        with self.assertRaises(SubscriptionError):
            self.activate(self.yearly, utc(2026, 11, 1))
        first.refresh_from_db()
        self.assertEqual(first.status, TenantSubscription.Status.ACTIVE)
        self.assertEqual(self.live().get(), first)

    def test_suspended_subscription_is_not_silently_lifted(self):
        TenantSubscription.objects.filter(tenant=self.tenant).update(
            status=TenantSubscription.Status.SUSPENDED
        )
        with self.assertRaises(SubscriptionError):
            activate_subscription(self.tenant, self.termly)
        self.assertEqual(
            TenantSubscription.objects.get(tenant=self.tenant).status,
            TenantSubscription.Status.SUSPENDED,
        )

    def test_failed_activation_rolls_back(self):
        with patch("core.subscriptions.AuditEvent.objects.create", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.activate(self.termly, utc(2026, 10, 26))
        self.assertEqual(self.live().get().plan.code, "trial")
        self.assertFalse(TenantSubscription.objects.filter(plan=self.termly).exists())

    def test_changes_are_audited(self):
        owner = User.objects.create_user(
            email="admin@example.com", password="Taller-River-804", full_name="Admin"
        )
        self.activate(self.termly, utc(2026, 10, 26), actor=owner)
        self.activate(self.termly, utc(2026, 11, 1), actor=owner)

        events = AuditEvent.objects.filter(
            tenant=self.tenant, action__startswith="subscription."
        ).order_by("timestamp")
        self.assertEqual(
            [event.action for event in events],
            ["subscription.activated", "subscription.renewed"],
        )
        self.assertEqual(events[0].actor, owner)
        self.assertEqual(events[1].metadata["plan"], "termly")
        self.assertEqual(
            events[1].metadata["previous_period_end"], utc(2027, 1, 26).isoformat()
        )
        self.assertFalse(
            AuditEvent.objects.filter(
                tenant=self.other, action__startswith="subscription."
            ).exists()
        )

    def test_activate_command(self):
        out = StringIO()
        call_command("activate_subscription", self.tenant.slug, "termly", stdout=out)
        self.assertIn("until", out.getvalue())
        for args in (
            (self.tenant.slug, "trial"),
            (self.tenant.slug, "missing-plan"),
            ("missing-school", "termly"),
        ):
            with self.assertRaises(CommandError):
                call_command("activate_subscription", *args)
        self.termly.is_active = False
        self.termly.save()
        with self.assertRaises(CommandError):
            call_command("activate_subscription", self.other.slug, "termly")


    def test_failed_renewal_rolls_back_and_leaves_no_audit_event(self):
        paid = self.activate(self.termly, utc(2026, 10, 26))
        events = AuditEvent.objects.filter(action__startswith="subscription.").count()

        with patch("core.subscriptions.AuditEvent.objects.create", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.activate(self.termly, utc(2026, 11, 1))

        paid.refresh_from_db()
        self.assertEqual(paid.current_period_end, utc(2027, 1, 26))
        self.assertEqual(
            AuditEvent.objects.filter(action__startswith="subscription.").count(), events
        )


@override_settings(
    DEBUG=True,
    NLSCHOOL_SETUP_TOKEN="",
    NLSCHOOL_BILLING_INSTRUCTIONS="Pay by mobile money and send the receipt.",
    NLSCHOOL_SUPPORT_EMAIL="billing@nlschool.example",
)
class SubscriptionStatusViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = create_tenant(
            name="North Star Academy",
            slug="north-star-status",
            email="office@northstar.example",
            phone="+233201234567",
        )
        self.other = create_tenant(
            name="Other School", slug="other-status", email="o@o.example", phone="9"
        )
        self.owner = self.member("owner@northstar.example", Membership.Role.OWNER)
        self.url = f"/api/tenants/{self.tenant.pk}/subscription/"
        self.client.force_authenticate(user=self.owner)

    def member(self, email, role, tenant=None):
        user = User.objects.create_user(
            email=email, password="Str0ng-test-pass!", full_name="Member"
        )
        Membership.objects.create(tenant=tenant or self.tenant, user=user, role=role)
        return user

    def plan(self, code, interval, price):
        return SubscriptionPlan.objects.create(
            name=code.title(), code=code, price=price, billing_interval=interval
        )

    def test_trial_is_reported_without_a_billing_interval(self):
        data = self.client.get(self.url).json()

        self.assertEqual(data["status"], "trialing")
        self.assertTrue(data["has_access"])
        self.assertTrue(data["plan"]["is_trial"])
        self.assertIsNone(data["plan"]["billing_interval"])
        self.assertEqual(data["plan"]["price"], "0.00")
        self.assertIsNotNone(data["expires_at"])
        self.assertEqual(
            data["renewal"],
            {
                "instructions": "Pay by mobile money and send the receipt.",
                "support_email": "billing@nlschool.example",
            },
        )

    def test_each_billing_interval_is_reported(self):
        for interval, code in (
            (SubscriptionPlan.Interval.MONTHLY, "monthly"),
            (SubscriptionPlan.Interval.TERMLY, "termly"),
            (SubscriptionPlan.Interval.YEARLY, "yearly"),
        ):
            plan = self.plan(code, interval, "250.50")
            activate_subscription(self.tenant, plan)
            data = self.client.get(self.url).json()
            self.assertEqual(data["status"], "active")
            self.assertEqual(data["plan"]["billing_interval"], code)
            self.assertEqual(data["plan"]["price"], "250.50")
            self.assertEqual(data["plan"]["currency"], "GHS")
            self.assertIsNotNone(data["started_at"])
            subscription = TenantSubscription.objects.get(
                tenant=self.tenant, status=TenantSubscription.Status.ACTIVE
            )
            self.assertEqual(
                data["expires_at"][:16],
                subscription.current_period_end.isoformat().replace("+00:00", "Z")[:16],
            )
            TenantSubscription.objects.filter(pk=subscription.pk).update(
                status=TenantSubscription.Status.CANCELLED
            )

    def test_expired_school_can_still_see_its_subscription_but_not_school_data(self):
        TenantSubscription.objects.filter(tenant=self.tenant).update(
            trial_ends_at=timezone.now() - timedelta(days=1)
        )

        data = self.client.get(self.url).json()
        self.assertEqual(data["status"], "expired")
        self.assertFalse(data["has_access"])
        self.assertEqual(
            self.client.get(f"/api/tenants/{self.tenant.pk}/students/").status_code, 402
        )

    def test_only_school_administrators_can_view_it(self):
        admin = self.member("admin@northstar.example", Membership.Role.SCHOOL_ADMIN)
        self.client.force_authenticate(user=admin)
        self.assertEqual(self.client.get(self.url).status_code, 200)

        for role in (
            Membership.Role.ACCOUNTANT,
            Membership.Role.TEACHER,
            Membership.Role.PARENT,
            Membership.Role.STUDENT,
        ):
            user = self.member(f"{role}@northstar.example", role)
            self.client.force_authenticate(user=user)
            self.assertEqual(self.client.get(self.url).status_code, 404, role)

        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_other_schools_cannot_see_it_and_get_only_their_own(self):
        rival = self.member("rival@other.example", Membership.Role.OWNER, self.other)
        self.client.force_authenticate(user=rival)
        self.assertEqual(self.client.get(self.url).status_code, 404)

        plan = self.plan("yearly", SubscriptionPlan.Interval.YEARLY, "900")
        activate_subscription(self.tenant, plan)
        own = self.client.get(f"/api/tenants/{self.other.pk}/subscription/").json()
        self.assertTrue(own["plan"]["is_trial"])

    def test_inactive_member_or_suspended_user_is_refused(self):
        Membership.objects.filter(user=self.owner).update(
            status=Membership.Status.SUSPENDED
        )
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_response_exposes_no_internal_fields(self):
        data = self.client.get(self.url).json()
        self.assertEqual(
            set(data),
            {
                "status",
                "has_access",
                "plan",
                "started_at",
                "current_period_start",
                "expires_at",
                "renewal",
            },
        )
        self.assertEqual(
            set(data["plan"]),
            {"code", "name", "is_trial", "billing_interval", "price", "currency"},
        )

    def test_school_without_any_subscription_reports_none(self):
        TenantSubscription.objects.filter(tenant=self.tenant).delete()
        data = self.client.get(self.url).json()
        self.assertEqual(data["status"], "none")
        self.assertFalse(data["has_access"])
        self.assertIsNone(data["plan"])

    def test_suspended_subscription_is_reported_as_such(self):
        TenantSubscription.objects.filter(tenant=self.tenant).update(
            status=TenantSubscription.Status.SUSPENDED
        )
        data = self.client.get(self.url).json()
        self.assertEqual(data["status"], "suspended")
        self.assertFalse(data["has_access"])


@skipUnless(connection.vendor == "postgresql", "Row-locking tests need PostgreSQL.")
class SubscriptionConcurrencyTests(TransactionTestCase):
    """Uses real, separate database connections (one per thread)."""

    def setUp(self):
        self.tenant = create_tenant(
            name="North Star Academy",
            slug="north-star-race",
            email="office@northstar.example",
            phone="+233201234567",
        )
        self.plan = SubscriptionPlan.objects.create(
            name="Termly",
            code="termly",
            price=300,
            billing_interval=SubscriptionPlan.Interval.TERMLY,
        )

    def in_thread(self, func, errors):
        def runner():
            try:
                func()
            except BaseException as error:
                errors.append(error)
            finally:
                connections.close_all()

        thread = Thread(target=runner)
        thread.start()
        return thread

    def race(self, workers):
        barrier, errors = Barrier(workers), []

        def attempt():
            barrier.wait(timeout=10)
            activate_subscription(self.tenant, self.plan)

        threads = [self.in_thread(attempt, errors) for _ in range(workers)]
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(errors, [])
        return workers

    def live(self):
        return TenantSubscription.objects.filter(
            tenant=self.tenant,
            status__in=(
                TenantSubscription.Status.TRIALING,
                TenantSubscription.Status.ACTIVE,
                TenantSubscription.Status.SUSPENDED,
            ),
        )

    def assert_serialised_periods(self, periods):
        active = self.live().get()
        self.assertEqual(active.status, TenantSubscription.Status.ACTIVE)
        expected = active.current_period_start
        for _ in range(periods):
            expected = add_months(expected, 3, anchor_day=active.current_period_start.day)
        # A lost update would leave fewer periods than activations.
        self.assertEqual(active.current_period_end, expected)
        actions = list(
            AuditEvent.objects.filter(
                tenant=self.tenant, action__startswith="subscription."
            )
            .order_by("timestamp")
            .values_list("action", flat=True)
        )
        self.assertEqual(
            sorted(actions),
            sorted(["subscription.activated"] + ["subscription.renewed"] * (periods - 1)),
        )

    def test_lock_runs_inside_a_transaction_before_subscriptions_are_read(self):
        with self.assertRaises(TransactionManagementError):
            Tenant.objects.select_for_update().get(pk=self.tenant.pk)

        with CaptureQueriesContext(connection) as queries:
            activate_subscription(self.tenant, self.plan)

        statements = [query["sql"] for query in queries.captured_queries]
        lock = next(
            index
            for index, sql in enumerate(statements)
            if "FOR UPDATE" in sql and 'FROM "core_tenant"' in sql
        )
        first_subscription_query = next(
            index
            for index, sql in enumerate(statements)
            if '"core_tenantsubscription"' in sql
        )
        self.assertLess(lock, first_subscription_query)

    def test_lock_is_held_until_the_whole_activation_commits(self):
        inside, release, errors = Event(), Event(), []
        real_create = AuditEvent.objects.create

        def paused_create(**kwargs):
            inside.set()
            if not release.wait(timeout=20):
                raise RuntimeError("never released")
            return real_create(**kwargs)

        with patch("core.subscriptions.AuditEvent.objects.create", side_effect=paused_create):
            worker = self.in_thread(
                lambda: activate_subscription(self.tenant, self.plan), errors
            )
            try:
                self.assertTrue(inside.wait(timeout=20))
                with self.assertRaises(DatabaseError), transaction.atomic():
                    Tenant.objects.select_for_update(nowait=True).get(pk=self.tenant.pk)
                # Uncommitted work is invisible to other connections.
                self.assertFalse(
                    TenantSubscription.objects.filter(
                        tenant=self.tenant, status=TenantSubscription.Status.ACTIVE
                    ).exists()
                )
            finally:
                release.set()
                worker.join(timeout=30)
        self.assertEqual(errors, [])

        with transaction.atomic():
            Tenant.objects.select_for_update(nowait=True).get(pk=self.tenant.pk)
        self.assertEqual(self.live().get().status, TenantSubscription.Status.ACTIVE)

    def test_concurrent_first_activations_from_a_trial_create_one_live_subscription(self):
        self.assert_serialised_periods(self.race(6))

    def test_concurrent_activations_with_no_live_subscription_create_one(self):
        TenantSubscription.objects.filter(tenant=self.tenant).delete()
        self.assertFalse(self.live().exists())
        self.assert_serialised_periods(self.race(6))

    def test_concurrent_renewals_do_not_overwrite_each_others_periods(self):
        activate_subscription(self.tenant, self.plan)
        barrier, errors = Barrier(4), []

        def renew():
            barrier.wait(timeout=10)
            activate_subscription(self.tenant, self.plan)

        for thread in [self.in_thread(renew, errors) for _ in range(4)]:
            thread.join(timeout=60)
        self.assertEqual(errors, [])
        self.assert_serialised_periods(5)

    def test_expiry_check_cannot_expire_a_period_that_is_being_renewed(self):
        TenantSubscription.objects.filter(tenant=self.tenant).delete()
        now = timezone.now()
        paid = TenantSubscription.objects.create(
            tenant=self.tenant,
            plan=self.plan,
            status=TenantSubscription.Status.ACTIVE,
            started_at=now,
            current_period_start=now,
            current_period_end=now + timedelta(seconds=2),
        )
        inside, release, errors, result = Event(), Event(), [], []
        real_create = AuditEvent.objects.create

        def paused_create(**kwargs):
            inside.set()
            if not release.wait(timeout=20):
                raise RuntimeError("never released")
            return real_create(**kwargs)

        with patch("core.subscriptions.AuditEvent.objects.create", side_effect=paused_create):
            renewal = self.in_thread(
                lambda: activate_subscription(self.tenant, self.plan), errors
            )
            check = None
            try:
                self.assertTrue(inside.wait(timeout=20))
                # The old period has now ended, but its renewal is not committed yet.
                time.sleep(max(0, (paid.current_period_end - timezone.now()).total_seconds()) + 0.3)
                check = self.in_thread(
                    lambda: result.append(has_active_subscription(self.tenant.pk)), errors
                )
                time.sleep(1)
                # The expiry check waits for the renewal's row lock instead of racing it.
                self.assertTrue(check.is_alive())
            finally:
                release.set()
                renewal.join(timeout=30)
                if check is not None:
                    check.join(timeout=30)
        self.assertEqual(errors, [])
        self.assertEqual(result, [True])
        paid.refresh_from_db()
        self.assertEqual(paid.status, TenantSubscription.Status.ACTIVE)
        self.assertGreater(paid.current_period_end, timezone.now() + timedelta(days=80))
        self.assertEqual(
            TenantSubscription.objects.filter(tenant=self.tenant).count(), 1
        )

    def test_failed_activation_commits_nothing(self):
        with patch("core.subscriptions.AuditEvent.objects.create", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                activate_subscription(self.tenant, self.plan)

        trial = self.live().get()
        self.assertEqual(trial.status, TenantSubscription.Status.TRIALING)
        self.assertIsNone(trial.cancelled_at)
        self.assertFalse(TenantSubscription.objects.filter(plan=self.plan).exists())
        self.assertFalse(
            AuditEvent.objects.filter(action__startswith="subscription.").exists()
        )


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class SubscriptionAdminActionTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            email="root@example.com", password="Taller-River-804", full_name="Root"
        )
        self.client.force_login(self.admin_user)
        self.termly = SubscriptionPlan.objects.create(
            name="Termly",
            code="termly",
            price=300,
            billing_interval=SubscriptionPlan.Interval.TERMLY,
        )
        self.paid = create_tenant(
            name="Paid School", slug="paid", email="p@p.example", phone="1"
        )
        self.trial = create_tenant(
            name="Trial School", slug="trial-school", email="t@t.example", phone="2"
        )
        self.untouched = create_tenant(
            name="Untouched School", slug="untouched", email="u@u.example", phone="3"
        )
        activate_subscription(self.paid, self.termly)

    def run_action(self, *subscriptions):
        return self.client.post(
            "/admin/core/tenantsubscription/",
            {
                "action": "renew_for_one_period",
                "_selected_action": [str(item.pk) for item in subscriptions],
            },
            follow=True,
        )

    def messages(self, response):
        return [str(message) for message in response.context["messages"]]

    def test_renews_paid_school_and_skips_trial_school(self):
        paid = TenantSubscription.objects.get(tenant=self.paid, plan=self.termly)
        trial = TenantSubscription.objects.get(tenant=self.trial)
        untouched = TenantSubscription.objects.get(tenant=self.untouched)
        old_end, trial_state = paid.current_period_end, (trial.status, trial.trial_ends_at)

        response = self.run_action(paid, trial)

        paid.refresh_from_db()
        trial.refresh_from_db()
        self.assertEqual(
            paid.current_period_end,
            add_months(old_end, 3, anchor_day=paid.current_period_start.day),
        )
        self.assertEqual((trial.status, trial.trial_ends_at), trial_state)
        self.assertFalse(
            TenantSubscription.objects.filter(
                tenant=self.trial, plan=self.termly
            ).exists()
        )
        self.assertEqual(
            TenantSubscription.objects.get(tenant=self.untouched).status,
            untouched.status,
        )
        text = " ".join(self.messages(response))
        self.assertIn("Renewed 1 school(s).", text)
        self.assertIn("Trial School: skipped", text)
        event = AuditEvent.objects.get(action="subscription.renewed")
        self.assertEqual(event.actor, self.admin_user)

    def test_selecting_the_same_school_twice_renews_it_once(self):
        paid = TenantSubscription.objects.get(tenant=self.paid, plan=self.termly)
        old_end = paid.current_period_end
        old = TenantSubscription.objects.create(
            tenant=self.paid,
            plan=self.termly,
            status=TenantSubscription.Status.EXPIRED,
            current_period_end=utc(2020, 1, 1),
        )

        self.run_action(old, paid)

        paid.refresh_from_db()
        self.assertEqual(
            paid.current_period_end,
            add_months(old_end, 3, anchor_day=paid.current_period_start.day),
        )
        self.assertEqual(AuditEvent.objects.filter(action="subscription.renewed").count(), 1)

    def test_cancelled_subscriptions_are_not_renewed(self):
        cancelled = TenantSubscription.objects.create(
            tenant=self.untouched,
            plan=self.termly,
            status=TenantSubscription.Status.CANCELLED,
        )
        response = self.run_action(cancelled)
        self.assertIn("skipped", " ".join(self.messages(response)))
        self.assertEqual(
            TenantSubscription.objects.filter(
                tenant=self.untouched, status=TenantSubscription.Status.ACTIVE
            ).count(),
            0,
        )

    def test_errors_are_reported_per_school(self):
        paid = TenantSubscription.objects.get(tenant=self.paid, plan=self.termly)
        self.termly.is_active = False
        self.termly.save()
        response = self.run_action(paid)
        self.assertIn("Paid School: The plan 'Termly' is not active.", self.messages(response))

    def test_unexpected_error_is_reported_for_that_school_only(self):
        second = create_tenant(
            name="Second Paid", slug="second-paid", email="s@s.example", phone="4"
        )
        activate_subscription(second, self.termly)
        first_sub = TenantSubscription.objects.get(tenant=self.paid, plan=self.termly)
        second_sub = TenantSubscription.objects.get(tenant=second, plan=self.termly)
        first_end, second_end = first_sub.current_period_end, second_sub.current_period_end
        real_create = AuditEvent.objects.create

        def flaky(**kwargs):
            if kwargs["tenant"].pk == self.paid.pk:
                raise RuntimeError("secret internal detail")
            return real_create(**kwargs)

        with patch("core.subscriptions.AuditEvent.objects.create", side_effect=flaky):
            response = self.run_action(first_sub, second_sub)

        self.assertEqual(response.status_code, 200)
        first_sub.refresh_from_db()
        second_sub.refresh_from_db()
        self.assertEqual(first_sub.current_period_end, first_end)
        self.assertGreater(second_sub.current_period_end, second_end)
        messages = " | ".join(self.messages(response))
        self.assertIn("Paid School: renewal failed unexpectedly and nothing was changed", messages)
        self.assertIn("Second Paid: renewed until", messages)
        self.assertIn("Renewed 1 school(s).", messages)
        self.assertNotIn("secret internal detail", messages)
        events = AuditEvent.objects.filter(action="subscription.renewed")
        self.assertEqual([event.tenant_id for event in events], [second.pk])

    def test_audit_failure_leaves_no_partial_change_or_audit_event(self):
        paid = TenantSubscription.objects.get(tenant=self.paid, plan=self.termly)
        old_end = paid.current_period_end
        events = AuditEvent.objects.filter(action__startswith="subscription.").count()

        with patch("core.subscriptions.AuditEvent.objects.create", side_effect=RuntimeError):
            response = self.run_action(paid)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Renewed", " ".join(self.messages(response)))
        paid.refresh_from_db()
        self.assertEqual(paid.current_period_end, old_end)
        self.assertEqual(
            AuditEvent.objects.filter(action__startswith="subscription.").count(), events
        )

    def test_admin_renewal_is_attributed_to_the_signed_in_user(self):
        paid = TenantSubscription.objects.get(tenant=self.paid, plan=self.termly)
        self.run_action(paid)

        event = AuditEvent.objects.filter(action="subscription.renewed").get()
        self.assertEqual(event.actor, self.admin_user)
        self.assertEqual(event.tenant, self.paid)
        self.assertEqual(event.metadata["source"], "django_admin")
        self.assertEqual(event.metadata["actor_email"], "root@example.com")
        self.assertEqual(event.metadata["plan"], "termly")

    def test_command_renewal_has_a_system_actor(self):
        call_command(
            "activate_subscription", self.untouched.slug, "termly", stdout=StringIO()
        )
        event = AuditEvent.objects.filter(
            tenant=self.untouched, action="subscription.activated"
        ).get()
        self.assertIsNone(event.actor)
        self.assertEqual(event.metadata["source"], "management_command")
        self.assertTrue(event.metadata["operator"])

    def test_staff_without_change_permission_cannot_run_it(self):
        viewer = User.objects.create_user(
            email="viewer@example.com",
            password="Taller-River-804",
            full_name="Viewer",
            is_staff=True,
        )
        viewer.user_permissions.add(
            Permission.objects.get(codename="view_tenantsubscription")
        )
        self.client.force_login(viewer)
        paid = TenantSubscription.objects.get(tenant=self.paid, plan=self.termly)
        old_end = paid.current_period_end

        self.run_action(paid)

        paid.refresh_from_db()
        self.assertEqual(paid.current_period_end, old_end)
