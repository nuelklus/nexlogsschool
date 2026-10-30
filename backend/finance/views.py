"""Finance API. Tenant and role come from the authenticated membership, never from the body."""

from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from core.enrollment import _audit, _membership
from core.models import AcademicYear, Campus, Membership, Student, StudentGuardian

from . import accounting, billing, payments, payables, receipts, reminders, reports
from . import serializers as out
from .ledger import FinanceError, ensure_ready, lock_finance
from .models import (
    Account,
    AccountingPeriod,
    FeeCategory,
    FeeSchedule,
    FinanceSettings,
    Invoice,
    Payment,
    PaymentMethod,
    ServiceEnrollment,
    ServiceEnrollmentEvent,
    ServiceUsage,
    Expense,
    ExpensePayment,
    OpeningBalanceImport,
    Supplier,
    SupplierBill,
    SupplierPaymentVoucher,
    StudentAdjustment,
)

Role = Membership.Role
APPROVERS = (Role.OWNER, Role.SCHOOL_ADMIN)
FINANCE = APPROVERS + (Role.ACCOUNTANT,)
PORTAL = (Role.PARENT, Role.STUDENT)


def portal_student_ids(user, tenant_id, membership):
    if membership.role == Role.PARENT:
        return set(StudentGuardian.objects.filter(tenant_id=tenant_id, user=user).values_list("student_id", flat=True))
    return set(Student.objects.filter(tenant_id=tenant_id, user=user).values_list("pk", flat=True))


class FinanceView(APIView):
    def handle_exception(self, exc):
        if isinstance(exc, FinanceError):
            body = {"detail": exc.message}
            if exc.field:
                body[exc.field] = [exc.message]
            return Response(body, status=exc.status)
        return super().handle_exception(exc)

    def member(self, request, tenant_id, roles=FINANCE):
        membership = _membership(request.user, tenant_id, roles)
        if membership is None:
            raise NotFound()
        return membership

    def audit(self, request, tenant_id, action, entity, entity_id, **metadata):
        _audit(request, tenant_id, f"finance.{action}", entity, entity_id, metadata)

    @staticmethod
    def key(request):
        return (request.headers.get("Idempotency-Key") or str(request.data.get("idempotency_key") or "")).strip()[:100]

    @staticmethod
    def get_or_404(model, tenant_id, pk, **extra):
        obj = model.objects.filter(pk=pk, tenant_id=tenant_id, **extra).first()
        if obj is None:
            raise NotFound()
        return obj


class SettingsView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        ensure_ready(tenant_id)
        return Response(out.settings_out(FinanceSettings.objects.get(tenant_id=tenant_id)))

    def patch(self, request, tenant_id):
        self.member(request, tenant_id, APPROVERS)
        data = request.data
        with transaction.atomic():
            settings = lock_finance(tenant_id)
            for field in ("invoice_prefix", "receipt_prefix", "credit_note_prefix"):
                if field in data:
                    value = out.parse_text(data, field, 10).upper()
                    if not value.isalnum():
                        raise FinanceError("Use letters and numbers only.", 400, field)
                    setattr(settings, field, value)
            if "default_payment_terms_days" in data:
                try:
                    days = int(data["default_payment_terms_days"])
                except (TypeError, ValueError):
                    raise FinanceError("Enter a whole number of days.", 400, "default_payment_terms_days")
                if not 0 <= days <= 365:
                    raise FinanceError("Use 0 to 365 days.", 400, "default_payment_terms_days")
                settings.default_payment_terms_days = days
            settings.save()
        self.audit(request, tenant_id, "settings_updated", "FinanceSettings", tenant_id)
        return Response(out.settings_out(settings))


class AcademicYearListView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        years = AcademicYear.objects.filter(tenant_id=tenant_id).order_by("-start_date")
        return Response([
            {"id": str(y.pk), "name": y.name, "start_date": y.start_date.isoformat(), "end_date": y.end_date.isoformat(), "is_current": y.is_current}
            for y in years
        ])

    def post(self, request, tenant_id):
        self.member(request, tenant_id, APPROVERS)
        data = request.data
        start, end = out.parse_date(data, "start_date"), out.parse_date(data, "end_date")
        if end <= start:
            raise FinanceError("The end date must be after the start date.", 400, "end_date")
        make_current = data.get("is_current") is True
        try:
            with transaction.atomic():
                if make_current:
                    AcademicYear.objects.filter(tenant_id=tenant_id, is_current=True).update(is_current=False)
                year = AcademicYear.objects.create(
                    tenant_id=tenant_id, name=out.parse_text(data, "name", 120), start_date=start, end_date=end, is_current=make_current
                )
        except IntegrityError:
            raise FinanceError("An academic year with this name already exists.", 409, "name")
        self.audit(request, tenant_id, "academic_year_created", "AcademicYear", year.pk)
        return Response({"id": str(year.pk), "name": year.name, "start_date": start.isoformat(), "end_date": end.isoformat(), "is_current": year.is_current}, status=201)


