"""Payments, allocations, reversals and refunds.

Only a *confirmed* payment touches the ledger. Cash, bank and cheque payments are
confirmed by the staff member who records them; mobile-money payments stay
``pending`` until staff confirm them against the provider's statement (a provider
webhook can call ``confirm_payment`` later). The browser can never set the status.
"""

import uuid

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import Student

from .billing import recompute_status
from .ledger import FinanceError, lock_finance, next_number, post_entry, reverse_entry, system_account
from .models import (
    ZERO,
    Invoice,
    JournalEntry,
    Payment,
    PaymentAllocation,
    PaymentMethod,
    Refund,
)


def _lock_invoices(tenant_id, student_id, ids=None):
    queryset = Invoice.objects.select_for_update().filter(
        tenant_id=tenant_id, student_id=student_id, status__in=(Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID)
    )
    if ids is not None:
        queryset = queryset.filter(pk__in=ids)
    return list(queryset.order_by("due_date", "created_at", "pk"))


def _plan_allocations(tenant_id, student_id, available, allocations, auto_allocate):
    """Return [(locked_invoice, amount)] after validating against live balances."""
    plan = []
    if allocations:
        ids = [item["invoice"] for item in allocations]
        if len(set(ids)) != len(ids):
            raise FinanceError("List each invoice only once.", 400, "allocations")
        invoices = {inv.pk: inv for inv in _lock_invoices(tenant_id, student_id, ids)}
        for item in allocations:
            invoice = invoices.get(item["invoice"])
            if invoice is None:
                raise FinanceError("An invoice is not payable or does not belong to this student.", 400, "allocations")
            if item["amount"] <= 0 or item["amount"] > invoice.balance:
                raise FinanceError(f"Invoice {invoice.number} has an outstanding balance of {invoice.balance}.", 400, "allocations")
            plan.append((invoice, item["amount"]))
    elif auto_allocate:
        remaining = available
        for invoice in _lock_invoices(tenant_id, student_id):
            if remaining <= 0:
                break
            portion = min(remaining, invoice.balance)
            if portion > 0:
                plan.append((invoice, portion))
                remaining -= portion
    if sum((amount for _, amount in plan), ZERO) > available:
        raise FinanceError("The allocations exceed the money available.", 400, "allocations")
    return plan


def _apply(plan, payment, entry):
    for invoice, amount in plan:
        PaymentAllocation.objects.create(
            tenant_id=payment.tenant_id, payment=payment, invoice=invoice, amount=amount, journal_entry=entry
        )
        invoice.amount_paid += amount
        recompute_status(invoice)
        invoice.save(update_fields=("amount_paid", "status"))


def _confirm(settings, payment, user, allocations, auto_allocate):
    tenant_id = payment.tenant_id
    plan = _plan_allocations(tenant_id, payment.student_id, payment.amount, allocations, auto_allocate)
    allocated = sum((amount for _, amount in plan), ZERO)
    advance = payment.amount - allocated
    receipt = next_number(settings, "receipt")
    lines = [(payment.method.account, payment.amount, ZERO, f"{payment.method.name} receipt {receipt}")]
    if allocated:
        lines.append((system_account(tenant_id, "receivable"), ZERO, allocated, "Applied to invoices"))
    if advance:
        lines.append((system_account(tenant_id, "student_advances"), ZERO, advance, "Advance / overpayment"))
    entry, _ = post_entry(
        tenant_id,
        entry_date=payment.payment_date,
        memo=f"Receipt {receipt}",
        source_type="payment",
        source_id=payment.pk,
        idempotency_key=f"payment:{payment.pk}",
        lines=lines,
        user=user,
    )
    _apply(plan, payment, entry)
    payment.status = Payment.Status.CONFIRMED
    payment.receipt_number = receipt
    payment.allocated_total = allocated
    payment.journal_entry = entry
    payment.confirmed_by = user
    payment.confirmed_at = timezone.now()
    payment.save()


