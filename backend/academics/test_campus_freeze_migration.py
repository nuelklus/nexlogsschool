"""Verify campus-freeze backfill using only the disposable PostgreSQL runner."""

from datetime import date
from unittest import SkipTest
from unittest.mock import patch

from django.conf import settings
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from core.models import AcademicYear, AuditEvent, Campus, Membership, Student, User
from core.tests import create_tenant
from .models import (
    AcademicTerm, Examination, ExaminationPublication, GradingConfiguration, ReportCardVersion,
    SchoolClass, StudentEnrollment, Subject, TeachingAssignment,
)


class CampusFreezeMigrationTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if (
            settings.TEST_RUNNER != "academics.fixture_test_settings.FixtureTestRunner"
            or connection.vendor != "postgresql"
            or not connection.settings_dict["NAME"].startswith("test_nlschool_fixture_")
        ):
            raise SkipTest("Requires the disposable PostgreSQL fixture runner.")
        super().setUpClass()

    @patch("academics.transfers.timezone.localdate", return_value=date(2026, 6, 1))
    def test_existing_enrollment_backfill_preserves_legacy_evidence(self, _today):
        executor = MigrationExecutor(connection)
        executor.migrate([("academics", "0020_enrollment_provenance_revocation")])
        self.addCleanup(lambda: MigrationExecutor(connection).migrate([("academics", "0021_class_campus_freeze")]))

        tenant = create_tenant(name="Legacy", slug="legacy-freeze", email="legacy@example.com", phone="1")
        owner = User.objects.create_user(email="legacy-owner@example.com", full_name="Owner")
        Membership.objects.create(tenant=tenant, user=owner, role=Membership.Role.OWNER)
        old = Campus.objects.create(tenant=tenant, name="Old", code="OLD")
        other = Campus.objects.create(tenant=tenant, name="Other", code="OTHER")
        year = AcademicYear.objects.create(
            tenant=tenant, name="2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), is_current=True
        )
        school_class = SchoolClass.objects.create(tenant=tenant, academic_year=year, campus=old, name="Source")
        target = SchoolClass.objects.create(tenant=tenant, academic_year=year, campus=old, name="Target")
        student = Student.objects.create(
            tenant=tenant, first_name="A", last_name="B", admission_number="A1", campus=old
        )
        source = StudentEnrollment.objects.create(
            tenant=tenant, student=student, school_class=school_class, campus=other, enrolled_on=date(2026, 1, 1)
        )
        incomplete_class = SchoolClass.objects.create(
            tenant=tenant, academic_year=year, campus=None, name="Incomplete"
        )
        incomplete_student = Student.objects.create(
            tenant=tenant, first_name="C", last_name="D", admission_number="A2", campus=old
        )
        incomplete = StudentEnrollment.objects.create(
            tenant=tenant, student=incomplete_student, school_class=incomplete_class,
            campus=old, enrolled_on=date(2026, 1, 1)
        )

        MigrationExecutor(connection).migrate([("academics", "0021_class_campus_freeze")])
        source.refresh_from_db()
        school_class.refresh_from_db()
        self.assertEqual(source.campus_id, other.pk)
        self.assertEqual(school_class.campus_id, old.pk)
        with self.assertRaises(IntegrityError):
            SchoolClass.objects.filter(pk=school_class.pk).update(campus=other)
        client = APIClient()
        client.force_authenticate(user=owner)
        response = client.post(f"/api/tenants/{tenant.pk}/academics/transfers/", {
            "enrollment": str(source.pk), "target_class": str(target.pk),
            "effective_date": "2026-02-01", "reason": "Transfer",
        }, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        source.refresh_from_db()
        self.assertEqual(source.campus_id, other.pk)
        self.assertEqual(source.status, StudentEnrollment.Status.ACTIVE)
        response = client.post(f"/api/tenants/{tenant.pk}/academics/transfers/", {
            "enrollment": str(incomplete.pk), "target_class": str(target.pk),
            "effective_date": "2026-02-01", "reason": "Transfer",
        }, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        incomplete.refresh_from_db()
        self.assertEqual(incomplete.campus_id, old.pk)
        self.assertEqual(incomplete.status, StudentEnrollment.Status.ACTIVE)
        with self.assertRaises(IntegrityError):
            SchoolClass.objects.filter(pk=incomplete_class.pk).update(campus=old)

        term = AcademicTerm.objects.create(
            tenant=tenant, academic_year=year, name="Term 1",
            start_date=date(2026, 1, 1), end_date=date(2026, 4, 30),
        )
        subject = Subject.objects.create(tenant=tenant, name="Math", code="MATH")
        TeachingAssignment.objects.create(
            tenant=tenant, school_class=school_class, subject=subject, teacher=owner,
        )
        config = GradingConfiguration.objects.create(
            tenant=tenant, name="Policy", examination_type="term", configuration={},
        )
        exam = Examination.objects.create(
            tenant=tenant, term=term, school_class=school_class, subject=subject, configuration=config,
            name="Exam", start_date=date(2026, 3, 10), end_date=date(2026, 3, 11),
            marks_deadline=date(2026, 3, 12), publication_date=date(2026, 3, 13), published_version=1,
        )
        publication = ExaminationPublication.objects.create(
            tenant=tenant, examination=exam, enrollment=source, version=1,
            snapshot={"subject": "Math", "examination": "Exam", "score": "70.00",
                      "grade": "P", "description": "Pass"},
        )
        head = User.objects.create_user(email="legacy-head@example.com", full_name="Head")
        Membership.objects.create(tenant=tenant, user=head, role=Membership.Role.HEADTEACHER)
        client.force_authenticate(user=head)
        review = client.post(f"/api/tenants/{tenant.pk}/academics/report-cards/", {
            "term": str(term.pk), "enrollment": str(source.pk), "examinations": [str(exam.pk)],
            "class_teacher_remark": "Good", "headteacher_remark": "Good",
        }, format="json")
        self.assertEqual(review.status_code, 400, review.data)
        self.assertFalse(ReportCardVersion.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(action="academic.report_card.reviewed").exists())

        snapshot = {"campus": "Other", "results": [{"publication_id": str(publication.pk)}]}
        legacy = ReportCardVersion.objects.create(
            tenant=tenant, term=term, enrollment=source, student=student, version=1, snapshot=snapshot,
        )
        client.force_authenticate(user=owner)
        publish = client.post(
            f"/api/tenants/{tenant.pk}/academics/report-cards/{legacy.pk}/publish/", {}, format="json"
        )
        self.assertEqual(publish.status_code, 400, publish.data)
        legacy.refresh_from_db()
        self.assertEqual(legacy.snapshot, snapshot)
        self.assertEqual(legacy.status, ReportCardVersion.Status.REVIEWED)
        self.assertFalse(AuditEvent.objects.filter(action="academic.report_card.published").exists())
