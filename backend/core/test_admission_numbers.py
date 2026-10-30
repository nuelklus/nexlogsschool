from datetime import date
from io import StringIO
from threading import Barrier, Thread
from unittest import skipUnless
from unittest.mock import patch

from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from .admission_numbers import next_admission_number, validate_config
from .models import (
    AcademicYear,
    AdmissionSequence,
    AdmissionSettings,
    AuditEvent,
    Membership,
    Student,
    Tenant,
    User,
)
from .tests import create_tenant


def make_user(tenant, email, role):
    user = User.objects.create_user(email=email, password="Quiet-Lagoon-731", full_name=email.split("@")[0])
    user.email_verified = True
    user.save(update_fields=("email_verified",))
    Membership.objects.create(tenant=tenant, user=user, role=role)
    return user


class AdmissionNumberTestBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = create_tenant(
            name="Accra Heights School", slug="accra-heights", email="a@a.example", phone="1"
        )
        self.owner = make_user(self.tenant, "owner@a.example", Membership.Role.OWNER)
        self.admin = make_user(self.tenant, "admin@a.example", Membership.Role.SCHOOL_ADMIN)
        self.client.force_authenticate(user=self.owner)
        self.base = f"/api/tenants/{self.tenant.pk}"

    def configure(self, **values):
        defaults = {"prefix": "ASHS"}
        defaults.update(values)
        AdmissionSettings.objects.update_or_create(tenant=self.tenant, defaults=defaults)

    def admit(self, first="Esi", last="Boateng", client=None, **extra):
        return (client or self.client).post(
            f"{self.base}/students/", {"first_name": first, "last_name": last, **extra}, format="json"
        )


