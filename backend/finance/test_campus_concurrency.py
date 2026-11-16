from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless

from django.db import connection, connections, transaction
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from core.models import AcademicYear, Campus, Membership
from core.tests import create_tenant

from . import ledger
from .models import Account, Expense, FinanceCampusGrant, Invoice, Payment, PaymentMethod
from .test_finance import Role, make_student, make_user


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locking")
class CampusGrantConcurrencyTests(TransactionTestCase):
    def test_revocation_prevents_waiting_invoice_payment(self):
        tenant = create_tenant(name="Payment race", slug="payment-race", email="payment-race@example.com", phone="1")
        owner = make_user(tenant, "payment-owner@example.com", Role.OWNER)
        accountant = make_user(tenant, "payment-accountant@example.com", Role.ACCOUNTANT)
        member = Membership.objects.get(tenant=tenant, user=accountant)
        campus = Campus.objects.create(tenant=tenant, name="Main", code="MAIN")
        student = make_student(tenant, campus=campus)
        year = AcademicYear.objects.create(
            tenant=tenant, name="2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
        )
        invoice = Invoice.objects.create(
            tenant=tenant, student=student, academic_year=year, campus=campus,
            due_date=date(2026, 6, 1), status=Invoice.Status.ISSUED, total="100.00"
        )
        ledger.ensure_ready(tenant.pk)
        method = PaymentMethod.objects.get(tenant=tenant, kind=PaymentMethod.Kind.CASH)
        FinanceCampusGrant.objects.create(membership=member, campus=campus)
        base = f"/api/tenants/{tenant.pk}/finance"
        ready = Event()
        request_pid = []

        def pay():
            try:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    request_pid.append(cursor.fetchone()[0])
                ready.set()
                client = APIClient()
                client.force_authenticate(user=accountant)
                return client.post(f"{base}/invoices/{invoice.pk}/payments/", {
                    "method": str(method.pk), "amount": "10.00",
                }, format="json", HTTP_IDEMPOTENCY_KEY="race").status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Membership.objects.select_for_update().get(pk=member.pk)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    lock_pid = cursor.fetchone()[0]
                result = pool.submit(pay)
                self.assertTrue(ready.wait(5))
                deadline = monotonic() + 10
                blocked = False
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT %s = ANY(pg_blocking_pids(%s))", [lock_pid, request_pid[0]])
                        blocked = cursor.fetchone()[0]
                    if blocked:
                        break
                    sleep(0.05)
                self.assertTrue(blocked, "Payment did not wait for the membership lock")
                client = APIClient()
                client.force_authenticate(user=owner)
                self.assertEqual(client.put(f"{base}/campus-access/{member.pk}/", {"campuses": []}, format="json").status_code, 200)
            self.assertEqual(result.result(timeout=10), 404)
        self.assertFalse(Payment.objects.filter(tenant=tenant).exists())
        invoice.refresh_from_db()
        self.assertEqual(invoice.amount_paid, 0)

    def test_revocation_prevents_waiting_expense_creation(self):
        tenant = create_tenant(name="Expense race", slug="expense-race", email="race@example.com", phone="1")
        owner = make_user(tenant, "expense-owner@example.com", Role.OWNER)
        accountant = make_user(tenant, "expense-accountant@example.com", Role.ACCOUNTANT)
        member = Membership.objects.get(tenant=tenant, user=accountant)
        campus = Campus.objects.create(tenant=tenant, name="Main", code="MAIN")
        ledger.ensure_ready(tenant.pk)
        account = Account.objects.get(tenant=tenant, code="6100")
        FinanceCampusGrant.objects.create(membership=member, campus=campus)
        base = f"/api/tenants/{tenant.pk}/finance"
        ready = Event()
        issue_pid = []

        def create():
            try:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    issue_pid.append(cursor.fetchone()[0])
                ready.set()
                client = APIClient()
                client.force_authenticate(user=accountant)
                return client.post(f"{base}/expenses/", {
                    "payee": "Vendor", "description": "Repair", "expense_account": str(account.pk),
                    "campus": str(campus.pk), "transaction_date": "2026-10-10", "amount": "10.00",
                }, format="json").status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Membership.objects.select_for_update().get(pk=member.pk)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    lock_pid = cursor.fetchone()[0]
                result = pool.submit(create)
                self.assertTrue(ready.wait(5), "Expense request did not start")
                deadline = monotonic() + 10
                blocked = False
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT %s = ANY(pg_blocking_pids(%s))", [lock_pid, issue_pid[0]])
                        blocked = cursor.fetchone()[0]
                    if blocked:
                        break
                    sleep(0.05)
                self.assertTrue(blocked, "Expense request did not wait for the membership lock")
                client = APIClient()
                client.force_authenticate(user=owner)
                response = client.put(f"{base}/campus-access/{member.pk}/", {"campuses": []}, format="json")
                self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(result.result(timeout=10), 404)
        self.assertFalse(Expense.objects.filter(tenant=tenant).exists())

    def test_revocation_commits_before_waiting_invoice_issue(self):
        tenant = create_tenant(name="Concurrency", slug="campus-concurrency", email="concurrency@example.com", phone="1")
        owner = make_user(tenant, "concurrency-owner@example.com", Role.OWNER)
        accountant = make_user(tenant, "concurrency-accountant@example.com", Role.ACCOUNTANT)
        member = Membership.objects.get(tenant=tenant, user=accountant)
        campus = Campus.objects.create(tenant=tenant, name="Main", code="MAIN")
        student = make_student(tenant, campus=campus)
        year = AcademicYear.objects.create(
            tenant=tenant, name="2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
        )
        invoice = Invoice.objects.create(
            tenant=tenant, student=student, academic_year=year, campus=campus, due_date=date(2026, 6, 1)
        )
        FinanceCampusGrant.objects.create(membership=member, campus=campus)
        base = f"/api/tenants/{tenant.pk}/finance"
        ready = Event()
        issue_pid = []

        def issue():
            try:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    issue_pid.append(cursor.fetchone()[0])
                ready.set()
                client = APIClient()
                client.force_authenticate(user=accountant)
                return client.post(f"{base}/invoices/{invoice.pk}/issue/").status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Membership.objects.select_for_update().get(pk=member.pk)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    lock_pid = cursor.fetchone()[0]
                result = pool.submit(issue)
                self.assertTrue(ready.wait(5), "Invoice request did not start")
                deadline = monotonic() + 10
                blocked = False
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT %s = ANY(pg_blocking_pids(%s))", [lock_pid, issue_pid[0]])
                        blocked = cursor.fetchone()[0]
                    if blocked:
                        break
                    sleep(0.05)
                self.assertTrue(blocked, "Invoice request did not wait for the membership lock")
                client = APIClient()
                client.force_authenticate(user=owner)
                response = client.put(f"{base}/campus-access/{member.pk}/", {"campuses": []}, format="json")
                self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(result.result(timeout=10), 404)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
