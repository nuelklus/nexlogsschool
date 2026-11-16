"""Invoice generation, issuing, voiding and credit notes. Every function runs atomically."""

from collections import defaultdict
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from academics.models import AcademicTerm, StudentEnrollment
from core.models import AcademicYear, Student

from .ledger import FinanceError, lock_finance, next_number, post_entry, reverse_entry, round_money, system_account
from .models import (
    ZERO,
    CreditNote,
    FeeSchedule,
    Invoice,
    InvoiceLine,
    ServiceEnrollment,
    ServiceUsage,
    StudentAdjustment,
)


def term_start_date(tenant_id, academic_year, period_label):
    base_label = period_label.split(" (supplementary", 1)[0].strip()
    term = AcademicTerm.objects.filter(
        tenant_id=tenant_id,
        academic_year=academic_year,
        name__iexact=base_label,
    ).first()
    return term.start_date if term else academic_year.start_date


def _term_start_enrollment(student, academic_year, period_label):
    base_label = period_label.split(" (supplementary", 1)[0].strip()
    term = AcademicTerm.objects.filter(
        tenant_id=student.tenant_id,
        academic_year=academic_year,
        name__iexact=base_label,
    ).first()
    on_date = term_start_date(student.tenant_id, academic_year, period_label)
    enrollments = list(
        StudentEnrollment.objects.filter(
            tenant_id=student.tenant_id,
            student=student,
            school_class__academic_year=academic_year,
            enrolled_on__lte=on_date,
            status__in=(StudentEnrollment.Status.ACTIVE, StudentEnrollment.Status.TRANSFERRED),
        ).filter(Q(ended_on__isnull=True) | Q(ended_on__gte=on_date))
        .select_related("school_class__class_level", "school_class__campus", "campus")
    )
    if len(enrollments) > 1:
        raise FinanceError(
            f"{student.first_name} {student.last_name} has multiple class enrollments covering "
            f"{term.name if term else academic_year.name}'s start. Resolve the academic history before billing.",
            409,
            "enrollment",
        )
    enrollment = enrollments[0] if enrollments else None
    warnings = []
    if term is None:
        warnings.append(
            "No academic term matches this billing period; the academic-year start was used to resolve enrollment."
        )
    if enrollment is None:
        warnings.append(
            "No class enrollment covers the term start; legacy profile class and campus were used for this preview."
        )
    elif not enrollment.campus_id and (
        enrollment.school_class.campus_id or student.campus_id
    ):
        warnings.append(
            "The enrollment has no campus snapshot; the class-group or legacy profile campus was used for billing."
        )
    return enrollment, on_date, warnings


def _enrollment_campus(student, enrollment):
    if enrollment:
        return enrollment.campus or enrollment.school_class.campus or student.campus
    return student.campus


def applicable_schedules(student, academic_year, on_date, *, enrollment=None):
    """The schedules that bill this student, at most one per fee category.

    When a category has both an all-classes and a class-specific schedule (or a
    campus-specific one), the most specific wins so the student is not billed twice.
    """
    candidates = FeeSchedule.objects.filter(
        tenant_id=student.tenant_id,
        academic_year=academic_year,
        status=FeeSchedule.Status.ACTIVE,
        effective_from__lte=on_date,
        category__is_active=True,
    ).select_related("category", "class_level")
    chosen = {}
    for schedule in candidates:
        if schedule.effective_to and schedule.effective_to < on_date:
            continue
        campus_id = _enrollment_campus(student, enrollment)
        campus_id = campus_id.pk if campus_id else None
        if schedule.campus_id and schedule.campus_id != campus_id:
            continue
        class_name = enrollment.school_class.name if enrollment else student.grade_level
        if schedule.class_level_id:
            if (
                not enrollment
                or schedule.class_level.tenant_id != student.tenant_id
                or enrollment.school_class.class_level_id != schedule.class_level_id
            ):
                continue
            class_match = True
        else:
            class_match = bool(schedule.grade_level)
        if class_match and schedule.grade_level and schedule.grade_level.casefold() != class_name.casefold():
            continue
        score = (int(class_match), int(bool(schedule.campus_id)), schedule.effective_from)
        current = chosen.get(schedule.category_id)
        if current is None or score > current[0]:
            chosen[schedule.category_id] = (score, schedule)
    return [schedule for _, schedule in sorted(chosen.values(), key=lambda item: item[1].category.name)]