class GenerationTests(AdmissionNumberTestBase):
    def test_new_student_gets_a_formatted_sequential_number(self):
        self.configure(prefix="ASHS")
        numbers = [self.admit(first=f"S{i}", enrolled_on="2026-09-01").data["admission_number"] for i in range(3)]
        self.assertEqual(numbers, ["ASHS-2026-0001", "ASHS-2026-0002", "ASHS-2026-0003"])
        self.assertEqual(Student.objects.get(admission_number="ASHS-2026-0002").tenant, self.tenant)

    def test_default_prefix_comes_from_the_school_name_and_is_not_hardcoded(self):
        response = self.admit(enrolled_on="2026-09-01")
        self.assertEqual(response.data["admission_number"], "AHS-2026-0001")
        other = create_tenant(name="Kumasi Prep", slug="kumasi-prep", email="k@k.example", phone="2")
        client = APIClient()
        client.force_authenticate(user=make_user(other, "o@k.example", Membership.Role.OWNER))
        theirs = client.post(
            f"/api/tenants/{other.pk}/students/",
            {"first_name": "A", "last_name": "B", "enrolled_on": "2026-09-01"},
            format="json",
        )
        self.assertEqual(theirs.data["admission_number"], "KP-2026-0001")

    def test_custom_format_and_padding(self):
        self.configure(prefix="NLS", number_format="{prefix}/{seq}/{year}", padding=6)
        number = self.admit(enrolled_on="2027-01-05").data["admission_number"]
        self.assertEqual(number, "NLS/000001/2027")

    def test_annual_sequence_restarts_each_year(self):
        self.configure(reset_policy="annual")
        self.assertEqual(self.admit(enrolled_on="2026-09-01").data["admission_number"], "ASHS-2026-0001")
        self.assertEqual(self.admit(enrolled_on="2026-09-02").data["admission_number"], "ASHS-2026-0002")
        self.assertEqual(self.admit(enrolled_on="2027-01-10").data["admission_number"], "ASHS-2027-0001")

    def test_continuous_sequence_does_not_restart(self):
        self.configure(reset_policy="continuous")
        self.assertEqual(self.admit(enrolled_on="2026-09-01").data["admission_number"], "ASHS-2026-0001")
        self.assertEqual(self.admit(enrolled_on="2027-01-10").data["admission_number"], "ASHS-2027-0002")

    def test_academic_year_policy_uses_the_current_academic_year(self):
        self.configure(year_policy="academic_year")
        AcademicYear.objects.create(
            tenant=self.tenant, name="2026/27", start_date=date(2026, 9, 1),
            end_date=date(2027, 7, 31), is_current=True,
        )
        self.assertEqual(self.admit(enrolled_on="2027-02-01").data["admission_number"], "ASHS-2026-0001")

    def test_academic_year_policy_falls_back_to_calendar_year(self):
        self.configure(year_policy="academic_year")
        self.assertEqual(self.admit(enrolled_on="2027-02-01").data["admission_number"], "ASHS-2027-0001")

    def test_other_tenants_academic_year_is_not_used(self):
        self.configure(year_policy="academic_year")
        other = create_tenant(name="Other", slug="other-ay", email="o@o.example", phone="3")
        AcademicYear.objects.create(
            tenant=other, name="2030/31", start_date=date(2030, 9, 1), end_date=date(2031, 7, 1), is_current=True
        )
        self.assertEqual(self.admit(enrolled_on="2027-02-01").data["admission_number"], "ASHS-2027-0001")

    def test_generation_skips_numbers_already_taken_by_hand(self):
        Student.objects.create(tenant=self.tenant, admission_number="ASHS-2026-0001", first_name="Old", last_name="One")
        self.configure()
        self.assertEqual(self.admit(enrolled_on="2026-09-01").data["admission_number"], "ASHS-2026-0002")

    def test_new_sequence_continues_after_existing_matching_numbers(self):
        for n in (1, 7):
            Student.objects.create(
                tenant=self.tenant, admission_number=f"ASHS-2026-{n:04d}", first_name="Old", last_name=str(n)
            )
        self.configure()
        self.assertEqual(self.admit(enrolled_on="2026-09-01").data["admission_number"], "ASHS-2026-0008")

    def test_response_list_and_detail_all_return_the_number(self):
        created = self.admit(enrolled_on="2026-09-01").data
        listed = self.client.get(f"{self.base}/students/").data[0]
        detail = self.client.get(f"{self.base}/students/{created['id']}/").data
        self.assertEqual(created["admission_number"], listed["admission_number"])
        self.assertEqual(created["admission_number"], detail["admission_number"])

    def test_failed_admission_rolls_the_sequence_back(self):
        self.admit(enrolled_on="2026-09-01")
        with patch("core.enrollment._audit", side_effect=DatabaseError("boom")):
            with self.assertRaises(DatabaseError):
                self.admit(first="Fails", enrolled_on="2026-09-01")
        self.assertFalse(Student.objects.filter(first_name="Fails").exists())
        self.assertEqual(AdmissionSequence.objects.get(tenant=self.tenant).last_number, 1)
        self.assertEqual(self.admit(first="Next", enrolled_on="2026-09-01").data["admission_number"], "AHS-2026-0002")

    def test_generator_refuses_to_run_outside_a_transaction(self):
        with patch("django.db.transaction.get_connection") as fake:
            fake.return_value.in_atomic_block = False
            with self.assertRaises(RuntimeError):
                next_admission_number(self.tenant.pk, date(2026, 1, 1))


