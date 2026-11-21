"""Term closure: checklist built from real data, plus the two-stage approval workflow."""

from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from core.models import Membership, Student

from .class_subjects import class_subject_ids
from .models import (
    Assessment,
    AssessmentResult,
    AttendanceRecord,
    StudentEnrollment,
    TermClosure,
    TermClosureApproval,
    TermClosureChecklistItem,
    TermClosureException,
)
from .models import AcademicTerm

S = TermClosure.Status
I = TermClosureChecklistItem.Status
ZERO = Decimal("0.00")
SCHOOL_TZ = ZoneInfo("Africa/Accra")
SAMPLE = 10

OWNER_ADMIN = (Membership.Role.OWNER, Membership.Role.SCHOOL_ADMIN)
HEADTEACHER = (Membership.Role.HEADTEACHER,)
STAFF_VIEW = OWNER_ADMIN + HEADTEACHER
VIEW_ROLES = STAFF_VIEW + (Membership.Role.ACCOUNTANT,)

EDITABLE = (S.IN_PROGRESS, S.RETURNED_FOR_CORRECTION, S.REOPENED)
ACTIVE = (S.IN_PROGRESS, S.SUBMITTED_FOR_REVIEW, S.RETURNED_FOR_CORRECTION, S.PENDING_FINAL_APPROVAL, S.REOPENED)


class ClosureError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.message = message
        self.status = status


def school_today():
    return timezone.now().astimezone(SCHOOL_TZ).date()


# ---------------------------------------------------------------- checklist

def _item(category, code, title, description="", *, required=True, status=I.COMPLETE, count=0, evidence="", details=None):
    return {
        "category": category, "code": code, "title": title, "description": description, "required": required,
        "status": status, "outstanding_count": count, "evidence_summary": evidence, "details": details or {},
    }


def _not_applicable(category, code, title, reason):
    return _item(category, code, title, reason, required=False, status=I.NOT_APPLICABLE, evidence=reason)


def _student_name(student):
    return f"{student.first_name} {student.last_name}".strip()


def _attendance_items(term, enrollments):
    classes = {}
    for enrollment in enrollments:
        classes.setdefault(enrollment.school_class_id, enrollment.school_class)
    if not classes:
        return [
            _not_applicable("attendance", "attendance_recorded", "Attendance recorded", "No students are enrolled in classes this year."),
        ]
    recorded = set(
        AttendanceRecord.objects.filter(
            tenant_id=term.tenant_id, enrollment__school_class_id__in=classes,
            date__gte=term.start_date, date__lte=term.end_date,
        ).values_list("enrollment__school_class_id", flat=True)
    )
    missing = [cls for pk, cls in classes.items() if pk not in recorded]
    names = sorted(cls.name for cls in missing)
    return [
        _item(
            "attendance", "attendance_recorded", "Attendance recorded for every class",
            "Each class with enrolled students has attendance recorded within the term dates.",
            status=I.INCOMPLETE if missing else I.COMPLETE, count=len(missing),
            evidence=f"{len(missing)} of {len(classes)} classes have no attendance records." if missing else f"All {len(classes)} classes have attendance records.",
            details={"classes": names},
        ),
        _not_applicable("attendance", "attendance_reports", "Attendance reports generated", "There is no attendance reporting module yet."),
    ]