class AccountListView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        ensure_ready(tenant_id)
        return Response([out.account_out(a) for a in Account.objects.filter(tenant_id=tenant_id)])

    def post(self, request, tenant_id):
        self.member(request, tenant_id, APPROVERS)
        ensure_ready(tenant_id)
        data = request.data
        kind = data.get("type")
        if kind not in Account.Type.values:
            raise FinanceError("Choose a valid account type.", 400, "type")
        parent = None
        if data.get("parent"):
            parent = self.get_or_404(Account, tenant_id, out.parse_uuid(data["parent"], "parent"), is_active=True)
            if not parent.is_header or parent.type != kind:
                raise FinanceError("Choose an active heading account of the same type.", 400, "parent")
        normal_balance = data.get("normal_balance") or ("debit" if kind in (Account.Type.ASSET, Account.Type.EXPENSE) else "credit")
        if normal_balance not in ("debit", "credit"):
            raise FinanceError("Normal balance must be debit or credit.", 400, "normal_balance")
        is_header = data.get("is_header") is True
        if is_header and data.get("system_key"):
            raise FinanceError("System-controlled accounts cannot be headings.", 400, "is_header")
        try:
            with transaction.atomic():
                account = Account.objects.create(
                    tenant_id=tenant_id,
                    parent=parent,
                    code=out.parse_text(data, "code", 20),
                    name=out.parse_text(data, "name", 160),
                    type=kind,
                    normal_balance=normal_balance,
                    reporting_class=out.parse_text(data, "reporting_class", 40, required=False),
                    is_header=is_header,
                )
        except IntegrityError:
            raise FinanceError("An account with this code already exists.", 409, "code")
        self.audit(request, tenant_id, "account_created", "Account", account.pk)
        return Response(out.account_out(account), status=201)


class AccountDetailView(FinanceView):
    def patch(self, request, tenant_id, account_id):
        self.member(request, tenant_id, APPROVERS)
        account = self.get_or_404(Account, tenant_id, account_id)
        data = request.data
        if account.system_key:
            raise FinanceError("System-controlled accounts cannot be changed here.", 409)
        changed = []
        if "code" in data:
            account.code = out.parse_text(data, "code", 20)
            changed.append("code")
        if "name" in data:
            account.name = out.parse_text(data, "name", 160)
            changed.append("name")
        if "type" in data:
            if data["type"] not in Account.Type.values:
                raise FinanceError("Choose a valid account type.", 400, "type")
            if account.journal_lines.exists() or account.fee_categories.exists():
                raise FinanceError("Accounts with posted activity or fee mappings cannot be reclassified.", 409, "type")
            account.type = data["type"]
            changed.append("type")
        if "normal_balance" in data:
            if data["normal_balance"] not in ("debit", "credit"):
                raise FinanceError("Normal balance must be debit or credit.", 400, "normal_balance")
            account.normal_balance = data["normal_balance"]
            changed.append("normal_balance")
        if "reporting_class" in data:
            account.reporting_class = out.parse_text(data, "reporting_class", 40, required=False)
            changed.append("reporting_class")
        if "is_header" in data:
            if not isinstance(data["is_header"], bool) or (data["is_header"] and account.journal_lines.exists()):
                raise FinanceError("Only accounts without posted entries can become headings.", 400, "is_header")
            account.is_header = data["is_header"]
            changed.append("is_header")
        if "is_active" in data:
            if not isinstance(data["is_active"], bool):
                raise FinanceError("is_active must be true or false.", 400, "is_active")
            account.is_active = data["is_active"]
            changed.append("is_active")
        if "parent" in data:
            parent = self.get_or_404(Account, tenant_id, out.parse_uuid(data["parent"], "parent"), is_active=True) if data["parent"] else None
            if parent and (parent.pk == account.pk or not parent.is_header or parent.type != account.type):
                raise FinanceError("Choose a different heading account of the same type.", 400, "parent")
            account.parent = parent
            changed.append("parent")
        try:
            account.save(update_fields=tuple(set(changed))) if changed else None
        except IntegrityError:
            raise FinanceError("An account with this code already exists.", 409, "code")
        self.audit(request, tenant_id, "account_updated", "Account", account.pk, fields=changed)
        return Response(out.account_out(account))


class PaymentMethodListView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        ensure_ready(tenant_id)
        return Response([out.method_out(m) for m in PaymentMethod.objects.filter(tenant_id=tenant_id)])

    def post(self, request, tenant_id):
        self.member(request, tenant_id, APPROVERS)
        ensure_ready(tenant_id)
        data = request.data
        if data.get("kind") not in PaymentMethod.Kind.values:
            raise FinanceError("Choose a valid kind.", 400, "kind")
        account = self.get_or_404(Account, tenant_id, out.parse_uuid(data.get("account"), "account"), is_active=True)
        if account.type != Account.Type.ASSET:
            raise FinanceError("Payments must land in an asset account.", 400, "account")
        try:
            with transaction.atomic():
                method = PaymentMethod.objects.create(
                    tenant_id=tenant_id, name=out.parse_text(data, "name", 80), kind=data["kind"], account=account
                )
        except IntegrityError:
            raise FinanceError("A payment method with this name already exists.", 409, "name")
        self.audit(request, tenant_id, "payment_method_created", "PaymentMethod", method.pk)
        return Response(out.method_out(method), status=201)


class PeriodListView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        return Response([out.period_out(p) for p in AccountingPeriod.objects.filter(tenant_id=tenant_id)])

    def post(self, request, tenant_id):
        self.member(request, tenant_id, APPROVERS)
        data = request.data
        start, end = out.parse_date(data, "start_date"), out.parse_date(data, "end_date")
        if end < start:
            raise FinanceError("The end date cannot be before the start date.", 400, "end_date")
        if AccountingPeriod.objects.filter(tenant_id=tenant_id, start_date__lte=end, end_date__gte=start).exists():
            raise FinanceError("This overlaps an existing period.", 409, "start_date")
        try:
            with transaction.atomic():
                period = AccountingPeriod.objects.create(tenant_id=tenant_id, name=out.parse_text(data, "name", 80), start_date=start, end_date=end)
        except IntegrityError:
            raise FinanceError("A period with this name already exists.", 409, "name")
        self.audit(request, tenant_id, "period_created", "AccountingPeriod", period.pk)
        return Response(out.period_out(period), status=201)