def recompute_status(invoice):
    if invoice.status in (Invoice.Status.DRAFT, Invoice.Status.VOIDED):
        return
    settled = invoice.amount_paid + invoice.credited_total
    if settled >= invoice.total:
        invoice.status = (
            Invoice.Status.CREDITED
            if invoice.credited_total > 0 and invoice.amount_paid == 0 and invoice.total > 0
            else Invoice.Status.PAID
        )
    elif settled > 0:
        invoice.status = Invoice.Status.PARTIALLY_PAID
    else:
        invoice.status = Invoice.Status.ISSUED


def _covering_enrollments(student, on_date):
    enrollments = ServiceEnrollment.objects.filter(tenant_id=student.tenant_id, student=student).select_related("category")
    return [e for e in enrollments if e.covers(on_date)]


def _condition_result(schedule, student, year, covered_codes):
    """(met, explanation) for a conditional fee."""
    kind, value = schedule.condition_type, schedule.condition_value
    Condition = FeeSchedule.Condition
    if kind == Condition.GENDER:
        if not student.gender:
            return False, "The student's gender is not recorded."
        return student.gender.casefold() == value.casefold(), f"Applies to {value} students only."
    if kind == Condition.NEW_STUDENT:
        return year.start_date <= student.enrolled_on <= year.end_date, "Applies to students admitted during this academic year."
    if kind == Condition.SERVICE:
        return value.casefold() in covered_codes, f"Requires a subscription to {value}."
    return False, "Unknown condition."


