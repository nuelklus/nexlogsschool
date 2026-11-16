"""Income that is not a student fee (donations, canteen, rentals ...) and the combined money-received report."""
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .ledger import FinanceError, lock_finance, post_entry, reverse_entry
from .models import Account, IncomeCategory, OtherIncome, Payment, PaymentMethod

ZERO = Decimal("0.00")

DEFAULT_INCOME_CATEGORIES = (
    ("Donations", "4050"),
    ("Grants", "4055"),
    ("Canteen sales", "4100"),
    ("Uniform and books sales", "4110"),
    ("Rentals", "4120"),
    ("Events", "4130"),
    ("Other", "4190"),
)
KIND_LABELS = {
    PaymentMethod.Kind.CASH: "Cash",
    PaymentMethod.Kind.BANK_TRANSFER: "Bank",
    PaymentMethod.Kind.CHEQUE: "Cheque",
    PaymentMethod.Kind.MOBILE_MONEY: "Mobile money",
}


def _s(value):
    return str(Decimal(value or 0).quantize(Decimal("0.01")))


def ensure_categories(tenant_id):
    """Create the default income categories (and their ledger accounts) the first time they are needed."""
    if IncomeCategory.objects.filter(tenant_id=tenant_id).exists():
        return
    with transaction.atomic():
        lock_finance(tenant_id)
        if IncomeCategory.objects.filter(tenant_id=tenant_id).exists():
            return
        for name, code in DEFAULT_INCOME_CATEGORIES:
            account = Account.objects.filter(tenant_id=tenant_id, code=code).first()
            if account is None:
                account = Account.objects.create(
                    tenant_id=tenant_id, code=code, name=name, type=Account.Type.INCOME,
                    normal_balance="credit", reporting_class=Account.Type.INCOME,
                )
            IncomeCategory.objects.create(tenant_id=tenant_id, name=name, income_account=account)


def create_category(tenant_id, *, name):
    with transaction.atomic():
        lock_finance(tenant_id)
        if IncomeCategory.objects.filter(tenant_id=tenant_id, name__iexact=name).exists():
            raise FinanceError("An income category with this name already exists.", 409, "name")
        used = set(Account.objects.filter(tenant_id=tenant_id).values_list("code", flat=True))
        code = next(str(number) for number in range(4200, 4900) if str(number) not in used)
        account = Account.objects.create(
            tenant_id=tenant_id, code=code, name=name, type=Account.Type.INCOME,
            normal_balance="credit", reporting_class=Account.Type.INCOME,
        )
        return IncomeCategory.objects.create(tenant_id=tenant_id, name=name, income_account=account)


def record_income(
    tenant_id, *, category_id, method_id, amount, income_date, payer_name="", reference="", description="",
    idempotency_key="", user,
):
    with transaction.atomic():
        lock_finance(tenant_id)
        if idempotency_key:
            prior = OtherIncome.objects.filter(tenant_id=tenant_id, idempotency_key=idempotency_key).first()
            if prior is not None:
                same = str(prior.category_id) == str(category_id) and str(prior.method_id) == str(method_id) and prior.amount == amount
                if not same:
                    raise FinanceError("That idempotency key was already used for different income.", 409)
                return prior, False
        category = IncomeCategory.objects.select_related("income_account").filter(
            pk=category_id, tenant_id=tenant_id, is_active=True
        ).first()
        if category is None:
            raise FinanceError("Income category not found.", 404, "category")
        method = PaymentMethod.objects.select_related("account").filter(pk=method_id, tenant_id=tenant_id, is_active=True).first()
        if method is None:
            raise FinanceError("Payment method not found.", 404, "method")
        if income_date > timezone.localdate():
            raise FinanceError("The date cannot be in the future.", 400, "income_date")
        if method.kind != PaymentMethod.Kind.CASH and not reference:
            raise FinanceError("Enter the bank, cheque or mobile-money reference.", 400, "reference")
        record = OtherIncome.objects.create(
            tenant_id=tenant_id, category=category, method=method, amount=amount, income_date=income_date,
            payer_name=payer_name, reference=reference, description=description,
            idempotency_key=idempotency_key, recorded_by=user,
        )
        entry, _ = post_entry(
            tenant_id, entry_date=income_date, memo=f"{category.name}: {description or payer_name or 'income'}"[:240],
            source_type="other_income", source_id=record.pk, idempotency_key=f"other_income:{record.pk}", user=user,
            lines=[
                (method.account, amount, ZERO, f"{category.name} via {method.name}"),
                (category.income_account, ZERO, amount, category.name),
            ],
        )
        record.journal_entry = entry
        record.save(update_fields=("journal_entry",))
    return record, True


def reverse_income(tenant_id, income_id, *, reason, user):
    with transaction.atomic():
        lock_finance(tenant_id)
        record = OtherIncome.objects.select_for_update().filter(pk=income_id, tenant_id=tenant_id).first()
        if record is None:
            raise FinanceError("Income record not found.", 404)
        if record.status == OtherIncome.Status.REVERSED:
            return record, False
        entry, _ = reverse_entry(record.journal_entry, user=user, memo=f"Reversal of {record.category.name} income")
        record.status = OtherIncome.Status.REVERSED
        record.reversed_at = timezone.now()
        record.reversal_reason = reason[:240]
        record.reversal_entry = entry
        record.save()
    return record, True


