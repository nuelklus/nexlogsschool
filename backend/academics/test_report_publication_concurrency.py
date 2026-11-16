from concurrent.futures import ThreadPoolExecutor
from datetime import date
from queue import Queue
from threading import Event
from time import monotonic, sleep
from unittest import SkipTest, mock

from django.conf import settings
from django.db import DatabaseError, close_old_connections, connection, connections
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from core.models import AuditEvent, Membership
from .models import (
    AcademicTerm, Assessment, Examination, ExaminationPublication, GradingConfiguration,
    ReportCardVersion, StudentEnrollment, TermClosure,
)
from .tests import AcademicFixture


class ReportPublicationConcurrencyTests(AcademicFixture, TransactionTestCase):
    require_fixture_campus = True
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
        super().setUp()
        self.head = self.make_member(self.tenant, "report-head@northstar.example", Membership.Role.HEADTEACHER)
        self.admin = self.make_member(self.tenant, "report-admin@northstar.example", Membership.Role.SCHOOL_ADMIN)
        self.enrollment_row = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        policy = {
            "components": [{"key": "exam", "maximum": "100", "weight": "100"}],
            "bands": [{"minimum": "0", "grade": "F", "description": "Fail", "passed": False}],
            "decimal_places": 2, "rounding": "half_up", "allow_decimal_scores": True,
            "exclude_not_applicable": False,
        }
        config = GradingConfiguration.objects.create(
            tenant=self.tenant, name="Publication policy", examination_type="term", configuration=policy,
        )
        exam = Examination.objects.create(
            tenant=self.tenant, term=self.term, school_class=self.school_class, subject=self.subject,
            configuration=config, name="Exam", start_date=date(2026, 3, 10), end_date=date(2026, 3, 11),
            marks_deadline=date(2026, 3, 15), publication_date=date(2026, 3, 16), published_version=1,
        )
        exam_assessment = Assessment.objects.create(
            tenant=self.tenant, term=self.term, school_class=self.school_class, subject=self.subject,
            examination=exam, component_key="exam", name="Exam component",
            assessment_date=date(2026, 3, 10), max_score="100",
        )
        ExaminationPublication.objects.create(
            tenant=self.tenant, examination=exam, enrollment=self.enrollment_row, version=1,
            snapshot={"subject": "Mathematics", "examination": "Exam", "score": "78.00",
                      "grade": "P", "description": "Pass"},
        )
        self.reports = self.base + "report-cards/"
        self.exam_assessment = exam_assessment
        self.payload = {
            "term": str(self.term.pk), "enrollment": str(self.enrollment_row.pk),
            "examinations": [str(exam.pk)], "class_teacher_remark": "Good",
            "headteacher_remark": "Good",
        }
        response = self.api(self.head).post(self.reports, self.payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.report_id = response.data["id"]

    @staticmethod
    def api(user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def operation(self, user, path, data=None):
        return lambda: self.api(user).post(path, data or {}, format="json").status_code

    def publish(self, user=None):
        return self.operation(user or self.owner, f"{self.reports}{self.report_id}/publish/")

    def withdraw(self):
        return self.operation(self.head, f"{self.reports}{self.report_id}/withdraw/", {"reason": "Correction"})

    def lock_term_sql(self, sql):
        return 'FROM "academics_academicterm"' in sql and "FOR UPDATE" in sql

    def run_race(self, first, second):
        entered, release = Event(), Event()
        pids = Queue()
        statements = {"first": [], "second": []}

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
                    statements[label].append(sql)
                    result = execute(sql, params, many, context)
                    if pause and self.lock_term_sql(sql) and not entered.is_set():
                        entered.set()
                        if not release.wait(15):
                            raise AssertionError("The first transaction was not released.")
                    return result

                try:
                    with db.execute_wrapper(trace):
                        return {"status": operation(), "sqlstate": None}
                except DatabaseError as exc:
                    return {"status": None, "sqlstate": getattr(exc.__cause__, "sqlstate", None)}
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(worker, "first", first, True)
            self.assertEqual(pids.get(timeout=5)[0], "first")
            self.assertTrue(entered.wait(10), "First operation did not acquire the term lock.")
            second_future = pool.submit(worker, "second", second, False)
            second_label, second_pid = pids.get(timeout=5)
            self.assertEqual(second_label, "second")
            blocked = False
            deadline = monotonic() + 8
            while monotonic() < deadline and not second_future.done():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT cardinality(pg_blocking_pids(%s))", [second_pid])
                    blocked = bool(cursor.fetchone()[0])
                if blocked:
                    break
                sleep(0.05)
            release.set()
            first_result = first_future.result(timeout=25)
            second_result = second_future.result(timeout=25)

        self.assertTrue(blocked, "Second transaction did not wait for the term row lock.")
        self.assertNotEqual(first_result["sqlstate"], "40P01")
        self.assertNotEqual(second_result["sqlstate"], "40P01")
        self.assertTrue(all(result["sqlstate"] is None for result in (first_result, second_result)))
        return first_result["status"], second_result["status"]

    def assert_only_action(self, action):
        report = ReportCardVersion.objects.get(pk=self.report_id)
        self.assertEqual(AuditEvent.objects.filter(entity_type="ReportCardVersion", entity_id=str(report.pk),
                                                   action=f"academic.report_card.{action}").count(), 1)

    def test_publication_first_then_withdrawal_rechecks_report_status(self):
        self.assertEqual(self.run_race(self.publish(), self.withdraw()), (200, 400))
        report = ReportCardVersion.objects.get(pk=self.report_id)
        self.assertEqual(report.status, ReportCardVersion.Status.PUBLISHED)
        self.assertIsNotNone(report.published_at)
        self.assert_only_action("published")

    def test_withdrawal_first_then_publication_rechecks_report_status(self):
        self.assertEqual(self.run_race(self.withdraw(), self.publish()), (200, 400))
        report = ReportCardVersion.objects.get(pk=self.report_id)
        self.assertEqual(report.status, ReportCardVersion.Status.WITHDRAWN)
        self.assertIsNone(report.published_at)
        self.assert_only_action("withdrawn")

    def test_review_first_blocks_publication_of_existing_review(self):
        review = self.operation(self.head, self.reports, self.payload)
        self.assertEqual(self.run_race(review, self.publish()), (400, 200))
        reports = list(ReportCardVersion.objects.filter(term=self.term).order_by("version"))
        self.assertEqual([report.status for report in reports], [
            ReportCardVersion.Status.PUBLISHED,
        ])
        self.assertEqual(AuditEvent.objects.filter(entity_type="ReportCardVersion",
                                                   action="academic.report_card.published").count(), 1)

    def test_publication_first_blocks_duplicate_review(self):
        review = self.operation(self.head, self.reports, self.payload)
        self.assertEqual(self.run_race(self.publish(), review), (200, 400))
        self.assertEqual(ReportCardVersion.objects.filter(term=self.term).count(), 1)
        self.assertEqual(ReportCardVersion.objects.get(pk=self.report_id).status,
                         ReportCardVersion.Status.PUBLISHED)

    def prepare_closure(self):
        from .models import AssessmentResult, AttendanceRecord

        AttendanceRecord.objects.create(tenant=self.tenant, enrollment=self.enrollment_row,
                                        date=date(2026, 2, 2), status="present")
        AssessmentResult.objects.create(tenant=self.tenant, assessment_id=self.assessment["id"],
                                        enrollment=self.enrollment_row, score="70")
        AssessmentResult.objects.create(tenant=self.tenant, assessment=self.exam_assessment,
                                        enrollment=self.enrollment_row, score="70")
        AcademicTerm.objects.create(tenant=self.tenant, academic_year=self.year, name="Term 2",
                                    start_date=date(2026, 5, 1), end_date=date(2026, 8, 30))
        closure_url = f"{self.base}terms/{self.term.pk}/closure/"
        with mock.patch("academics.closure.school_today", return_value=date(2026, 5, 15)):
            self.assertEqual(self.api(self.owner).post(closure_url + "initialize/", {}, format="json").status_code, 201)
            submitted = self.api(self.head).post(closure_url + "submit/", {}, format="json")
            self.assertEqual(submitted.status_code, 200, submitted.data)
            response = self.api(self.head).post(
                closure_url + "academic-approval/", {"decision": "approve"}, format="json")
        self.assertEqual(response.data["status"], "pending_final_approval")
        return closure_url

    def test_publication_first_then_term_closure(self):
        closure_url = self.prepare_closure()
        closure = self.operation(self.owner, closure_url + "final-authorization/", {"decision": "approve"})
        self.assertEqual(self.run_race(self.publish(), closure), (200, 200))
        self.assertTrue(AcademicTerm.objects.get(pk=self.term.pk).is_completed)
        self.assertEqual(ReportCardVersion.objects.get(pk=self.report_id).status, ReportCardVersion.Status.PUBLISHED)

    def test_term_closure_first_rejects_publication_after_wait(self):
        closure_url = self.prepare_closure()
        closure = self.operation(self.owner, closure_url + "final-authorization/", {"decision": "approve"})
        self.assertEqual(self.run_race(closure, self.publish()), (200, 400))
        self.assertTrue(AcademicTerm.objects.get(pk=self.term.pk).is_completed)
        self.assertEqual(ReportCardVersion.objects.get(pk=self.report_id).status, ReportCardVersion.Status.REVIEWED)
        self.assertEqual(AuditEvent.objects.filter(entity_type="ReportCardVersion",
                                                   action="academic.report_card.published").count(), 0)

    def test_publication_audit_failure_rolls_back_status_change(self):
        from unittest.mock import patch

        client = self.api(self.owner)
        client.raise_request_exception = False
        before = AuditEvent.objects.filter(entity_type="ReportCardVersion", entity_id=self.report_id).count()
        with patch("academics.report_cards._audit", side_effect=RuntimeError("test rollback")):
            response = client.post(f"{self.reports}{self.report_id}/publish/", {}, format="json")
        self.assertEqual(response.status_code, 500)
        report = ReportCardVersion.objects.get(pk=self.report_id)
        self.assertEqual(report.status, ReportCardVersion.Status.REVIEWED)
        self.assertIsNone(report.published_at)
        self.assertEqual(AuditEvent.objects.filter(entity_type="ReportCardVersion", entity_id=self.report_id).count(),
                         before)