class PeriodDetailView(FinanceView):
    def patch(self, request, tenant_id, period_id):
        self.member(request, tenant_id, APPROVERS)
        period = self.get_or_404(AccountingPeriod, tenant_id, period_id)
        new = request.data.get("status")
        if new not in AccountingPeriod.Status.values:
            raise FinanceError("Status must be open or closed.", 400, "status")
        period.status = new
        period.save(update_fields=("status",))
        self.audit(request, tenant_id, f"period_{new}", "AccountingPeriod", period.pk)
        return Response(out.period_out(period))


class FeeCategoryListView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        ensure_ready(tenant_id)
        return Response([out.category_out(c) for c in FeeCategory.objects.filter(tenant_id=tenant_id)])

    def post(self, request, tenant_id):
        self.member(request, tenant_id, FINANCE)
        ensure_ready(tenant_id)
        data = request.data
        account = self.get_or_404(Account, tenant_id, out.parse_uuid(data.get("income_account"), "income_account"), is_active=True)
        if account.type != Account.Type.INCOME:
            raise FinanceError("Choose an income account.", 400, "income_account")
        try:
            with transaction.atomic():
                category = FeeCategory.objects.create(
                    tenant_id=tenant_id, code=out.parse_text(data, "code", 30).lower(),
                    name=out.parse_text(data, "name", 100), income_account=account,
                )
        except IntegrityError:
            raise FinanceError("A fee category with this code already exists.", 409, "code")
        self.audit(request, tenant_id, "fee_category_created", "FeeCategory", category.pk)
        return Response(out.category_out(category), status=201)


def _schedule_fields(request, tenant_id, view, data):
    year = view.get_or_404(AcademicYear, tenant_id, out.parse_uuid(data.get("academic_year"), "academic_year"))
    category = view.get_or_404(FeeCategory, tenant_id, out.parse_uuid(data.get("category"), "category"), is_active=True)
    campus = None
    if data.get("campus"):
        campus = view.get_or_404(Campus, tenant_id, out.parse_uuid(data["campus"], "campus"))
    start = out.parse_date(data, "effective_from")
    end = out.parse_date(data, "effective_to", required=False)
    if end and end < start:
        raise FinanceError("The end date cannot be before the start date.", 400, "effective_to")
    rules = _applicability_fields(tenant_id, data)
    return {
        "academic_year": year, "category": category, "campus": campus,
        "grade_level": out.parse_text(data, "grade_level", 40, required=False),
        "amount": out.parse_money(data), "effective_from": start, "effective_to": end, **rules,
    }


def _is_enrollable(tenant_id, category):
    """Only services with an optional or individually assigned fee can have subscriptions."""
    return FeeSchedule.objects.filter(
        tenant_id=tenant_id, category=category,
        applicability__in=(FeeSchedule.Applicability.OPTIONAL, FeeSchedule.Applicability.INDIVIDUAL),
    ).exists()


def _applicability_fields(tenant_id, data):
    Applicability, Basis, Condition = FeeSchedule.Applicability, FeeSchedule.Basis, FeeSchedule.Condition
    applicability = data.get("applicability") or Applicability.COMPULSORY
    if applicability not in Applicability.values:
        raise FinanceError("Choose compulsory, optional, conditional or individual.", 400, "applicability")
    basis = data.get("billing_basis") or Basis.FIXED
    if basis not in Basis.values:
        raise FinanceError("Choose fixed or per-unit billing.", 400, "billing_basis")
    unit_label = out.parse_text(data, "unit_label", 30, required=False) if basis == Basis.PER_UNIT else ""
    if basis == Basis.PER_UNIT and not unit_label:
        raise FinanceError("Say what one unit is, for example 'meal'.", 400, "unit_label")
    condition_type, condition_value = "", ""
    if applicability == Applicability.CONDITIONAL:
        condition_type = data.get("condition_type") or ""
        if condition_type not in (Condition.GENDER, Condition.NEW_STUDENT, Condition.SERVICE):
            raise FinanceError("Choose the condition this fee depends on.", 400, "condition_type")
        if condition_type != Condition.NEW_STUDENT:
            condition_value = out.parse_text(data, "condition_value", 60)
        if condition_type == Condition.SERVICE:
            required = FeeCategory.objects.filter(tenant_id=tenant_id, code__iexact=condition_value).first()
            if required is None or not _is_enrollable(tenant_id, required):
                raise FinanceError("That service does not exist or has no optional fee. Create its optional fee first.", 400, "condition_value")
    return {
        "applicability": applicability, "billing_basis": basis, "unit_label": unit_label,
        "condition_type": condition_type, "condition_value": condition_value,
    }


class FeeScheduleListView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        queryset = FeeSchedule.objects.filter(tenant_id=tenant_id).select_related("category")
        if request.query_params.get("academic_year"):
            queryset = queryset.filter(academic_year_id=out.parse_uuid(request.query_params["academic_year"], "academic_year"))
        return Response([out.schedule_out(f) for f in queryset])

    def post(self, request, tenant_id):
        self.member(request, tenant_id, FINANCE)
        fields = _schedule_fields(request, tenant_id, self, request.data)
        schedule = FeeSchedule.objects.create(tenant_id=tenant_id, created_by=request.user, **fields)
        self.audit(request, tenant_id, "fee_schedule_created", "FeeSchedule", schedule.pk, amount=str(schedule.amount))
        return Response(out.schedule_out(schedule), status=201)