def income_out(record):
    return {
        "id": str(record.pk), "category": str(record.category_id), "category_name": record.category.name,
        "method": str(record.method_id), "method_name": record.method.name, "method_kind": record.method.kind,
        "amount": _s(record.amount), "income_date": record.income_date.isoformat(), "payer_name": record.payer_name,
        "reference": record.reference, "description": record.description, "status": record.status,
        "reversal_reason": record.reversal_reason, "currency": "GHS",
    }


def money_received(tenant_id, *, start=None, end=None, kind=None, method_id=None, source=None, category_id=None,
                   grade_level=None, campus_id=None, status="confirmed", limit=500, campus_scope=None):
    """Every receipt (fee payments and other income) with totals by method and by source.

    ``source`` is "fees" or "other"; ``category_id`` narrows other income to one category.
    Grade and campus filters restrict fee payments to amounts allocated to matching
    invoice snapshots. Unallocated and refunded amounts are never attributed to a class.
    """
    rows = []
    fees_wanted = source in (None, "", "fees") and category_id is None
    other_wanted = source in (None, "", "other") and not (grade_level or campus_id) and campus_scope is None
    pay_status = Payment.Status.PENDING if status == "pending" else Payment.Status.CONFIRMED
    if status == "reversed":
        pay_status = Payment.Status.REVERSED

    if fees_wanted:
        payments = (
            Payment.objects.filter(tenant_id=tenant_id, status=pay_status)
            .select_related("student", "method")
            .prefetch_related("allocations__invoice")
        )
        if start:
            payments = payments.filter(payment_date__gte=start)
        if end:
            payments = payments.filter(payment_date__lte=end)
        if kind:
            payments = payments.filter(method__kind=kind)
        if method_id:
            payments = payments.filter(method_id=method_id)
        for p in payments:
            allocations = list(p.allocations.all())
            selected = [
                allocation for allocation in allocations
                if (not grade_level or allocation.invoice.grade_level.casefold() == grade_level.casefold())
                and (not campus_id or allocation.invoice.campus_id == campus_id)
                and (campus_scope is None or allocation.invoice.campus_id in campus_scope)
            ]
            filtered = bool(grade_level or campus_id) or campus_scope is not None
            amount = sum((item.amount for item in selected), ZERO) if filtered else p.amount
            if not amount:
                continue
            grades = sorted({item.invoice.grade_level or "No class" for item in selected})
            grade = ", ".join(grades) if grades else "Unallocated"
            if not filtered and (p.amount > sum((item.amount for item in selected), ZERO)):
                grade = f"{grade}, unallocated or refunded" if grades else "Unallocated or refunded"
            rows.append({
                "date": p.payment_date.isoformat(), "source": "Student fees", "category": "Student fees",
                "from": f"{p.student.first_name} {p.student.last_name}", "grade_level": grade,
                "method": p.method.name, "kind": p.method.kind, "reference": p.reference or p.receipt_number,
                "amount": amount, "status": p.status,
            })
    if other_wanted and status != "pending":
        records = OtherIncome.objects.filter(tenant_id=tenant_id, status=status).select_related("category", "method")
        if start:
            records = records.filter(income_date__gte=start)
        if end:
            records = records.filter(income_date__lte=end)
        if kind:
            records = records.filter(method__kind=kind)
        if method_id:
            records = records.filter(method_id=method_id)
        if category_id:
            records = records.filter(category_id=category_id)
        for r in records:
            rows.append({
                "date": r.income_date.isoformat(), "source": "Other income", "category": r.category.name,
                "from": r.payer_name or r.description, "grade_level": "",
                "method": r.method.name, "kind": r.method.kind, "reference": r.reference,
                "amount": r.amount, "status": r.status,
            })

    rows.sort(key=lambda row: row["date"], reverse=True)
    by_method, by_kind, by_source = (defaultdict(lambda: ZERO) for _ in range(3))
    total = ZERO
    for row in rows:
        total += row["amount"]
        by_method[row["method"]] += row["amount"]
        by_kind[row["kind"]] += row["amount"]
        by_source[row["category"]] += row["amount"]

    def listing(mapping, key, label=lambda name: name):
        return [{key: label(name), "amount": _s(amount)} for name, amount in sorted(mapping.items())]

    return {
        "currency": "GHS",
        "basis": (
            "Only allocations to invoices in your authorized campuses are shown. Unallocated advances, refunds, "
            "pending payments and other income are excluded; later allocations can change these amounts."
        ) if campus_scope is not None else (
            "Unfiltered totals are gross receipts by payment date. Class and campus filters include only "
            "the matching current invoice allocations; unallocated or refunded amounts are excluded. "
            "Later allocations can change filtered results."
        ),
        "total": _s(total),
        "count": len(rows),
        "by_kind": listing(by_kind, "kind", lambda k: KIND_LABELS.get(k, k)),
        "by_method": listing(by_method, "method"),
        "by_source": listing(by_source, "source"),
        "truncated": len(rows) > limit,
        "rows": [{**row, "amount": _s(row["amount"])} for row in rows[:limit]],
    }
