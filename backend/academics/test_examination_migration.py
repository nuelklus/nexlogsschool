from datetime import date
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ExaminationMigrationTests(TransactionTestCase):
    def tearDown(self):
        try:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
        finally:
            super().tearDown()

    def test_existing_results_and_publications_keep_their_values(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("academics", "0013_gradingconfiguration")])
        apps = executor.loader.project_state([("academics", "0013_gradingconfiguration")]).apps
        Tenant = apps.get_model("core", "Tenant")
        Year = apps.get_model("core", "AcademicYear")
        Student = apps.get_model("core", "Student")
        Term = apps.get_model("academics", "AcademicTerm")
        SchoolClass = apps.get_model("academics", "SchoolClass")
        Subject = apps.get_model("academics", "Subject")
        Enrollment = apps.get_model("academics", "StudentEnrollment")
        Assessment = apps.get_model("academics", "Assessment")
        Result = apps.get_model("academics", "AssessmentResult")
        Config = apps.get_model("academics", "GradingConfiguration")
        tenant = Tenant.objects.create(name="Migration", slug="exam-migration", email="migration@example.com", phone="1")
        year = Year.objects.create(tenant=tenant, name="2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        term = Term.objects.create(tenant=tenant, academic_year=year, name="Term", start_date=date(2026, 1, 1), end_date=date(2026, 4, 30))
        school_class = SchoolClass.objects.create(tenant=tenant, academic_year=year, name="Class")
        subject = Subject.objects.create(tenant=tenant, name="Math", code="M")
        student = Student.objects.create(tenant=tenant, admission_number="A1", first_name="A", last_name="B")
        enrollment = Enrollment.objects.create(tenant=tenant, student=student, school_class=school_class)
        assessment = Assessment.objects.create(tenant=tenant, term=term, school_class=school_class, subject=subject,
                                               name="Legacy", assessment_date=date(2026, 3, 10), max_score=100)
        result = Result.objects.create(tenant=tenant, assessment=assessment, enrollment=enrollment, score=Decimal("87.50"))
        config = Config.objects.create(tenant=tenant, name="Policy", examination_type="Exam", configuration={})

        executor = MigrationExecutor(connection)
        executor.migrate([("academics", "0014_examination_examinationpublication_and_more")])
        apps = executor.loader.project_state([("academics", "0014_examination_examinationpublication_and_more")]).apps
        migrated = apps.get_model("academics", "AssessmentResult").objects.get(pk=result.pk)
        self.assertEqual(migrated.score, Decimal("87.50"))
        self.assertEqual(migrated.mark_status, "scored")
        self.assertIsNone(apps.get_model("academics", "Assessment").objects.get(pk=assessment.pk).examination_id)
        exam = apps.get_model("academics", "Examination").objects.create(
            tenant_id=tenant.pk, term_id=term.pk, school_class_id=school_class.pk, subject_id=subject.pk,
            configuration_id=config.pk, name="Exam", start_date=date(2026, 3, 10), end_date=date(2026, 3, 11),
            marks_deadline=date(2026, 3, 12), publication_date=date(2026, 3, 13), status="published",
        )
        publication = apps.get_model("academics", "ExaminationPublication").objects.create(
            tenant_id=tenant.pk, examination_id=exam.pk, enrollment_id=enrollment.pk, snapshot={"score": "87.50"}
        )
        executor = MigrationExecutor(connection)
        target = ("academics", "0015_remove_examinationpublication_one_exam_publication_per_enrollment_and_more")
        executor.migrate([target])
        apps = executor.loader.project_state([target]).apps
        self.assertEqual(apps.get_model("academics", "Examination").objects.get(pk=exam.pk).published_version, 1)
        saved = apps.get_model("academics", "ExaminationPublication").objects.get(pk=publication.pk)
        self.assertEqual((saved.version, saved.snapshot["score"]), (1, "87.50"))
