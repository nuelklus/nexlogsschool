import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Event
from time import monotonic, sleep
from unittest.mock import patch
from uuid import UUID

from django.db import NotSupportedError, close_old_connections, connection, connections, transaction
from django.test import SimpleTestCase

from .coordination import academic_coordination_key, acquire_academic_coordination


TENANT = UUID("f0c1a8bc-55cf-4e16-bd39-904d8cbb1b00")
OTHER_TENANT = UUID("407208d5-b4a2-4e52-8bd5-743b696323b9")


class AcademicCoordinationTests(SimpleTestCase):
    databases = {"default"}

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Academic coordination requires PostgreSQL.")

    def worker(self, tenant_id, started, acquired):
        close_old_connections()
        try:
            with transaction.atomic():
                with connections["default"].cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '10s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    started.put(cursor.fetchone()[0])
                acquire_academic_coordination(tenant_id)
                acquired.set()
        finally:
            connections.close_all()

    def wait_blocked(self, pid):
        deadline = monotonic() + 8
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute("SELECT cardinality(pg_blocking_pids(%s))", [pid])
                if cursor.fetchone()[0]:
                    return
            sleep(0.02)
        self.fail("The worker did not wait for a PostgreSQL lock.")

    def test_requires_atomic_and_valid_uuid(self):
        with self.assertRaisesRegex(RuntimeError, "transaction.atomic"):
            acquire_academic_coordination(TENANT)
        for value in (None, "", "invalid", 0, UUID(int=0)):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "tenant UUID"):
                academic_coordination_key(value)
        with transaction.atomic():
            with self.assertRaisesRegex(ValueError, "tenant UUID"):
                acquire_academic_coordination(None)

    def test_unsupported_backend_fails_closed(self):
        with patch.object(connections["default"], "vendor", "sqlite"):
            with self.assertRaisesRegex(NotSupportedError, "requires PostgreSQL"):
                acquire_academic_coordination(TENANT)

    def test_stable_key_matches_another_process_and_uuid_spellings(self):
        script = (
            "from academics.coordination import academic_coordination_key; "
            f"print(academic_coordination_key('{TENANT}'))"
        )
        child = subprocess.check_output([sys.executable, "-c", script], text=True, timeout=10)
        self.assertEqual(int(child.strip()), academic_coordination_key(TENANT))
        self.assertEqual(academic_coordination_key(str(TENANT).upper()), academic_coordination_key(TENANT))
        self.assertNotEqual(academic_coordination_key(TENANT), academic_coordination_key(OTHER_TENANT))

    def test_same_tenant_waits_until_commit(self):
        started, acquired = Queue(), Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                acquire_academic_coordination(TENANT)
                future = pool.submit(self.worker, TENANT, started, acquired)
                self.wait_blocked(started.get(timeout=5))
                self.assertFalse(acquired.is_set())
            future.result(timeout=10)
        self.assertTrue(acquired.is_set())

    def test_same_tenant_waits_until_rollback(self):
        started, acquired = Queue(), Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                acquire_academic_coordination(TENANT)
                future = pool.submit(self.worker, TENANT, started, acquired)
                self.wait_blocked(started.get(timeout=5))
                transaction.set_rollback(True)
            future.result(timeout=10)
        self.assertTrue(acquired.is_set())

    def test_different_tenants_are_independent(self):
        started, acquired = Queue(), Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                acquire_academic_coordination(TENANT)
                future = pool.submit(self.worker, OTHER_TENANT, started, acquired)
                started.get(timeout=5)
                self.assertTrue(acquired.wait(5))
                future.result(timeout=5)

    def test_nested_reentry_survives_savepoint_rollback(self):
        started, acquired = Queue(), Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                acquire_academic_coordination(TENANT)
                acquire_academic_coordination(str(TENANT))
                with transaction.atomic():
                    acquire_academic_coordination(TENANT)
                    transaction.set_rollback(True)
                acquire_academic_coordination(TENANT)
                future = pool.submit(self.worker, TENANT, started, acquired)
                self.wait_blocked(started.get(timeout=5))
                self.assertFalse(acquired.is_set())
            future.result(timeout=10)
        self.assertTrue(acquired.is_set())

    def test_first_acquisition_in_savepoint_is_rejected(self):
        with transaction.atomic():
            with transaction.atomic():
                with self.assertRaisesRegex(RuntimeError, "before entering a savepoint"):
                    acquire_academic_coordination(TENANT)
            acquire_academic_coordination(TENANT)

    def test_first_acquisition_in_savepoint_free_nested_atomic(self):
        started, acquired = Queue(), Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                with transaction.atomic(savepoint=False):
                    acquire_academic_coordination(TENANT)
                future = pool.submit(self.worker, TENANT, started, acquired)
                self.wait_blocked(started.get(timeout=5))
            future.result(timeout=10)
        self.assertTrue(acquired.is_set())

    def test_new_transaction_does_not_reuse_previous_lock_state(self):
        with transaction.atomic():
            acquire_academic_coordination(TENANT)
        with transaction.atomic():
            with transaction.atomic():
                with self.assertRaisesRegex(RuntimeError, "before entering a savepoint"):
                    acquire_academic_coordination(TENANT)

    def test_multiple_tenants_in_one_transaction_rejected(self):
        with transaction.atomic():
            acquire_academic_coordination(TENANT)
            with self.assertRaisesRegex(RuntimeError, "only one tenant"):
                acquire_academic_coordination(OTHER_TENANT)

    def test_non_read_committed_transaction_rejected(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            with self.assertRaisesRegex(RuntimeError, "READ COMMITTED"):
                acquire_academic_coordination(TENANT)