def _exam_items(term, enrollments):
    assessments = list(Assessment.objects.filter(tenant_id=term.tenant_id, term=term).select_related("school_class", "subject"))
    if not assessments:
        reason = "No assessments are scheduled in this term."
        return [
            _not_applicable("examinations", "exams_held", "Examinations completed", reason),
            _not_applicable("examinations", "marks_entered", "Subject marks entered", reason),
        ]
    today = school_today()
    pending_exams = [a for a in assessments if a.assessment_date > today]
    by_class = {}
    for enrollment in enrollments:
        by_class.setdefault(enrollment.school_class_id, []).append(enrollment)
    scored = set(
        AssessmentResult.objects.filter(assessment__in=assessments).exclude(
            mark_status__in=(AssessmentResult.Status.MISSING, AssessmentResult.Status.ABSENT,
                             AssessmentResult.Status.NOT_APPLICABLE)
        ).values_list("assessment_id", "enrollment_id")
    )
    missing_total, classes, students = 0, set(), {}
    subjects_by_class = {
        class_id: set(class_subject_ids(term.tenant_id, class_id)) for class_id in by_class
    }
    for assessment in assessments:
        if assessment.assessment_date > today:
            continue
        for enrollment in by_class.get(assessment.school_class_id, []):
            if assessment.subject_id not in subjects_by_class[assessment.school_class_id]:
                continue
            if (assessment.pk, enrollment.pk) not in scored:
                missing_total += 1
                classes.add(assessment.school_class.name)
                students[enrollment.student_id] = _student_name(enrollment.student)
    return [
        _item(
            "examinations", "exams_held", "Examinations completed",
            "No assessment in the term is dated in the future.",
            status=I.INCOMPLETE if pending_exams else I.COMPLETE, count=len(pending_exams),
            evidence=f"{len(pending_exams)} assessments are dated after today." if pending_exams else "All assessments have taken place.",
            details={"classes": sorted({a.school_class.name for a in pending_exams})},
        ),
        _item(
            "examinations", "marks_entered", "Subject marks entered for every student",
            "Every student expected to sit an assessment has a recorded score.",
            status=I.INCOMPLETE if missing_total else I.COMPLETE, count=missing_total,
            evidence=f"{missing_total} scores are missing." if missing_total else "All expected scores are entered.",
            details={"classes": sorted(classes), "students": sorted(students.values())[:SAMPLE]},
        ),
    ]


def _reporting_items():
    reason = "This module has not been set up for the school."
    return [
        _not_applicable("reporting", "report_cards", "Report cards generated", reason),
        _not_applicable("reporting", "teacher_remarks", "Teacher remarks completed", reason),
        _not_applicable("reporting", "headteacher_remarks", "Headteacher remarks completed", reason),
        _not_applicable("reporting", "broadsheets", "Broadsheets generated", reason),
        _not_applicable("examinations", "grading", "Grading completed", "There is no grading module yet."),
    ]


def _progression_items(term, year):
    from core.models import AcademicYear

    items = []
    is_last_term = not AcademicTerm.objects.filter(academic_year=year, start_date__gt=term.start_date).exists()
    next_year = AcademicYear.objects.filter(tenant_id=term.tenant_id, start_date__gt=year.start_date).order_by("start_date").first()
    if is_last_term and next_year:
        active = StudentEnrollment.objects.filter(
            tenant_id=term.tenant_id, school_class__academic_year=year, status=StudentEnrollment.Status.ACTIVE,
            student__status=Student.Status.ACTIVE,
        ).select_related("student")
        placed = set(
            StudentEnrollment.objects.filter(tenant_id=term.tenant_id, school_class__academic_year=next_year).values_list("student_id", flat=True)
        )
        pending = [e for e in active if e.student_id not in placed]
        items.append(_item(
            "progression", "promotion_reviewed", "Promotion decisions reviewed",
            f"Students enrolled this year are placed in {next_year.name}, repeated or graduated.",
            required=False, status=I.INCOMPLETE if pending else I.COMPLETE, count=len(pending),
            evidence=f"{len(pending)} students have no decision for {next_year.name}." if pending else "Every student has been placed.",
            details={"students": sorted(_student_name(e.student) for e in pending)[:SAMPLE]},
        ))
    else:
        items.append(_not_applicable(
            "progression", "promotion_reviewed", "Promotion decisions reviewed",
            "Promotion is reviewed at the end of the last term of a year, once the next year exists.",
        ))
    withdrawn = StudentEnrollment.objects.filter(
        tenant_id=term.tenant_id, school_class__academic_year=year, status=StudentEnrollment.Status.WITHDRAWN
    ).count()
    items.append(_item(
        "progression", "withdrawn_handled", "Withdrawn and transferred students handled",
        "Withdrawn students are recorded as withdrawn.", required=False,
        evidence=f"{withdrawn} withdrawn enrollments this year.",
    ))
    return items