class UniquenessAndTenantTests(AdmissionNumberTestBase):
    def test_database_enforces_uniqueness_within_a_tenant(self):
        Student.objects.create(tenant=self.tenant, admission_number="X-1", first_name="A", last_name="B")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Student.objects.create(tenant=self.tenant, admission_number="X-1", first_name="C", last_name="D")

    def test_same_number_is_allowed_in_different_tenants(self):
        other = create_tenant(name="Other", slug="other-t", email="o@o.example", phone="3")
        Student.objects.create(tenant=self.tenant, admission_number="X-1", first_name="A", last_name="B")
        Student.objects.create(tenant=other, admission_number="X-1", first_name="C", last_name="D")
        self.assertEqual(Student.objects.filter(admission_number="X-1").count(), 2)

    def test_two_tenants_with_the_same_prefix_each_start_at_one(self):
        other = create_tenant(name="Other", slug="other-p", email="o@o.example", phone="3")
        AdmissionSettings.objects.create(tenant=other, prefix="ASHS")
        self.configure()
        mine = self.admit(enrolled_on="2026-09-01").data["admission_number"]
        client = APIClient()
        client.force_authenticate(user=make_user(other, "o@o.example", Membership.Role.OWNER))
        theirs = client.post(
            f"/api/tenants/{other.pk}/students/", {"first_name": "A", "last_name": "B", "enrolled_on": "2026-09-01"},
            format="json",
        ).data["admission_number"]
        self.assertEqual(mine, theirs)
        self.assertEqual(Student.objects.filter(admission_number=mine).count(), 2)

    def test_outsider_cannot_admit_or_read_another_tenants_students(self):
        other = create_tenant(name="Other", slug="other-o", email="o@o.example", phone="3")
        outsider = APIClient()
        outsider.force_authenticate(user=make_user(other, "o@o.example", Membership.Role.OWNER))
        created = self.admit().data
        self.assertEqual(self.admit(client=outsider).status_code, 404)
        self.assertEqual(outsider.get(f"{self.base}/students/").status_code, 404)
        self.assertEqual(outsider.get(f"{self.base}/students/{created['id']}/").status_code, 404)
        self.assertEqual(outsider.get(f"{self.base}/admission-settings/").status_code, 404)
        self.assertEqual(outsider.patch(f"{self.base}/admission-settings/", {"prefix": "HACK"}, format="json").status_code, 404)
        self.assertEqual(Student.objects.filter(tenant=self.tenant).count(), 1)
        self.assertFalse(AdmissionSettings.objects.filter(tenant=other).exists())

    def test_unauthenticated_requests_are_rejected(self):
        response = APIClient().post(f"{self.base}/students/", {"first_name": "A", "last_name": "B"}, format="json")
        self.assertEqual(response.status_code, 401)


class PermissionAndOverrideTests(AdmissionNumberTestBase):
    def roles(self):
        return {
            role: self.client_for(role)
            for role in ("accountant", "teacher", "parent", "student")
        }

    def client_for(self, role):
        client = APIClient()
        client.force_authenticate(user=make_user(self.tenant, f"{role}@a.example", role))
        return client

    def test_only_owners_and_school_admins_can_admit(self):
        self.assertEqual(self.admit().status_code, 201)
        admin = APIClient()
        admin.force_authenticate(user=self.admin)
        self.assertEqual(self.admit(first="B", client=admin).status_code, 201)
        for role, client in self.roles().items():
            self.assertEqual(self.admit(first=role, client=client).status_code, 404, role)
        self.assertEqual(Student.objects.count(), 2)

    def test_supplied_number_is_ignored_on_admission(self):
        self.configure()
        response = self.admit(admission_number="HACK-1", enrolled_on="2026-09-01")
        self.assertEqual(response.data["admission_number"], "ASHS-2026-0001")
        self.assertFalse(Student.objects.filter(admission_number="HACK-1").exists())

    def test_non_managers_cannot_change_numbers_or_settings(self):
        student = self.admit().data
        for role, client in self.roles().items():
            patch_student = client.patch(
                f"{self.base}/students/{student['id']}/", {"admission_number": "HACK"}, format="json"
            )
            self.assertEqual(patch_student.status_code, 404, role)
            patch_settings = client.patch(f"{self.base}/admission-settings/", {"prefix": "HACK"}, format="json")
            self.assertEqual(patch_settings.status_code, 404, role)
            self.assertEqual(client.get(f"{self.base}/admission-settings/").status_code, 404, role)
        self.assertEqual(Student.objects.get(pk=student["id"]).admission_number, student["admission_number"])

    def test_admin_override_is_validated_and_audited(self):
        student = self.admit(enrolled_on="2026-09-01").data
        other = self.admit(first="Kojo", enrolled_on="2026-09-01").data
        url = f"{self.base}/students/{student['id']}/"

        response = self.client.patch(url, {"admission_number": " LEGACY-77 "}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["admission_number"], "LEGACY-77")
        event = AuditEvent.objects.get(action="student.admission_number_overridden")
        self.assertEqual(event.actor, self.owner)
        self.assertEqual(event.tenant, self.tenant)
        self.assertEqual(event.entity_id, student["id"])
        self.assertEqual(event.metadata, {"previous": student["admission_number"], "new": "LEGACY-77"})

        duplicate = self.client.patch(url, {"admission_number": other["admission_number"]}, format="json")
        self.assertEqual(duplicate.status_code, 409)
        blank = self.client.patch(url, {"admission_number": "   "}, format="json")
        self.assertEqual(blank.status_code, 400)
        self.assertEqual(AuditEvent.objects.filter(action="student.admission_number_overridden").count(), 1)

    def test_unrelated_edit_does_not_log_an_override(self):
        student = self.admit().data
        self.client.patch(f"{self.base}/students/{student['id']}/", {"grade_level": "JHS 1"}, format="json")
        self.assertFalse(AuditEvent.objects.filter(action="student.admission_number_overridden").exists())


