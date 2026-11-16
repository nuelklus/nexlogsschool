"""Read-only finance reports. Every figure states its accounting basis.

* Invoiced: issued invoices (not draft, not voided) by ``issue_date``.
* Collected: *confirmed* payments by ``payment_date``. Pending, failed and reversed
  payments are never counted. Collected money is cash received, not fee income.
* Outstanding: live balance of issued / partially-paid invoices.
* Overdue: outstanding invoices whose ``due_date`` is before today.
"""

from collections import defaultdict
from decimal import Decimal

from django.db.models import DecimalField, Exists, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import (
    ZERO,
    Account,
    CreditNote,
    Invoice,
    JournalLine,
    Payment,
    PaymentAllocation,
    Refund,
)

RECEIVABLE = (Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID)
BILLED = (Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.PAID, Invoice.Status.CREDITED)


def _s(value):
    return str(Decimal(value or 0).quantize(Decimal("0.01")))


def legacy_attribution_reconciliation(tenant_id, *, limit=50, offset=0):
    """Read-only review queue; current student details never overwrite invoice snapshots."""
    invoice_issues = (
        Invoice.objects.filter(tenant_id=tenant_id)
        .exclude(status=Invoice.Status.DRAFT)
        .filter(
            Q(grade_level="")
            | Q(campus__isnull=True)
            | ~Q(student__tenant_id=F("tenant_id"))
            | ~Q(academic_year__tenant_id=F("tenant_id"))
            | ~Q(campus__tenant_id=F("tenant_id"))
        )
        .select_related("student", "academic_year", "campus")
        .order_by("created_at", "id")
    )

    allocation_totals = (
        PaymentAllocation.objects.filter(payment_id=OuterRef("pk"))
        .values("payment_id")
        .annotate(total=Sum("amount"))
        .values("total")[:1]
    )
    inconsistent_allocations = PaymentAllocation.objects.filter(payment_id=OuterRef("pk")).filter(
        ~Q(tenant_id=F("payment__tenant_id"))
        | ~Q(invoice__tenant_id=F("payment__tenant_id"))
        | ~Q(invoice__student_id=F("payment__student_id"))
    )
    unattributed_invoice_allocations = PaymentAllocation.objects.filter(payment_id=OuterRef("pk")).filter(
        Q(invoice__grade_level="")
        | Q(invoice__campus__isnull=True)
        | ~Q(invoice__campus__tenant_id=F("invoice__tenant_id"))
    )
    zero_money = Value(Decimal("0.00"), output_field=DecimalField(max_digits=14, decimal_places=2))
    payment_issues = (
        Payment.objects.filter(tenant_id=tenant_id, status=Payment.Status.CONFIRMED)
        .annotate(
            allocation_sum=Coalesce(
                Subquery(allocation_totals, output_field=DecimalField(max_digits=14, decimal_places=2)),
                zero_money,
            ),
            has_inconsistent_allocation=Exists(inconsistent_allocations),
            has_unattributed_invoice_allocation=Exists(unattributed_invoice_allocations),
        )
        .filter(
            Q(amount__gt=F("allocated_total") + F("refunded_total"))
            | ~Q(allocated_total=F("allocation_sum"))
            | ~Q(student__tenant_id=F("tenant_id"))
            | ~Q(method__tenant_id=F("tenant_id"))
            | Q(has_inconsistent_allocation=True)
            | Q(has_unattributed_invoice_allocation=True)
        )
        .select_related("student", "method")
        .prefetch_related("allocations__invoice__campus")
        .order_by("payment_date", "created_at", "id")
    )

    invoice_rows = []
    for invoice in invoice_issues[offset:offset + limit]:
        issues = []
        student_matches_tenant = invoice.student.tenant_id == invoice.tenant_id
        if not invoice.grade_level:
            issues.append("invoice_missing_class_snapshot")
        if invoice.campus_id is None:
            issues.append("invoice_missing_campus_snapshot")
        elif invoice.campus.tenant_id != invoice.tenant_id:
            issues.append("invoice_campus_tenant_mismatch")
        if not student_matches_tenant:
            issues.append("invoice_student_tenant_mismatch")
        academic_year_matches_tenant = invoice.academic_year.tenant_id == invoice.tenant_id
        if not academic_year_matches_tenant:
            issues.append("invoice_academic_year_tenant_mismatch")
        invoice_rows.append({
            "id": str(invoice.pk),
            "number": invoice.number,
            "status": invoice.status,
            "student": (
                f"{invoice.student.first_name} {invoice.student.last_name}"
                if student_matches_tenant else "Student record belongs to another school"
            ),
            "admission_number": invoice.student.admission_number if student_matches_tenant else "",
            "academic_year": invoice.academic_year.name if academic_year_matches_tenant else "Other school academic year",
            "period": invoice.period_label,
            "issue_date": invoice.issue_date.isoformat() if invoice.issue_date else None,
            "class_snapshot": invoice.grade_level,
            "campus_snapshot": (
                {"id": str(invoice.campus_id), "name": invoice.campus.name}
                if invoice.campus_id and invoice.campus.tenant_id == invoice.tenant_id else None
            ),
            "current_student_class_context": invoice.student.grade_level if student_matches_tenant else "",
            "current_student_campus_context": (
                str(invoice.student.campus_id)
                if student_matches_tenant and invoice.student.campus_id
                and invoice.student.campus.tenant_id == invoice.tenant_id else None
            ),
            "total": _s(invoice.total),
            "amount_paid": _s(invoice.amount_paid),
            "issues": issues,
        })

    payment_rows = []
    for payment in payment_issues[offset:offset + limit]:
        allocations = list(payment.allocations.all())
        allocated_sum = sum((allocation.amount for allocation in allocations), Decimal("0.00"))
        issues = []
        if payment.amount > payment.allocated_total + payment.refunded_total:
            issues.append("payment_has_unattributed_amount")
        if payment.allocated_total != allocated_sum:
            issues.append("payment_allocation_total_mismatch")
        if payment.student.tenant_id != payment.tenant_id:
            issues.append("payment_student_tenant_mismatch")
        if payment.method.tenant_id != payment.tenant_id:
            issues.append("payment_method_tenant_mismatch")
        allocation_rows = []
        for allocation in allocations:
            invoice = allocation.invoice
            invoice_matches_tenant = invoice.tenant_id == payment.tenant_id
            allocation_issues = []
            if allocation.tenant_id != payment.tenant_id or invoice.tenant_id != payment.tenant_id:
                allocation_issues.append("allocation_tenant_mismatch")
            if invoice.student_id != payment.student_id:
                allocation_issues.append("allocation_student_mismatch")
            if not invoice.grade_level:
                allocation_issues.append("invoice_missing_class_snapshot")
            if invoice.campus_id is None:
                allocation_issues.append("invoice_missing_campus_snapshot")
            elif invoice.campus.tenant_id != invoice.tenant_id:
                allocation_issues.append("invoice_campus_tenant_mismatch")
            allocation_rows.append({
                "invoice": str(invoice.pk),
                "invoice_number": invoice.number if invoice_matches_tenant else "",
                "class_snapshot": invoice.grade_level if invoice_matches_tenant else "",
                "campus_snapshot": (
                    {"id": str(invoice.campus_id), "name": invoice.campus.name}
                    if invoice_matches_tenant and invoice.campus_id and invoice.campus.tenant_id == invoice.tenant_id else None
                ),
                "amount": _s(allocation.amount),
                "issues": allocation_issues,
            })
        student_matches_tenant = payment.student.tenant_id == payment.tenant_id
        method_matches_tenant = payment.method.tenant_id == payment.tenant_id
        payment_rows.append({
            "id": str(payment.pk),
            "receipt_number": payment.receipt_number,
            "payment_date": payment.payment_date.isoformat(),
            "student": (
                f"{payment.student.first_name} {payment.student.last_name}"
                if student_matches_tenant else "Student record belongs to another school"
            ),
            "admission_number": payment.student.admission_number if student_matches_tenant else "",
            "method": payment.method.name if method_matches_tenant else "Payment method belongs to another school",
            "amount": _s(payment.amount),
            "allocated_total": _s(payment.allocated_total),
            "allocation_records_total": _s(allocated_sum),
            "refunded_total": _s(payment.refunded_total),
            "unattributed_amount": _s(payment.amount - payment.allocated_total - payment.refunded_total),
            "issues": issues,
            "allocations": allocation_rows,
        })

    return {
        "read_only": True,
        "basis": (
            "Invoice class and campus fields are historical snapshots. Current student class and campus are "
            "shown only as context and are not proposed corrections. Payment attribution is derived from "
            "allocations; unallocated confirmed amounts have no safe class or campus attribution. Every row "
            "is a review signal, not proof of an error or an automatic correction."
        ),
        "pagination": {
            "limit": limit,
            "offset": offset,
            "invoice_total": invoice_issues.count(),
            "payment_total": payment_issues.count(),
        },
        "invoice_issues": invoice_rows,
        "payment_issues": payment_rows,
    }