def calculate_billing(student, year, period_label, on_date, *, only_categories=None, skip_categories=()):
    """Work out what one student would be billed. Reads only; the same result is used for previews and drafts.

    Returns the invoice lines (fees, extra charges, discounts), every fee that matched the
    student's class and campus but was left out (with the reason), and any blockers
    (for example per-unit usage that has not been recorded yet).
    """
    Applicability = FeeSchedule.Applicability
    covering = _covering_enrollments(student, on_date)
    covered_ids = {e.category_id for e in covering}
    covered_codes = {e.category.code.casefold() for e in covering}
    exemptions = {
        a.category_id: a
        for a in StudentAdjustment.objects.filter(
            tenant_id=student.tenant_id, student=student, academic_year=year,
            kind=StudentAdjustment.Kind.EXEMPTION, status=StudentAdjustment.Status.APPROVED,
        )
    }
    usage = {
        u.category_id: u.quantity
        for u in ServiceUsage.objects.filter(
            tenant_id=student.tenant_id, student=student, academic_year=year, period_label=period_label
        )
    }
    lines, excluded, blockers = [], [], []

    def leave_out(schedule, code, reason):
        excluded.append(
            {"description": schedule.category.name, "applicability": schedule.applicability, "code": code, "reason": reason}
        )

    enrollment, term_start, warnings = _term_start_enrollment(student, year, period_label)
    class_name = enrollment.school_class.name if enrollment else student.grade_level
    campus = _enrollment_campus(student, enrollment)
    for schedule in applicable_schedules(student, year, on_date, enrollment=enrollment):
        category = schedule.category
        if only_categories is not None and category.pk not in only_categories:
            continue
        if category.pk in skip_categories:
            leave_out(schedule, "already_billed", "Already billed on an earlier invoice for this term.")
            continue
        if schedule.applicability == Applicability.OPTIONAL and category.pk not in covered_ids:
            leave_out(schedule, "not_subscribed", f"Not subscribed to {category.name}.")
            continue
        if schedule.applicability == Applicability.INDIVIDUAL and category.pk not in covered_ids:
            leave_out(schedule, "not_assigned", f"{category.name} is not assigned to this student.")
            continue
        if schedule.applicability == Applicability.CONDITIONAL:
            met, explanation = _condition_result(schedule, student, year, covered_codes)
            if not met:
                leave_out(schedule, "condition_not_met", explanation)
                continue
        if category.pk in exemptions:
            leave_out(schedule, "exempt", f"Exempt: {exemptions[category.pk].reason}")
            continue
        if schedule.billing_basis == FeeSchedule.Basis.PER_UNIT:
            quantity = usage.get(category.pk)
            if quantity is None:
                blockers.append(f"Record {category.name} usage ({schedule.unit_label}) for {period_label or 'this period'} first.")
                continue
            if quantity == 0:
                leave_out(schedule, "no_usage", f"No {schedule.unit_label} recorded for {period_label or 'this period'}.")
                continue
            lines.append(dict(
                kind="fee", category=category, schedule=schedule, description=f"{category.name} ({quantity} x {schedule.unit_label})",
                amount=round_money(schedule.amount * quantity), quantity=quantity, unit_price=schedule.amount,
            ))
        else:
            lines.append(dict(kind="fee", category=category, schedule=schedule, description=category.name, amount=schedule.amount))

    discounts = []
    if only_categories is None:
        approved = StudentAdjustment.objects.filter(
            tenant_id=student.tenant_id, student=student, academic_year=year, status=StudentAdjustment.Status.APPROVED
        ).exclude(kind=StudentAdjustment.Kind.EXEMPTION)
        for adjustment in approved:
            if adjustment.kind == StudentAdjustment.Kind.CHARGE:
                lines.append(dict(kind="charge", category=None, schedule=None, description=adjustment.description, amount=adjustment.amount))
            else:
                discounts.append(adjustment)

    subtotal = sum((line["amount"] for line in lines), ZERO)
    # Percentage scholarships apply to the fees only, never to extra charges.
    fee_subtotal = sum((line["amount"] for line in lines if line["kind"] == "fee"), ZERO)
    remaining = subtotal
    for discount in discounts:
        value = discount.amount if discount.amount is not None else round_money(fee_subtotal * discount.percent / 100)
        value = min(value, remaining)
        if value <= 0:
            continue
        remaining -= value
        lines.append(dict(kind="discount", category=None, schedule=None, description=discount.description, amount=value))
    return {
        "lines": lines, "excluded": excluded, "blockers": blockers,
        "subtotal": subtotal, "discount_total": subtotal - remaining, "total": remaining,
        "enrollment": enrollment, "enrollment_date": term_start, "warnings": warnings,
        "class_name": class_name, "campus": campus,
    }


def plan_invoice(student, year, period_label, on_date, *, supplementary=False, category_ids=None):
    """calculate_billing plus the period label the invoice would carry (supplementary invoices get their own)."""
    period_label = period_label.strip()
    only, skip, label = None, set(), period_label
    if supplementary:
        if not period_label:
            raise FinanceError("Choose the term this supplementary invoice belongs to.", 400, "period_label")
        if not category_ids:
            raise FinanceError("Choose the services to bill.", 400, "categories")
        only = set(category_ids)
        live = Invoice.objects.filter(tenant_id=student.tenant_id, student=student, academic_year=year).exclude(
            status=Invoice.Status.VOIDED
        ).filter(Q(period_label=period_label) | Q(period_label__startswith=f"{period_label} (supplementary"))
        skip = set(
            InvoiceLine.objects.filter(invoice__in=live, kind="fee").values_list("category_id", flat=True)
        )
        existing = set(live.values_list("period_label", flat=True))
        n = 1
        while f"{period_label} (supplementary {n})" in existing:
            n += 1
        label = f"{period_label} (supplementary {n})"
    calc = calculate_billing(student, year, period_label, on_date, only_categories=only, skip_categories=skip)
    calc["period_label"] = label
    return calc