class AdmissionSettingsApiTests(AdmissionNumberTestBase):
    url = property(lambda self: f"{self.base}/admission-settings/")

    def test_defaults_are_returned_and_stored(self):
        data = self.client.get(self.url).data
        self.assertEqual(data, {
            "prefix": "AHS", "number_format": "{prefix}-{year}-{seq}", "padding": 4,
            "reset_policy": "annual", "year_policy": "calendar",
        })

    def test_admin_updates_settings_and_it_is_audited(self):
        response = self.client.patch(
            self.url, {"prefix": "ashs", "padding": 5, "reset_policy": "continuous"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["prefix"], "ASHS")
        event = AuditEvent.objects.get(action="admission_settings.updated")
        self.assertEqual(event.actor, self.owner)
        self.assertEqual(event.metadata["previous"]["prefix"], "AHS")
        self.assertEqual(event.metadata["new"]["prefix"], "ASHS")

    def test_invalid_configuration_is_rejected(self):
        bad = [
            {"prefix": "has space"},
            {"prefix": ""},
            {"padding": 0},
            {"padding": 20},
            {"number_format": "{prefix}-{seq}-{seq}"},
            {"number_format": "{prefix}-{year}"},
            {"number_format": "{prefix}-{year}-{seq}-{evil}"},
            {"number_format": "{seq.__class__}"},
            {"number_format": "{prefix}-{seq}", "reset_policy": "annual"},
            {"reset_policy": "weekly"},
            {"year_policy": "lunar"},
            {"prefix": "ABCDEFGHIJKL", "number_format": "{prefix}-{prefix}-{prefix}-{year}-{seq}-{prefix}", "padding": 8},
        ]
        for payload in bad:
            self.assertEqual(self.client.patch(self.url, payload, format="json").status_code, 400, payload)
        self.assertFalse(AuditEvent.objects.filter(action="admission_settings.updated").exists())

    def test_continuous_format_without_year_is_valid(self):
        response = self.client.patch(
            self.url, {"number_format": "{prefix}{seq}", "reset_policy": "continuous"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)

    def test_validate_config_accepts_the_defaults(self):
        self.assertEqual(
            validate_config(
                prefix="ASHS", number_format="{prefix}-{year}-{seq}", padding=4,
                reset_policy="annual", year_policy="calendar",
            ),
            {},
        )

    def test_changing_the_format_never_rewrites_existing_numbers(self):
        created = self.admit(enrolled_on="2026-09-01").data
        self.client.patch(self.url, {"prefix": "NEW"}, format="json")
        self.assertEqual(
            Student.objects.get(pk=created["id"]).admission_number, created["admission_number"]
        )
        # The sequence keeps counting when only the prefix changes, so no number is reused.
        self.assertEqual(self.admit(first="B", enrolled_on="2026-09-01").data["admission_number"], "NEW-2026-0002")


class AuditCommandTests(AdmissionNumberTestBase):
    def run_command(self, *args):
        out = StringIO()
        call_command("audit_admission_numbers", self.tenant.slug, *args, stdout=out)
        return out.getvalue()

    def test_reports_without_changing_anything(self):
        Student.objects.create(tenant=self.tenant, admission_number="", first_name="Blank", last_name="One")
        Student.objects.create(tenant=self.tenant, admission_number="a-1", first_name="Low", last_name="Case")
        Student.objects.create(tenant=self.tenant, admission_number="A-1", first_name="Up", last_name="Case")
        output = self.run_command()
        self.assertIn("Blank admission numbers: 1", output)
        self.assertIn("differ only by case or spaces: 1", output)
        self.assertEqual(Student.objects.filter(admission_number="").count(), 1)
        self.assertFalse(AdmissionSequence.objects.exists())

    def test_assign_blank_keeps_existing_numbers_and_audits(self):
        Student.objects.create(tenant=self.tenant, admission_number="LEGACY-1", first_name="Keep", last_name="Me")
        blank = Student.objects.create(tenant=self.tenant, admission_number="", first_name="Blank", last_name="One")
        self.run_command("--assign-blank")
        blank.refresh_from_db()
        self.assertRegex(blank.admission_number, r"^AHS-\d{4}-0001$")
        self.assertEqual(Student.objects.get(first_name="Keep").admission_number, "LEGACY-1")
        event = AuditEvent.objects.get(action="student.admission_number_backfilled")
        self.assertEqual(event.tenant, self.tenant)
        self.assertEqual(event.metadata["source"], "management_command")


class MigrationPreservesNumbersTests(TransactionTestCase):
    def test_existing_numbers_survive_the_migration(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("core", "0005_termly_billing_interval")])
        old_apps = executor.loader.project_state([("core", "0005_termly_billing_interval")]).apps
        OldTenant, OldStudent = old_apps.get_model("core", "Tenant"), old_apps.get_model("core", "Student")
        tenant = OldTenant.objects.create(name="Legacy", slug="legacy", email="l@l.example", phone="1")
        OldStudent.objects.create(tenant=tenant, admission_number="OLD/001", first_name="A", last_name="B")
        OldStudent.objects.create(tenant=tenant, admission_number="", first_name="C", last_name="D")

        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

        self.assertEqual(
            sorted(Student.objects.filter(tenant_id=tenant.pk).values_list("admission_number", flat=True)),
            ["", "OLD/001"],
        )
        self.assertFalse(AdmissionSettings.objects.exists())


@skipUnless(connection.vendor == "postgresql", "Row-locking tests need PostgreSQL.")
class AdmissionConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.tenant = create_tenant(name="Race School", slug="race-school", email="r@r.example", phone="1")
        self.owner = make_user(self.tenant, "owner@r.example", Membership.Role.OWNER)

    def admit_concurrently(self, workers):
        barrier, errors, statuses = Barrier(workers), [], []

        def attempt():
            try:
                client = APIClient()
                client.force_authenticate(user=self.owner)
                barrier.wait(timeout=10)
                response = client.post(
                    f"/api/tenants/{self.tenant.pk}/students/",
                    {"first_name": "S", "last_name": "T", "enrolled_on": "2026-09-01"},
                    format="json",
                )
                statuses.append(response.status_code)
            except BaseException as error:
                errors.append(error)
            finally:
                connections.close_all()

        threads = [Thread(target=attempt) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(errors, [])
        return statuses

    def test_simultaneous_first_admissions_get_distinct_consecutive_numbers(self):
        # No sequence row exists yet, so this also covers creation of the first one.
        statuses = self.admit_concurrently(8)
        self.assertEqual(statuses, [201] * 8)
        numbers = sorted(Student.objects.filter(tenant=self.tenant).values_list("admission_number", flat=True))
        self.assertEqual(numbers, [f"RS-2026-{n:04d}" for n in range(1, 9)])
        self.assertEqual(AdmissionSequence.objects.get(tenant=self.tenant).last_number, 8)

    def test_repeated_races_never_duplicate(self):
        for _ in range(3):
            self.assertEqual(self.admit_concurrently(6), [201] * 6)
        numbers = list(Student.objects.filter(tenant=self.tenant).values_list("admission_number", flat=True))
        self.assertEqual(len(numbers), 18)
        self.assertEqual(len(set(numbers)), 18)

    def test_tenant_row_is_locked_while_a_number_is_reserved(self):
        with transaction.atomic():
            number = next_admission_number(self.tenant.pk, date(2026, 9, 1))
            # A second connection must not be able to lock the same tenant row meanwhile.
            result = []

            def other():
                try:
                    with transaction.atomic():
                        locked = Tenant.objects.select_for_update(nowait=True).filter(pk=self.tenant.pk)
                        list(locked)
                    result.append("acquired")
                except DatabaseError:
                    result.append("blocked")
                finally:
                    connections.close_all()

            thread = Thread(target=other)
            thread.start()
            thread.join(timeout=30)
        self.assertEqual(result, ["blocked"])
        self.assertEqual(number, "RS-2026-0001")