def _in_range(queryset, field, start, end):
    if start:
        queryset = queryset.filter(**{f"{field}__gte": start})
    if end:
        queryset = queryset.filter(**{f"{field}__lte": end})
    return queryset


def is_overdue(invoice, today=None):
    return invoice.is_receivable and invoice.due_date < (today or timezone.localdate())


def outstanding_invoices(tenant_id, *, student_id=None, overdue_only=False, campus_id=None, grade_level=None, campus_scope=None):
    today = timezone.localdate()
    queryset = Invoice.objects.filter(tenant_id=tenant_id, status__in=RECEIVABLE).select_related("student", "academic_year")
    if student_id:
        queryset = queryset.filter(student_id=student_id)
    if campus_id:
        queryset = queryset.filter(campus_id=campus_id)
    if campus_scope is not None:
        queryset = queryset.filter(campus_id__in=campus_scope)
    if grade_level:
        queryset = queryset.filter(grade_level__iexact=grade_level)
    if overdue_only:
        queryset = queryset.filter(due_date__lt=today)
    return list(queryset.order_by("due_date", "number"))


def student_search(tenant_id, query, limit=10):
    """Active students matching a name or admission number, each with what they owe and any advance held."""
    from django.db.models import Q
    from core.models import Student

    students = Student.objects.filter(tenant_id=tenant_id, status=Student.Status.ACTIVE)
    for word in query.split():
        students = students.filter(
            Q(first_name__icontains=word) | Q(last_name__icontains=word) | Q(admission_number__icontains=word)
        )
    rows = []
    for student in students.order_by("first_name", "last_name")[:limit]:
        owed = sum((i.balance for i in outstanding_invoices(tenant_id, student_id=student.pk)), ZERO)
        advance = sum(
            (p.unallocated for p in Payment.objects.filter(tenant_id=tenant_id, student=student, status=Payment.Status.CONFIRMED)),
            ZERO,
        )
        rows.append(
            {
                "id": str(student.pk), "name": f"{student.first_name} {student.last_name}",
                "admission_number": student.admission_number, "grade_level": student.grade_level,
                "amount_owed": _s(owed), "advance_credit": _s(advance),
            }
        )
    return rows