def record_payment(
    tenant_id, *, student_id, method_id, amount, payment_date, reference="", payer_name="",
    idempotency_key="", allocations=None, auto_allocate=False, user,
):
    """Record a payment. Returns (payment, created); a replayed idempotency key returns the original."""
    with transaction.atomic():
        settings = lock_finance(tenant_id)
        if idempotency_key:
            prior = Payment.objects.filter(tenant_id=tenant_id, idempotency_key=idempotency_key).first()
            if prior is not None:
                same = (
                    str(prior.student_id) == str(student_id)
                    and str(prior.method_id) == str(method_id)
                    and prior.amount == amount
                )
                if not same:
                    raise FinanceError("That idempotency key was already used for a different payment.", 409)
                return prior, False
        student = Student.objects.filter(pk=student_id, tenant_id=tenant_id).first()
        if student is None:
            raise FinanceError("Student not found.", 404, "student")
        method = PaymentMethod.objects.select_related("account").filter(pk=method_id, tenant_id=tenant_id, is_active=True).first()
        if method is None:
            raise FinanceError("Payment method not found.", 404, "method")
        if payment_date > timezone.localdate():
            raise FinanceError("The payment date cannot be in the future.", 400, "payment_date")
        if reference and Payment.objects.filter(tenant_id=tenant_id, method=method, reference=reference).exclude(
            status=Payment.Status.FAILED
        ).exists():
            raise FinanceError("A payment with this reference has already been recorded.", 409, "reference")
        pending = method.kind == PaymentMethod.Kind.MOBILE_MONEY
        if pending and not reference:
            raise FinanceError("Enter the mobile-money transaction ID.", 400, "reference")
        if method.kind in (PaymentMethod.Kind.CHEQUE, PaymentMethod.Kind.BANK_TRANSFER) and not reference:
            raise FinanceError("Enter the cheque number or bank reference.", 400, "reference")
        try:
            with transaction.atomic():
                payment = Payment.objects.create(
                    tenant_id=tenant_id, student=student, method=method, amount=amount,
                    payment_date=payment_date, reference=reference, payer_name=payer_name,
                    idempotency_key=idempotency_key, recorded_by=user,
                )
        except IntegrityError:
            raise FinanceError("A payment with this reference has already been recorded.", 409, "reference")
        if not pending:
            _confirm(settings, payment, user, allocations, auto_allocate)
    return payment, True


def confirm_payment(tenant_id, payment_id, *, user, allocations=None, auto_allocate=False):
    """Confirm a pending (e.g. mobile-money) payment once the money is verified."""
    with transaction.atomic():
        settings = lock_finance(tenant_id)
        payment = (
            Payment.objects.select_for_update(of=("self",)).select_related("method__account")
            .filter(pk=payment_id, tenant_id=tenant_id).first()
        )
        if payment is None:
            raise FinanceError("Payment not found.", 404)
        if payment.status == Payment.Status.CONFIRMED:
            return payment, False
        if payment.status != Payment.Status.PENDING:
            raise FinanceError(f"A {payment.status} payment cannot be confirmed.", 409)
        _confirm(settings, payment, user, allocations, auto_allocate)
    return payment, True


def fail_payment(tenant_id, payment_id, *, reason, user):
    with transaction.atomic():
        lock_finance(tenant_id)
        payment = Payment.objects.select_for_update().filter(pk=payment_id, tenant_id=tenant_id).first()
        if payment is None:
            raise FinanceError("Payment not found.", 404)
        if payment.status != Payment.Status.PENDING:
            raise FinanceError("Only a pending payment can be marked as failed.", 409)
        payment.status = Payment.Status.FAILED
        payment.reversal_reason = reason[:240]
        payment.save(update_fields=("status", "reversal_reason"))
    return payment


def allocate_advance(tenant_id, payment_id, *, allocations, user, idempotency_key="", auto_allocate=False):
    """Apply the unallocated part of a confirmed payment to invoices issued later."""
    with transaction.atomic():
        lock_finance(tenant_id)
        key = f"allocate:{payment_id}:{idempotency_key or uuid.uuid4()}"
        prior = JournalEntry.objects.filter(tenant_id=tenant_id, idempotency_key=key).first()
        payment = Payment.objects.select_for_update().filter(pk=payment_id, tenant_id=tenant_id).first()
        if payment is None:
            raise FinanceError("Payment not found.", 404)
        if prior is not None:
            return payment, False
        if payment.status != Payment.Status.CONFIRMED:
            raise FinanceError("Only a confirmed payment can be allocated.", 409)
        plan = _plan_allocations(tenant_id, payment.student_id, payment.unallocated, allocations, auto_allocate)
        total = sum((amount for _, amount in plan), ZERO)
        if not total:
            raise FinanceError("Choose at least one invoice and amount.", 400, "allocations")
        entry, _ = post_entry(
            tenant_id,
            entry_date=timezone.localdate(),
            memo=f"Advance from receipt {payment.receipt_number} applied",
            source_type="allocation",
            source_id=payment.pk,
            idempotency_key=key,
            lines=[
                (system_account(tenant_id, "student_advances"), total, ZERO, "Advance applied"),
                (system_account(tenant_id, "receivable"), ZERO, total, "Applied to invoices"),
            ],
            user=user,
        )
        _apply(plan, payment, entry)
        payment.allocated_total += total
        payment.save(update_fields=("allocated_total",))
    return payment, True


