from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from core.enrollment import _audit, _membership

from . import closure as workflow
from .models import AcademicTerm, TermClosure

ACTIVE_OR_DONE = workflow.ACTIVE + (TermClosure.Status.COMPLETED,)


def _term(tenant_id, term_id):
    term = AcademicTerm.objects.select_related("academic_year").filter(pk=term_id, tenant_id=tenant_id).first()
    if term is None:
        raise NotFound()
    return term


def _current(term):
    return TermClosure.objects.filter(term=term, status__in=ACTIVE_OR_DONE).select_related(
        "academic_approved_by", "final_approved_by"
    ).first()


def _fail(error):
    return Response({"detail": error.message}, status=error.status)


def _redact_finance(data, membership):
    if membership.role not in workflow.OWNER_ADMIN:
        finance_codes = {item["code"] for item in data["items"] if item["category"] == "finance"}
        data["items"] = [item for item in data["items"] if item["category"] != "finance"]
        data["exceptions"] = [item for item in data["exceptions"] if item["item_code"] not in finance_codes]
        data["finance"] = None
    return data


class TermClosureView(APIView):
    def get(self, request, tenant_id, term_id):
        membership = _membership(request.user, tenant_id, workflow.VIEW_ROLES)
        if membership is None:
            raise NotFound()
        term = _term(tenant_id, term_id)
        data = workflow.overview(term, _current(term), include_students=membership.role in workflow.STAFF_VIEW)
        data["permissions"] = {
            "initialize": membership.role in workflow.STAFF_VIEW,
            "academic_approval": membership.role in workflow.HEADTEACHER,
            "final_authorization": membership.role in workflow.OWNER_ADMIN,
            "reopen": membership.role in workflow.OWNER_ADMIN,
        }
        return Response(_redact_finance(data, membership))


class TermClosureActionView(APIView):
    """POST actions that move a closure through its workflow; status is only ever changed server-side."""

    ACTIONS = {
        "initialize": workflow.STAFF_VIEW,
        "refresh": workflow.STAFF_VIEW,
        "exceptions": workflow.STAFF_VIEW,
        "submit": workflow.STAFF_VIEW,
        "academic-approval": workflow.HEADTEACHER,
        "final-authorization": workflow.OWNER_ADMIN,
        "reopen": workflow.OWNER_ADMIN,
        "cancel": workflow.OWNER_ADMIN,
    }

    def post(self, request, tenant_id, term_id, action, exception_id=None):
        roles = workflow.HEADTEACHER if exception_id else self.ACTIONS.get(action)
        if roles is None or _membership(request.user, tenant_id, roles) is None:
            raise NotFound()
        term = _term(tenant_id, term_id)
        body = request.data if isinstance(request.data, dict) else {}
        user = request.user
        created = action in ("exceptions",)
        try:
            if exception_id:
                decision = body.get("decision")
                if decision not in ("approve", "reject"):
                    return Response({"detail": "Choose approve or reject."}, status=400)
                workflow.decide_exception(tenant_id, term.pk, exception_id, user, approve=decision == "approve", comments=body.get("comments", ""))
                audit = f"term_closure.exception_{decision}d".replace("rejectd", "rejected")
                meta = {"exception": str(exception_id)}
            elif action == "initialize":
                _, created = workflow.initialize(tenant_id, term.pk, user)
                audit, meta = "term_closure.initialized" if created else "term_closure.initialize_repeated", {}
            elif action == "refresh":
                workflow.refresh(tenant_id, term.pk, user)
                audit, meta = "term_closure.checklist_refreshed", {}
            elif action == "exceptions":
                _, exception = workflow.request_exception(
                    tenant_id, term.pk, user, item_code=body.get("item"), reason=body.get("reason"), notes=body.get("supporting_notes", "")
                )
                audit, meta = "term_closure.exception_requested", {"exception": str(exception.pk), "item": body.get("item")}
            elif action == "submit":
                workflow.submit(tenant_id, term.pk, user, body.get("comments", ""))
                audit, meta = "term_closure.submitted", {}
            elif action == "academic-approval":
                decision = body.get("decision")
                if decision not in ("approve", "return"):
                    return Response({"detail": "Choose approve or return."}, status=400)
                workflow.academic_decision(tenant_id, term.pk, user, approve=decision == "approve", comments=body.get("comments", ""))
                audit, meta = f"term_closure.academic_{'approved' if decision == 'approve' else 'returned'}", {}
            elif action == "final-authorization":
                decision = body.get("decision")
                workflow.final_decision(tenant_id, term.pk, user, decision=decision, comments=body.get("comments", ""))
                audit, meta = f"term_closure.final_{decision}", {}
            elif action == "reopen":
                workflow.reopen(tenant_id, term.pk, user, body.get("reason"))
                audit, meta = "term_closure.reopened", {"reason": str(body.get("reason", ""))[:200]}
            else:
                workflow.cancel(tenant_id, term.pk, user, body.get("reason"))
                audit, meta = "term_closure.cancelled", {}
        except workflow.ClosureError as error:
            return _fail(error)
        _audit(request, tenant_id, audit, "AcademicTerm", term.pk, {"term": term.name, "year": term.academic_year.name, **meta})
        membership = _membership(user, tenant_id, workflow.VIEW_ROLES)
        data = workflow.overview(term, _current(term), include_students=membership.role in workflow.STAFF_VIEW)
        return Response(_redact_finance(data, membership), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
