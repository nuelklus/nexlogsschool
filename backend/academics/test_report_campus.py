from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
from queue import Queue
from threading import Event
from time import monotonic, sleep
from unittest.mock import patch

from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.test import override_settings

from core.models import AuditEvent, Campus, Membership, Student, Tenant, User
from .models import ReportCardVersion, SchoolClass, StudentEnrollment
from .test_report_publication_concurrency import ReportPublicationConcurrencyTests


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ReportCampusTests(ReportPublicationConcurrencyTests):
    def remove_review(self):
        ReportCardVersion.objects.filter(pk=self.report_id).delete()

    def review(self, reason=""):
        return self.api(self.head).post(self.reports, {**self.payload, "reason": reason}, format="json")

    def test_valid_review_and_publication_have_verified_identity(self):
        report = ReportCardVersion.objects.get(pk=self.report_id)
        provenance = report.snapshot["_provenance"]
        self.assertEqual(provenance["enrollment"], str(self.enrollment_row.pk))
        self.assertEqual(provenance["class"], str(self.school_class.pk))
        self.assertEqual(provenance["campus"], str(self.fixture_campus.pk))
        self.assertEqual(self.publish()(), 200)

    def test_review_and_publication_reject_missing_or_foreign_campus(self):
        StudentEnrollment.objects.filter(pk=self.enrollment_row.pk).update(campus=None)
        self.assertEqual(self.publish()(), 400)
        report = ReportCardVersion.objects.get(pk=self.report_id)
        self.assertEqual(report.status, ReportCardVersion.Status.REVIEWED)
        self.assertFalse(AuditEvent.objects.filter(action="academic.report_card.published").exists())
        self.remove_review()
        self.assertEqual(self.review().status_code, 400)
        self.assertFalse(ReportCardVersion.objects.exists())

        StudentEnrollment.objects.filter(pk=self.enrollment_row.pk).update(campus=self.fixture_campus)
        self.fixture_campus.tenant = self.other_tenant
        self.fixture_campus.save(update_fields=("tenant",))
        self.assertEqual(self.review().status_code, 400)
        self.assertFalse(ReportCardVersion.objects.exists())

    def test_legacy_review_is_not_published_or_rewritten(self):
        report = ReportCardVersion.objects.get(pk=self.report_id)
        snapshot = deepcopy(report.snapshot)
        snapshot.pop("_provenance")
        report.snapshot = snapshot
        report.save(update_fields=("snapshot",))
        self.assertEqual(self.publish()(), 400)
        report.refresh_from_db()
        self.assertEqual(report.snapshot, snapshot)
        self.assertEqual(report.status, ReportCardVersion.Status.REVIEWED)
        report.snapshot = []
        report.save(update_fields=("snapshot",))
        self.assertEqual(self.publish()(), 400)
        report.refresh_from_db()
        self.assertEqual(report.snapshot, [])
        withdrawn = self.api(self.head).post(
            self.reports + self.report_id + "/withdraw/", {"reason": "Recheck provenance"}, format="json"
        )
        self.assertEqual(withdrawn.status_code, 200, withdrawn.data)
        revised = self.review("Recheck provenance")
        self.assertEqual(revised.status_code, 201, revised.data)
        self.assertEqual(revised.data["version"], 2)
        self.assertEqual(self.api(self.owner).post(
            self.reports + revised.data["id"] + "/publish/", {}, format="json"
        ).status_code, 200)
        report.refresh_from_db()
        self.assertEqual(report.snapshot, [])
        self.assertEqual(report.status, ReportCardVersion.Status.WITHDRAWN)

    def test_correction_is_reviewed_and_preserves_published_snapshot(self):
        self.assertEqual(self.publish()(), 200)
        original = ReportCardVersion.objects.get(pk=self.report_id)
        snapshot = deepcopy(original.snapshot)
        self.fixture_campus.name = "Renamed campus"
        self.fixture_campus.save(update_fields=("name",))
        self.assertEqual(self.review().status_code, 400)
        correction = self.review("Correct remarks")
        self.assertEqual(correction.status_code, 201, correction.data)
        self.assertEqual(correction.data["version"], 2)
        self.assertEqual(correction.data["status"], ReportCardVersion.Status.REVIEWED)
        self.assertEqual(correction.data["snapshot"]["campus"], "Renamed campus")
        self.assertEqual(self.api(self.owner).post(
            self.reports + correction.data["id"] + "/publish/", {}, format="json"
        ).status_code, 200)
        original.refresh_from_db()
        self.assertEqual(original.snapshot, snapshot)
        self.assertEqual(original.status, ReportCardVersion.Status.PUBLISHED)

    def test_audit_failure_rolls_back_review_and_publication(self):
        with patch("academics.report_cards._audit", side_effect=RuntimeError("forced audit failure")):
            with self.assertRaisesRegex(RuntimeError, "forced audit failure"):
                self.publish()()
        self.assertEqual(ReportCardVersion.objects.get(pk=self.report_id).status, ReportCardVersion.Status.REVIEWED)
        self.remove_review()
        with patch("academics.report_cards._audit", side_effect=RuntimeError("forced audit failure")):
            with self.assertRaisesRegex(RuntimeError, "forced audit failure"):
                self.review()
        self.assertFalse(ReportCardVersion.objects.exists())

    def test_foreign_tenant_cannot_publish_or_review(self):
        response = self.api(self.other_owner).post(
            self.reports + self.report_id + "/publish/", {}, format="json"
        )
        self.assertEqual(response.status_code, 404)
        other_url = f"/api/tenants/{self.other_tenant.pk}/academics/report-cards/"
        other_head = self.make_member(self.other_tenant, "other-head@example.com", Membership.Role.HEADTEACHER)
        response = self.api(other_head).post(other_url, self.payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ReportCardVersion.objects.get(pk=self.report_id).status, ReportCardVersion.Status.REVIEWED)

    def test_conflict_retry_restarts_transaction_and_publishes_once(self):
        retrying, release = Event(), Event()

        def wait_for_release(_delay):
            retrying.set()
            if not release.wait(12):
                raise AssertionError("Retry was not released")

        def publish():
            close_old_connections()
            try:
                return self.publish()()
            finally:
                connections["default"].close()

        with patch("academics.report_campus.sleep", side_effect=wait_for_release):
            with ThreadPoolExecutor(max_workers=1) as pool:
                try:
                    with transaction.atomic():
                        StudentEnrollment.objects.select_for_update().get(pk=self.enrollment_row.pk)
                        future = pool.submit(publish)
                        self.assertTrue(retrying.wait(8))
                        self.assertEqual(ReportCardVersion.objects.get(pk=self.report_id).status,
                                         ReportCardVersion.Status.REVIEWED)
                    release.set()
                    self.assertEqual(future.result(timeout=15), 200)
                finally:
                    release.set()
        self.assertEqual(AuditEvent.objects.filter(action="academic.report_card.published").count(), 1)

    def test_existing_ambiguous_enrollment_blocks_publication(self):
        other = SchoolClass.objects.create(
            tenant=self.tenant, academic_year=self.year, name="Other", campus=self.fixture_campus,
        )
        StudentEnrollment.objects.create(
            tenant=self.tenant, student=self.student, school_class=other, campus=self.fixture_campus,
            enrolled_on=self.enrollment_row.enrolled_on,
        )
        self.assertEqual(self.publish()(), 400)
        self.assertEqual(ReportCardVersion.objects.get(pk=self.report_id).status, ReportCardVersion.Status.REVIEWED)

    def test_direct_insert_waits_until_publication_commits(self):
        other = SchoolClass.objects.create(
            tenant=self.tenant, academic_year=self.year, name="Other", campus=self.fixture_campus,
        )
        entered, release = Event(), Event()
        pids = Queue()

        def publish():
            close_old_connections()
            db = connections["default"]
            try:
                def trace(execute, sql, params, many, context):
                    result = execute(sql, params, many, context)
                    if 'FROM "core_campus"' in sql and "FOR UPDATE" in sql:
                        entered.set()
                        if not release.wait(12):
                            raise AssertionError("Publication was not released")
                    return result
                with db.execute_wrapper(trace):
                    return self.publish()()
            finally:
                db.close()

        def insert():
            close_old_connections()
            db = connections["default"]
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put(cursor.fetchone()[0])
                StudentEnrollment.objects.create(
                    tenant=self.tenant, student=self.student, school_class=other,
                    campus=self.fixture_campus, enrolled_on=self.enrollment_row.enrolled_on,
                )
                return "committed"
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(publish)
            try:
                self.assertTrue(entered.wait(8))
                second = pool.submit(insert)
                pid = pids.get(timeout=5)
                deadline = monotonic() + 8
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_blocking_pids(%s)", [pid])
                        if cursor.fetchone()[0]:
                            break
                    sleep(0.02)
                else:
                    self.fail("Concurrent enrollment did not wait for the student foreign-key check")
                self.assertEqual(StudentEnrollment.objects.filter(student=self.student).count(), 1)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=15), 200)
            self.assertEqual(second.result(timeout=15), "committed")
        self.assertEqual(self.review("Correction").status_code, 400)

    def test_enrollment_fk_update_does_not_deadlock_publication(self):
        other = Student.objects.create(
            tenant=self.tenant, first_name="Other", last_name="Student",
            admission_number="OTHER", campus=self.fixture_campus,
        )
        held, release = Event(), Event()

        def writer():
            close_old_connections()
            db = connections["default"]
            try:
                with transaction.atomic():
                    StudentEnrollment.objects.filter(pk=self.enrollment_row.pk).update(student=other)
                    StudentEnrollment.objects.filter(pk=self.enrollment_row.pk).update(student=self.student)
                    held.set()
                    if not release.wait(12):
                        raise AssertionError("Enrollment transaction was not released")
                return "committed"
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(writer)
            try:
                self.assertTrue(held.wait(8))
                self.assertEqual(self.publish()(), 409)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=15), "committed")
        self.assertEqual(self.publish()(), 200)

    def test_later_term_transfer_does_not_deadlock_publication(self):
        destination = SchoolClass.objects.create(
            tenant=self.tenant, academic_year=self.year, name="Destination", campus=self.fixture_campus,
        )
        held, release = Event(), Event()

        def transfer():
            close_old_connections()
            db = connections["default"]
            try:
                def trace(execute, sql, params, many, context):
                    result = execute(sql, params, many, context)
                    if 'FROM "core_student"' in sql and "FOR UPDATE" in sql and not held.is_set():
                        held.set()
                        if not release.wait(12):
                            raise AssertionError("Transfer was not released")
                    return result
                with patch("academics.transfers.timezone.localdate", return_value=date(2026, 6, 1)), db.execute_wrapper(trace):
                    return self.api(self.owner).post(self.base + "transfers/", {
                        "enrollment": str(self.enrollment_row.pk), "target_class": str(destination.pk),
                        "effective_date": "2026-05-01", "reason": "Move",
                    }, format="json").status_code
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(transfer)
            try:
                self.assertTrue(held.wait(8))
                self.assertEqual(self.publish()(), 409)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=15), 201)
        self.assertEqual(self.publish()(), 200)

    def run_transfer_report_race(self, review, transfer_first):
        destination = SchoolClass.objects.create(
            tenant=self.tenant, academic_year=self.year, name="Destination", campus=self.fixture_campus,
        )
        if review:
            self.remove_review()
        report_operation = self.operation(self.head, self.reports, self.payload) if review else self.publish()
        transfer_operation = self.operation(self.owner, self.base + "transfers/", {
            "enrollment": str(self.enrollment_row.pk), "target_class": str(destination.pk),
            "effective_date": "2026-03-20", "reason": "Move",
        })
        with patch("academics.transfers.timezone.localdate", return_value=date(2026, 6, 1)):
            if transfer_first:
                self.assertEqual(self.run_race(transfer_operation, report_operation), (201, 400))
            else:
                self.assertEqual(self.run_race(report_operation, transfer_operation), (201 if review else 200, 201))
        reports = list(ReportCardVersion.objects.filter(tenant=self.tenant, term=self.term))
        if review and transfer_first:
            self.assertEqual(reports, [])
        else:
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0].status, ReportCardVersion.Status.REVIEWED if review or transfer_first
                             else ReportCardVersion.Status.PUBLISHED)
            self.assertEqual(reports[0].snapshot["_provenance"]["campus"], str(self.fixture_campus.pk))

    def test_review_first_transfer_waits(self):
        self.run_transfer_report_race(True, False)

    def test_transfer_first_review_rejects_multiple_term_enrollments(self):
        self.run_transfer_report_race(True, True)

    def test_publication_first_transfer_waits(self):
        self.run_transfer_report_race(False, False)

    def test_transfer_first_publication_rejects_multiple_term_enrollments(self):
        self.run_transfer_report_race(False, True)

    def run_enrollment_race(self, review, writer_first):
        if review:
            self.remove_review()
        action = "academic.report_card.reviewed" if review else "academic.report_card.published"
        before = AuditEvent.objects.filter(action=action).count()
        entered, release = Event(), Event()
        pids = Queue()

        def report_worker():
            close_old_connections()
            db = connections["default"]
            try:
                def trace(execute, sql, params, many, context):
                    result = execute(sql, params, many, context)
                    if not writer_first and 'FROM "core_campus"' in sql and "FOR UPDATE" in sql:
                        entered.set()
                        if not release.wait(12):
                            raise AssertionError("Report transaction was not released")
                    return result

                with db.execute_wrapper(trace):
                    return self.review().status_code if review else self.publish()()
            finally:
                db.close()

        def writer():
            close_old_connections()
            db = connections["default"]
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put(cursor.fetchone()[0])
                with transaction.atomic():
                    StudentEnrollment.objects.filter(pk=self.enrollment_row.pk).update(campus=None)
                    if writer_first:
                        entered.set()
                        if not release.wait(12):
                            raise AssertionError("Enrollment transaction was not released")
                return "committed"
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            if writer_first:
                first = pool.submit(writer)
                pids.get(timeout=5)
                try:
                    self.assertTrue(entered.wait(8))
                    second = pool.submit(report_worker)
                    self.assertEqual(second.result(timeout=8), 409)
                    self.assertEqual(AuditEvent.objects.filter(action=action).count(), before)
                    if review:
                        self.assertFalse(ReportCardVersion.objects.exists())
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=15), "committed")
                self.assertEqual(self.review().status_code if review else self.publish()(), 400)
            else:
                first = pool.submit(report_worker)
                try:
                    self.assertTrue(entered.wait(8))
                    second = pool.submit(writer)
                    pid = pids.get(timeout=5)
                    deadline = monotonic() + 8
                    while monotonic() < deadline:
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT pg_blocking_pids(%s)", [pid])
                            if cursor.fetchone()[0]:
                                break
                        sleep(0.02)
                    else:
                        self.fail("Enrollment change did not wait for report provenance locks")
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=15), 201 if review else 200)
                self.assertEqual(second.result(timeout=15), "committed")
                report = ReportCardVersion.objects.get(
                    tenant=self.tenant, term=self.term, student=self.student
                )
                self.assertEqual(report.snapshot["_provenance"]["campus"], str(self.fixture_campus.pk))

    def test_review_first_enrollment_change_waits(self):
        self.run_enrollment_race(True, False)

    def test_enrollment_change_first_review_retries_then_rejects(self):
        self.run_enrollment_race(True, True)

    def test_publication_first_enrollment_change_waits(self):
        self.run_enrollment_race(False, False)

    def test_enrollment_change_first_publication_retries_then_rejects(self):
        self.run_enrollment_race(False, True)

    def run_campus_race(self, review, writer_first):
        if review:
            self.remove_review()
        other = Campus.objects.create(tenant=self.tenant, name="Other", code="OTHER")
        entered, release = Event(), Event()
        pids = Queue()

        def report_worker():
            close_old_connections()
            db = connections["default"]
            try:
                def trace(execute, sql, params, many, context):
                    result = execute(sql, params, many, context)
                    if not writer_first and 'FROM "core_campus"' in sql and "FOR UPDATE" in sql:
                        entered.set()
                        if not release.wait(12):
                            raise AssertionError("Report transaction was not released")
                    return result
                with db.execute_wrapper(trace):
                    return self.review().status_code if review else self.publish()()
            finally:
                db.close()

        def writer():
            close_old_connections()
            db = connections["default"]
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put(cursor.fetchone()[0])
                with transaction.atomic():
                    if writer_first:
                        SchoolClass.objects.select_for_update().get(pk=self.school_class.pk)
                        entered.set()
                        if not release.wait(12):
                            raise AssertionError("Class transaction was not released")
                    try:
                        with transaction.atomic():
                            SchoolClass.objects.filter(pk=self.school_class.pk).update(campus=other)
                    except IntegrityError as exc:
                        return getattr(exc.__cause__, "sqlstate", None)
                    return "unexpectedly committed"
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            if writer_first:
                first = pool.submit(writer)
                pids.get(timeout=5)
                try:
                    self.assertTrue(entered.wait(8))
                    second = pool.submit(report_worker)
                    self.assertEqual(second.result(timeout=8), 409)
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=15), "23514")
                self.assertEqual(self.review().status_code if review else self.publish()(), 201 if review else 200)
            else:
                first = pool.submit(report_worker)
                try:
                    self.assertTrue(entered.wait(8))
                    second = pool.submit(writer)
                    pid = pids.get(timeout=5)
                    deadline = monotonic() + 8
                    while monotonic() < deadline:
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT pg_blocking_pids(%s)", [pid])
                            if cursor.fetchone()[0]:
                                break
                        sleep(0.02)
                    else:
                        self.fail("Campus change did not wait for report provenance locks")
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=15), 201 if review else 200)
                self.assertEqual(second.result(timeout=15), "23514")
        self.school_class.refresh_from_db()
        self.assertEqual(self.school_class.campus_id, self.fixture_campus.pk)

    def test_review_first_campus_change_waits_and_fails(self):
        self.run_campus_race(True, False)

    def test_campus_change_first_review_retries_then_succeeds(self):
        self.run_campus_race(True, True)

    def test_publication_first_campus_change_waits_and_fails(self):
        self.run_campus_race(False, False)

    def test_campus_change_first_publication_retries_then_succeeds(self):
        self.run_campus_race(False, True)

    def run_audit_reference_race(self, model, pk, writer_first):
        held, release = Event(), Event()
        pids = Queue()
        table = model._meta.db_table

        def publish():
            close_old_connections()
            db = connections["default"]
            try:
                def trace(execute, sql, params, many, context):
                    result = execute(sql, params, many, context)
                    if not writer_first and f'FROM "{table}"' in sql and "FOR KEY SHARE NOWAIT" in sql:
                        held.set()
                        if not release.wait(12):
                            raise AssertionError("Publication was not released")
                    return result
                with db.execute_wrapper(trace):
                    return self.publish()()
            finally:
                db.close()

        def writer():
            close_old_connections()
            db = connections["default"]
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put(cursor.fetchone()[0])
                with transaction.atomic():
                    model.objects.select_for_update().get(pk=pk)
                    if writer_first:
                        held.set()
                        if not release.wait(12):
                            raise AssertionError("Reference transaction was not released")
                return "committed"
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            if writer_first:
                first = pool.submit(writer)
                pids.get(timeout=5)
                try:
                    self.assertTrue(held.wait(8))
                    second = pool.submit(publish)
                    self.assertEqual(second.result(timeout=8), 409)
                    self.assertFalse(AuditEvent.objects.filter(action="academic.report_card.published").exists())
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=15), "committed")
                self.assertEqual(self.publish()(), 200)
            else:
                first = pool.submit(publish)
                try:
                    self.assertTrue(held.wait(8))
                    second = pool.submit(writer)
                    pid = pids.get(timeout=5)
                    deadline = monotonic() + 8
                    while monotonic() < deadline:
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT pg_blocking_pids(%s)", [pid])
                            if cursor.fetchone()[0]:
                                break
                        sleep(0.02)
                    else:
                        self.fail("Reference writer did not wait for the audit foreign-key lock")
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=15), 200)
                self.assertEqual(second.result(timeout=15), "committed")
        self.assertEqual(AuditEvent.objects.filter(action="academic.report_card.published").count(), 1)

    def test_tenant_writer_first_publication_returns_conflict(self):
        self.run_audit_reference_race(Tenant, self.tenant.pk, True)

    def test_publication_first_tenant_writer_waits(self):
        self.run_audit_reference_race(Tenant, self.tenant.pk, False)

    def test_user_writer_first_publication_returns_conflict(self):
        self.run_audit_reference_race(User, self.owner.pk, True)

    def test_publication_first_user_writer_waits(self):
        self.run_audit_reference_race(User, self.owner.pk, False)