def snapshot(calc, on_date):
    """JSON-safe record of how a bill was calculated; kept on the invoice and shown in previews."""
    def line(l):
        return {
            "description": l["description"], "amount": str(l["amount"]),
            "applicability": l["schedule"].applicability if l.get("schedule") else "",
            "quantity": l.get("quantity"), "unit_price": str(l["unit_price"]) if l.get("unit_price") is not None else None,
        }

    return {
        "calculated_on": on_date.isoformat(), "period_label": calc["period_label"],
        "class_at_term_start": calc["class_name"],
        "academic_enrollment": (
            {
                "id": str(calc["enrollment"].pk),
                "school_class": str(calc["enrollment"].school_class_id),
                "class_level": (
                    str(calc["enrollment"].school_class.class_level_id)
                    if calc["enrollment"].school_class.class_level_id else None
                ),
                "campus": str(calc["campus"].pk) if calc["campus"] else None,
                "enrolled_on": calc["enrollment"].enrolled_on.isoformat(),
                "as_of": calc["enrollment_date"].isoformat(),
            }
            if calc["enrollment"] else None
        ),
        "warnings": calc["warnings"],
        "fees": [line(l) for l in calc["lines"] if l["kind"] == "fee"],
        "charges": [line(l) for l in calc["lines"] if l["kind"] == "charge"],
        "discounts": [line(l) for l in calc["lines"] if l["kind"] == "discount"],
        "excluded": calc["excluded"],
    }


def _live_invoice_exists(tenant_id, student, year, label):
    return Invoice.objects.filter(
        tenant_id=tenant_id, student=student, academic_year=year, period_label=label
    ).exclude(status=Invoice.Status.VOIDED).exists()


def generate_invoice(
    tenant_id, *, student_id, academic_year_id, period_label, due_date, user, supplementary=False, category_ids=None
):
    """Create a draft invoice from the student's eligible fees, service subscriptions and approved adjustments."""
    with transaction.atomic():
        settings = lock_finance(tenant_id)
        student = Student.objects.filter(pk=student_id, tenant_id=tenant_id).first()
        if student is None:
            raise FinanceError("Student not found.", 404, "student")
        if student.status != Student.Status.ACTIVE:
            raise FinanceError("Only active students can be billed.", 400, "student")
        year = AcademicYear.objects.filter(pk=academic_year_id, tenant_id=tenant_id).first()
        if year is None:
            raise FinanceError("Academic year not found.", 404, "academic_year")
        # Billing stays closed for a completed term until it is reopened. The term row stays locked until this
        # transaction ends, so closure (which locks the same row) cannot complete the term mid-invoice, and a
        # closure that committed while we waited is seen here. Order: FinanceSettings, then term; closure never
        # takes FinanceSettings, so this adds no lock-order inversion.
        from academics.closure import is_term_closed

        if is_term_closed(tenant_id, year.pk, period_label, lock=True):
            raise FinanceError("This term has been closed. Reopen it in Academic Management before billing it.", 409, "period_label")
        today = timezone.localdate()
        due_date = due_date or today + timedelta(days=settings.default_payment_terms_days)
        if due_date < today:
            raise FinanceError("The due date cannot be in the past.", 400, "due_date")
        calc = plan_invoice(student, year, period_label, today, supplementary=supplementary, category_ids=category_ids)
        period_label = calc["period_label"]
        if _live_invoice_exists(tenant_id, student, year, period_label):
            raise FinanceError("This student already has an invoice for that academic year and period.", 409)
        if calc["blockers"]:
            raise FinanceError(" ".join(calc["blockers"]), 400, "usage")
        lines = calc["lines"]
        if not any(line["kind"] in ("fee", "charge") for line in lines):
            if calc["excluded"]:
                raise FinanceError("Nothing to bill: every fee that matches this student was excluded. Check the preview for the reasons.", 400)
            raise FinanceError("No active fee schedule applies to this student for that academic year.", 400)
        subtotal, discount_total = calc["subtotal"], calc["discount_total"]

        try:
            with transaction.atomic():
                invoice = Invoice.objects.create(
                    tenant_id=tenant_id,
                    student=student,
                    academic_year=year,
                    campus=calc["campus"],
                    grade_level=calc["class_name"],
                    period_label=period_label,
                    due_date=due_date,
                    subtotal=subtotal,
                    discount_total=discount_total,
                    total=subtotal - discount_total,
                    calculation=snapshot(calc, today),
                    created_by=user,
                )
                InvoiceLine.objects.bulk_create(InvoiceLine(invoice=invoice, **line) for line in lines)
        except IntegrityError:
            raise FinanceError("This student already has an invoice for that academic year and period.", 409)
    return invoice