def finance_summary(term):
    from finance.models import Invoice, PaymentAllocation

    invoices = Invoice.objects.filter(tenant_id=term.tenant_id, academic_year_id=term.academic_year_id).filter(
        Q(period_label=term.name) | Q(period_label__startswith=f"{term.name} (supplementary")
    )
    open_invoices = invoices.filter(status__in=("issued", "partially_paid"))
    outstanding = [i for i in open_invoices.only("student_id", "total", "amount_paid", "credited_total") if i.balance > 0]
    drafts = invoices.filter(status="draft").aggregate(total=Sum("total"))
    draft_count = invoices.filter(status="draft").count()
    live = invoices.exclude(status__in=("draft", "voided"))
    collected = live.aggregate(total=Sum("amount_paid"))["total"] or ZERO
    by_method = (
        PaymentAllocation.objects.filter(invoice__in=live, payment__status="confirmed")
        .values("payment__method__name").annotate(total=Sum("amount")).order_by("-total")
    )
    def money(value):
        return str((value or ZERO).quantize(ZERO))

    return {
        "outstanding_invoice_count": len(outstanding),
        "outstanding_total": money(sum((i.balance for i in outstanding), ZERO)),
        "students_with_balances": len({i.student_id for i in outstanding}),
        "draft_invoice_count": draft_count,
        "draft_invoice_total": money(drafts["total"]),
        "invoiced_total": money(live.aggregate(total=Sum("total"))["total"]),
        "collected_total": money(collected),
        "collections_by_method": [{"method": row["payment__method__name"], "total": money(row["total"])} for row in by_method],
        "currency": "GHS",
    }


def _finance_items(term):
    summary = finance_summary(term)
    return [
        _item("finance", "invoices_outstanding", "Outstanding invoices reviewed",
              "Unpaid balances stay on the student account and carry forward.", required=False,
              evidence=f"{summary['outstanding_invoice_count']} invoices, GHS {summary['outstanding_total']} outstanding."),
        _item("finance", "draft_invoices", "Draft invoices identified",
              "Drafts are not receivables until issued.", required=False,
              status=I.INCOMPLETE if summary["draft_invoice_count"] else I.COMPLETE, count=summary["draft_invoice_count"],
              evidence=f"{summary['draft_invoice_count']} drafts worth GHS {summary['draft_invoice_total']}."),
        _item("finance", "collections_summary", "Collections summary displayed", required=False,
              evidence=f"GHS {summary['collected_total']} collected against GHS {summary['invoiced_total']} invoiced."),
        _not_applicable("finance", "reconciliation_review", "Financial reconciliation review recorded",
                        "Reconciliation is handled in Finance and is separate from academic term closure."),
    ]


def compute_items(term):
    year = term.academic_year
    enrollments = list(
        StudentEnrollment.objects.filter(
            tenant_id=term.tenant_id, school_class__academic_year=year, status=StudentEnrollment.Status.ACTIVE,
        ).select_related("student", "school_class").prefetch_related("subjects")
    )
    return (
        _attendance_items(term, enrollments) + _exam_items(term, enrollments) + _reporting_items()
        + _progression_items(term, year) + _finance_items(term)
    )


def sync_items(closure, user=None):
    """Recalculate every checklist item from live data. Never takes statuses from a client."""
    now = timezone.now()
    existing = {item.code: item for item in closure.items.all()}
    approved = {
        exc.checklist_item_id for exc in TermClosureException.objects.filter(
            checklist_item__closure=closure, approval_status=TermClosureException.Decision.APPROVED
        )
    }
    for data in compute_items(closure.term):
        item = existing.get(data["code"]) or TermClosureChecklistItem(closure=closure, code=data["code"])
        status = data["status"]
        if status == I.INCOMPLETE and item.pk and item.pk in approved and data["required"]:
            status = I.EXCEPTION_APPROVED
        item.category, item.title, item.description = data["category"], data["title"], data["description"]
        item.required, item.outstanding_count = data["required"], data["outstanding_count"]
        item.evidence_summary, item.details = data["evidence_summary"], data["details"]
        if item.status != status or not item.pk:
            item.completed_at = now if status in (I.COMPLETE, I.EXCEPTION_APPROVED) else None
        item.status = status
        if user is not None:
            item.reviewed_by = user
        item.save()


def blocking_items(closure):
    return [item for item in closure.items.all() if item.required and item.status == I.INCOMPLETE]


# ----------------------------------------------------------------- workflow

def _record(closure, stage, decision, user, comments, previous):
    TermClosureApproval.objects.create(
        tenant_id=closure.tenant_id, closure=closure, approval_stage=stage, decision=decision,
        approved_by=user, comments=comments, previous_status=previous, new_status=closure.status,
    )


