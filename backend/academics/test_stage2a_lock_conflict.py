"""Reproduce the cross-feature conflict before changing admission lock order."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from queue import Queue
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.test import SimpleTestCase

from core.models import AcademicYear, Tenant

from .coordination import acquire_academic_coordination


class Stage2ALockConflictTests(SimpleTestCase):
    databases = {"default"}

    def test_tenant_first_admission_deadlocks_with_uncoordinated_finance_year(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL lock behavior required.")
        token = uuid4().hex
        tenant = Tenant.objects.create(name="Lock test", slug=token, email="test@example.com", phone="1")
        self.addCleanup(lambda: Tenant.objects.filter(pk=tenant.pk).delete())
        year = AcademicYear.objects.create(
            tenant=tenant, name="Current", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31), is_current=True,
        )
        tenant_locked, year_updated, finish_finance = Event(), Event(), Event()
        admission_pid = Queue()

        def admission():
            close_old_connections()
            try:
                try:
                    with transaction.atomic():
                        with connections["default"].cursor() as cursor:
                            cursor.execute("SET LOCAL lock_timeout = '8s'")
                            cursor.execute("SELECT pg_backend_pid()")
                            admission_pid.put(cursor.fetchone()[0])
                        acquire_academic_coordination(tenant.pk)
                        Tenant.objects.select_for_update().get(pk=tenant.pk)
                        tenant_locked.set()
                        if not year_updated.wait(5):
                            raise AssertionError("Finance did not lock the year")
                        AcademicYear.objects.select_for_update().get(pk=year.pk)
                    return "committed"
                except DatabaseError as exc:
                    return getattr(exc.__cause__, "sqlstate", None)
            finally:
                connections.close_all()

        def finance():
            close_old_connections()
            try:
                try:
                    with transaction.atomic():
                        with connections["default"].cursor() as cursor:
                            cursor.execute("SET LOCAL lock_timeout = '8s'")
                        if not tenant_locked.wait(5):
                            raise AssertionError("Admission did not lock the tenant")
                        AcademicYear.objects.filter(pk=year.pk).update(is_current=False)
                        year_updated.set()
                        if not finish_finance.wait(5):
                            raise AssertionError("Admission did not wait for the year")
                        AcademicYear.objects.create(
                            tenant_id=tenant.pk, name="Next", start_date=date(2027, 1, 1),
                            end_date=date(2027, 12, 31), is_current=True,
                        )
                    return "committed"
                except DatabaseError as exc:
                    return getattr(exc.__cause__, "sqlstate", None)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(admission)
            f = pool.submit(finance)
            try:
                pid = admission_pid.get(timeout=5)
                self.assertTrue(year_updated.wait(5))
                deadline = monotonic() + 5
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT cardinality(pg_blocking_pids(%s))", [pid])
                        if cursor.fetchone()[0]:
                            break
                    sleep(0.02)
                else:
                    self.fail("Admission did not block on finance's year update")
            finally:
                finish_finance.set()
            outcomes = [a.result(timeout=12), f.result(timeout=12)]
        self.assertCountEqual(outcomes, ["committed", "40P01"])