def reverse_payment(tenant_id, payment_id, *, reason, user):
    """Undo a confirmed payment (bounced cheque, wrong student, error)."""
    with transaction.atomic():
        lock_finance(tenant_id)
        payment = Payment.objects.select_for_update().filter(pk=payment_id, tenant_id=tenant_id).first()
        if payment is None:
            raise FinanceError("Payment not found.", 404)
        if payment.status == Payment.Status.REVERSED:
            return payment, False
        if payment.status != Payment.Status.CONFIRMED:
            raise FinanceError("Only a confirmed payment can be reversed.", 409)
        if payment.refunded_total > 0:
            raise FinanceError("Part of this payment was refunded. A refund cannot be undone in this version.", 409)

        allocations = list(payment.allocations.select_related("invoice").order_by("invoice_id"))
        invoices = {
            inv.pk: inv
            for inv in Invoice.objects.select_for_update().filter(pk__in=[a.invoice_id for a in allocations]).order_by("pk")
        }
        for allocation in allocations:
            invoice = invoices[allocation.invoice_id]
            invoice.amount_paid -= allocation.amount
            if invoice.status in (Invoice.Status.PAID, Invoice.Status.PARTIALLY_PAID, Invoice.Status.CREDITED):
                invoice.status = Invoice.Status.ISSUED
            recompute_status(invoice)
        for invoice in invoices.values():
            invoice.save(update_fields=("amount_paid", "status"))

        entries = {a.journal_entry_id: a.journal_entry for a in allocations}
        entries[payment.journal_entry_id] = payment.journal_entry
        reversal = None
        for entry_id in sorted(entries, key=str):
            reversed_entry, _ = reverse_entry(entries[entry_id], user=user, memo=f"Reversal of receipt {payment.receipt_number}")
            if entry_id == payment.journal_entry_id:
                reversal = reversed_entry
        payment.status = Payment.Status.REVERSED
        payment.reversed_at = timezone.now()
        payment.reversal_reason = reason[:240]
        payment.reversal_entry = reversal
        payment.save()
    return payment, True


def refund_payment(tenant_id, payment_id, *, amount, reason, refund_date, user, idempotency_key=""):
    """Return money from the unallocated part of a confirmed payment."""
    with transaction.atomic():
        lock_finance(tenant_id)
        key = f"refund:{payment_id}:{idempotency_key or uuid.uuid4()}"
        prior = JournalEntry.objects.filter(tenant_id=tenant_id, idempotency_key=key).first()
        if prior is not None:
            return Refund.objects.get(journal_entry=prior), False
        payment = (
            Payment.objects.select_for_update(of=("self",)).select_related("method__account")
            .filter(pk=payment_id, tenant_id=tenant_id).first()
        )
        if payment is None:
            raise FinanceError("Payment not found.", 404)
        if payment.status != Payment.Status.CONFIRMED:
            raise FinanceError("Only a confirmed payment can be refunded.", 409)
        if amount <= 0 or amount > payment.unallocated:
            raise FinanceError(
                f"Only the unallocated balance of {payment.unallocated} can be refunded. "
                "Money applied to an invoice needs the payment reversed or a credit note first.",
                400,
                "amount",
            )
        if refund_date > timezone.localdate():
            raise FinanceError("The refund date cannot be in the future.", 400, "refund_date")
        entry, _ = post_entry(
            tenant_id,
            entry_date=refund_date,
            memo=f"Refund on receipt {payment.receipt_number}",
            source_type="refund",
            source_id=payment.pk,
            idempotency_key=key,
            lines=[
                (system_account(tenant_id, "student_advances"), amount, ZERO, reason),
                (payment.method.account, ZERO, amount, "Refund paid out"),
            ],
            user=user,
        )
        refund = Refund.objects.create(
            tenant_id=tenant_id, payment=payment, amount=amount, reason=reason,
            refund_date=refund_date, journal_entry=entry, created_by=user,
        )
        payment.refunded_total += amount
        payment.save(update_fields=("refunded_total",))
    return refund, True
