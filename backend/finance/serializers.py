"""Output shapes and strict input parsing for the finance API (money always as strings)."""

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from .ledger import FinanceError, money
from .models import Invoice
from .reports import is_overdue


def s(value):
    return None if value is None else str(value)


def parse_uuid(value, field):
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        raise FinanceError("Enter a valid identifier.", 400, field)


def parse_date(data, field, required=True):
    raw = data.get(field)
    if raw in (None, ""):
        if required:
            raise FinanceError("This field is required.", 400, field)
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        raise FinanceError("Enter a valid date (YYYY-MM-DD).", 400, field)


def parse_money(data, field="amount", required=True):
    if data.get(field) in (None, ""):
        if required:
            raise FinanceError("This field is required.", 400, field)
        return None
    amount = money(data[field], field)
    if amount <= 0:
        raise FinanceError("Amount must be greater than zero.", 400, field)
    return amount


def parse_text(data, field, max_length, required=True):
    value = str(data.get(field) or "").strip()
    if required and not value:
        raise FinanceError("This field is required.", 400, field)
    if len(value) > max_length:
        raise FinanceError(f"Use at most {max_length} characters.", 400, field)
    return value


def parse_allocations(data):
    items = data.get("allocations") or []
    if not isinstance(items, list):
        raise FinanceError("Allocations must be a list.", 400, "allocations")
    parsed = []
    for item in items:
        if not isinstance(item, dict) or not item.get("invoice"):
            raise FinanceError("Each allocation needs an invoice and an amount.", 400, "allocations")
        parsed.append({"invoice": parse_uuid(item["invoice"], "allocations"), "amount": parse_money(item, "amount")})
    return parsed


def account_out(a):
    return {
        "id": str(a.pk),
        "code": a.code,
        "name": a.name,
        "type": a.type,
        "system_key": a.system_key,
        "is_active": a.is_active,
        "parent": str(a.parent_id) if a.parent_id else None,
        "normal_balance": a.normal_balance or ("debit" if a.type in ("asset", "expense") else "credit"),
        "reporting_class": a.reporting_class,
        "is_header": a.is_header,
    }


def parse_quantity(value, field="quantity"):
    if isinstance(value, float):
        raise FinanceError("Send quantities as decimal strings.", 400, field)
    try:
        quantity = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise FinanceError("Enter a valid quantity.", 400, field)
    if not quantity.is_finite() or quantity <= 0 or quantity != quantity.quantize(Decimal("0.001")):
        raise FinanceError("Quantity must be positive with at most 3 decimal places.", 400, field)
    if quantity > Decimal("999999999.999"):
        raise FinanceError("Quantity is too large.", 400, field)
    return quantity


def parse_percentage(value, field="rate"):
    try:
        rate = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise FinanceError("Enter a valid percentage.", 400, field)
    if not rate.is_finite() or rate < 0 or rate > 100 or rate != rate.quantize(Decimal("0.01")):
        raise FinanceError("Percentage must be from 0 to 100 with at most 2 decimal places.", 400, field)
    return rate


def method_out(m):
    return {"id": str(m.pk), "name": m.name, "kind": m.kind, "account": str(m.account_id), "is_active": m.is_active}


def category_out(c):
    return {"id": str(c.pk), "code": c.code, "name": c.name, "income_account": str(c.income_account_id), "is_active": c.is_active}


def schedule_out(f):
    return {
        "id": str(f.pk), "academic_year": str(f.academic_year_id), "category": str(f.category_id),
        "category_name": f.category.name, "grade_level": f.grade_level,
        "campus": str(f.campus_id) if f.campus_id else None, "amount": s(f.amount),
        "effective_from": f.effective_from.isoformat(),
        "effective_to": f.effective_to.isoformat() if f.effective_to else None, "status": f.status,
        "applicability": f.applicability, "billing_basis": f.billing_basis, "unit_label": f.unit_label,
        "condition_type": f.condition_type, "condition_value": f.condition_value,
    }


def adjustment_out(a):
    return {
        "id": str(a.pk), "student": str(a.student_id), "academic_year": str(a.academic_year_id),
        "kind": a.kind, "description": a.description, "reason": a.reason, "amount": s(a.amount), "percent": s(a.percent),
        "category": str(a.category_id) if a.category_id else None, "status": a.status,
        "student_name": f"{a.student.first_name} {a.student.last_name}",
    }


