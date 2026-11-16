from datetime import date

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ClassManagementMigrationTests(TransactionTestCase):
    migrate_from = ("academics", "0009_studentenrollment_ended_on_and_more")
    migrate_to = ("academics", "0010_schoolclass_class_teacher_schoolclass_is_active")

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

    def test_existing_class_and_enrollment_survive_forward_and_reverse(self):
        Tenant = self.old_apps.get_model("core", "Tenant")
        Year = self.old_apps.get_model("core", "AcademicYear")
        Student = self.old_apps.get_model("core", "Student")
        SchoolClass = self.old_apps.get_model("academics", "SchoolClass")
        Enrollment = self.old_apps.get_model("academics", "StudentEnrollment")
        tenant = Tenant.objects.create(name="Migration", slug="class-management-migration", email="test@example.com", phone="1")
        year = Year.objects.create(tenant=tenant, name="Year", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        student = Student.objects.create(tenant=tenant, admission_number="T1", first_name="A", last_name="B")
        group = SchoolClass.objects.create(tenant=tenant, academic_year=year, name="Class")
        enrollment = Enrollment.objects.create(tenant=tenant, student=student, school_class=group)

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        apps = executor.loader.project_state([self.migrate_to]).apps
        migrated = apps.get_model("academics", "SchoolClass").objects.get(pk=group.pk)
        self.assertTrue(migrated.is_active)
        self.assertIsNone(migrated.class_teacher_id)
        self.assertEqual(apps.get_model("academics", "StudentEnrollment").objects.get(pk=enrollment.pk).school_class_id, group.pk)

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        apps = executor.loader.project_state([self.migrate_from]).apps
        self.assertEqual(apps.get_model("academics", "SchoolClass").objects.get(pk=group.pk).name, "Class")
        self.assertEqual(apps.get_model("academics", "StudentEnrollment").objects.get(pk=enrollment.pk).school_class_id, group.pk)
