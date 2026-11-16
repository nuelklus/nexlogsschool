from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from queue import Queue
from time import monotonic, sleep
from unittest import SkipTest
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.test import Client, TransactionTestCase, override_settings

from academics.tests import AcademicFixture
from core.models import AdmissionSequence, AuditEvent, SubscriptionPlan, Tenant, TenantSubscription


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class WriterBypassProtectionTests(AcademicFixture, TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if (
            settings.TEST_RUNNER != "academics.fixture_test_settings.FixtureTestRunner"
            or connection.vendor != "postgresql"
            or not connection.settings_dict["NAME"].startswith("test_nlschool_fixture_")
        ):
            raise SkipTest("Requires the disposable PostgreSQL fixture runner.")
        super().setUpClass()

    def blank_student(self, tenant=None):
        return self.student.__class__.objects.create(
            tenant=tenant or self.tenant, admission_number="", first_name="Blank", last_name="Candidate",
        )

    @staticmethod
    def run_audit_command(slug, pids=None):
        close_old_connections()
        try:
            if pids is not None:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put(cursor.fetchone()[0])
            output = StringIO()
            call_command("audit_admission_numbers", slug, "--assign-blank", stdout=output)
            return {"output": output.getvalue(), "sqlstate": None}
        except DatabaseError as exc:
            return {"output": "", "sqlstate": getattr(exc.__cause__, "sqlstate", None)}
        finally:
            connections.close_all()

    def wait_for_blocking(self, pid, fragment):
        deadline = monotonic() + 8
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT cardinality(pg_blocking_pids(pid)), query "
                    "FROM pg_stat_activity WHERE pid = %s",
                    [pid],
                )
                row = cursor.fetchone()
            if row and row[0] and fragment in row[1]:
                return
            sleep(0.05)
        self.fail(f"Command did not wait on the expected lock: {fragment}")

    def test_command_waits_for_tenant_lock_and_rechecks_blank_candidate(self):
        candidate = self.blank_student()
        pids = Queue()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Tenant.objects.select_for_update().get(pk=self.tenant.pk)
                pending = pool.submit(self.run_audit_command, self.tenant.slug, pids)
                pid = pids.get(timeout=5)
                self.wait_for_blocking(pid, "core_tenant")
                candidate.admission_number = "MANUAL-2026-01"
                candidate.save(update_fields=("admission_number",))
            result = pending.result(timeout=15)
        self.assertIsNone(result["sqlstate"])
        candidate.refresh_from_db()
        self.assertEqual(candidate.admission_number, "MANUAL-2026-01")
        self.assertFalse(AdmissionSequence.objects.filter(tenant=self.tenant).exists())
        self.assertFalse(AuditEvent.objects.filter(
            action="student.admission_number_backfilled", entity_id=str(candidate.pk),
        ).exists())

    def test_command_rollback_restores_student_sequence_and_audit(self):
        candidate = self.blank_student()
        with patch(
            "core.management.commands.audit_admission_numbers.AuditEvent.objects.create",
            side_effect=RuntimeError("test rollback"),
        ):
            with self.assertRaisesRegex(RuntimeError, "test rollback"):
                call_command("audit_admission_numbers", self.tenant.slug, "--assign-blank", stdout=StringIO())
        candidate.refresh_from_db()
        self.assertEqual(candidate.admission_number, "")
        self.assertFalse(AdmissionSequence.objects.filter(tenant=self.tenant).exists())
        self.assertFalse(AuditEvent.objects.filter(
            action="student.admission_number_backfilled", entity_id=str(candidate.pk),
        ).exists())

    def test_command_does_not_wait_on_another_tenants_row(self):
        candidate = self.blank_student(self.other_tenant)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Tenant.objects.select_for_update().get(pk=self.tenant.pk)
                result = pool.submit(self.run_audit_command, self.other_tenant.slug).result(timeout=8)
                self.assertIsNone(result["sqlstate"])
        candidate.refresh_from_db()
        self.assertNotEqual(candidate.admission_number, "")
        self.assertTrue(AuditEvent.objects.filter(
            action="student.admission_number_backfilled", entity_id=str(candidate.pk),
        ).exists())

    def admin_client(self):
        user_model = get_user_model()
        admin = user_model.objects.create_superuser(
            email="platform-admin@example.com", password="Disposable-Admin-Password-1",
            full_name="Platform Admin",
        )
        client = Client()
        client.force_login(admin)
        return client

    def test_subscription_plan_code_is_readonly_in_actual_admin_form(self):
        plan = SubscriptionPlan.objects.get(code="trial")
        client = self.admin_client()
        path = f"/admin/core/subscriptionplan/{plan.pk}/change/"
        page = client.get(path)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "trial")
        response = client.post(path, {
            "name": "Trial", "code": "renamed-trial", "description": "", "price": "0.00",
            "currency": "GHS", "billing_interval": "monthly", "trial_days": "30",
            "entitlements": "{}", "_save": "Save",
        })
        self.assertEqual(response.status_code, 302)
        plan.refresh_from_db()
        self.assertEqual(plan.code, "trial")
        delete_response = client.post(f"/admin/core/subscriptionplan/{plan.pk}/delete/", {"post": "yes"})
        self.assertEqual(delete_response.status_code, 403)

    def test_subscription_admin_cannot_bypass_service_with_direct_row_edit(self):
        subscription = TenantSubscription.objects.filter(tenant=self.tenant).first()
        self.assertIsNotNone(subscription)
        subscription.status = TenantSubscription.Status.ACTIVE
        subscription.save(update_fields=("status",))
        client = self.admin_client()
        self.assertEqual(client.get("/admin/core/tenantsubscription/add/").status_code, 403)
        path = f"/admin/core/tenantsubscription/{subscription.pk}/change/"
        response = client.post(path, {"status": TenantSubscription.Status.CANCELLED, "_save": "Save"})
        self.assertEqual(response.status_code, 403)
        delete_response = client.post(f"/admin/core/tenantsubscription/{subscription.pk}/delete/", {"post": "yes"})
        self.assertEqual(delete_response.status_code, 403)
        subscription.refresh_from_db()
        self.assertEqual(subscription.status, TenantSubscription.Status.ACTIVE)