class FeeScheduleDetailView(FinanceView):
    """Edits only affect invoices generated afterwards; issued invoices keep their snapshot."""

    def patch(self, request, tenant_id, schedule_id):
        self.member(request, tenant_id, FINANCE)
        schedule = self.get_or_404(FeeSchedule, tenant_id, schedule_id)
        data = request.data
        before = {"amount": str(schedule.amount), "status": schedule.status}
        if "amount" in data:
            schedule.amount = out.parse_money(data)
        if "status" in data:
            if data["status"] not in FeeSchedule.Status.values:
                raise FinanceError("Status must be active or inactive.", 400, "status")
            schedule.status = data["status"]
        if "effective_to" in data:
            end = out.parse_date(data, "effective_to", required=False)
            if end and end < schedule.effective_from:
                raise FinanceError("The end date cannot be before the start date.", 400, "effective_to")
            schedule.effective_to = end
        schedule.save()
        self.audit(request, tenant_id, "fee_schedule_updated", "FeeSchedule", schedule.pk, before=before, after={"amount": str(schedule.amount), "status": schedule.status})
        return Response(out.schedule_out(schedule))


class AdjustmentListView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        queryset = StudentAdjustment.objects.filter(tenant_id=tenant_id).select_related("student")
        if request.query_params.get("student"):
            queryset = queryset.filter(student_id=out.parse_uuid(request.query_params["student"], "student"))
        return Response([out.adjustment_out(a) for a in queryset.order_by("-created_at")[:500]])

    def post(self, request, tenant_id):
        self.member(request, tenant_id, FINANCE)
        data = request.data
        student = self.get_or_404(Student, tenant_id, out.parse_uuid(data.get("student"), "student"))
        year = self.get_or_404(AcademicYear, tenant_id, out.parse_uuid(data.get("academic_year"), "academic_year"))
        kind = data.get("kind")
        if kind not in StudentAdjustment.Kind.values:
            raise FinanceError("Choose scholarship, discount, charge or exemption.", 400, "kind")
        reason = out.parse_text(data, "reason", 240)
        category = None
        amount = percent = None
        if kind == StudentAdjustment.Kind.EXEMPTION:
            category = self.get_or_404(FeeCategory, tenant_id, out.parse_uuid(data.get("category"), "category"))
            if StudentAdjustment.objects.filter(
                tenant_id=tenant_id, student=student, academic_year=year, kind=kind, category=category,
                status=StudentAdjustment.Status.APPROVED,
            ).exists():
                raise FinanceError("This student is already exempt from that fee for the year.", 409, "category")
        else:
            amount = out.parse_money(data, "amount", required=False)
            if data.get("percent") not in (None, ""):
                from .ledger import money

                percent = money(data["percent"], "percent")
                if not 0 < percent <= 100:
                    raise FinanceError("Percent must be above 0 and at most 100.", 400, "percent")
            if (amount is None) == (percent is None):
                raise FinanceError("Give either a fixed amount or a percentage.", 400, "amount")
            if kind == StudentAdjustment.Kind.CHARGE and amount is None:
                raise FinanceError("An extra charge needs a fixed amount.", 400, "amount")
        description = out.parse_text(data, "description", 160, required=kind != StudentAdjustment.Kind.EXEMPTION)
        if kind == StudentAdjustment.Kind.EXEMPTION:
            description = description or f"Exempt from {category.name}"
        adjustment = StudentAdjustment.objects.create(
            tenant_id=tenant_id, student=student, academic_year=year, kind=kind, category=category,
            description=description, reason=reason, amount=amount, percent=percent, approved_by=request.user,
        )
        self.audit(
            request, tenant_id, "adjustment_approved", "StudentAdjustment", adjustment.pk,
            kind=kind, student=str(student.pk), reason=reason, amount=str(amount) if amount else None,
            percent=str(percent) if percent else None, category=str(category.pk) if category else None,
        )
        return Response(out.adjustment_out(adjustment), status=201)


class AdjustmentRevokeView(FinanceView):
    def post(self, request, tenant_id, adjustment_id):
        self.member(request, tenant_id, FINANCE)
        adjustment = self.get_or_404(StudentAdjustment, tenant_id, adjustment_id)
        reason = out.parse_text(request.data, "reason", 240)
        adjustment.status = StudentAdjustment.Status.REVOKED
        adjustment.save(update_fields=("status",))
        self.audit(request, tenant_id, "adjustment_revoked", "StudentAdjustment", adjustment.pk, reason=reason)
        return Response(out.adjustment_out(adjustment))


def _event(enrollment, action, user, **detail):
    ServiceEnrollmentEvent.objects.create(
        tenant_id=enrollment.tenant_id, enrollment=enrollment, action=action, actor=user, detail=detail
    )


def _snapshot(enrollment):
    return {
        "status": enrollment.status, "start_date": enrollment.start_date.isoformat(),
        "end_date": enrollment.end_date.isoformat() if enrollment.end_date else None, "notes": enrollment.notes,
    }


def _check_no_overlap(enrollment):
    """A student cannot hold two live subscriptions to the same service over the same dates."""
    if enrollment.status == ServiceEnrollment.Status.CANCELLED:
        return
    clash = ServiceEnrollment.objects.filter(
        tenant_id=enrollment.tenant_id, student_id=enrollment.student_id, category_id=enrollment.category_id,
    ).exclude(status=ServiceEnrollment.Status.CANCELLED).exclude(pk=enrollment.pk)
    for other in clash:
        starts_before_other_ends = other.end_date is None or enrollment.start_date <= other.end_date
        ends_after_other_starts = enrollment.end_date is None or enrollment.end_date >= other.start_date
        if starts_before_other_ends and ends_after_other_starts:
            raise FinanceError("The student already has a subscription to this service covering those dates.", 409, "start_date")


