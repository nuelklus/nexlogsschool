"""Rollback-only PostgreSQL race for the real shared trial-plan bootstrap."""

import os
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Event
from time import monotonic, sleep
from unittest import SkipTest
from uuid import uuid4

from django.db import close_old_connections, connection, connections, transaction
from django.test import SimpleTestCase

from core.models import SubscriptionPlan, Tenant, TenantSubscription
from core.subscriptions import TRIAL_PLAN_CODE
from core.views import start_trial_subscription


class TrialPlanBootstrapRaceTests(SimpleTestCase):
    databases = {"default"}

    def setUp(self):
        if connection.vendor != "postgresql":
            raise SkipTest("PostgreSQL is required.")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_database(), (SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid())"
            )
            database, other_connections = cursor.fetchone()
        if not database.startswith("test_") or os.environ.get("NLSCHOOL_ROLLBACK_AUDIT_DB") != database:
            raise SkipTest("Set NLSCHOOL_ROLLBACK_AUDIT_DB to an isolated disposable test database.")
        if other_connections:
            raise SkipTest("The isolated database must have no other connections.")
        if SubscriptionPlan.objects.filter(code=TRIAL_PLAN_CODE).exists():
            raise SkipTest("The isolated database must not contain a trial plan.")

    def test_trial_plan_unique_index_wait_released_by_rollback(self):
        entered, release = Event(), Event()
        pids = Queue()
        token = uuid4().hex

        def worker(label, hold):
            close_old_connections()
            try:
                with transaction.atomic():
                    with connections["default"].cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '10s'")
                        cursor.execute("SET LOCAL statement_timeout = '15s'")
                        cursor.execute("SELECT pg_backend_pid()")
                        pids.put(cursor.fetchone()[0])
                    tenant = Tenant.objects.create(
                        name=label, slug=f"{label}-{token}", email="test@example.com", phone="1"
                    )
                    subscription = start_trial_subscription(tenant)
                    result = (subscription.plan.code, subscription.status)
                    if hold:
                        entered.set()
                        if not release.wait(10):
                            raise AssertionError("First transaction was not released")
                    transaction.set_rollback(True)
                    return result
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(worker, "first", True)
            try:
                first_pid = pids.get(timeout=5)
                self.assertTrue(entered.wait(5))
                second = pool.submit(worker, "second", False)
                second_pid = pids.get(timeout=5)
                deadline = monotonic() + 8
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT pg_blocking_pids(%s), wait_event_type, wait_event, query "
                            "FROM pg_stat_activity WHERE pid = %s",
                            [second_pid, second_pid],
                        )
                        state = cursor.fetchone()
                    if state and first_pid in state[0]:
                        break
                    sleep(0.02)
                else:
                    self.fail("Second bootstrap did not block on the first transaction")
                self.assertEqual(state[1:3], ("Lock", "transactionid"))
                self.assertIn('INSERT INTO "core_subscriptionplan"', state[3])
            finally:
                release.set()
            self.assertEqual(first.result(timeout=15), (TRIAL_PLAN_CODE, TenantSubscription.Status.TRIALING))
            self.assertEqual(second.result(timeout=15), (TRIAL_PLAN_CODE, TenantSubscription.Status.TRIALING))

        self.assertFalse(SubscriptionPlan.objects.filter(code=TRIAL_PLAN_CODE).exists())
        self.assertFalse(Tenant.objects.filter(slug__endswith=token).exists())
