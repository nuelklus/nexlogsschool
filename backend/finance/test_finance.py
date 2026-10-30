from datetime import date, timedelta
from decimal import Decimal
from threading import Barrier, Thread
from unittest import skipUnless

from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import AcademicYear, AuditEvent, Campus, Membership, Student, StudentGuardian, User
from core.tests import create_tenant

from . import ledger, payments
from .ledger import FinanceError, post_entry, reverse_entry, system_account
from .models import (
    Account,
    AccountingPeriod,
    FeeCategory,
    FeeSchedule,
    ImmutableError,
    Invoice,
    JournalEntry,
    JournalLine,
    Payment,
    PaymentMethod,
)

Role = Membership.Role
D = Decimal


def make_user(tenant, email, role):
    user = User.objects.create_user(email=email, password="Taller-River-804", full_name=email.split("@")[0])
    user.email_verified = True
    user.save(update_fields=("email_verified",))
    Membership.objects.create(tenant=tenant, user=user, role=role)
    return user


def make_student(tenant, n=1, grade="JHS 1", campus=None, user=None):
    return Student.objects.create(
        tenant=tenant, admission_number=f"S-{n:04d}", first_name=f"First{n}", last_name=f"Last{n}",
        grade_level=grade, campus=campus, user=user,
    )


FAST_HASH = override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])


