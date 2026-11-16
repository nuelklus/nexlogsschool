from datetime import date

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class TransferMigrationTests(TransactionTestCase):
    migrate_from = ("academics", "0008_schoolclasslevel_is_terminal_and_more")
    migrate_to = ("academics", "0009_studentenrollment_ended_on_and_more")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        self.old_apps = executor.loader.project_state([self.migrate_from]).apps

    def tearDown(self):
        try:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
        finally:
            super().tearDown()

    def test_existing_enrollment_survives_forward_and_reverse(self):
        Tenant = self.old_apps.get_model("core", "Tenant")
        AcademicYear = self.old_apps.get_model("core", "AcademicYear")
        Student = self.old_apps.get_model("core", "Student")
        SchoolClass = self.old_apps.get_model("academics", "SchoolClass")
        Enrollment = self.old_apps.get_model("academics", "StudentEnrollment")
        tenant = Tenant.objects.create(name="Migration test", slug="transfer-migration", email="test@example.com", phone="1")
        year = AcademicYear.objects.create(
            tenant=tenant, name="Year", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
        )
        student = Student.objects.create(tenant=tenant, admission_number="T1", first_name="A", last_name="B")
        school_class = SchoolClass.objects.create(tenant=tenant, academic_year=year, name="Class")
        enrollment = Enrollment.objects.create(
            tenant=tenant, student=student, school_class=school_class, enrolled_on=date(2026, 1, 1)
        )

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        new_apps = executor.loader.project_state([self.migrate_to]).apps
        migrated = new_apps.get_model("academics", "StudentEnrollment").objects.get(pk=enrollment.pk)
        self.assertIsNone(migrated.ended_on)
        self.assertEqual(migrated.student_id, student.pk)
        self.assertEqual(migrated.school_class_id, school_class.pk)
        migrated.ended_on = date(2026, 3, 31)
        migrated.save(update_fields=("ended_on",))

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        restored = old_apps.get_model("academics", "StudentEnrollment").objects.get(pk=enrollment.pk)
        self.assertEqual(restored.student_id, student.pk)
        self.assertEqual(restored.school_class_id, school_class.pk)
        self.assertEqual(restored.enrolled_on, date(2026, 1, 1))
