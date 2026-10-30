"""Read-only finance reports. Every figure states its accounting basis.

* Invoiced: issued invoices (not draft, not voided) by ``issue_date``.
* Collected: *confirmed* payments by ``payment_date``. Pending, failed and reversed
  payments are never counted. Collected money is cash received, not fee income.
* Outstanding: live balance of issued / partially-paid invoices.
* Overdue: outstanding invoices whose ``due_date`` is before today.
"""

from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import (
    ZERO,
    Account,
    CreditNote,
    Invoice,
    JournalLine,
    Payment,
    Refund,
)

RECEIVABLE = (Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID)
BILLED = (Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.PAID, Invoice.Status.CREDITED)


def _s(value):
    return str(Decimal(value or 0).quantize(Decimal("0.01")))


def _in_range(queryset, field, start, end):
    if start:
        queryset = queryset.filter(**{f"{field}__gte": start})
    if end:
        queryset = queryset.filter(**{f"{field}__lte": end})
    return queryset


def is_overdue(invoice, today=None):
    return invoice.is_receivable and invoice.due_date < (today or timezone.localdate())


def outstanding_invoices(tenant_id, *, student_id=None, overdue_only=False, campus_id=None, grade_level=None):
    today = timezone.localdate()
    queryset = Invoice.objects.filter(tenant_id=tenant_id, status__in=RECEIVABLE).select_related("student", "academic_year")
    if student_id:
        queryset = queryset.filter(student_id=student_id)
    if campus_id:
        queryset = queryset.filter(campus_id=campus_id)
    if grade_level:
        queryset = queryset.filter(grade_level__iexact=grade_level)
    if overdue_only:
        queryset = queryset.filter(due_date__lt=today)
    return list(queryset.order_by("due_date", "number"))


def outstanding_by_student(tenant_id, **filters):
    today = timezone.localdate()
    rows = {}
    for invoice in outstanding_invoices(tenant_id, **filters):
        row = rows.setdefault(
            invoice.student_id,
            {
                "student": str(invoice.student_id),
                "student_name": f"{invoice.student.first_name} {invoice.student.last_name}",
                "admission_number": invoice.student.admission_number,
                "grade_level": invoice.student.grade_level,
                "invoices": 0,
                "balance": ZERO,
                "overdue_balance": ZERO,
                "oldest_due_date": invoice.due_date,
            },
        )
        row["invoices"] += 1
        row["balance"] += invoice.balance
        if invoice.due_date < today:
            row["overdue_balance"] += invoice.balance
        row["oldest_due_date"] = min(row["oldest_due_date"], invoice.due_date)
    result = sorted(rows.values(), key=lambda r: r["balance"], reverse=True)
    for row in result:
        row["balance"] = _s(row["balance"])
        row["overdue_balance"] = _s(row["overdue_balance"])
        row["oldest_due_date"] = row["oldest_due_date"].isoformat()
    return result


def advances_by_student(tenant_id, campus_id=None, grade_level=None):
    """Students holding unapplied money (confirmed payment amount not yet allocated or refunded)."""
    queryset = Payment.objects.filter(tenant_id=tenant_id, status=Payment.Status.CONFIRMED).select_related("student")
    if campus_id:
        queryset = queryset.filter(student__campus_id=campus_id)
    if grade_level:
        queryset = queryset.filter(student__grade_level__iexact=grade_level)
    rows = {}
    for payment in queryset.order_by("payment_date", "created_at"):
        advance = payment.unallocated
        if advance <= 0:
            continue
        student = payment.student
        row = rows.setdefault(
            student.pk,
            {
                "student": str(student.pk),
                "student_name": f"{student.first_name} {student.last_name}",
                "admission_number": student.admission_number,
                "grade_level": student.grade_level,
                "advance": ZERO,
                "payments": [],
            },
        )
        row["advance"] += advance
        row["payments"].append({"payment": str(payment.pk), "receipt_number": payment.receipt_number, "advance": _s(advance)})
    open_balances = defaultdict(lambda: ZERO)
    for invoice in outstanding_invoices(tenant_id):
        if invoice.student_id in rows:
            open_balances[invoice.student_id] += invoice.balance
    result = sorted(rows.items(), key=lambda item: item[1]["advance"], reverse=True)
    report = []
    for student_id, row in result:
        row["open_balance"] = _s(open_balances[student_id])
        row["advance"] = _s(row["advance"])
        report.append(row)
    return report