class ServiceEnrollmentListView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        queryset = ServiceEnrollment.objects.filter(tenant_id=tenant_id).select_related("student", "category")
        params = request.query_params
        if params.get("student"):
            queryset = queryset.filter(student_id=out.parse_uuid(params["student"], "student"))
        if params.get("category"):
            queryset = queryset.filter(category_id=out.parse_uuid(params["category"], "category"))
        if params.get("status") in ServiceEnrollment.Status.values:
            queryset = queryset.filter(status=params["status"])
        return Response([out.enrollment_out(e) for e in queryset[:500]])

    def post(self, request, tenant_id):
        self.member(request, tenant_id, FINANCE)
        data = request.data
        with transaction.atomic():
            lock_finance(tenant_id)
            student = self.get_or_404(Student, tenant_id, out.parse_uuid(data.get("student"), "student"))
            if student.status != Student.Status.ACTIVE:
                raise FinanceError("Only active students can be subscribed.", 400, "student")
            category = self.get_or_404(FeeCategory, tenant_id, out.parse_uuid(data.get("category"), "category"), is_active=True)
            if not _is_enrollable(tenant_id, category):
                raise FinanceError("This category has no optional or individually assigned fee, so it cannot be subscribed to.", 400, "category")
            start = out.parse_date(data, "start_date")
            end = out.parse_date(data, "end_date", required=False)
            if end and end < start:
                raise FinanceError("The end date cannot be before the start date.", 400, "end_date")
            enrollment = ServiceEnrollment(
                tenant_id=tenant_id, student=student, category=category, start_date=start, end_date=end,
                notes=out.parse_text(data, "notes", 240, required=False), created_by=request.user,
            )
            _check_no_overlap(enrollment)
            enrollment.save()
            _event(enrollment, "created", request.user, **_snapshot(enrollment))
        self.audit(request, tenant_id, "service_subscribed", "ServiceEnrollment", enrollment.pk, student=str(student.pk), category=category.code)
        return Response(out.enrollment_out(enrollment), status=201)


class ServiceEnrollmentDetailView(FinanceView):
    """Changes apply to invoices generated afterwards; issued invoices keep their snapshot."""

    def get(self, request, tenant_id, enrollment_id):
        self.member(request, tenant_id)
        enrollment = self.get_or_404(ServiceEnrollment, tenant_id, enrollment_id)
        events = enrollment.events.select_related("actor")
        return Response({**out.enrollment_out(enrollment), "history": [out.event_out(e) for e in events]})

    def patch(self, request, tenant_id, enrollment_id):
        self.member(request, tenant_id, FINANCE)
        data = request.data
        with transaction.atomic():
            lock_finance(tenant_id)
            enrollment = ServiceEnrollment.objects.select_for_update().filter(pk=enrollment_id, tenant_id=tenant_id).first()
            if enrollment is None:
                raise NotFound()
            before = _snapshot(enrollment)
            if "status" in data:
                if data["status"] not in ServiceEnrollment.Status.values:
                    raise FinanceError("Choose active, suspended or cancelled.", 400, "status")
                enrollment.status = data["status"]
            if "start_date" in data:
                enrollment.start_date = out.parse_date(data, "start_date")
            if "end_date" in data:
                enrollment.end_date = out.parse_date(data, "end_date", required=False)
            if "notes" in data:
                enrollment.notes = out.parse_text(data, "notes", 240, required=False)
            if enrollment.end_date and enrollment.end_date < enrollment.start_date:
                raise FinanceError("The end date cannot be before the start date.", 400, "end_date")
            _check_no_overlap(enrollment)
            after = _snapshot(enrollment)
            if after == before:
                return Response(out.enrollment_out(enrollment))
            enrollment.save()
            _event(enrollment, "updated", request.user, before=before, after=after, reason=out.parse_text(data, "reason", 240, required=False))
        self.audit(request, tenant_id, "service_updated", "ServiceEnrollment", enrollment.pk, before=before, after=after)
        return Response(out.enrollment_out(enrollment))


class ServiceUsageView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        queryset = ServiceUsage.objects.filter(tenant_id=tenant_id).select_related("student", "category")
        params = request.query_params
        if params.get("academic_year"):
            queryset = queryset.filter(academic_year_id=out.parse_uuid(params["academic_year"], "academic_year"))
        if params.get("period_label"):
            queryset = queryset.filter(period_label=params["period_label"])
        if params.get("student"):
            queryset = queryset.filter(student_id=out.parse_uuid(params["student"], "student"))
        return Response([out.usage_out(u) for u in queryset.order_by("student__last_name")[:500]])

    def post(self, request, tenant_id):
        self.member(request, tenant_id, FINANCE)
        data = request.data
        student = self.get_or_404(Student, tenant_id, out.parse_uuid(data.get("student"), "student"))
        category = self.get_or_404(FeeCategory, tenant_id, out.parse_uuid(data.get("category"), "category"))
        year = self.get_or_404(AcademicYear, tenant_id, out.parse_uuid(data.get("academic_year"), "academic_year"))
        if not FeeSchedule.objects.filter(
            tenant_id=tenant_id, category=category, billing_basis=FeeSchedule.Basis.PER_UNIT
        ).exists():
            raise FinanceError("This category is not billed per unit.", 400, "category")
        period = out.parse_text(data, "period_label", 60)
        try:
            quantity = int(str(data.get("quantity")).strip())
        except (TypeError, ValueError):
            raise FinanceError("Enter a whole number of units.", 400, "quantity")
        if not 0 <= quantity <= 10000:
            raise FinanceError("Units must be between 0 and 10,000.", 400, "quantity")
        with transaction.atomic():
            lock_finance(tenant_id)
            usage, created = ServiceUsage.objects.select_for_update().get_or_create(
                tenant_id=tenant_id, student=student, category=category, academic_year=year, period_label=period,
                defaults={"quantity": quantity, "recorded_by": request.user},
            )
            previous = usage.quantity
            if not created:
                usage.quantity, usage.recorded_by = quantity, request.user
                usage.save(update_fields=("quantity", "recorded_by", "updated_at"))
        self.audit(
            request, tenant_id, "usage_recorded", "ServiceUsage", usage.pk,
            student=str(student.pk), category=category.code, period=period, quantity=quantity, previous=None if created else previous,
        )
        return Response(out.usage_out(usage), status=201 if created else 200)


