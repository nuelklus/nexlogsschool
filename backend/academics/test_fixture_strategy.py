"""Validate disposable shared PostgreSQL test fixtures, not business workflows."""

from concurrent.futures import ThreadPoolExecutor
import os
from queue import Queue
from threading import Event
from time import monotonic, sleep
import unittest
from uuid import uuid4

from django.conf import settings
from django.db import close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase

from core.models import Tenant


class SharedFixtureTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if (
            settings.TEST_RUNNER != "academics.fixture_test_settings.FixtureTestRunner"
            or not connection.settings_dict["NAME"].startswith("test_nlschool_fixture_")
        ):
            raise unittest.SkipTest("Requires the disposable fixture test runner.")
        super().setUpClass()

    def test_01_shared_fixture_wait_and_rollback(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.django_migrations')")
            if cursor.fetchone()[0] is not None:
                cursor.execute("SELECT COUNT(*) FROM django_migrations")
                self.assertEqual(cursor.fetchone()[0], 0)
        slug = f"fixture-{uuid4().hex}"
        tenant = Tenant.objects.create(name="Fixture", slug=slug, email="fixture@example.com", phone="1")
        entered, release = Event(), Event()
        pids = Queue()

        def worker(first):
            close_old_connections()
            try:
                with transaction.atomic():
                    with connections["default"].cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '10s'")
                        cursor.execute("SET LOCAL statement_timeout = '15s'")
                        cursor.execute("SELECT pg_backend_pid()")
                        pid = cursor.fetchone()[0]
                    visible = Tenant.objects.get(pk=tenant.pk)
                    pids.put((pid, visible.slug))
                    locked = Tenant.objects.select_for_update().get(pk=tenant.pk)
                    if first:
                        locked.name = "Rolled back"
                        locked.save(update_fields=("name",))
                        entered.set()
                        if not release.wait(10):
                            raise AssertionError("Timed out waiting for release")
                        transaction.set_rollback(True)
                    return locked.name
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(worker, True)
            try:
                first_pid, first_seen = pids.get(timeout=5)
                self.assertTrue(entered.wait(5))
                second = pool.submit(worker, False)
                second_pid, second_seen = pids.get(timeout=5)
                self.assertNotEqual(first_pid, second_pid)
                self.assertEqual((first_seen, second_seen), (slug, slug))
                deadline = monotonic() + 8
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_blocking_pids(%s)", [second_pid])
                        blockers = cursor.fetchone()[0]
                    if first_pid in blockers:
                        break
                    sleep(0.02)
                else:
                    self.fail("Second connection did not wait for the fixture row.")
            finally:
                release.set()
            self.assertEqual(first.result(timeout=15), "Rolled back")
            self.assertEqual(second.result(timeout=15), "Fixture")

        tenant.refresh_from_db()
        self.assertEqual(tenant.name, "Fixture")

    def test_02_flushes_after_test_and_failure(self):
        self.assertFalse(Tenant.objects.filter(slug__startswith="fixture-").exists())
        slug = f"failed-fixture-{uuid4().hex}"

        class ExpectedFailureCase(TransactionTestCase):
            def runTest(inner_self):
                Tenant.objects.create(name="Failure", slug=slug, email="fixture@example.com", phone="1")
                inner_self.fail("Intentional failure to exercise transaction-test cleanup")

        result = unittest.TestResult()
        ExpectedFailureCase()(result)
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.errors, [])
        self.assertFalse(Tenant.objects.filter(slug=slug).exists())

    @unittest.skipUnless(
        os.environ.get("NLSCHOOL_FIXTURE_FAILURE_PROBE") == "1",
        "Only run when checking the runner's failure cleanup.",
    )
    def test_03_runner_failure_cleanup_probe(self):
        Tenant.objects.create(
            name="Failure probe", slug=f"probe-{uuid4().hex}",
            email="fixture@example.com", phone="1",
        )
        self.fail("Intentional runner failure for teardown verification")
