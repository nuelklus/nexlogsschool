from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class TerminalLevelMigrationTests(TransactionTestCase):
    migrate_from = ("academics", "0007_schoolclass_class_level")
    migrate_to = ("academics", "0008_schoolclasslevel_is_terminal_and_more")

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

    def test_existing_level_survives_forward_and_reverse_migration(self):
        Tenant = self.old_apps.get_model("core", "Tenant")
        Level = self.old_apps.get_model("academics", "SchoolClassLevel")
        tenant = Tenant.objects.create(name="Migration test", slug="migration-test", email="test@example.com", phone="1")
        level = Level.objects.create(tenant=tenant, display_name="Existing")

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        new_apps = executor.loader.project_state([self.migrate_to]).apps
        migrated = new_apps.get_model("academics", "SchoolClassLevel").objects.get(pk=level.pk)
        self.assertFalse(migrated.is_terminal)
        migrated.is_terminal = True
        migrated.save(update_fields=("is_terminal",))

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        restored = old_apps.get_model("academics", "SchoolClassLevel").objects.get(pk=level.pk)
        self.assertEqual(restored.display_name, "Existing")