def _invoice_queryset(tenant_id):
    return Invoice.objects.filter(tenant_id=tenant_id).select_related("student", "academic_year")


class InvoiceListView(FinanceView):
    def get(self, request, tenant_id):
        membership = self.member(request, tenant_id, FINANCE + PORTAL)
        queryset = _invoice_queryset(tenant_id)
        if membership.role in PORTAL:
            queryset = queryset.filter(
                student_id__in=portal_student_ids(request.user, tenant_id, membership)
            ).exclude(status__in=(Invoice.Status.DRAFT, Invoice.Status.VOIDED))
        params = request.query_params
        if params.get("status") in Invoice.Status.values:
            queryset = queryset.filter(status=params["status"])
        if params.get("student"):
            queryset = queryset.filter(student_id=out.parse_uuid(params["student"], "student"))
        return Response([out.invoice_out(i) for i in queryset.order_by("-created_at")[:500]])

    def post(self, request, tenant_id):
        self.member(request, tenant_id, FINANCE)
        data = request.data
        invoice = billing.generate_invoice(
            tenant_id,
            student_id=out.parse_uuid(data.get("student"), "student"),
            academic_year_id=out.parse_uuid(data.get("academic_year"), "academic_year"),
            period_label=out.parse_text(data, "period_label", 60, required=False),
            due_date=out.parse_date(data, "due_date", required=False),
            user=request.user,
            **_supplementary(data),
        )
        self.audit(request, tenant_id, "invoice_drafted", "Invoice", invoice.pk, total=str(invoice.total))
        return Response(out.invoice_out(invoice, detail=True), status=201)


def _supplementary(data):
    supplementary = data.get("supplementary") is True
    categories = data.get("categories") or []
    if not isinstance(categories, list):
        raise FinanceError("Categories must be a list.", 400, "categories")
    return {
        "supplementary": supplementary,
        "category_ids": {out.parse_uuid(c, "categories") for c in categories} if supplementary else None,
    }


BULK_LIMIT = 300


def _selected_students(view, tenant_id, data):
    """Active students chosen by id, class or campus, always within this tenant."""
    queryset = Student.objects.filter(tenant_id=tenant_id, status=Student.Status.ACTIVE)
    ids = data.get("students")
    if ids:
        if not isinstance(ids, list):
            raise FinanceError("Students must be a list.", 400, "students")
        queryset = queryset.filter(pk__in=[out.parse_uuid(i, "students") for i in ids])
    elif data.get("grade_level") or data.get("campus"):
        if data.get("grade_level"):
            queryset = queryset.filter(grade_level__iexact=str(data["grade_level"]).strip())
        if data.get("campus"):
            queryset = queryset.filter(campus_id=out.parse_uuid(data["campus"], "campus"))
    else:
        raise FinanceError("Choose students, a class or a campus.", 400, "students")
    students = list(queryset.select_related("campus").order_by("last_name", "first_name")[: BULK_LIMIT + 1])
    if not students:
        raise FinanceError("No active students match that selection.", 400, "students")
    if len(students) > BULK_LIMIT:
        raise FinanceError(f"Select at most {BULK_LIMIT} students at a time.", 400, "students")
    return students


class InvoicePreviewView(FinanceView):
    """Shows what generating invoices would produce. Saves nothing."""

    def post(self, request, tenant_id):
        self.member(request, tenant_id, FINANCE)
        data = request.data
        year = self.get_or_404(AcademicYear, tenant_id, out.parse_uuid(data.get("academic_year"), "academic_year"))
        students = _selected_students(self, tenant_id, data)
        rows = billing.preview_invoices(
            tenant_id, students, year, out.parse_text(data, "period_label", 60, required=False), **_supplementary(data)
        )
        return Response({
            "students": [out.preview_out(r) for r in rows],
            "ready": sum(1 for r in rows if r["can_generate"]),
            "blocked": sum(1 for r in rows if not r["can_generate"]),
            "total": out.s(sum((r["totals"]["total"] for r in rows if r["can_generate"]), billing.ZERO)),
        })


class InvoiceBulkView(FinanceView):
    """Creates draft invoices for many students. Drafts are not visible to parents until issued."""

    def post(self, request, tenant_id):
        self.member(request, tenant_id, FINANCE)
        data = request.data
        students = _selected_students(self, tenant_id, data)
        results = billing.generate_bulk(
            tenant_id, students,
            academic_year_id=out.parse_uuid(data.get("academic_year"), "academic_year"),
            period_label=out.parse_text(data, "period_label", 60, required=False),
            due_date=out.parse_date(data, "due_date", required=False), user=request.user, **_supplementary(data),
        )
        for result in results:
            if result["invoice"]:
                self.audit(request, tenant_id, "invoice_drafted", "Invoice", result["invoice"].pk, total=str(result["invoice"].total), bulk=True)
        return Response({
            "created": sum(1 for r in results if r["invoice"]),
            "failed": sum(1 for r in results if not r["invoice"]),
            "results": [
                {
                    "student": str(r["student"].pk), "student_name": f"{r['student'].first_name} {r['student'].last_name}",
                    "invoice": str(r["invoice"].pk) if r["invoice"] else None, "total": out.s(r["invoice"].total) if r["invoice"] else None,
                    "error": r["error"],
                }
                for r in results
            ],
        }, status=201 if any(r["invoice"] for r in results) else 400)