def preview_invoices(tenant_id, students, year, period_label, *, supplementary=False, category_ids=None):
    """What generating invoices now would produce, per student, without saving anything."""
    today = timezone.localdate()
    rows = []
    for student in students:
        row = {
            "student": student, "issues": [], "warnings": [], "calculation": None,
            "totals": None, "can_generate": False,
        }
        try:
            if student.status != Student.Status.ACTIVE:
                raise FinanceError("Only active students can be billed.")
            calc = plan_invoice(student, year, period_label, today, supplementary=supplementary, category_ids=category_ids)
            row["calculation"] = snapshot(calc, today)
            row["warnings"] = list(calc["warnings"])
            row["totals"] = {"subtotal": calc["subtotal"], "discount_total": calc["discount_total"], "total": calc["total"]}
            row["issues"] = list(calc["blockers"])
            if _live_invoice_exists(tenant_id, student, year, calc["period_label"]):
                row["issues"].append("Already has an invoice for this period.")
            if not any(l["kind"] in ("fee", "charge") for l in calc["lines"]):
                row["issues"].append("Nothing to bill for this student.")
        except FinanceError as error:
            row["issues"].append(error.message)
        row["can_generate"] = not row["issues"]
        rows.append(row)
    return rows


def generate_bulk(tenant_id, students, *, academic_year_id, period_label, due_date, user, supplementary=False, category_ids=None):
    """Draft invoices for many students. Each is its own transaction, so one failure never blocks the rest."""
    results = []
    for student in students:
        try:
            invoice = generate_invoice(
                tenant_id, student_id=student.pk, academic_year_id=academic_year_id, period_label=period_label,
                due_date=due_date, user=user, supplementary=supplementary, category_ids=category_ids,
            )
            results.append({"student": student, "invoice": invoice, "error": ""})
        except FinanceError as error:
            results.append({"student": student, "invoice": None, "error": error.message})
    return results


def issue_batch(tenant_id, invoice_ids, *, user):
    results = []
    for invoice_id in invoice_ids:
        try:
            invoice, issued = issue_invoice(tenant_id, invoice_id, user=user)
            results.append({"invoice": invoice, "issued": issued, "error": ""})
        except FinanceError as error:
            results.append({"invoice": None, "invoice_id": invoice_id, "issued": False, "error": error.message})
    return results


def issue_invoice(tenant_id, invoice_id, *, user):
    """Issue a draft: assign the number and post the receivable. Returns (invoice, issued_now)."""
    with transaction.atomic():
        settings = lock_finance(tenant_id)
        invoice = Invoice.objects.select_for_update().filter(pk=invoice_id, tenant_id=tenant_id).first()
        if invoice is None:
            raise FinanceError("Invoice not found.", 404)
        if invoice.status == Invoice.Status.VOIDED:
            raise FinanceError("A voided invoice cannot be issued.", 409)
        if invoice.status != Invoice.Status.DRAFT:
            return invoice, False

        today = timezone.localdate()
        receivable = system_account(tenant_id, "receivable")
        income = defaultdict(lambda: ZERO)
        adjustments_account = None
        for line in invoice.lines.select_related("category__income_account"):
            if line.kind == InvoiceLine.Kind.DISCOUNT:
                continue
            if line.category_id:
                income[line.category.income_account] += line.amount
            else:
                adjustments_account = adjustments_account or system_account(tenant_id, "fee_adjustments")
                income[adjustments_account] += line.amount
        entry_lines = [(receivable, invoice.total, ZERO, "Student fees receivable")]
        if invoice.discount_total > 0:
            entry_lines.append((system_account(tenant_id, "discounts"), invoice.discount_total, ZERO, "Discounts and scholarships"))
        entry_lines += [(account, ZERO, amount, "Fee income") for account, amount in income.items()]

        number = next_number(settings, "invoice")
        entry, _ = post_entry(
            tenant_id,
            entry_date=today,
            memo=f"Invoice {number}",
            source_type="invoice",
            source_id=invoice.pk,
            idempotency_key=f"invoice-issue:{invoice.pk}",
            lines=entry_lines,
            user=user,
        )
        invoice.number = number
        invoice.status = Invoice.Status.ISSUED
        invoice.issue_date = today
        invoice.journal_entry = entry
        recompute_status(invoice)
        invoice.save()
    return invoice, True