def _locked(tenant_id, term_id):
    term = AcademicTerm.objects.select_for_update().select_related("academic_year").filter(pk=term_id, tenant_id=tenant_id).first()
    if term is None:
        raise ClosureError("Term not found.", 404)
    closure = TermClosure.objects.select_for_update().filter(term=term, status__in=ACTIVE + (S.COMPLETED,)).first()
    return term, closure


def _require_state(closure, allowed, action):
    if closure is None:
        raise ClosureError("Start the term closure first.")
    if closure.status not in allowed:
        raise ClosureError(f"Cannot {action} while the closure is '{closure.status}'.")


def _clean(text, field, *, required=False, limit=500):
    text = (text or "").strip() if isinstance(text, str) or text is None else None
    if text is None:
        raise ClosureError(f"{field} must be text.", 400)
    if required and not text:
        raise ClosureError(f"{field} is required.", 400)
    return text[:limit]


def initialize(tenant_id, term_id, user):
    with transaction.atomic():
        term, closure = _locked(tenant_id, term_id)
        if closure is not None:
            return closure, False
        if term.is_completed:
            closure = TermClosure.objects.create(
                tenant_id=tenant_id, academic_year=term.academic_year, term=term, status=S.COMPLETED,
                initiated_by=user, completed_at=timezone.now(),
            )
            _record(closure, "initialize", "completed_before_workflow", user, "Completed before the approval workflow existed.", S.NOT_STARTED)
        else:
            closure = TermClosure.objects.create(
                tenant_id=tenant_id, academic_year=term.academic_year, term=term, status=S.IN_PROGRESS, initiated_by=user,
            )
            _record(closure, "initialize", "started", user, "", S.NOT_STARTED)
        sync_items(closure, user)
    return closure, True


def refresh(tenant_id, term_id, user):
    with transaction.atomic():
        term, closure = _locked(tenant_id, term_id)
        _require_state(closure, ACTIVE, "refresh the checklist")
        sync_items(closure, user)
    return closure


def request_exception(tenant_id, term_id, user, *, item_code, reason, notes):
    reason = _clean(reason, "A reason", required=True, limit=500)
    notes = _clean(notes, "Notes", limit=2000)
    with transaction.atomic():
        term, closure = _locked(tenant_id, term_id)
        _require_state(closure, ACTIVE, "request an exception")
        sync_items(closure)
        item = closure.items.filter(code=item_code).first()
        if item is None:
            raise ClosureError("Checklist item not found.", 404)
        if not item.required or item.status != I.INCOMPLETE:
            raise ClosureError("Only incomplete required items need an exception.")
        if item.exceptions.filter(approval_status=TermClosureException.Decision.PENDING).exists():
            raise ClosureError("An exception is already waiting for the headteacher for this item.")
        exception = TermClosureException.objects.create(
            tenant_id=tenant_id, checklist_item=item, reason=reason, supporting_notes=notes, requested_by=user,
        )
        _record(closure, "exception", "requested", user, f"{item.code}: {reason}", closure.status)
    return closure, exception


def decide_exception(tenant_id, term_id, exception_id, user, *, approve, comments):
    comments = _clean(comments, "Comments", required=not approve, limit=300)
    with transaction.atomic():
        term, closure = _locked(tenant_id, term_id)
        _require_state(closure, ACTIVE, "decide an exception")
        exception = TermClosureException.objects.select_for_update().filter(
            pk=exception_id, tenant_id=tenant_id, checklist_item__closure=closure
        ).select_related("checklist_item").first()
        if exception is None:
            raise ClosureError("Exception not found.", 404)
        if exception.approval_status != TermClosureException.Decision.PENDING:
            raise ClosureError("This exception has already been decided.")
        exception.approval_status = TermClosureException.Decision.APPROVED if approve else TermClosureException.Decision.REJECTED
        exception.approved_by, exception.approved_at, exception.decision_comments = user, timezone.now(), comments
        exception.save()
        sync_items(closure)
        _record(closure, "exception", "approved" if approve else "rejected", user, f"{exception.checklist_item.code}: {comments}", closure.status)
    return closure, exception


def _incomplete_message(items):
    return "Required checklist items are incomplete: " + "; ".join(f"{i.title} ({i.outstanding_count})" for i in items) + "."


