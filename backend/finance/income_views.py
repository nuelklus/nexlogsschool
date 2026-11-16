from datetime import date

from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response

from core.models import Membership

from . import income
from .ledger import FinanceError
from .models import IncomeCategory, OtherIncome, PaymentMethod
from .serializers import parse_date, parse_money, parse_text, parse_uuid
from .views import APPROVERS, FINANCE, FinanceView, _range


def _category_out(category):
    return {"id": str(category.pk), "name": category.name, "is_active": category.is_active}


class IncomeCategoryListView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id, FINANCE)
        income.ensure_categories(tenant_id)
        return Response([_category_out(c) for c in IncomeCategory.objects.filter(tenant_id=tenant_id, is_active=True)])

    def post(self, request, tenant_id):
        self.member(request, tenant_id, APPROVERS)
        income.ensure_categories(tenant_id)
        category = income.create_category(tenant_id, name=parse_text(request.data, "name", 80))
        self.audit(request, tenant_id, "income_category_created", "IncomeCategory", category.pk)
        return Response(_category_out(category), status=201)


class OtherIncomeListView(FinanceView):
    def get(self, request, tenant_id):
        self.member(request, tenant_id, FINANCE)
        queryset = OtherIncome.objects.filter(tenant_id=tenant_id).select_related("category", "method")
        if request.query_params.get("category"):
            queryset = queryset.filter(category_id=parse_uuid(request.query_params["category"], "category"))
        return Response([income.income_out(r) for r in queryset[:500]])

    def post(self, request, tenant_id):
        self.member(request, tenant_id, FINANCE)
        data = request.data
        income.ensure_categories(tenant_id)
        record, created = income.record_income(
            tenant_id,
            category_id=parse_uuid(data.get("category"), "category"),
            method_id=parse_uuid(data.get("method"), "method"),
            amount=parse_money(data),
            income_date=parse_date(data, "income_date", required=False) or date.today(),
            payer_name=parse_text(data, "payer_name", 120, required=False),
            reference=parse_text(data, "reference", 80, required=False),
            description=parse_text(data, "description", 240, required=False),
            idempotency_key=self.key(request),
            user=request.user,
        )
        if created:
            self.audit(request, tenant_id, "other_income_recorded", "OtherIncome", record.pk, amount=str(record.amount))
        record = OtherIncome.objects.select_related("category", "method").get(pk=record.pk)
        return Response(income.income_out(record), status=201 if created else 200)


class OtherIncomeReverseView(FinanceView):
    def post(self, request, tenant_id, income_id):
        self.member(request, tenant_id, APPROVERS)
        record, done = income.reverse_income(
            tenant_id, income_id, reason=parse_text(request.data, "reason", 240), user=request.user
        )
        if done:
            self.audit(request, tenant_id, "other_income_reversed", "OtherIncome", record.pk, reason=record.reversal_reason)
        record = OtherIncome.objects.select_related("category", "method").get(pk=record.pk)
        return Response(income.income_out(record))


class MoneyReceivedView(FinanceView):
    def get(self, request, tenant_id):
        membership = self.member(request, tenant_id, FINANCE, campus_scoped=True)
        params = request.query_params
        kind = params.get("kind") or None
        if kind and kind not in PaymentMethod.Kind.values:
            raise FinanceError("Choose a valid payment type.", 400, "kind")
        status = params.get("status") or "confirmed"
        if status not in ("confirmed", "pending", "reversed"):
            raise FinanceError("Choose confirmed, pending or reversed.", 400, "status")
        source = params.get("source") or None
        if source and source not in ("fees", "other"):
            raise FinanceError("Choose fees or other.", 400, "source")
        scope = None
        campus_id = parse_uuid(params["campus"], "campus") if params.get("campus") else None
        if membership.role == Membership.Role.ACCOUNTANT:
            scope = set(self.granted_campuses(membership, tenant_id))
            if campus_id and campus_id not in scope:
                raise NotFound()
            if source == "other" or params.get("category"):
                raise PermissionDenied("Other income is not available with campus-restricted access.")
        start, end = _range(request)
        return Response(
            income.money_received(
                tenant_id, start=start, end=end, kind=kind,
                method_id=parse_uuid(params["method"], "method") if params.get("method") else None,
                source=source,
                category_id=parse_uuid(params["category"], "category") if params.get("category") else None,
                grade_level=params.get("grade_level") or None,
                campus_id=campus_id, status=status, campus_scope=scope,
            )
        )