def void_invoice(tenant_id, invoice_id, *, reason, user):
    with transaction.atomic():
        lock_finance(tenant_id)
        invoice = Invoice.objects.select_for_update().filter(pk=invoice_id, tenant_id=tenant_id).first()
        if invoice is None:
            raise FinanceError("Invoice not found.", 404)
        if invoice.status == Invoice.Status.VOIDED:
            return invoice
        if invoice.status != Invoice.Status.DRAFT:
            if invoice.amount_paid > 0 or invoice.credited_total > 0:
                raise FinanceError(
                    "This invoice has payments or credit notes. Reverse the payments or raise a credit note instead.", 409
                )
            reverse_entry(invoice.journal_entry, user=user, memo=f"Void of invoice {invoice.number}")
        invoice.status = Invoice.Status.VOIDED
        invoice.voided_at = timezone.now()
        invoice.void_reason = reason[:240]
        invoice.save(update_fields=("status", "voided_at", "void_reason"))
    return invoice


def create_credit_note(tenant_id, invoice_id, *, amount, reason_type, reason, user, idempotency_key=""):
    """Reduce an invoice's outstanding balance (correction or bad-debt write-off)."""
    with transaction.atomic():
        settings = lock_finance(tenant_id)
        invoice = Invoice.objects.select_for_update().filter(pk=invoice_id, tenant_id=tenant_id).first()
        if invoice is None:
            raise FinanceError("Invoice not found.", 404)
        key = f"credit-note:{invoice.pk}:{idempotency_key}" if idempotency_key else ""
        if key:
            from .models import JournalEntry

            prior = JournalEntry.objects.filter(tenant_id=tenant_id, idempotency_key=key).first()
            if prior is not None:
                return CreditNote.objects.get(journal_entry=prior), False
        if not invoice.is_receivable:
            raise FinanceError("Only an issued invoice with an outstanding balance can be credited.", 409)
        if amount <= 0 or amount > invoice.balance:
            raise FinanceError(f"The credit must be between 0.01 and the outstanding balance of {invoice.balance}.", 400, "amount")
        if reason_type not in CreditNote.Reason.values:
            raise FinanceError("Choose a valid reason type.", 400, "reason_type")

        number = next_number(settings, "credit_note")
        debit_account = system_account(tenant_id, "bad_debt" if reason_type == CreditNote.Reason.BAD_DEBT else "fee_adjustments")
        entry, _ = post_entry(
            tenant_id,
            entry_date=timezone.localdate(),
            memo=f"Credit note {number} for invoice {invoice.number}",
            source_type="credit_note",
            source_id=invoice.pk,
            idempotency_key=key or f"credit-note:{invoice.pk}:{number}",
            lines=[
                (debit_account, amount, ZERO, reason),
                (system_account(tenant_id, "receivable"), ZERO, amount, f"Invoice {invoice.number}"),
            ],
            user=user,
        )
        note = CreditNote.objects.create(
            tenant_id=tenant_id, number=number, invoice=invoice, reason_type=reason_type,
            reason=reason, amount=amount, journal_entry=entry, created_by=user,
        )
        invoice.credited_total += amount
        recompute_status(invoice)
        invoice.save(update_fields=("credited_total", "status"))
    return note, True