def submit(tenant_id, term_id, user, comments=""):
    comments = _clean(comments, "Comments")
    with transaction.atomic():
        term, closure = _locked(tenant_id, term_id)
        _require_state(closure, EDITABLE, "submit for review")
        sync_items(closure, user)
        blockers = blocking_items(closure)
        if blockers:
            raise ClosureError(_incomplete_message(blockers))
        previous = closure.status
        closure.status, closure.submitted_at = S.SUBMITTED_FOR_REVIEW, timezone.now()
        closure.save()
        _record(closure, "submission", "submitted", user, comments, previous)
    return closure


def academic_decision(tenant_id, term_id, user, *, approve, comments):
    comments = _clean(comments, "Comments", required=not approve)
    with transaction.atomic():
        term, closure = _locked(tenant_id, term_id)
        _require_state(closure, (S.SUBMITTED_FOR_REVIEW,), "decide academic approval")
        previous = closure.status
        if approve:
            sync_items(closure)
            blockers = blocking_items(closure)
            if blockers:
                raise ClosureError(_incomplete_message(blockers) + " Return it for correction or approve an exception.")
            closure.status = S.PENDING_FINAL_APPROVAL
            closure.academic_approved_by, closure.academic_approved_at = user, timezone.now()
        else:
            closure.status = S.RETURNED_FOR_CORRECTION
        closure.save()
        _record(closure, "academic", "approved" if approve else "returned", user, comments, previous)
    return closure


def final_decision(tenant_id, term_id, user, *, decision, comments):
    if decision not in ("approve", "reject", "return"):
        raise ClosureError("Choose approve, reject or return.", 400)
    comments = _clean(comments, "Comments", required=decision != "approve")
    with transaction.atomic():
        term, closure = _locked(tenant_id, term_id)
        _require_state(closure, (S.PENDING_FINAL_APPROVAL,), "give final authorization")
        previous = closure.status
        if decision == "approve":
            if closure.academic_approved_by_id is None:
                raise ClosureError("The headteacher's academic approval is required first.")
            if closure.academic_approved_by_id == user.pk:
                raise ClosureError("Final authorization must come from a different person than the academic approver.", 403)
            if school_today() < term.end_date:
                raise ClosureError(f"This term ends on {term.end_date.isoformat()}. It cannot be closed before then.")
            sync_items(closure)
            blockers = blocking_items(closure)
            if blockers:
                raise ClosureError(_incomplete_message(blockers) + " Return the closure for correction.")
            now = timezone.now()
            closure.status, closure.final_approved_by, closure.final_approved_at, closure.completed_at = S.COMPLETED, user, now, now
            term.is_completed = True
            if term.is_current:
                term.is_current = False
                upcoming = (
                    AcademicTerm.objects.filter(tenant_id=tenant_id, is_completed=False, start_date__gt=term.start_date)
                    .exclude(pk=term.pk).order_by("start_date").first()
                )
                term.save(update_fields=("is_completed", "is_current"))
                if upcoming:
                    upcoming.is_current = True
                    upcoming.save(update_fields=("is_current",))
            else:
                term.save(update_fields=("is_completed",))
        elif decision == "reject":
            closure.status = S.REJECTED
        else:
            closure.status = S.RETURNED_FOR_CORRECTION
        closure.save()
        _record(closure, "final", {"approve": "approved", "reject": "rejected", "return": "returned"}[decision], user, comments, previous)
    return closure


def reopen(tenant_id, term_id, user, reason):
    reason = _clean(reason, "A reason", required=True)
    with transaction.atomic():
        term, closure = _locked(tenant_id, term_id)
        if closure is None or closure.status != S.COMPLETED:
            raise ClosureError("Only a completed term can be reopened.")
        previous = closure.status
        closure.status, closure.reopened_at = S.REOPENED, timezone.now()
        # Approvals stay in the history table; the term must be approved again.
        closure.academic_approved_by = closure.academic_approved_at = None
        closure.final_approved_by = closure.final_approved_at = closure.completed_at = None
        closure.submitted_at = None
        closure.save()
        term.is_completed = False
        term.save(update_fields=("is_completed",))
        sync_items(closure)
        _record(closure, "reopen", "reopened", user, reason, previous)
    return closure