def outstanding_by_student(tenant_id, *, snapshot_classes=False, **filters):
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
        if snapshot_classes and invoice.grade_level:
            row.setdefault("_classes", set()).add(invoice.grade_level)
        row["balance"] += invoice.balance
        if invoice.due_date < today:
            row["overdue_balance"] += invoice.balance
        row["oldest_due_date"] = min(row["oldest_due_date"], invoice.due_date)
    result = sorted(rows.values(), key=lambda r: r["balance"], reverse=True)
    for row in result:
        if snapshot_classes:
            row["grade_level"] = ", ".join(sorted(row.pop("_classes", set())))
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
        Payment.objects.filter(tenant_id=tenant_id, status=Payment.Status.CONFIRMED)
        .select_related("method")
        .prefetch_related("allocations__invoice__campus"),
        "payment_date", start, end,
    )
    pending = _in_range(Payment.objects.filter(tenant_id=tenant_id, status=Payment.Status.PENDING), "payment_date", start, end)
    open_invoices = outstanding_invoices(tenant_id)

    by_method, by_campus, by_class, by_date = (defaultdict(lambda: ZERO) for _ in range(4))
    total = ZERO
    unallocated = ZERO
    refunded = ZERO
    for payment in confirmed:
        total += payment.amount
        by_method[payment.method.name] += payment.amount
        for allocation in payment.allocations.all():
            invoice = allocation.invoice
            by_campus[invoice.campus.name if invoice.campus_id else "No campus"] += allocation.amount
            by_class[invoice.grade_level or "No class"] += allocation.amount
        unallocated += payment.unallocated
        refunded += payment.refunded_total
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
            "collection_breakdown": (
                "Class and campus use the allocated invoice snapshots and current allocations. "
                "Unallocated and refunded amounts are shown separately; later allocations may change this breakdown."
            ),
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
        "collections_unallocated": _s(unallocated),
        "collections_refunded_unallocated": _s(refunded),
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
