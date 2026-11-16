"""Real cross-module workflow races on disposable PostgreSQL (audit only; no locking is added).

Every operation is an actual DRF endpoint, Django admin change form or application service.
The first operation is paused (inside its transaction) after a chosen SQL statement; the
second is then started and PostgreSQL's ``pg_blocking_pids`` shows whether it waits.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from queue import Queue
from threading import Event
from time import monotonic, sleep
from unittest import SkipTest, mock

from django.conf import settings
from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.test import Client, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from core.models import Tenant, AcademicYear, Membership, Student, SubscriptionPlan, TenantSubscription, User
from core.views import start_trial_subscription
from finance import ledger
from finance.models import FeeCategory, FeeSchedule, Invoice, PaymentMethod

from .models import (
    AcademicTerm, Assessment, AssessmentResult, AttendanceRecord, Examination, ExaminationPublication,
    GradingConfiguration, ReportCardVersion, SchoolClass, StudentEnrollment, Subject, TeachingAssignment,
    TermClosure, EnrollmentProvenanceAttestation, EnrollmentProvenanceRevocation,
)
from .tests import AcademicFixture

AFTER_TERM = mock.patch("academics.closure.school_today", return_value=date(2026, 5, 15))


def for_update(table):
    return lambda sql: f'FROM "{table}"' in sql and "FOR UPDATE" in sql


def write_to(verb, table):
    return lambda sql: sql.startswith(f'{verb} "{table}"') or sql.startswith(f"{verb} INTO \"{table}\"")


def term_exists_check(sql):
    return 'FROM "academics_academicterm"' in sql and '"is_completed"' in sql and "FOR UPDATE" not in sql


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CrossModuleRaceTests(AcademicFixture, TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if (
            settings.TEST_RUNNER != "academics.fixture_test_settings.FixtureTestRunner"
            or connection.vendor != "postgresql"
            or not connection.settings_dict["NAME"].startswith("test_nlschool_fixture_")
        ):
            raise SkipTest("Requires the disposable PostgreSQL fixture runner.")
        super().setUpClass()

    def setUp(self):
        self.require_fixture_campus = self._testMethodName.startswith(("test_D_", "test_F", "test_G"))
        super().setUp()
        self.head = self.make_member(self.tenant, "head@northstar.example", Membership.Role.HEADTEACHER)
        self.admin = self.make_member(self.tenant, "admin@northstar.example", Membership.Role.SCHOOL_ADMIN)
        self.enrollment_row = StudentEnrollment.objects.get(pk=self.enrollment["id"])

    # ---- harness -------------------------------------------------------------------------
    def api(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def post(self, user, path, data=None):
        return lambda: self.api(user).post(path, data or {}, format="json").status_code

    @staticmethod
    def lock_order(statements):
        order = []
        for sql in statements:
            if "FOR UPDATE" in sql or "FOR NO KEY UPDATE" in sql:
                tables = re.findall(r'(?:FROM|JOIN) "(\w+)"', sql)
                label = "+".join(dict.fromkeys(tables))
                order.append(f"lock:{label}")
            else:
                match = re.match(r'(INSERT INTO|UPDATE|DELETE FROM) "(\w+)"', sql)
                if match:
                    order.append(f"{match.group(1).split()[0].lower()}:{match.group(2)}")
        deduped = []
        for item in order:
            if item not in deduped:
                deduped.append(item)
        return deduped

    def race(self, name, first, second, hold, expect_blocked, second_hold=None):
        """Pause `first` after the SQL matching `hold`; start `second`; report waits and outcomes."""
        entered, release = Event(), Event()
        pids, traces = Queue(), {"first": [], "second": []}

        def worker(label, operation, pause):
            close_old_connections()
            db = connections["default"]
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '12s'")
                    cursor.execute("SET statement_timeout = '20s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put((label, cursor.fetchone()[0]))

                def trace(execute, sql, params, many, context):
                    traces[label].append(sql)
                    result = execute(sql, params, many, context)
                    if pause and pause(sql) and not entered.is_set():
                        entered.set()
                        if not release.wait(15):
                            raise AssertionError("First operation was not released")
                    return result

                try:
                    with db.execute_wrapper(trace):
                        return {"status": operation(), "sqlstate": None}
                except DatabaseError as exc:
                    return {"status": None, "sqlstate": getattr(exc.__cause__, "sqlstate", None)}
            finally:
                db.close()

        blocked, wait_detail = False, None
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(worker, "first", first, hold)
            try:
                self.assertEqual(pids.get(timeout=5)[0], "first")
                self.assertTrue(entered.wait(10), f"{name}: first operation never reached its hold point")
                b = pool.submit(worker, "second", second, second_hold)
                self.assertEqual(pids.get(timeout=5)[0], "second")
                first_pid = None
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pid FROM pg_stat_activity WHERE datname = current_database() "
                        "AND state = 'idle in transaction' AND pid <> pg_backend_pid()"
                    )
                    held = [row[0] for row in cursor.fetchall()]
                deadline = monotonic() + 6
                while monotonic() < deadline and not b.done():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT pid, pg_blocking_pids(pid), wait_event_type, wait_event, left(query, 160) FROM pg_stat_activity "
                            "WHERE datname = current_database() AND cardinality(pg_blocking_pids(pid)) > 0"
                        )
                        waiting = cursor.fetchall()
                    if waiting:
                        blocked = True
                        wait_detail = [(row[2], row[3], row[4]) for row in waiting]
                        break
                    sleep(0.05)
            finally:
                release.set()
            outcomes = {"first": a.result(timeout=25), "second": b.result(timeout=25)}
        summary = {
            "test": name, "blocked": blocked, "wait": wait_detail, "first": outcomes["first"], "second": outcomes["second"],
            "first_order": self.lock_order(traces["first"]), "second_order": self.lock_order(traces["second"]),
        }
        print("RACE-RESULT " + json.dumps(summary, default=str))
        self.assertEqual(blocked, expect_blocked, f"{name}: unexpected blocking result")
        self.assertNotIn("40P01", (outcomes["first"]["sqlstate"], outcomes["second"]["sqlstate"]))
        return outcomes

    # ---- shared fixtures -----------------------------------------------------------------
    def make_exam(self, published=True):
        policy = {
            "components": [{"key": "exam", "maximum": "100", "weight": "100"}],
            "bands": [{"minimum": "0", "grade": "F", "description": "Fail", "passed": False}],
            "decimal_places": 2, "rounding": "half_up", "allow_decimal_scores": True,
            "exclude_not_applicable": False,
        }
        config = GradingConfiguration.objects.create(
            tenant=self.tenant, name="Policy", examination_type="term", configuration=policy,
        )
        exam = Examination.objects.create(
            tenant=self.tenant, term=self.term, school_class=self.school_class, subject=self.subject,
            configuration=config, name="Exam", start_date=date(2026, 3, 10), end_date=date(2026, 3, 11),
            marks_deadline=date(2026, 3, 15), publication_date=date(2026, 3, 16), published_version=1,
        )
        Assessment.objects.create(
            tenant=self.tenant, term=self.term, school_class=self.school_class, subject=self.subject,
            examination=exam, component_key="exam", name="Exam component",
            assessment_date=date(2026, 3, 10), max_score="100",
        )
        ExaminationPublication.objects.create(
            tenant=self.tenant, examination=exam, enrollment=self.enrollment_row, version=1,
            snapshot={"subject": "Mathematics", "examination": "Exam", "score": "78.00", "grade": "P",
                      "description": "Pass"},
        )
        return exam

    def marks_op(self, exam):
        payload = {"entries": [{"enrollment": str(self.enrollment_row.pk), "component": "exam",
                                "status": "scored", "score": "55", "remarks": ""}]}
        return self.post(self.teacher, self.base + f"examinations/{exam.pk}/", payload)

    def review_op(self, exam):
        payload = {
            "term": str(self.term.pk), "enrollment": str(self.enrollment_row.pk),
            "examinations": [str(exam.pk)], "class_teacher_remark": "Good", "headteacher_remark": "Good",
        }
        return self.post(self.head, self.base + "report-cards/", payload)

    def finance_ready(self):
        ledger.ensure_ready(self.tenant.pk)
        category = FeeCategory.objects.get(tenant=self.tenant, code="tuition")
        FeeSchedule.objects.create(
            tenant=self.tenant, academic_year=self.year, category=category, grade_level="", campus=None,
            amount=Decimal("1000.00"), effective_from=date(2026, 1, 1),
        )
        self.finance = f"/api/tenants/{self.tenant.pk}/finance"

    def invoice_body(self, student=None):
        return {"student": str((student or self.student).pk), "academic_year": str(self.year.pk),
                "period_label": "Term 1"}

    def closure_to_pending_final(self):
        AttendanceRecord.objects.create(tenant=self.tenant, enrollment=self.enrollment_row,
                                        date=date(2026, 2, 2), status="present")
        AssessmentResult.objects.create(tenant=self.tenant, assessment_id=self.assessment["id"],
                                        enrollment=self.enrollment_row, score="70")
        AcademicTerm.objects.create(tenant=self.tenant, academic_year=self.year, name="Term 2",
                                    start_date=date(2026, 5, 1), end_date=date(2026, 8, 30))
        AFTER_TERM.start()
        self.addCleanup(mock.patch.stopall)
        url = f"{self.base}terms/{self.term.pk}/closure/"
        self.closure_url = url
        self.assertEqual(self.api(self.owner).post(url + "initialize/", {}, format="json").status_code, 201)
        self.assertEqual(self.api(self.head).post(url + "submit/", {}, format="json").status_code, 200)
        approved = self.api(self.head).post(url + "academic-approval/", {"decision": "approve"}, format="json")
        self.assertEqual(approved.data["status"], "pending_final_approval")

    # ---- A: finance-year creation vs term closure ------------------------------------------
    def finance_year_op(self):
        return self.post(self.owner, f"/api/tenants/{self.tenant.pk}/finance/academic-years/", {
            "name": "Next", "start_date": "2027-01-01", "end_date": "2027-12-31", "is_current": True})

    def initialize_op(self):
        return self.post(self.owner, f"{self.base}terms/{self.term.pk}/closure/initialize/")

    def assert_year_state(self):
        self.assertEqual(AcademicYear.objects.filter(tenant=self.tenant, is_current=True).count(), 1)
        self.assertTrue(AcademicYear.objects.filter(tenant=self.tenant, name="Next").exists())
        self.assertEqual(TermClosure.objects.filter(term=self.term).count(), 1)

    def test_A1_finance_year_holds_year_then_term_closure_waits(self):
        out = self.race("A1", self.finance_year_op(), self.initialize_op(),
                        write_to("UPDATE", "core_academicyear"), True)
        self.assertEqual((out["first"]["status"], out["second"]["status"]), (201, 201))
        self.assert_year_state()

    def test_A2_term_closure_holds_term_and_year_then_finance_year_waits(self):
        out = self.race("A2", self.initialize_op(), self.finance_year_op(),
                        for_update("academics_academicterm"), True)
        self.assertEqual((out["first"]["status"], out["second"]["status"]), (201, 201))
        self.assert_year_state()

    # ---- B: invoice creation vs term closure (fixed: both lock the term row) --------------
    def invoice_rows(self):
        from finance.models import InvoiceLine, JournalEntry
        return (Invoice.objects.filter(tenant=self.tenant).count(),
                InvoiceLine.objects.filter(invoice__tenant=self.tenant).count(),
                JournalEntry.objects.filter(tenant=self.tenant).count())

    def test_B1_schedule_A_invoice_first_holds_term_lock_closure_waits(self):
        self.finance_ready()
        self.closure_to_pending_final()
        journals = self.invoice_rows()[2]
        out = self.race(
            "B1", self.post(self.owner, f"{self.finance}/invoices/", self.invoice_body()),
            self.post(self.owner, self.closure_url + "final-authorization/", {"decision": "approve"}),
            for_update("academics_academicterm"), True,
        )
        self.assertEqual((out["first"]["status"], out["second"]["status"]), (201, 200))
        self.assertTrue(AcademicTerm.objects.get(pk=self.term.pk).is_completed)
        self.assertEqual(self.invoice_rows()[0], 1)
        self.assertEqual(self.invoice_rows()[2], journals)

    def test_B2_schedule_B_closure_first_invoice_waits_then_rejected(self):
        self.finance_ready()
        self.closure_to_pending_final()
        before = self.invoice_rows()
        out = self.race(
            "B2", self.post(self.owner, self.closure_url + "final-authorization/", {"decision": "approve"}),
            self.post(self.owner, f"{self.finance}/invoices/", self.invoice_body()),
            for_update("academics_academicterm"), True,
        )
        self.assertEqual((out["first"]["status"], out["second"]["status"]), (200, 409))
        self.assertTrue(AcademicTerm.objects.get(pk=self.term.pk).is_completed)
        self.assertEqual(self.invoice_rows(), before)

    def test_B3_invoice_for_other_tenant_term_is_not_blocked_by_closure(self):
        self.finance_ready()
        self.closure_to_pending_final()
        other_year = AcademicYear.objects.create(
            tenant=self.other_tenant, name="Other 2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        AcademicTerm.objects.create(tenant=self.other_tenant, academic_year=other_year, name="Term 1",
                                    start_date=date(2026, 1, 1), end_date=date(2026, 4, 30))
        from academics.closure import is_term_closed
        entered, release, done = Event(), Event(), Event()

        def hold():
            close_old_connections()
            try:
                with transaction.atomic():
                    list(AcademicTerm.objects.filter(tenant=self.tenant).select_for_update())
                    entered.set()
                    release.wait(10)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(hold)
            self.assertTrue(entered.wait(5))
            with transaction.atomic():
                connection.cursor().execute("SET LOCAL lock_timeout = '2s'")
                self.assertFalse(is_term_closed(self.other_tenant.pk, other_year.pk, "Term 1", lock=True))
            release.set()
            future.result(timeout=15)

    # ---- C: payment allocation vs invoice void ---------------------------------------------
    def advance_and_invoice(self):
        self.finance_ready()
        method = PaymentMethod.objects.get(tenant=self.tenant, kind="cash")
        paid = self.api(self.owner).post(f"{self.finance}/payments/", {
            "student": str(self.student.pk), "method": str(method.pk), "amount": "400.00"}, format="json")
        self.assertEqual(paid.status_code, 201, paid.data)
        draft = self.api(self.owner).post(f"{self.finance}/invoices/", self.invoice_body(), format="json")
        self.assertEqual(draft.status_code, 201, draft.data)
        issued = self.api(self.owner).post(f"{self.finance}/invoices/{draft.data['id']}/issue/", format="json")
        self.assertEqual(issued.status_code, 200, issued.data)
        return paid.data["id"], draft.data["id"]

    def allocate_op(self, payment_id):
        return self.post(self.owner, f"{self.finance}/payments/{payment_id}/allocate/", {"auto_allocate": True})

    def void_op(self, invoice_id):
        return self.post(self.owner, f"{self.finance}/invoices/{invoice_id}/void/", {"reason": "Error"})

    def test_C1_allocation_holds_finance_lock_then_void_waits(self):
        payment_id, invoice_id = self.advance_and_invoice()
        out = self.race("C1", self.allocate_op(payment_id), self.void_op(invoice_id),
                        for_update("finance_financesettings"), True)
        self.assertEqual(out["first"]["status"], 200)
        invoice = Invoice.objects.get(pk=invoice_id)
        self.assertIn(out["second"]["status"], (200, 400, 409))
        self.assertTrue(invoice.status in ("voided", "partially_paid", "paid", "issued"))

    def test_C2_void_holds_finance_lock_then_allocation_waits(self):
        payment_id, invoice_id = self.advance_and_invoice()
        out = self.race("C2", self.void_op(invoice_id), self.allocate_op(payment_id),
                        for_update("finance_financesettings"), True)
        self.assertEqual(out["first"]["status"], 200)
        self.assertIn(out["second"]["status"], (400, 409))
        self.assertEqual(Invoice.objects.get(pk=invoice_id).status, "voided")

    # ---- D: concurrent transfers of the same student ---------------------------------------
    def test_D_two_transfers_for_same_student_serialize_on_term(self):
        grade2 = SchoolClass.objects.create(
            tenant=self.tenant, academic_year=self.year, name="Grade 2", campus=self.fixture_campus
        )
        grade3 = SchoolClass.objects.create(
            tenant=self.tenant, academic_year=self.year, name="Grade 3", campus=self.fixture_campus
        )

        def transfer(target):
            return self.post(self.owner, self.base + "transfers/", {
                "enrollment": str(self.enrollment_row.pk), "target_class": str(target.pk),
                "effective_date": "2026-04-01", "reason": "Move"})

        with mock.patch("academics.transfers.timezone.localdate", return_value=date(2026, 6, 1)):
            out = self.race("D", transfer(grade2), transfer(grade3), for_update("academics_academicterm"), True)
        self.assertEqual(sorted([out["first"]["status"], out["second"]["status"]])[0], 201)
        self.assertNotEqual(out["first"]["status"], out["second"]["status"])
        active = StudentEnrollment.objects.filter(student=self.student, ended_on__isnull=True)
        self.assertEqual(active.count(), 1)

    # ---- E: teacher-assignment creation vs marks submission --------------------------------
    def test_E_assignment_creation_is_not_blocked_by_marks_term_lock(self):
        exam = self.make_exam()
        science = Subject.objects.create(tenant=self.tenant, name="Science", code="SCI")
        other_teacher = self.make_member(self.tenant, "teacher2@northstar.example", Membership.Role.TEACHER)
        out = self.race(
            "E", self.marks_op(exam),
            self.post(self.owner, self.base + "teacher-assignments/", {
                "school_class": str(self.school_class.pk), "subject": str(science.pk),
                "teacher": str(other_teacher.pk)}),
            for_update("academics_academicterm"), False,
        )
        self.assertEqual(out["first"]["status"], 200)
        self.assertEqual(out["second"]["status"], 201)

    # ---- F: marks vs report review ----------------------------------------------------------
    def test_F1_marks_hold_term_then_review_waits(self):
        exam = self.make_exam()
        out = self.race("F1", self.marks_op(exam), self.review_op(exam),
                        for_update("academics_academicterm"), True)
        self.assertEqual((out["first"]["status"], out["second"]["status"]), (200, 201))

    def test_F2_review_holds_term_then_marks_wait(self):
        exam = self.make_exam()
        out = self.race("F2", self.review_op(exam), self.marks_op(exam),
                        for_update("academics_academicterm"), True)
        self.assertEqual((out["first"]["status"], out["second"]["status"]), (201, 200))
        self.assertEqual(ReportCardVersion.objects.count(), 1)

    # ---- G: provenance revocation vs review ------------------------------------------------
    def attested_withdrawn_student(self):
        second = Student.objects.create(tenant=self.tenant, admission_number="NSA-002",
                                        first_name="Kofi", last_name="Boateng", campus=self.fixture_campus)
        created = self.api(self.owner).post(self.base + "enrollments/", {
            "student": str(second.pk), "school_class": str(self.school_class.pk),
            "subjects": [str(self.subject.pk)], "enrolled_on": "2026-01-05"}, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        row = StudentEnrollment.objects.get(pk=created.data["id"])
        row.status, row.ended_on = StudentEnrollment.Status.WITHDRAWN, date(2026, 3, 19)
        row.save(update_fields=("status", "ended_on"))
        url = self.base + "enrollment-provenance/"
        report = self.api(self.owner).post(url + "report/", {
            "term": str(self.term.pk), "school_class": str(self.school_class.pk)}, format="json")
        self.assertEqual(report.status_code, 200, report.data)
        target = next(item for item in report.data["students"] if item["enrollment"] == str(row.pk)
                      ) if "enrollment" in report.data["students"][0] else report.data["students"][-1]
        attest = self.api(self.owner).post(url + "attest/", {
            "term": str(self.term.pk), "enrollment": str(row.pk), "previous_attestation": None,
            "fingerprint": target["fingerprint"], "segment_start": target["segment_start"],
            "segment_end": target["segment_end"], "campus": str(self.fixture_campus.pk), "no_campus": False,
            "subjects": [str(self.subject.pk)], "reason": "Verified register", "evidence": "Register"},
            format="json")
        self.assertEqual(attest.status_code, 201, attest.data)
        return url + f"{attest.data['id']}/revoke/", attest.data["id"]

    def test_G1_review_holds_term_then_revocation_waits(self):
        exam = self.make_exam()
        revoke_url, attestation_id = self.attested_withdrawn_student()
        out = self.race("G1", self.review_op(exam),
                        self.post(self.owner, revoke_url, {"reason": "Correction"}),
                        for_update("academics_academicterm"), True)
        self.assertEqual((out["first"]["status"], out["second"]["status"]), (201, 201))
        self.assertEqual(EnrollmentProvenanceRevocation.objects.filter(attestation_id=attestation_id).count(), 1)

    def test_G2_revocation_holds_term_then_review_waits(self):
        exam = self.make_exam()
        revoke_url, attestation_id = self.attested_withdrawn_student()
        out = self.race("G2", self.post(self.owner, revoke_url, {"reason": "Correction"}),
                        self.review_op(exam), for_update("academics_academicterm"), True)
        self.assertEqual((out["first"]["status"], out["second"]["status"]), (201, 201))
        self.assertEqual(EnrollmentProvenanceRevocation.objects.filter(attestation_id=attestation_id).count(), 1)

    # ---- H: plan modification (Django admin) vs trial subscription creation -----------------
    def admin_plan_edit(self, plan, **changes):
        staff = User.objects.create_user(email="platform@example.com", password="x-Disposable-1", full_name="Platform")
        staff.is_staff = staff.is_superuser = True
        staff.save()
        client = Client()
        client.force_login(staff)
        data = {"name": plan.name, "code": plan.code, "description": "", "price": "0", "currency": "GHS",
                "billing_interval": "monthly", "trial_days": "30", "entitlements": "{}", "is_active": "on",
                **changes}
        return lambda: client.post(f"/admin/core/subscriptionplan/{plan.pk}/change/", data).status_code

    def fresh_tenant(self):
        self.third = Tenant.objects.create(name="Third School", slug="third-school")
        return self.third

    @staticmethod
    def trial_op(tenant):
        def run():
            with transaction.atomic():
                return start_trial_subscription(tenant).status and 201
        return run

    def test_H1_plan_name_edit_does_not_block_trial_creation(self):
        plan, _ = SubscriptionPlan.objects.get_or_create(code="trial", defaults={"name": "Free trial", "price": 0, "trial_days": 30})
        plan.name = "Free trial"
        plan.save()
        out = self.race("H1", self.admin_plan_edit(plan, name="Free trial (edited)"),
                        self.trial_op(self.fresh_tenant()), write_to("UPDATE", "core_subscriptionplan"), False)
        self.assertEqual((out["first"]["status"], out["second"]["status"]), (302, 201))
        self.assertEqual(TenantSubscription.objects.filter(tenant=self.third, plan=plan).count(), 1)

    def test_H2_admin_cannot_rename_trial_plan_during_trial_creation(self):
        plan, _ = SubscriptionPlan.objects.get_or_create(code="trial", defaults={"name": "Free trial", "price": 0, "trial_days": 30})
        plan.name = "Free trial"
        plan.save()
        out = self.race("H2", self.admin_plan_edit(plan, code="trial-old"),
                        self.trial_op(self.fresh_tenant()), write_to("UPDATE", "core_subscriptionplan"), False)
        self.assertEqual((out["first"]["status"], out["second"]["status"]), (302, 201))
        subscription = TenantSubscription.objects.get(tenant=self.third)
        self.assertEqual(subscription.plan_id, plan.pk)
        self.assertEqual(SubscriptionPlan.objects.get(pk=plan.pk).code, "trial")