def cancel(tenant_id, term_id, user, reason):
    reason = _clean(reason, "A reason", required=True)
    with transaction.atomic():
        term, closure = _locked(tenant_id, term_id)
        _require_state(closure, ACTIVE, "cancel the closure")
        previous = closure.status
        closure.status = S.CANCELLED
        closure.save()
        _record(closure, "cancel", "cancelled", user, reason, previous)
    return closure


# ------------------------------------------------------------------ output

def overview(term, closure, *, include_students=True):
    history, exceptions = [], []
    if closure is None:
        item_rows = compute_items(term)
        for row in item_rows:
            row["id"] = None
    else:
        history = list(closure.approvals.select_related("approved_by"))
        exceptions = list(TermClosureException.objects.filter(checklist_item__closure=closure).select_related("checklist_item", "requested_by", "approved_by"))
        item_rows = [
            {
                "id": str(i.pk), "category": i.category, "code": i.code, "title": i.title, "description": i.description,
                "required": i.required, "status": i.status, "outstanding_count": i.outstanding_count,
                "evidence_summary": i.evidence_summary, "details": i.details,
                "completed_at": i.completed_at.isoformat() if i.completed_at else None,
            }
            for i in closure.items.all()
        ]
    if not include_students:
        for row in item_rows:
            row["details"] = {key: value for key, value in row["details"].items() if key != "students"}
    required = [r for r in item_rows if r["required"] and r["status"] != I.NOT_APPLICABLE]
    done = [r for r in required if r["status"] in (I.COMPLETE, I.EXCEPTION_APPROVED)]
    name = lambda user: (user.full_name or user.email) if user else ""
    return {
        "term": str(term.pk), "term_name": term.name, "academic_year": str(term.academic_year_id),
        "academic_year_name": term.academic_year.name, "start_date": term.start_date.isoformat(),
        "end_date": term.end_date.isoformat(), "is_completed": term.is_completed,
        "can_close_from": term.end_date.isoformat(), "today": school_today().isoformat(),
        "closure": None if closure is None else {
            "id": str(closure.pk), "status": closure.status,
            "submitted_at": closure.submitted_at.isoformat() if closure.submitted_at else None,
            "academic_approved_by": name(closure.academic_approved_by),
            "academic_approved_at": closure.academic_approved_at.isoformat() if closure.academic_approved_at else None,
            "final_approved_by": name(closure.final_approved_by),
            "final_approved_at": closure.final_approved_at.isoformat() if closure.final_approved_at else None,
            "completed_at": closure.completed_at.isoformat() if closure.completed_at else None,
            "reopened_at": closure.reopened_at.isoformat() if closure.reopened_at else None,
        },
        "status": closure.status if closure else S.NOT_STARTED,
        "progress": {"done": len(done), "required": len(required)},
        "items": item_rows,
        "exceptions": [
            {
                "id": str(e.pk), "item_code": e.checklist_item.code, "item_title": e.checklist_item.title, "reason": e.reason,
                "supporting_notes": e.supporting_notes, "status": e.approval_status, "requested_by": name(e.requested_by),
                "approved_by": name(e.approved_by), "approved_at": e.approved_at.isoformat() if e.approved_at else None,
                "decision_comments": e.decision_comments, "created_at": e.created_at.isoformat(),
            }
            for e in exceptions
        ],
        "finance": finance_summary(term),
        "history": [
            {
                "id": str(h.pk), "stage": h.approval_stage, "decision": h.decision, "user": name(h.approved_by),
                "comments": h.comments, "previous_status": h.previous_status, "new_status": h.new_status,
                "timestamp": h.timestamp.isoformat(),
            }
            for h in history
        ],
    }


def is_term_closed(tenant_id, academic_year_id, period_label, *, lock=False):
    """True when the base term named by a billing period label has been completed.

    With ``lock=True`` (inside ``atomic``) the term row is locked FOR UPDATE of the term itself, the lock
    closure takes, and ``is_completed`` is read from the locked row. The lock is deliberately taken
    without filtering on ``is_completed``: a filter on a stale snapshot would match no row and wait for nothing.
    """
    base = (period_label or "").split(" (supplementary")[0].strip()
    terms = AcademicTerm.objects.filter(tenant_id=tenant_id, academic_year_id=academic_year_id, name=base)
    if lock:
        return any(term.is_completed for term in terms.select_for_update(of=("self",)).order_by("pk"))
    return terms.filter(is_completed=True).exists()