class InvoiceIssueBatchView(FinanceView):
    def post(self, request, tenant_id):
        self.member(request, tenant_id, FINANCE)
        ids = request.data.get("invoices")
        if not isinstance(ids, list) or not ids:
            raise FinanceError("Choose the draft invoices to issue.", 400, "invoices")
        if len(ids) > BULK_LIMIT:
            raise FinanceError(f"Issue at most {BULK_LIMIT} invoices at a time.", 400, "invoices")
        results = billing.issue_batch(tenant_id, [out.parse_uuid(i, "invoices") for i in ids], user=request.user)
        for result in results:
            if result["issued"]:
                self.audit(request, tenant_id, "invoice_issued", "Invoice", result["invoice"].pk, number=result["invoice"].number, total=str(result["invoice"].total), bulk=True)
        return Response({
            "issued": sum(1 for r in results if r["issued"]),
            "failed": sum(1 for r in results if r["error"]),
            "errors": [{"invoice": str(r.get("invoice_id", "")), "error": r["error"]} for r in results if r["error"]],
        })


class InvoiceDetailView(FinanceView):
    def get(self, request, tenant_id, invoice_id):
        membership = self.member(request, tenant_id, FINANCE + PORTAL)
        invoice = self.get_or_404(Invoice, tenant_id, invoice_id)
        if membership.role in PORTAL and (
            invoice.student_id not in portal_student_ids(request.user, tenant_id, membership)
            or invoice.status in (Invoice.Status.DRAFT, Invoice.Status.VOIDED)
        ):
            raise NotFound()
        return Response(out.invoice_out(_invoice_queryset(tenant_id).get(pk=invoice.pk), detail=True, internal=membership.role not in PORTAL))


class InvoiceIssueView(FinanceView):
    def post(self, request, tenant_id, invoice_id):
        self.member(request, tenant_id, FINANCE)
        invoice, issued = billing.issue_invoice(tenant_id, invoice_id, user=request.user)
        if issued:
            self.audit(request, tenant_id, "invoice_issued", "Invoice", invoice.pk, number=invoice.number, total=str(invoice.total))
        return Response(out.invoice_out(_invoice_queryset(tenant_id).get(pk=invoice.pk), detail=True))


class InvoiceVoidView(FinanceView):
    def post(self, request, tenant_id, invoice_id):
        self.member(request, tenant_id, APPROVERS)
        invoice = billing.void_invoice(tenant_id, invoice_id, reason=out.parse_text(request.data, "reason", 240), user=request.user)
        self.audit(request, tenant_id, "invoice_voided", "Invoice", invoice.pk, reason=invoice.void_reason)
        return Response(out.invoice_out(_invoice_queryset(tenant_id).get(pk=invoice.pk), detail=True))


class CreditNoteView(FinanceView):
    def post(self, request, tenant_id, invoice_id):
        self.member(request, tenant_id, APPROVERS)
        data = request.data
        if data.get("reason_type") not in ("correction", "bad_debt"):
            raise FinanceError("Choose correction or bad debt.", 400, "reason_type")
        note, created = billing.create_credit_note(
            tenant_id, invoice_id, amount=out.parse_money(data), reason_type=data["reason_type"],
            reason=out.parse_text(data, "reason", 240), user=request.user, idempotency_key=self.key(request),
        )
        if created:
            self.audit(request, tenant_id, "credit_note_created", "CreditNote", note.pk, amount=str(note.amount), reason_type=note.reason_type)
        return Response(
            {"number": note.number, "amount": str(note.amount), "invoice": out.invoice_out(_invoice_queryset(tenant_id).get(pk=note.invoice_id), detail=True)},
            status=201 if created else 200,
        )


def _payment_queryset(tenant_id):
    return Payment.objects.filter(tenant_id=tenant_id).select_related("student", "method", "tenant")


class PaymentListView(FinanceView):
    def get(self, request, tenant_id):
        membership = self.member(request, tenant_id, FINANCE + PORTAL)
        queryset = _payment_queryset(tenant_id)
        if membership.role in PORTAL:
            queryset = queryset.filter(
                student_id__in=portal_student_ids(request.user, tenant_id, membership), status=Payment.Status.CONFIRMED
            )
        params = request.query_params
        if params.get("status") in Payment.Status.values:
            queryset = queryset.filter(status=params["status"])
        if params.get("student"):
            queryset = queryset.filter(student_id=out.parse_uuid(params["student"], "student"))
        return Response([out.payment_out(p) for p in queryset.order_by("-payment_date", "-created_at")[:500]])

    def post(self, request, tenant_id):
        self.member(request, tenant_id, FINANCE)
        data = request.data
        payment, created = payments.record_payment(
            tenant_id,
            student_id=out.parse_uuid(data.get("student"), "student"),
            method_id=out.parse_uuid(data.get("method"), "method"),
            amount=out.parse_money(data),
            payment_date=out.parse_date(data, "payment_date", required=False) or timezone.localdate(),
            reference=out.parse_text(data, "reference", 80, required=False),
            payer_name=out.parse_text(data, "payer_name", 120, required=False),
            idempotency_key=self.key(request),
            allocations=out.parse_allocations(data),
            auto_allocate=bool(data.get("auto_allocate")),
            user=request.user,
        )
        if created:
            self.audit(request, tenant_id, "payment_recorded", "Payment", payment.pk, amount=str(payment.amount), status=payment.status)
        payment = _payment_queryset(tenant_id).get(pk=payment.pk)
        return Response(out.payment_out(payment), status=201 if created else 200)


def _visible_payment(view, request, tenant_id, payment_id):
    membership = view.member(request, tenant_id, FINANCE + PORTAL)
    payment = view.get_or_404(Payment, tenant_id, payment_id)
    if membership.role in PORTAL and (
        payment.student_id not in portal_student_ids(request.user, tenant_id, membership)
        or payment.status != Payment.Status.CONFIRMED
    ):
        raise NotFound()
    return _payment_queryset(tenant_id).get(pk=payment.pk)


