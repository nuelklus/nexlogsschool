from datetime import date
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from core.models import AcademicYear, Campus, Membership
from core.tests import create_tenant

from . import ledger
from .models import FinanceCampusGrant, Invoice, Payment, PaymentMethod
from .test_finance import Role, make_student, make_user


class CampusGrantMigrationTests(TransactionTestCase):
    def migrate_finance(self, name):
        MigrationExecutor(connection).migrate([("finance", name)])

    def tearDown(self):
        self.migrate_finance("0007_financecampusgrant")
        super().tearDown()

    def test_round_trip_preserves_financial_records_but_removes_grants(self):
        tenant = create_tenant(name="Migration", slug="campus-migration", email="migration@example.com", phone="1")
        accountant = make_user(tenant, "migration-acc@example.com", Role.ACCOUNTANT)
        member = Membership.objects.get(tenant=tenant, user=accountant)
        campus = Campus.objects.create(tenant=tenant, name="Main", code="MAIN")
        student = make_student(tenant, campus=campus)
        year = AcademicYear.objects.create(
            tenant=tenant, name="2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
        )
        ledger.ensure_ready(tenant.pk)
        invoice = Invoice.objects.create(
            tenant=tenant, student=student, academic_year=year, campus=campus,
            grade_level="JHS 1", due_date=date(2026, 6, 1), total=Decimal("100.00")
        )
        payment = Payment.objects.create(
            tenant=tenant, student=student, method=PaymentMethod.objects.get(tenant=tenant, kind="cash"),
            amount=Decimal("25.00"), payment_date=date(2026, 6, 1)
        )
        FinanceCampusGrant.objects.create(membership=member, campus=campus)

        self.migrate_finance("0006_other_income")
        self.assertEqual(Invoice.objects.get(pk=invoice.pk).campus_id, campus.pk)
        self.assertEqual(Payment.objects.get(pk=payment.pk).amount, Decimal("25.00"))
        self.migrate_finance("0007_financecampusgrant")
        self.assertFalse(FinanceCampusGrant.objects.exists())
        self.assertEqual(Invoice.objects.get(pk=invoice.pk).grade_level, "JHS 1")
        self.assertEqual(Invoice.objects.get(pk=invoice.pk).total, Decimal("100.00"))
        self.assertEqual(Payment.objects.get(pk=payment.pk).student_id, student.pk)