def summary(tenant_id, start=None, end=None):
    today = timezone.localdate()
    invoices = _in_range(Invoice.objects.filter(tenant_id=tenant_id, status__in=BILLED), "issue_date", start, end)
    invoiced = invoices.aggregate(t=Sum("total"), d=Sum("discount_total"))
    confirmed = _in_range(
        Payment.objects.filter(tenant_id=tenant_id, status=Payment.Status.CONFIRMED).select_related("student", "method", "student__campus"),
        "payment_date", start, end,
    )
    pending = _in_range(Payment.objects.filter(tenant_id=tenant_id, status=Payment.Status.PENDING), "payment_date", start, end)
    open_invoices = outstanding_invoices(tenant_id)

    by_method, by_campus, by_class, by_date = (defaultdict(lambda: ZERO) for _ in range(4))
    total = ZERO
    for payment in confirmed:
        total += payment.amount
        by_method[payment.method.name] += payment.amount
        by_campus[payment.student.campus.name if payment.student.campus_id else "No campus"] += payment.amount
        by_class[payment.student.grade_level or "No class"] += payment.amount
        by_date[payment.payment_date.isoformat()] += payment.amount

    def rows(mapping, key):
        return [{key: name, "amount": _s(amount)} for name, amount in sorted(mapping.items())]

    advances = sum((p.unallocated for p in Payment.objects.filter(tenant_id=tenant_id, status=Payment.Status.CONFIRMED)), ZERO)
    return {
        "currency": "GHS",
        "period": {"start": start.isoformat() if start else None, "end": end.isoformat() if end else None},
        "basis": {
            "invoiced": "Issued invoices by issue date; drafts and voided invoices excluded.",
            "collected": "Confirmed payments by payment date; pending, failed and reversed excluded.",
            "outstanding": "Balance of issued and partially paid invoices as of today.",
        },
        "total_invoiced": _s(invoiced["t"]),
        "total_discounts": _s(invoiced["d"]),
        "total_collected": _s(total),
        "pending_unconfirmed": _s(pending.aggregate(t=Sum("amount"))["t"]),
        "outstanding_balance": _s(sum((i.balance for i in open_invoices), ZERO)),
        "overdue_balance": _s(sum((i.balance for i in open_invoices if i.due_date < today), ZERO)),
        "overdue_invoices": sum(1 for i in open_invoices if i.due_date < today),
        "student_advances_held": _s(advances),
        "collections_by_method": rows(by_method, "method"),
        "collections_by_campus": rows(by_campus, "campus"),
        "collections_by_class": rows(by_class, "grade_level"),
        "collections_by_date": rows(by_date, "date"),
    }


def statement(tenant_id, student):
    """Chronological account with a running balance (positive = owes the school)."""
    items = []
    for inv in Invoice.objects.filter(tenant_id=tenant_id, student=student, status__in=BILLED):
        items.append((inv.issue_date, inv.created_at, "invoice", inv.number, f"Invoice {inv.number}", inv.total))
    for note in CreditNote.objects.filter(tenant_id=tenant_id, invoice__student=student):
        items.append((note.created_at.date(), note.created_at, "credit_note", note.number, f"Credit note {note.number}: {note.reason}", -note.amount))
    payments = Payment.objects.filter(
        tenant_id=tenant_id, student=student, status__in=(Payment.Status.CONFIRMED, Payment.Status.REVERSED)
    )
    for pay in payments:
        items.append((pay.payment_date, pay.created_at, "payment", pay.receipt_number, f"Payment {pay.receipt_number} ({pay.method.name})", -pay.amount))
        if pay.status == Payment.Status.REVERSED and pay.reversed_at:
            items.append((pay.reversed_at.date(), pay.reversed_at, "reversal", pay.receipt_number, f"Reversal of {pay.receipt_number}", pay.amount))
    for refund in Refund.objects.filter(tenant_id=tenant_id, payment__student=student):
        items.append((refund.refund_date, refund.created_at, "refund", refund.payment.receipt_number, f"Refund on {refund.payment.receipt_number}", refund.amount))
    items.sort(key=lambda row: (row[0], row[1]))
    balance, entries = ZERO, []
    for date, _, kind, reference, description, amount in items:
        balance += amount
        entries.append(
            {
                "date": date.isoformat(), "type": kind, "reference": reference,
                "description": description,
                "debit": _s(amount) if amount > 0 else "0.00",
                "credit": _s(-amount) if amount < 0 else "0.00",
                "balance": _s(balance),
            }
        )
    open_invoices = outstanding_invoices(tenant_id, student_id=student.pk)
    return {
        "currency": "GHS",
        "student": {
            "id": str(student.pk), "name": f"{student.first_name} {student.last_name}",
            "admission_number": student.admission_number, "grade_level": student.grade_level,
        },
        "entries": entries,
        "closing_balance": _s(balance),
        "outstanding_invoices": _s(sum((i.balance for i in open_invoices), ZERO)),
        "advance_credit": _s(sum((p.unallocated for p in payments.filter(status=Payment.Status.CONFIRMED)), ZERO)),
    }


def transactions(tenant_id, start=None, end=None, limit=200):
    queryset = _in_range(
        JournalLine.objects.filter(entry__tenant_id=tenant_id).select_related("entry", "account"),
        "entry__entry_date", start, end,
    ).order_by("-entry__entry_date", "-entry__created_at", "-id")[:limit]
    return [
        {
            "date": line.entry.entry_date.isoformat(), "entry": line.entry.number,
            "memo": line.entry.memo, "source": line.entry.source_type,
            "account": f"{line.account.code} {line.account.name}",
            "debit": _s(line.debit), "credit": _s(line.credit),
        }
        for line in queryset
    ]


def trial_balance(tenant_id):
    rows = []
    totals = {
        row["account_id"]: row
        for row in JournalLine.objects.filter(entry__tenant_id=tenant_id)
        .values("account_id").annotate(d=Sum("debit"), c=Sum("credit"))
    }
    debit_total = credit_total = ZERO
    for account in Account.objects.filter(tenant_id=tenant_id):
        row = totals.get(account.pk)
        if not row:
            continue
        net = (row["d"] or ZERO) - (row["c"] or ZERO)
        debit, credit = (net, ZERO) if net >= 0 else (ZERO, -net)
        debit_total += debit
        credit_total += credit
        rows.append({"account": f"{account.code} {account.name}", "type": account.type, "debit": _s(debit), "credit": _s(credit)})
    return {"rows": rows, "total_debit": _s(debit_total), "total_credit": _s(credit_total), "balanced": debit_total == credit_total}