class PaymentDetailView(FinanceView):
    def get(self, request, tenant_id, payment_id):
        return Response(out.payment_out(_visible_payment(self, request, tenant_id, payment_id)))


class PaymentReceiptView(FinanceView):
    def get(self, request, tenant_id, payment_id):
        payment = _visible_payment(self, request, tenant_id, payment_id)
        if not payment.receipt_number:
            raise NotFound()
        return Response(receipts.receipt_data(payment))


class PaymentReceiptPdfView(FinanceView):
    def get(self, request, tenant_id, payment_id):
        payment = _visible_payment(self, request, tenant_id, payment_id)
        if not payment.receipt_number:
            raise NotFound()
        response = HttpResponse(receipts.render_receipt_pdf(payment), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{payment.receipt_number}.pdf"'
        return response


class PaymentConfirmView(FinanceView):
    def post(self, request, tenant_id, payment_id):
        self.member(request, tenant_id, FINANCE)
        payment, done = payments.confirm_payment(
            tenant_id, payment_id, user=request.user,
            allocations=out.parse_allocations(request.data), auto_allocate=bool(request.data.get("auto_allocate")),
        )
        if done:
            self.audit(request, tenant_id, "payment_confirmed", "Payment", payment.pk, amount=str(payment.amount))
        return Response(out.payment_out(_payment_queryset(tenant_id).get(pk=payment.pk)))


class PaymentFailView(FinanceView):
    def post(self, request, tenant_id, payment_id):
        self.member(request, tenant_id, FINANCE)
        payment = payments.fail_payment(tenant_id, payment_id, reason=out.parse_text(request.data, "reason", 240), user=request.user)
        self.audit(request, tenant_id, "payment_failed", "Payment", payment.pk)
        return Response(out.payment_out(_payment_queryset(tenant_id).get(pk=payment.pk)))


class PaymentAllocateView(FinanceView):
    def post(self, request, tenant_id, payment_id):
        self.member(request, tenant_id, FINANCE)
        allocations = out.parse_allocations(request.data)
        payment, done = payments.allocate_advance(
            tenant_id, payment_id, allocations=allocations, user=request.user, idempotency_key=self.key(request),
            auto_allocate=bool(request.data.get("auto_allocate")),
        )
        if done:
            self.audit(request, tenant_id, "advance_allocated", "Payment", payment.pk)
        return Response(out.payment_out(_payment_queryset(tenant_id).get(pk=payment.pk)))


class PaymentReverseView(FinanceView):
    def post(self, request, tenant_id, payment_id):
        self.member(request, tenant_id, APPROVERS)
        payment, done = payments.reverse_payment(tenant_id, payment_id, reason=out.parse_text(request.data, "reason", 240), user=request.user)
        if done:
            self.audit(request, tenant_id, "payment_reversed", "Payment", payment.pk, reason=payment.reversal_reason)
        return Response(out.payment_out(_payment_queryset(tenant_id).get(pk=payment.pk)))


class PaymentRefundView(FinanceView):
    def post(self, request, tenant_id, payment_id):
        self.member(request, tenant_id, APPROVERS)
        data = request.data
        refund, done = payments.refund_payment(
            tenant_id, payment_id, amount=out.parse_money(data), reason=out.parse_text(data, "reason", 240),
            refund_date=out.parse_date(data, "refund_date", required=False) or timezone.localdate(),
            user=request.user, idempotency_key=self.key(request),
        )
        if done:
            self.audit(request, tenant_id, "payment_refunded", "Payment", refund.payment_id, amount=str(refund.amount))
        return Response(
            out.payment_out(_payment_queryset(tenant_id).get(pk=refund.payment_id)), status=201 if done else 200
        )


class StatementView(FinanceView):
    def get(self, request, tenant_id, student_id):
        membership = self.member(request, tenant_id, FINANCE + PORTAL)
        if membership.role in PORTAL and student_id not in portal_student_ids(request.user, tenant_id, membership):
            raise NotFound()
        student = self.get_or_404(Student, tenant_id, student_id)
        return Response(reports.statement(tenant_id, student))


def _range(request):
    return out.parse_date(request.query_params, "start", False), out.parse_date(request.query_params, "end", False)


class SummaryReportView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        return Response(reports.summary(tenant_id, *_range(request)))


class OutstandingReportView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        params = request.query_params
        return Response(
            reports.outstanding_by_student(
                tenant_id,
                overdue_only=params.get("overdue") == "true",
                campus_id=out.parse_uuid(params["campus"], "campus") if params.get("campus") else None,
                grade_level=params.get("grade_level") or None,
            )
        )


class AdvancesReportView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        params = request.query_params
        return Response(
            reports.advances_by_student(
                tenant_id,
                campus_id=out.parse_uuid(params["campus"], "campus") if params.get("campus") else None,
                grade_level=params.get("grade_level") or None,
            )
        )


class TransactionsView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        return Response(reports.transactions(tenant_id, *_range(request)))


class TrialBalanceView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id, APPROVERS + (Role.ACCOUNTANT,))
        return Response(reports.trial_balance(tenant_id))


class ReminderView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id)
        return Response(reminders.reminder_candidates(tenant_id, overdue_only=request.query_params.get("overdue") != "false"))

    def post(self, request, tenant_id):
        self.member(request, tenant_id, APPROVERS)
        channel = request.data.get("channel")
        if channel not in ("sms", "whatsapp"):
            raise FinanceError("Choose sms or whatsapp.", 400, "channel")
        results = reminders.send_reminders(tenant_id, channel, confirm=request.data.get("confirm") is True)
        self.audit(request, tenant_id, "reminders_requested", "Reminder", channel, sent=sum(r["status"] == "sent" for r in results))
        return Response({"results": results})