def period_out(p):
    return {"id": str(p.pk), "name": p.name, "start_date": p.start_date.isoformat(), "end_date": p.end_date.isoformat(), "status": p.status}


def settings_out(f):
    return {
        "currency": f.currency, "invoice_prefix": f.invoice_prefix, "receipt_prefix": f.receipt_prefix,
        "credit_note_prefix": f.credit_note_prefix, "default_payment_terms_days": f.default_payment_terms_days,
    }


def invoice_out(i, detail=False, internal=True):
    data = {
        "id": str(i.pk), "number": i.number, "status": i.status, "overdue": is_overdue(i),
        "student": str(i.student_id), "student_name": f"{i.student.first_name} {i.student.last_name}",
        "admission_number": i.student.admission_number, "grade_level": i.grade_level,
        "academic_year": str(i.academic_year_id), "academic_year_name": i.academic_year.name,
        "period_label": i.period_label,
        "issue_date": i.issue_date.isoformat() if i.issue_date else None, "due_date": i.due_date.isoformat(),
        "subtotal": s(i.subtotal), "discount_total": s(i.discount_total), "total": s(i.total),
        "amount_paid": s(i.amount_paid), "credited_total": s(i.credited_total), "balance": s(i.balance),
        "currency": "GHS",
    }
    if detail:
        data["lines"] = [
            {"id": str(l.pk), "kind": l.kind, "description": l.description, "amount": s(l.amount),
             "quantity": l.quantity, "unit_price": s(l.unit_price)}
            for l in i.lines.all().order_by("kind", "description")
        ]
        data["calculation"] = i.calculation if internal else None
        data["payments"] = [
            {"payment": str(a.payment_id), "receipt_number": a.payment.receipt_number, "amount": s(a.amount),
             "date": a.payment.payment_date.isoformat()}
            for a in i.allocations.select_related("payment").order_by("created_at")
        ]
        data["credit_notes"] = [
            {"number": n.number, "amount": s(n.amount), "reason": n.reason, "reason_type": n.reason_type}
            for n in i.credit_notes.order_by("created_at")
        ]
    return data


def payment_out(p):
    return {
        "id": str(p.pk), "student": str(p.student_id), "student_name": f"{p.student.first_name} {p.student.last_name}",
        "method": str(p.method_id), "method_name": p.method.name, "method_kind": p.method.kind,
        "amount": s(p.amount), "payment_date": p.payment_date.isoformat(), "reference": p.reference,
        "payer_name": p.payer_name, "status": p.status, "receipt_number": p.receipt_number,
        "allocated_total": s(p.allocated_total), "refunded_total": s(p.refunded_total), "unallocated": s(p.unallocated),
        "reversal_reason": p.reversal_reason, "currency": "GHS",
    }


def is_draft(invoice):
    return invoice.status == Invoice.Status.DRAFT


def enrollment_out(e):
    return {
        "id": str(e.pk), "student": str(e.student_id), "student_name": f"{e.student.first_name} {e.student.last_name}",
        "admission_number": e.student.admission_number, "grade_level": e.student.grade_level,
        "category": str(e.category_id), "category_name": e.category.name, "status": e.status,
        "start_date": e.start_date.isoformat(), "end_date": e.end_date.isoformat() if e.end_date else None,
        "notes": e.notes,
    }


def event_out(ev):
    return {
        "id": str(ev.pk), "action": ev.action, "detail": ev.detail, "created_at": ev.created_at.isoformat(),
        "actor": ev.actor.full_name or ev.actor.email if ev.actor_id else "",
    }


def usage_out(u):
    return {
        "id": str(u.pk), "student": str(u.student_id), "student_name": f"{u.student.first_name} {u.student.last_name}",
        "category": str(u.category_id), "category_name": u.category.name, "academic_year": str(u.academic_year_id),
        "period_label": u.period_label, "quantity": u.quantity,
    }


def preview_out(row):
    student = row["student"]
    totals = row["totals"]
    return {
        "student": str(student.pk), "student_name": f"{student.first_name} {student.last_name}",
        "admission_number": student.admission_number, "grade_level": student.grade_level,
        "calculation": row["calculation"],
        "totals": {k: s(v) for k, v in totals.items()} if totals else None,
        "issues": row["issues"], "can_generate": row["can_generate"],
    }