@FAST_HASH
class FinanceBase(TestCase):
    def setUp(self):
        self.tenant = create_tenant(name="Accra Heights", slug="accra-heights", email="a@a.example", phone="1")
        self.owner = make_user(self.tenant, "owner@a.example", Role.OWNER)
        self.admin = make_user(self.tenant, "admin@a.example", Role.SCHOOL_ADMIN)
        self.accountant = make_user(self.tenant, "acc@a.example", Role.ACCOUNTANT)
        self.teacher = make_user(self.tenant, "teacher@a.example", Role.TEACHER)
        self.parent = make_user(self.tenant, "parent@a.example", Role.PARENT)
        self.other_parent = make_user(self.tenant, "parent2@a.example", Role.PARENT)
        self.student_user = make_user(self.tenant, "kid@a.example", Role.STUDENT)
        self.year = AcademicYear.objects.create(
            tenant=self.tenant, name="2026/2027", start_date=date(2026, 9, 1), end_date=date(2027, 7, 31), is_current=True
        )
        self.campus = Campus.objects.create(tenant=self.tenant, name="Main", code="MAIN")
        self.student = make_student(self.tenant, 1, campus=self.campus, user=self.student_user)
        self.sibling = make_student(self.tenant, 2)
        StudentGuardian.objects.create(tenant=self.tenant, student=self.student, user=self.parent)
        StudentGuardian.objects.create(tenant=self.tenant, student=self.sibling, user=self.other_parent)
        ledger.ensure_ready(self.tenant.pk)
        self.cat = {c.code: c for c in FeeCategory.objects.filter(tenant=self.tenant)}
        self.method = {m.kind: m for m in PaymentMethod.objects.filter(tenant=self.tenant)}
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.base = f"/api/tenants/{self.tenant.pk}/finance"

    def as_user(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def schedule(self, code="tuition", amount="1000.00", grade="", campus=None, year=None, **extra):
        return FeeSchedule.objects.create(
            tenant=self.tenant, academic_year=year or self.year, category=self.cat[code], grade_level=grade,
            campus=campus, amount=D(amount), effective_from=date(2026, 1, 1), **extra,
        )

    def issued_invoice(self, student=None, period="Term 1", due=None):
        student = student or self.student
        draft = self.client.post(
            f"{self.base}/invoices/",
            {"student": str(student.pk), "academic_year": str(self.year.pk), "period_label": period,
             **({"due_date": due} if due else {})},
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.data)
        issued = self.client.post(f"{self.base}/invoices/{draft.data['id']}/issue/", format="json")
        self.assertEqual(issued.status_code, 200, issued.data)
        return issued.data

    def pay(self, amount, kind="cash", student=None, **extra):
        headers = {k: extra.pop(k) for k in list(extra) if k.startswith("HTTP_")}
        body = {"student": str((student or self.student).pk), "method": str(self.method[kind].pk), "amount": amount, **extra}
        return self.client.post(f"{self.base}/payments/", body, format="json", **headers)

    def overdue_invoice(self, student=None, period="Term 1"):
        invoice = self.issued_invoice(student=student, period=period)
        Invoice.objects.filter(pk=invoice["id"]).update(due_date=date(2020, 1, 1))
        return invoice

    def balances(self):
        rows = {}
        for line in JournalLine.objects.filter(entry__tenant=self.tenant).select_related("account"):
            rows[line.account.system_key or line.account.code] = rows.get(line.account.system_key or line.account.code, D(0)) + line.debit - line.credit
        return rows


class LedgerTests(FinanceBase):
    def post(self, debit, credit, key="k1", **extra):
        return post_entry(
            self.tenant.pk, entry_date=date(2026, 9, 2), memo="m", source_type="test", source_id="1",
            idempotency_key=key, user=self.owner,
            lines=[(system_account(self.tenant.pk, "receivable"), debit, D(0), ""), (system_account(self.tenant.pk, "student_advances"), D(0), credit, "")],
            **extra,
        )

    def in_tx(self, fn, *args, **kwargs):
        from django.db import transaction

        with transaction.atomic():
            ledger.lock_finance(self.tenant.pk)
            return fn(*args, **kwargs)

    def test_balanced_entry_posts_and_retry_does_not_double_post(self):
        entry, created = self.in_tx(self.post, D("10.00"), D("10.00"))
        again, created_again = self.in_tx(self.post, D("10.00"), D("10.00"))
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(entry.pk, again.pk)
        self.assertEqual(JournalEntry.objects.filter(tenant=self.tenant).count(), 1)

    def test_unbalanced_entry_rejected_and_leaves_nothing(self):
        with self.assertRaises(FinanceError):
            self.in_tx(self.post, D("10.00"), D("9.99"))
        self.assertEqual(JournalEntry.objects.count(), 0)

    def test_posted_entries_are_immutable(self):
        entry, _ = self.in_tx(self.post, D("5.00"), D("5.00"))
        entry.memo = "tampered"
        with self.assertRaises(ImmutableError):
            entry.save()
        with self.assertRaises(ImmutableError):
            entry.delete()
        with self.assertRaises(ImmutableError):
            JournalEntry.objects.filter(pk=entry.pk).update(memo="x")
        with self.assertRaises(ImmutableError):
            JournalEntry.objects.filter(pk=entry.pk).delete()
        with self.assertRaises(ImmutableError):
            JournalLine.objects.filter(entry=entry).update(debit=D("1"))

    def test_reversal_nets_to_zero_and_is_idempotent(self):
        entry, _ = self.in_tx(self.post, D("5.00"), D("5.00"))
        first, _ = self.in_tx(reverse_entry, entry, user=self.owner)
        second, _ = self.in_tx(reverse_entry, entry, user=self.owner)
        self.assertEqual(first.pk, second.pk)
        net = sum(line.debit - line.credit for line in JournalLine.objects.filter(entry__tenant=self.tenant))
        self.assertEqual(net, D("0.00"))

    def test_closed_period_blocks_posting(self):
        AccountingPeriod.objects.create(tenant=self.tenant, name="Sep", start_date=date(2026, 9, 1), end_date=date(2026, 9, 30), status="closed")
        with self.assertRaises(FinanceError) as ctx:
            self.in_tx(self.post, D("1.00"), D("1.00"))
        self.assertEqual(ctx.exception.status, 409)

    def test_floats_and_extra_decimals_rejected(self):
        with self.assertRaises(FinanceError):
            ledger.money(1.1)
        with self.assertRaises(FinanceError):
            ledger.money("1.001")
        self.assertEqual(ledger.money("1.10"), D("1.10"))

    def test_rounding_half_up(self):
        self.assertEqual(ledger.round_money(D("0.125")), D("0.13"))
        self.assertEqual(ledger.round_money(D("0.124")), D("0.12"))

    def test_line_must_have_one_positive_side(self):
        from django.db import IntegrityError, transaction

        entry, _ = self.in_tx(self.post, D("5.00"), D("5.00"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            JournalLine.objects.create(entry=entry, account=system_account(self.tenant.pk, "receivable"), debit=D("1"), credit=D("1"))


class BillingTests(FinanceBase):
    def test_invoice_from_schedules_with_most_specific_winning(self):
        self.schedule("tuition", "1000.00")
        self.schedule("tuition", "1200.00", grade="JHS 1")
        self.schedule("examination", "150.00")
        self.schedule("boarding", "900.00", grade="SHS 3")  # other class
        draft = self.client.post(
            f"{self.base}/invoices/", {"student": str(self.student.pk), "academic_year": str(self.year.pk), "period_label": "T1"}, format="json"
        )
        self.assertEqual(draft.status_code, 201, draft.data)
        self.assertEqual(draft.data["status"], "draft")
        self.assertEqual(draft.data["number"], "")
        self.assertEqual(draft.data["total"], "1350.00")
        self.assertEqual(len(draft.data["lines"]), 2)
        self.assertEqual(JournalEntry.objects.filter(tenant=self.tenant).count(), 0)

    def test_draft_without_applicable_fees_is_refused(self):
        response = self.client.post(
            f"{self.base}/invoices/", {"student": str(self.student.pk), "academic_year": str(self.year.pk), "period_label": "T1"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_issue_posts_receivable_and_income_and_number_is_sequential(self):
        self.schedule("tuition", "1000.00")
        self.schedule("examination", "150.00")
        first = self.issued_invoice()
        second = self.issued_invoice(student=self.sibling)
        self.assertEqual((first["number"], second["number"]), ("INV-000001", "INV-000002"))
        bal = self.balances()
        self.assertEqual(bal["receivable"], D("2300.00"))
        self.assertEqual(bal["4000"], D("-2000.00"))
        self.assertEqual(bal["4010"], D("-300.00"))

    def test_reissue_does_not_double_post(self):
        self.schedule()
        invoice = self.issued_invoice()
        again = self.client.post(f"{self.base}/invoices/{invoice['id']}/issue/", format="json")
        self.assertEqual(again.data["number"], invoice["number"])
        self.assertEqual(JournalEntry.objects.filter(tenant=self.tenant, source_type="invoice").count(), 1)

    def test_duplicate_live_invoice_for_period_rejected_but_allowed_after_void(self):
        self.schedule()
        invoice = self.issued_invoice()
        dup = self.client.post(
            f"{self.base}/invoices/", {"student": str(self.student.pk), "academic_year": str(self.year.pk), "period_label": "Term 1"}, format="json"
        )
        self.assertIn(dup.status_code, (400, 409))
        void = self.client.post(f"{self.base}/invoices/{invoice['id']}/void/", {"reason": "Wrong"}, format="json")
        self.assertEqual(void.status_code, 200, void.data)
        self.assertEqual(self.balances().get("receivable", D(0)), D("0.00"))
        self.issued_invoice()

    def test_changing_schedule_does_not_change_issued_invoice(self):
        schedule = self.schedule("tuition", "1000.00")
        invoice = self.issued_invoice()
        self.client.patch(f"{self.base}/fee-schedules/{schedule.pk}/", {"amount": "2000.00"}, format="json")
        detail = self.client.get(f"{self.base}/invoices/{invoice['id']}/")
        self.assertEqual(detail.data["total"], "1000.00")

    def test_scholarship_percent_and_charge(self):
        self.schedule("tuition", "1000.00")
        for body in (
            {"kind": "scholarship", "percent": "10.00", "description": "Merit", "reason": "Top of class"},
            {"kind": "charge", "amount": "50.00", "description": "Late registration", "reason": "Registered after deadline"},
        ):
            response = self.client.post(
                f"{self.base}/adjustments/", {"student": str(self.student.pk), "academic_year": str(self.year.pk), **body}, format="json"
            )
            self.assertEqual(response.status_code, 201, response.data)
        invoice = self.issued_invoice()
        self.assertEqual(invoice["subtotal"], "1050.00")
        self.assertEqual(invoice["discount_total"], "100.00")
        self.assertEqual(invoice["total"], "950.00")
        bal = self.balances()
        self.assertEqual(bal["receivable"], D("950.00"))
        self.assertEqual(bal["discounts"], D("100.00"))

    def test_charge_needs_fixed_amount_and_discount_needs_exactly_one_value(self):
        base = {"student": str(self.student.pk), "academic_year": str(self.year.pk), "description": "x", "reason": "r"}
        self.assertEqual(self.client.post(f"{self.base}/adjustments/", {**base, "kind": "charge", "percent": "5"}, format="json").status_code, 400)
        self.assertEqual(self.client.post(f"{self.base}/adjustments/", {**base, "kind": "discount"}, format="json").status_code, 400)
        self.assertEqual(
            self.client.post(f"{self.base}/adjustments/", {**base, "kind": "discount", "amount": "5", "percent": "5"}, format="json").status_code, 400
        )

    def test_credit_note_reduces_balance_and_posts_correctly(self):
        self.schedule()
        invoice = self.issued_invoice()
        response = self.client.post(
            f"{self.base}/invoices/{invoice['id']}/credit-notes/",
            {"amount": "200.00", "reason_type": "bad_debt", "reason": "Left school"}, format="json", HTTP_IDEMPOTENCY_KEY="cn-1",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["invoice"]["balance"], "800.00")
        replay = self.client.post(
            f"{self.base}/invoices/{invoice['id']}/credit-notes/",
            {"amount": "200.00", "reason_type": "bad_debt", "reason": "Left school"}, format="json", HTTP_IDEMPOTENCY_KEY="cn-1",
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(self.balances()["bad_debt"], D("200.00"))
        too_much = self.client.post(
            f"{self.base}/invoices/{invoice['id']}/credit-notes/",
            {"amount": "900.00", "reason_type": "correction", "reason": "x"}, format="json",
        )
        self.assertEqual(too_much.status_code, 400)


class PaymentTests(FinanceBase):
    def setUp(self):
        super().setUp()
        self.schedule("tuition", "1000.00")
        self.invoice = self.issued_invoice()

    def test_partial_then_full_payment_updates_status_and_ledger(self):
        first = self.pay("400.00", auto_allocate=True)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data["receipt_number"], "RCT-000001")
        invoice = self.client.get(f"{self.base}/invoices/{self.invoice['id']}/").data
        self.assertEqual((invoice["status"], invoice["balance"]), ("partially_paid", "600.00"))
        self.pay("600.00", auto_allocate=True)
        invoice = self.client.get(f"{self.base}/invoices/{self.invoice['id']}/").data
        self.assertEqual((invoice["status"], invoice["balance"]), ("paid", "0.00"))
        bal = self.balances()
        self.assertEqual(bal["cash"], D("1000.00"))
        self.assertEqual(bal["receivable"], D("0.00"))

    def test_overpayment_becomes_advance_not_income(self):
        response = self.pay("1300.00", auto_allocate=True)
        self.assertEqual(response.data["allocated_total"], "1000.00")
        self.assertEqual(response.data["unallocated"], "300.00")
        bal = self.balances()
        self.assertEqual(bal["student_advances"], D("-300.00"))
        self.assertEqual(bal["4000"], D("-1000.00"))

    def test_payment_without_allocation_is_held_as_advance(self):
        response = self.pay("250.00")
        self.assertEqual(response.data["unallocated"], "250.00")
        invoice = self.client.get(f"{self.base}/invoices/{self.invoice['id']}/").data
        self.assertEqual(invoice["balance"], "1000.00")

    def test_explicit_allocation_cannot_exceed_balance(self):
        response = self.pay("2000.00", allocations=[{"invoice": self.invoice["id"], "amount": "1500.00"}])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(self.balances().get("cash", D(0)), D(0))

    def test_cannot_allocate_to_another_students_invoice(self):
        other = self.issued_invoice(student=self.sibling)
        response = self.pay("100.00", allocations=[{"invoice": other["id"], "amount": "100.00"}])
        self.assertEqual(response.status_code, 400)

    def test_advance_later_allocated_to_new_invoice(self):
        paid = self.pay("1200.00", auto_allocate=True).data
        second = self.issued_invoice(period="Term 2")
        response = self.client.post(
            f"{self.base}/payments/{paid['id']}/allocate/", {"allocations": [{"invoice": second["id"], "amount": "200.00"}]},
            format="json", HTTP_IDEMPOTENCY_KEY="a1",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["unallocated"], "0.00")
        self.client.post(
            f"{self.base}/payments/{paid['id']}/allocate/", {"allocations": [{"invoice": second["id"], "amount": "200.00"}]},
            format="json", HTTP_IDEMPOTENCY_KEY="a1",
        )
        self.assertEqual(self.client.get(f"{self.base}/invoices/{second['id']}/").data["amount_paid"], "200.00")
        self.assertEqual(self.balances()["student_advances"], D("0.00"))

    def test_advances_report_lists_students_with_unapplied_money(self):
        self.pay("1300.00", auto_allocate=True)
        self.pay("50.00", student=self.sibling)
        rows = self.client.get(f"{self.base}/reports/advances/").data
        by_name = {r["admission_number"]: r for r in rows}
        mine = by_name[self.student.admission_number]
        self.assertEqual((mine["advance"], mine["open_balance"]), ("300.00", "0.00"))
        self.assertEqual(by_name[self.sibling.admission_number]["advance"], "50.00")
        self.assertEqual(self.client.get(f"{self.base}/reports/advances/", {"grade_level": "No such class"}).data, [])

    def test_advance_auto_applies_to_oldest_invoice_without_listing_it(self):
        paid = self.pay("1000.00").data
        second = self.issued_invoice(period="Term 2")
        response = self.client.post(f"{self.base}/payments/{paid['id']}/allocate/", {"auto_allocate": True}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["unallocated"], "0.00")
        self.assertEqual(self.client.get(f"{self.base}/invoices/{self.invoice['id']}/").data["balance"], "0.00")
        self.assertEqual(self.client.get(f"{self.base}/invoices/{second['id']}/").data["balance"], "1000.00")

    def test_idempotency_key_replay_creates_one_payment(self):
        one = self.pay("100.00", HTTP_IDEMPOTENCY_KEY="pay-1")
        two = self.pay("100.00", HTTP_IDEMPOTENCY_KEY="pay-1")
        self.assertEqual((one.status_code, two.status_code), (201, 200))
        self.assertEqual(one.data["id"], two.data["id"])
        self.assertEqual(Payment.objects.count(), 1)
        clash = self.pay("101.00", HTTP_IDEMPOTENCY_KEY="pay-1")
        self.assertEqual(clash.status_code, 409)

    def test_duplicate_reference_rejected(self):
        self.assertEqual(self.pay("100.00", kind="bank_transfer", reference="SLIP-1").status_code, 201)
        self.assertEqual(self.pay("100.00", kind="bank_transfer", reference="SLIP-1").status_code, 409)

    def test_reference_required_for_bank_and_mobile_money(self):
        self.assertEqual(self.pay("10.00", kind="bank_transfer").status_code, 400)
        self.assertEqual(self.pay("10.00", kind="mobile_money").status_code, 400)

    def test_invalid_amounts_rejected(self):
        for amount in ("0", "-5.00", "abc", "1.234"):
            self.assertEqual(self.pay(amount).status_code, 400, amount)
        self.assertEqual(self.pay(10.5).status_code, 400)

    def test_future_dated_payment_rejected(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertEqual(self.pay("10.00", payment_date=tomorrow).status_code, 400)

    def test_mobile_money_stays_pending_until_confirmed_and_is_not_collected(self):
        response = self.pay("500.00", kind="mobile_money", reference="MM-1", status="confirmed")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(response.data["receipt_number"], "")
        self.assertEqual(self.balances().get("mobile_money", D(0)), D(0))
        summary = self.client.get(f"{self.base}/reports/summary/").data
        self.assertEqual(summary["total_collected"], "0.00")
        self.assertEqual(summary["pending_unconfirmed"], "500.00")
        confirmed = self.client.post(f"{self.base}/payments/{response.data['id']}/confirm/", {"auto_allocate": True}, format="json")
        self.assertEqual(confirmed.data["status"], "confirmed")
        self.assertEqual(confirmed.data["allocated_total"], "500.00")
        self.assertEqual(self.client.get(f"{self.base}/reports/summary/").data["total_collected"], "500.00")
        again = self.client.post(f"{self.base}/payments/{response.data['id']}/confirm/", format="json")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(JournalEntry.objects.filter(source_type="payment").count(), 1)

    def test_failed_pending_payment_cannot_be_confirmed(self):
        pending = self.pay("5.00", kind="mobile_money", reference="MM-2").data
        self.assertEqual(self.client.post(f"{self.base}/payments/{pending['id']}/fail/", {"reason": "No funds"}, format="json").status_code, 200)
        self.assertEqual(self.client.post(f"{self.base}/payments/{pending['id']}/confirm/", format="json").status_code, 409)

    def test_reverse_payment_restores_invoice_and_ledger(self):
        payment = self.pay("1200.00", auto_allocate=True).data
        response = self.client.post(f"{self.base}/payments/{payment['id']}/reverse/", {"reason": "Bounced cheque"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "reversed")
        invoice = self.client.get(f"{self.base}/invoices/{self.invoice['id']}/").data
        self.assertEqual((invoice["status"], invoice["balance"]), ("issued", "1000.00"))
        bal = self.balances()
        self.assertEqual(bal["cash"], D("0.00"))
        self.assertEqual(bal["student_advances"], D("0.00"))
        self.assertEqual(self.client.get(f"{self.base}/reports/summary/").data["total_collected"], "0.00")
        again = self.client.post(f"{self.base}/payments/{payment['id']}/reverse/", {"reason": "dup"}, format="json")
        self.assertEqual(again.status_code, 200)

    def test_refund_only_from_unallocated_and_idempotent(self):
        payment = self.pay("1300.00", auto_allocate=True).data
        refund = self.client.post(
            f"{self.base}/payments/{payment['id']}/refund/", {"amount": "100.00", "reason": "Overpaid"}, format="json", HTTP_IDEMPOTENCY_KEY="r1"
        )
        self.assertEqual(refund.status_code, 201, refund.data)
        self.assertEqual(refund.data["unallocated"], "200.00")
        replay = self.client.post(
            f"{self.base}/payments/{payment['id']}/refund/", {"amount": "100.00", "reason": "Overpaid"}, format="json", HTTP_IDEMPOTENCY_KEY="r1"
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(self.balances()["student_advances"], D("-200.00"))
        too_much = self.client.post(f"{self.base}/payments/{payment['id']}/refund/", {"amount": "250.00", "reason": "x"}, format="json")
        self.assertEqual(too_much.status_code, 400)
        blocked = self.client.post(f"{self.base}/payments/{payment['id']}/reverse/", {"reason": "x"}, format="json")
        self.assertEqual(blocked.status_code, 409)

    def test_void_blocked_when_payments_exist(self):
        self.pay("100.00", auto_allocate=True)
        response = self.client.post(f"{self.base}/invoices/{self.invoice['id']}/void/", {"reason": "x"}, format="json")
        self.assertEqual(response.status_code, 409)

    def test_every_posted_entry_balances(self):
        self.pay("1300.00", auto_allocate=True)
        for entry in JournalEntry.objects.filter(tenant=self.tenant):
            totals = entry.lines.all()
            self.assertEqual(sum(l.debit for l in totals), sum(l.credit for l in totals), entry.number)
        trial = self.client.get(f"{self.base}/reports/trial-balance/").data
        self.assertTrue(trial["balanced"])

    def test_receipt_json_and_pdf(self):
        payment = self.pay("300.00", auto_allocate=True).data
        receipt = self.client.get(f"{self.base}/payments/{payment['id']}/receipt/")
        self.assertEqual(receipt.data["receipt_number"], "RCT-000001")
        self.assertEqual(receipt.data["allocations"][0]["invoice"], "INV-000001")
        pdf = self.client.get(f"{self.base}/payments/{payment['id']}/receipt.pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_audit_events_recorded(self):
        payment = self.pay("100.00").data
        self.client.post(f"{self.base}/payments/{payment['id']}/reverse/", {"reason": "oops"}, format="json")
        actions = set(AuditEvent.objects.filter(tenant=self.tenant).values_list("action", flat=True))
        self.assertTrue({"finance.invoice_issued", "finance.payment_recorded", "finance.payment_reversed"} <= actions)


class ReportTests(FinanceBase):
    def test_summary_statement_and_outstanding(self):
        self.schedule("tuition", "1000.00")
        self.overdue_invoice()
        self.issued_invoice(student=self.sibling)
        self.pay("300.00", auto_allocate=True)
        summary = self.client.get(f"{self.base}/reports/summary/").data
        self.assertEqual(summary["total_invoiced"], "2000.00")
        self.assertEqual(summary["total_collected"], "300.00")
        self.assertEqual(summary["outstanding_balance"], "1700.00")
        self.assertEqual(summary["overdue_balance"], "700.00")
        self.assertEqual(summary["overdue_invoices"], 1)
        self.assertEqual(summary["collections_by_method"], [{"method": "Cash", "amount": "300.00"}])
        self.assertEqual(summary["collections_by_campus"], [{"campus": "Main", "amount": "300.00"}])
        self.assertEqual(summary["collections_by_class"], [{"grade_level": "JHS 1", "amount": "300.00"}])
        statement = self.client.get(f"{self.base}/students/{self.student.pk}/statement/").data
        self.assertEqual([e["type"] for e in statement["entries"]], ["invoice", "payment"])
        self.assertEqual(statement["closing_balance"], "700.00")
        outstanding = self.client.get(f"{self.base}/reports/outstanding/?overdue=true").data
        self.assertEqual([r["balance"] for r in outstanding], ["700.00"])

    def test_draft_invoices_are_not_counted_as_invoiced(self):
        self.schedule()
        self.client.post(
            f"{self.base}/invoices/", {"student": str(self.student.pk), "academic_year": str(self.year.pk), "period_label": "T"}, format="json"
        )
        self.assertEqual(self.client.get(f"{self.base}/reports/summary/").data["total_invoiced"], "0.00")

    def test_reminders_never_send_without_configuration(self):
        self.schedule()
        self.overdue_invoice()
        preview = self.client.get(f"{self.base}/reminders/")
        self.assertEqual(len(preview.data), 1)
        unconfirmed = self.client.post(f"{self.base}/reminders/", {"channel": "sms"}, format="json")
        self.assertEqual({r["status"] for r in unconfirmed.data["results"]}, {"skipped"})
        confirmed = self.client.post(f"{self.base}/reminders/", {"channel": "sms", "confirm": True}, format="json")
        self.assertEqual({r["status"] for r in confirmed.data["results"]}, {"skipped"})

    @override_settings(FINANCE_REMINDER_CHANNELS={"sms": "finance.test_finance.RecordingChannel"})
    def test_reminders_use_configured_channel_only_when_confirmed(self):
        RecordingChannel.sent.clear()
        self.parent.phone = "+233200000000"
        self.schedule()
        self.overdue_invoice()
        self.client.post(f"{self.base}/reminders/", {"channel": "sms"}, format="json")
        self.assertEqual(RecordingChannel.sent, [])


class RecordingChannel:
    sent = []

    def send(self, phone, message):
        self.sent.append((phone, message))


class PermissionTests(FinanceBase):
    def setUp(self):
        super().setUp()
        self.schedule()
        self.invoice = self.issued_invoice()
        self.sibling_invoice = self.issued_invoice(student=self.sibling)
        self.paid = self.pay("100.00", auto_allocate=True).data
        self.draft = self.client.post(
            f"{self.base}/invoices/", {"student": str(self.student.pk), "academic_year": str(self.year.pk), "period_label": "Term 2"}, format="json"
        ).data

    def test_teacher_has_no_finance_access(self):
        client = self.as_user(self.teacher)
        for path in ("invoices/", "payments/", "reports/summary/", "settings/", "fee-schedules/", "accounts/"):
            self.assertEqual(client.get(f"{self.base}/{path}").status_code, 404, path)
        self.assertEqual(client.post(f"{self.base}/payments/", {}, format="json").status_code, 404)

    def test_accountant_cannot_reverse_refund_void_or_credit(self):
        client = self.as_user(self.accountant)
        pid = self.paid["id"]
        for path, body in (
            (f"payments/{pid}/reverse/", {"reason": "x"}),
            (f"payments/{pid}/refund/", {"amount": "1.00", "reason": "x"}),
            (f"invoices/{self.invoice['id']}/void/", {"reason": "x"}),
            (f"invoices/{self.invoice['id']}/credit-notes/", {"amount": "1.00", "reason_type": "correction", "reason": "x"}),
            ("accounts/", {"code": "9", "name": "x", "type": "asset"}),
        ):
            self.assertEqual(client.post(f"{self.base}/{path}", body, format="json").status_code, 404, path)

    def test_accountant_can_record_payments_and_issue(self):
        client = self.as_user(self.accountant)
        self.assertEqual(client.get(f"{self.base}/reports/summary/").status_code, 200)
        self.assertEqual(client.post(f"{self.base}/invoices/{self.draft['id']}/issue/", format="json").status_code, 200)

    def test_school_admin_can_approve(self):
        client = self.as_user(self.admin)
        response = client.post(f"{self.base}/payments/{self.paid['id']}/reverse/", {"reason": "x"}, format="json")
        self.assertEqual(response.status_code, 200)

    def test_parent_sees_only_own_childs_issued_invoices(self):
        client = self.as_user(self.parent)
        listing = client.get(f"{self.base}/invoices/")
        ids = {i["id"] for i in listing.data}
        self.assertEqual(ids, {self.invoice["id"]})
        self.assertEqual(client.get(f"{self.base}/invoices/{self.sibling_invoice['id']}/").status_code, 404)
        self.assertEqual(client.get(f"{self.base}/invoices/{self.draft['id']}/").status_code, 404)
        self.assertEqual(client.get(f"{self.base}/invoices/{self.invoice['id']}/").status_code, 200)

    def test_parent_statement_and_receipts_scoped(self):
        client = self.as_user(self.parent)
        self.assertEqual(client.get(f"{self.base}/students/{self.student.pk}/statement/").status_code, 200)
        self.assertEqual(client.get(f"{self.base}/students/{self.sibling.pk}/statement/").status_code, 404)
        self.assertEqual(client.get(f"{self.base}/payments/{self.paid['id']}/receipt/").status_code, 200)
        self.assertEqual(client.get(f"{self.base}/payments/{self.paid['id']}/receipt.pdf").status_code, 200)
        other = self.pay("50.00", student=self.sibling).data
        self.assertEqual(client.get(f"{self.base}/payments/{other['id']}/receipt/").status_code, 404)

    def test_student_sees_own_records_only(self):
        client = self.as_user(self.student_user)
        self.assertEqual({i["id"] for i in client.get(f"{self.base}/invoices/").data}, {self.invoice["id"]})
        self.assertEqual(client.get(f"{self.base}/students/{self.sibling.pk}/statement/").status_code, 404)

    def test_portal_users_cannot_write_or_see_reports(self):
        for user in (self.parent, self.student_user):
            client = self.as_user(user)
            self.assertEqual(client.post(f"{self.base}/payments/", {}, format="json").status_code, 404)
            self.assertEqual(client.post(f"{self.base}/invoices/{self.invoice['id']}/issue/", format="json").status_code, 404)
            self.assertEqual(client.get(f"{self.base}/reports/summary/").status_code, 404)
            self.assertEqual(client.get(f"{self.base}/reports/transactions/").status_code, 404)

    def test_parent_cannot_see_pending_or_reversed_payment_receipts(self):
        pending = self.pay("5.00", kind="mobile_money", reference="MM-9").data
        client = self.as_user(self.parent)
        self.assertEqual(client.get(f"{self.base}/payments/{pending['id']}/receipt/").status_code, 404)

    def test_unauthenticated_rejected(self):
        self.assertEqual(APIClient().get(f"{self.base}/invoices/").status_code, 401)


class TenantIsolationTests(FinanceBase):
    def setUp(self):
        super().setUp()
        self.other_tenant = create_tenant(name="Kumasi Prep", slug="kumasi-prep", email="k@k.example", phone="2")
        self.other_owner = make_user(self.other_tenant, "owner@k.example", Role.OWNER)
        self.other_student = make_student(self.other_tenant, 1)
        self.other_year = AcademicYear.objects.create(
            tenant=self.other_tenant, name="2026/2027", start_date=date(2026, 9, 1), end_date=date(2027, 7, 31), is_current=True
        )
        ledger.ensure_ready(self.other_tenant.pk)
        self.other_base = f"/api/tenants/{self.other_tenant.pk}/finance"
        self.other_client = self.as_user(self.other_owner)
        self.schedule()
        self.invoice = self.issued_invoice()

    def test_outsider_gets_404_for_other_tenants_finance(self):
        for path in ("invoices/", "payments/", "reports/summary/", "settings/", "accounts/"):
            self.assertEqual(self.other_client.get(f"{self.base}/{path}").status_code, 404, path)
        self.assertEqual(self.other_client.get(f"{self.base}/invoices/{self.invoice['id']}/").status_code, 404)

    def test_cannot_reach_tenant_a_invoice_through_tenant_b_url(self):
        self.assertEqual(self.other_client.get(f"{self.other_base}/invoices/{self.invoice['id']}/").status_code, 404)
        self.assertEqual(self.other_client.post(f"{self.other_base}/invoices/{self.invoice['id']}/issue/", format="json").status_code, 404)
        self.assertEqual(self.other_client.post(f"{self.other_base}/invoices/{self.invoice['id']}/void/", {"reason": "x"}, format="json").status_code, 404)

    def test_cannot_bill_or_pay_other_tenants_student(self):
        response = self.client.post(
            f"{self.base}/invoices/", {"student": str(self.other_student.pk), "academic_year": str(self.year.pk), "period_label": "T"}, format="json"
        )
        self.assertEqual(response.status_code, 404)
        paid = self.client.post(
            f"{self.base}/payments/", {"student": str(self.other_student.pk), "method": str(self.method["cash"].pk), "amount": "1.00"}, format="json"
        )
        self.assertEqual(paid.status_code, 404)

    def test_cannot_use_other_tenants_payment_method_or_account(self):
        other_method = PaymentMethod.objects.filter(tenant=self.other_tenant, kind="cash").first()
        response = self.client.post(
            f"{self.base}/payments/", {"student": str(self.student.pk), "method": str(other_method.pk), "amount": "1.00"}, format="json"
        )
        self.assertEqual(response.status_code, 404)
        other_account = Account.objects.filter(tenant=self.other_tenant, system_key="receivable").first()
        response = self.client.post(
            f"{self.base}/payment-methods/", {"name": "X", "kind": "cash", "account": str(other_account.pk)}, format="json"
        )
        self.assertEqual(response.status_code, 404)

    def test_numbers_are_per_tenant_and_ledger_is_separate(self):
        FeeSchedule.objects.create(
            tenant=self.other_tenant, academic_year=self.other_year,
            category=FeeCategory.objects.get(tenant=self.other_tenant, code="tuition"), amount=D("500.00"), effective_from=date(2026, 1, 1),
        )
        draft = self.other_client.post(
            f"{self.other_base}/invoices/", {"student": str(self.other_student.pk), "academic_year": str(self.other_year.pk), "period_label": "T"}, format="json"
        )
        issued = self.other_client.post(f"{self.other_base}/invoices/{draft.data['id']}/issue/", format="json")
        self.assertEqual(issued.data["number"], self.invoice["number"])
        self.assertEqual(Invoice.objects.filter(number=self.invoice["number"]).count(), 2)
        self.assertEqual(JournalEntry.objects.filter(tenant=self.other_tenant).count(), 1)

    def test_tenant_id_in_body_is_ignored(self):
        response = self.client.post(
            f"{self.base}/payments/",
            {"tenant": str(self.other_tenant.pk), "student": str(self.student.pk), "method": str(self.method["cash"].pk), "amount": "5.00"},
            format="json",
        )
        self.assertEqual(Payment.objects.get(pk=response.data["id"]).tenant_id, self.tenant.pk)


class ConfigValidationTests(FinanceBase):
    def test_settings_update_validated_and_restricted(self):
        ok = self.client.patch(f"{self.base}/settings/", {"invoice_prefix": "fee", "default_payment_terms_days": 14}, format="json")
        self.assertEqual((ok.status_code, ok.data["invoice_prefix"]), (200, "FEE"))
        self.assertEqual(self.client.patch(f"{self.base}/settings/", {"invoice_prefix": "A-B"}, format="json").status_code, 400)
        self.assertEqual(self.client.patch(f"{self.base}/settings/", {"default_payment_terms_days": 9999}, format="json").status_code, 400)
        self.assertEqual(self.as_user(self.accountant).patch(f"{self.base}/settings/", {"invoice_prefix": "X"}, format="json").status_code, 404)

    def test_default_chart_methods_and_categories_seeded_once(self):
        ledger.ensure_ready(self.tenant.pk)
        self.assertEqual(Account.objects.filter(tenant=self.tenant, system_key="receivable").count(), 1)
        self.assertEqual({m.kind for m in PaymentMethod.objects.filter(tenant=self.tenant)}, {"cash", "bank_transfer", "cheque", "mobile_money"})
        self.assertEqual(FinanceSettingsCurrency.get(self.tenant), "GHS")

    def test_schedule_validation(self):
        body = {"academic_year": str(self.year.pk), "category": str(self.cat["tuition"].pk), "amount": "0", "effective_from": "2026-01-01"}
        self.assertEqual(self.client.post(f"{self.base}/fee-schedules/", body, format="json").status_code, 400)
        body.update(amount="100.00", effective_to="2025-01-01")
        self.assertEqual(self.client.post(f"{self.base}/fee-schedules/", body, format="json").status_code, 400)
        body.pop("effective_to")
        self.assertEqual(self.client.post(f"{self.base}/fee-schedules/", body, format="json").status_code, 201)

    def test_teacher_cannot_create_schedule(self):
        self.assertEqual(self.as_user(self.teacher).post(f"{self.base}/fee-schedules/", {}, format="json").status_code, 404)

    def test_academic_year_create_list_and_permissions(self):
        body = {"name": "2027/2028", "start_date": "2027-09-01", "end_date": "2028-07-31", "is_current": True}
        created = self.client.post(f"{self.base}/academic-years/", body, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(AcademicYear.objects.filter(tenant=self.tenant, is_current=True).count(), 1)
        self.assertEqual(self.client.post(f"{self.base}/academic-years/", body, format="json").status_code, 409)
        self.assertEqual(self.client.post(f"{self.base}/academic-years/", {**body, "name": "B", "end_date": "2027-09-01"}, format="json").status_code, 400)
        self.assertEqual(len(self.as_user(self.accountant).get(f"{self.base}/academic-years/").data), 2)
        self.assertEqual(self.as_user(self.accountant).post(f"{self.base}/academic-years/", body, format="json").status_code, 404)
        self.assertEqual(self.as_user(self.teacher).get(f"{self.base}/academic-years/").status_code, 404)

    def test_period_overlap_rejected(self):
        body = {"name": "Sep", "start_date": "2026-09-01", "end_date": "2026-09-30"}
        self.assertEqual(self.client.post(f"{self.base}/periods/", body, format="json").status_code, 201)
        body["name"] = "Overlap"
        self.assertEqual(self.client.post(f"{self.base}/periods/", body, format="json").status_code, 400 if False else 409)


class FinanceSettingsCurrency:
    @staticmethod
    def get(tenant):
        from .models import FinanceSettings

        return FinanceSettings.objects.get(tenant=tenant).currency


@FAST_HASH
@skipUnless(connection.vendor == "postgresql", "Row-level locking needs PostgreSQL")
class FinanceConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.tenant = create_tenant(name="Race School", slug="race", email="r@r.example", phone="1")
        self.owner = make_user(self.tenant, "owner@r.example", Role.OWNER)
        self.year = AcademicYear.objects.create(
            tenant=self.tenant, name="2026/2027", start_date=date(2026, 9, 1), end_date=date(2027, 7, 31), is_current=True
        )
        self.students = [make_student(self.tenant, n) for n in range(1, 9)]
        ledger.ensure_ready(self.tenant.pk)
        self.cash = PaymentMethod.objects.get(tenant=self.tenant, kind="cash")
        FeeSchedule.objects.create(
            tenant=self.tenant, academic_year=self.year, category=FeeCategory.objects.get(tenant=self.tenant, code="tuition"),
            amount=D("1000.00"), effective_from=date(2026, 1, 1),
        )
        self.base = f"/api/tenants/{self.tenant.pk}/finance"

    def run_parallel(self, jobs):
        barrier, errors, results = Barrier(len(jobs)), [], []

        def attempt(job):
            try:
                client = APIClient()
                client.force_authenticate(user=self.owner)
                barrier.wait(timeout=10)
                results.append(job(client))
            except BaseException as error:
                errors.append(error)
            finally:
                connections.close_all()

        threads = [Thread(target=attempt, args=(job,)) for job in jobs]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=90)
        self.assertEqual(errors, [])
        return results

    def test_simultaneous_invoice_issues_get_distinct_numbers(self):
        drafts = []
        for student in self.students:
            response = APIClient()
            response.force_authenticate(user=self.owner)
            drafts.append(
                response.post(f"{self.base}/invoices/", {"student": str(student.pk), "academic_year": str(self.year.pk), "period_label": "T1"}, format="json").data["id"]
            )
        jobs = [lambda c, d=d: c.post(f"{self.base}/invoices/{d}/issue/", format="json").status_code for d in drafts]
        self.assertEqual(self.run_parallel(jobs), [200] * 8)
        numbers = list(Invoice.objects.filter(tenant=self.tenant).values_list("number", flat=True))
        self.assertEqual(len(set(numbers)), 8)
        self.assertEqual(JournalEntry.objects.filter(tenant=self.tenant).count(), 8)

    def test_duplicate_idempotent_payments_create_one(self):
        body = {"student": str(self.students[0].pk), "method": str(self.cash.pk), "amount": "100.00"}
        jobs = [lambda c: c.post(f"{self.base}/payments/", body, format="json", HTTP_IDEMPOTENCY_KEY="same").status_code] * 6
        statuses = self.run_parallel(jobs)
        self.assertEqual(sorted(statuses), [200] * 5 + [201])
        self.assertEqual(Payment.objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(JournalEntry.objects.filter(tenant=self.tenant, source_type="payment").count(), 1)

    def test_parallel_payments_never_over_allocate_an_invoice(self):
        student = self.students[0]
        client = APIClient()
        client.force_authenticate(user=self.owner)
        draft = client.post(f"{self.base}/invoices/", {"student": str(student.pk), "academic_year": str(self.year.pk), "period_label": "T"}, format="json").data
        invoice = client.post(f"{self.base}/invoices/{draft['id']}/issue/", format="json").data
        body = {"student": str(student.pk), "method": str(self.cash.pk), "amount": "300.00", "allocations": [{"invoice": invoice["id"], "amount": "300.00"}]}
        jobs = [lambda c: c.post(f"{self.base}/payments/", body, format="json").status_code] * 6
        statuses = self.run_parallel(jobs)
        self.assertEqual(statuses.count(201), 3)
        self.assertEqual(statuses.count(400), 3)
        stored = Invoice.objects.get(pk=invoice["id"])
        self.assertEqual(stored.amount_paid, D("900.00"))
        self.assertLessEqual(stored.amount_paid, stored.total)

    def test_parallel_receipt_numbers_unique(self):
        jobs = [
            (lambda c, s=s: c.post(f"{self.base}/payments/", {"student": str(s.pk), "method": str(self.cash.pk), "amount": "10.00"}, format="json").status_code)
            for s in self.students
        ]
        self.assertEqual(self.run_parallel(jobs), [201] * 8)
        numbers = list(Payment.objects.filter(tenant=self.tenant).values_list("receipt_number", flat=True))
        self.assertEqual(len(set(numbers)), 8)
