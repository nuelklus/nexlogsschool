from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier, Event
from time import monotonic, sleep
from unittest.mock import patch

from django.db import close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase, skipUnlessDBFeature
from rest_framework.test import APIClient

from core.models import Membership
from .models import (
    AcademicTerm, Assessment, Examination, ExaminationPublication, GradingConfiguration,
    ReportCardVersion, SchoolClass, StudentEnrollment,
)
from .report_card_preview import _preview
from .tests import AcademicFixture


@skipUnlessDBFeature("has_select_for_update")
class ReportCardLockOrderTests(AcademicFixture, TransactionTestCase):
    require_fixture_campus = True
    def setUp(self):
        super().setUp()
        if connection.vendor != "postgresql":
            self.skipTest("Concurrency verification requires PostgreSQL.")
        self.head = self.make_member(self.tenant, "lock-head@northstar.example", Membership.Role.HEADTEACHER)
        self.admin = self.make_member(self.tenant, "lock-admin@northstar.example", Membership.Role.SCHOOL_ADMIN)
        self.enrollment_row = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        policy = {
            "components": [{"key": "exam", "maximum": "100", "weight": "100"}],
            "bands": [{"minimum": "0", "grade": "F", "description": "Fail", "passed": False}],
            "decimal_places": 2, "rounding": "half_up", "allow_decimal_scores": True,
            "exclude_not_applicable": False,
        }
        config = GradingConfiguration.objects.create(
            tenant=self.tenant, name="Lock policy", examination_type="term", configuration=policy,
        )
        self.exam = Examination.objects.create(
            tenant=self.tenant, term=self.term, school_class=self.school_class, subject=self.subject,
            configuration=config, name="Exam", start_date=date(2026, 3, 10), end_date=date(2026, 3, 11),
            marks_deadline=date(2026, 3, 15), publication_date=date(2026, 3, 16), published_version=1,
        )
        Assessment.objects.create(
            tenant=self.tenant, term=self.term, school_class=self.school_class, subject=self.subject,
            examination=self.exam, component_key="exam", name="Exam component",
            assessment_date=date(2026, 3, 10), max_score="100",
        )
        ExaminationPublication.objects.create(
            tenant=self.tenant, examination=self.exam, enrollment=self.enrollment_row, version=1,
            snapshot={"subject": "Mathematics", "examination": "Exam", "score": "78.00",
                      "grade": "P", "description": "Pass"},
        )
        self.reports = self.base + "report-cards/"
        self.payload = {
            "term": str(self.term.pk), "enrollment": str(self.enrollment_row.pk),
            "examinations": [str(self.exam.pk)], "class_teacher_remark": "Good",
            "headteacher_remark": "Good",
        }

    def wait_blocked(self, pid):
        until = monotonic() + 10
        while monotonic() < until:
            with connection.cursor() as cursor:
                cursor.execute("SELECT cardinality(pg_blocking_pids(%s))", [pid])
                if cursor.fetchone()[0]:
                    return
            sleep(0.05)
        self.fail("The concurrent transaction did not wait for a PostgreSQL lock.")

    def worker(self, user, path, payload, started=None, gate=None):
        close_old_connections()
        try:
            client = APIClient()
            client.force_authenticate(user=user)
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pid = cursor.fetchone()[0]
            if started is not None:
                started.append(pid)
            if gate is not None:
                gate.wait(timeout=10)
            response = client.post(path, payload, format="json")
            return response.status_code
        finally:
            connections.close_all()

    def test_mark_writer_waits_for_term_before_locking_exam(self):
        started = []
        payload = {"entries": [{"enrollment": str(self.enrollment_row.pk), "component": "exam",
                                "status": "scored", "score": "0", "remarks": ""}]}
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                AcademicTerm.objects.select_for_update().get(pk=self.term.pk)
                pending = pool.submit(self.worker, self.teacher, self.base + f"examinations/{self.exam.pk}/", payload, started)
                until = monotonic() + 10
                while not started and monotonic() < until:
                    sleep(0.05)
                self.assertTrue(started)
                self.wait_blocked(started[0])
                Examination.objects.select_for_update(nowait=True).get(pk=self.exam.pk)
            self.assertEqual(pending.result(timeout=10), 200)

    def test_transfer_waits_for_review_and_blocks_subsequent_publication(self):
        target = SchoolClass.objects.create(
            tenant=self.tenant, academic_year=self.year, name="Grade 2", campus=self.fixture_campus
        )
        entered, release = Event(), Event()
        started = []

        def delayed_preview(tenant_id, data):
            snapshot = _preview(tenant_id, data)
            entered.set()
            if not release.wait(10):
                raise TimeoutError("Review was not released.")
            return snapshot

        transfer = {
            "enrollment": str(self.enrollment_row.pk), "target_class": str(target.pk),
            "effective_date": "2026-03-20", "reason": "Transfer", "subjects": [],
        }
        with patch("academics.report_cards._preview", side_effect=delayed_preview), \
                patch("academics.transfers.timezone.localdate", return_value=date(2026, 3, 20)):
            with ThreadPoolExecutor(max_workers=2) as pool:
                review = pool.submit(self.worker, self.head, self.reports, self.payload)
                try:
                    self.assertTrue(entered.wait(10))
                    move = pool.submit(self.worker, self.owner, self.base + "transfers/", transfer, started)
                    until = monotonic() + 10
                    while not started and monotonic() < until:
                        sleep(0.05)
                    self.assertTrue(started)
                    self.wait_blocked(started[0])
                finally:
                    release.set()
                self.assertEqual(review.result(timeout=10), 201)
                self.assertEqual(move.result(timeout=10), 201)
        report = ReportCardVersion.objects.get()
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(self.reports + f"{report.pk}/publish/", {}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("reconciliation", str(response.data))
        report.refresh_from_db()
        self.assertEqual(report.status, ReportCardVersion.Status.REVIEWED)

    def test_concurrent_reviews_and_publications_create_single_outcome(self):
        gate = Barrier(2, timeout=10)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.worker(self.head, self.reports, self.payload, gate=gate), range(2)))
        self.assertEqual(sorted(results), [201, 400])
        report = ReportCardVersion.objects.get()
        gate = Barrier(2, timeout=10)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda user: self.worker(user, self.reports + f"{report.pk}/publish/", {}, gate=gate),
                (self.owner, self.admin),
            ))
        self.assertEqual(sorted(results), [200, 400])
        report.refresh_from_db()
        self.assertEqual(report.status, ReportCardVersion.Status.PUBLISHED)
