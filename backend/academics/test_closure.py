from datetime import date
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from core.models import AuditEvent, Membership

from .models import (
    AcademicTerm,
    AssessmentResult,
    AttendanceRecord,
    StudentEnrollment,
    TermClosure,
)
from .tests import AcademicFixture

AFTER_TERM = mock.patch("academics.closure.school_today", return_value=date(2026, 5, 15))


class TermClosureTests(AcademicFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.head = self.make_member(self.tenant, "head@northstar.example", Membership.Role.HEADTEACHER)
        self.accountant = self.make_member(self.tenant, "acc@northstar.example", Membership.Role.ACCOUNTANT)
        self.url = f"{self.base}terms/{self.term.pk}/closure/"
        self.term2 = AcademicTerm.objects.create(
            tenant=self.tenant, academic_year=self.year, name="Term 2", start_date=date(2026, 5, 1), end_date=date(2026, 8, 30)
        )
        AFTER_TERM.start()
        self.addCleanup(mock.patch.stopall)

    def as_user(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def act(self, user, action, body=None):
        return self.as_user(user).post(f"{self.url}{action}/", body or {}, format="json")

    def items(self, response):
        return {item["code"]: item for item in response.data["items"]}

    def complete_academics(self):
        enrollment = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        AttendanceRecord.objects.create(tenant=self.tenant, enrollment=enrollment, date=date(2026, 2, 2), status="present")
        AssessmentResult.objects.create(tenant=self.tenant, assessment_id=self.assessment["id"], enrollment=enrollment, score="70")

    def to_pending_final(self):
        self.complete_academics()
        self.act(self.owner, "initialize")
        self.assertEqual(self.act(self.head, "submit").status_code, 200)
        response = self.act(self.head, "academic-approval", {"decision": "approve"})
        self.assertEqual(response.data["status"], "pending_final_approval")

    def test_initialize_builds_checklist_from_real_data(self):
        preview = self.as_user(self.owner).get(self.url)
        self.assertEqual(preview.data["status"], "not_started")
        response = self.act(self.owner, "initialize")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "in_progress")
        items = self.items(response)
        self.assertEqual(items["attendance_recorded"]["status"], "incomplete")
        self.assertEqual(items["attendance_recorded"]["details"]["classes"], ["Grade 1"])
        self.assertEqual(items["marks_entered"]["outstanding_count"], 1)
        self.assertEqual(items["marks_entered"]["details"]["students"], ["Ama Mensah"])
        self.assertEqual(items["report_cards"]["status"], "not_applicable")
        self.assertFalse(items["draft_invoices"]["required"])
        self.assertEqual(self.act(self.owner, "initialize").status_code, 200)
        self.assertEqual(TermClosure.objects.filter(term=self.term).count(), 1)

    def test_school_wide_financial_evidence_is_not_visible_to_restricted_staff(self):
        for user in (self.accountant, self.head):
            response = self.as_user(user).get(self.url)
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(response.data["finance"])
            self.assertFalse(any(item["category"] == "finance" for item in response.data["items"]))
        self.assertIsNotNone(self.as_user(self.owner).get(self.url).data["finance"])

    def test_submission_blocked_until_required_items_complete(self):
        self.act(self.owner, "initialize")
        response = self.act(self.head, "submit")
        self.assertEqual(response.status_code, 409)
        self.assertIn("Subject marks entered", response.data["detail"])
        self.complete_academics()
        self.assertEqual(self.act(self.head, "submit").status_code, 200)

    def test_approved_exception_unblocks_submission(self):
        self.act(self.owner, "initialize")
        self.complete_academics()
        enrollment = StudentEnrollment.objects.get(pk=self.enrollment["id"])
        AssessmentResult.objects.all().delete()
        asked = self.act(self.owner, "exceptions", {"item": "marks_entered", "reason": "Exam paper lost"})
        self.assertEqual(asked.status_code, 201)
        exception_id = asked.data["exceptions"][0]["id"]
        url = f"{self.url}exceptions/{exception_id}/decision/"
        self.assertEqual(self.as_user(self.owner).post(url, {"decision": "approve"}, format="json").status_code, 404)
        self.assertEqual(self.as_user(self.teacher).post(url, {"decision": "approve"}, format="json").status_code, 404)
        approved = self.as_user(self.head).post(url, {"decision": "approve"}, format="json")
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(self.items(approved)["marks_entered"]["status"], "exception_approved")
        self.assertEqual(self.as_user(self.head).post(url, {"decision": "reject", "comments": "x"}, format="json").status_code, 409)
        self.assertEqual(self.act(self.head, "submit").status_code, 200)
        del enrollment

    def test_two_stage_approval_and_separation_of_duties(self):
        self.complete_academics()
        self.act(self.owner, "initialize")
        self.act(self.head, "submit")
        self.assertEqual(self.act(self.owner, "academic-approval", {"decision": "approve"}).status_code, 404)
        self.assertEqual(self.act(self.teacher, "academic-approval", {"decision": "approve"}).status_code, 404)
        self.assertEqual(self.act(self.accountant, "academic-approval", {"decision": "approve"}).status_code, 404)
        self.assertEqual(self.act(self.owner, "final-authorization", {"decision": "approve"}).status_code, 409)
        self.assertEqual(self.act(self.head, "academic-approval", {"decision": "approve"}).status_code, 200)
        # Duplicate approval is rejected.
        self.assertEqual(self.act(self.head, "academic-approval", {"decision": "approve"}).status_code, 409)
        self.assertEqual(self.act(self.head, "final-authorization", {"decision": "approve"}).status_code, 404)
        self.assertEqual(self.act(self.accountant, "final-authorization", {"decision": "approve"}).status_code, 404)
        done = self.act(self.owner, "final-authorization", {"decision": "approve", "comments": "ok"})
        self.assertEqual(done.status_code, 200, done.data)
        self.assertEqual(done.data["status"], "completed")
        self.term.refresh_from_db()
        self.term2.refresh_from_db()
        self.assertTrue(self.term.is_completed)
        self.assertFalse(self.term.is_current)
        self.assertTrue(self.term2.is_current)
        stages = [(h["stage"], h["decision"]) for h in done.data["history"]]
        self.assertEqual(stages[-2:], [("academic", "approved"), ("final", "approved")])
        self.assertEqual(done.data["history"][-1]["previous_status"], "pending_final_approval")
        actions = set(AuditEvent.objects.filter(tenant=self.tenant).values_list("action", flat=True))
        for name in ("term_closure.initialized", "term_closure.submitted", "term_closure.academic_approved", "term_closure.final_approve"):
            self.assertIn(name, actions)
        self.assertEqual(self.act(self.owner, "final-authorization", {"decision": "approve"}).status_code, 409)

    def test_return_for_correction_and_resubmit(self):
        self.complete_academics()
        self.act(self.owner, "initialize")
        self.act(self.head, "submit")
        self.assertEqual(self.act(self.head, "academic-approval", {"decision": "return"}).status_code, 400)
        returned = self.act(self.head, "academic-approval", {"decision": "return", "comments": "Fix Grade 1 marks"})
        self.assertEqual(returned.data["status"], "returned_for_correction")
        self.assertEqual(self.act(self.head, "submit").data["status"], "submitted_for_review")

    def test_invalid_transitions(self):
        self.assertEqual(self.act(self.head, "submit").status_code, 409)
        self.act(self.owner, "initialize")
        self.assertEqual(self.act(self.head, "academic-approval", {"decision": "approve"}).status_code, 409)
        self.assertEqual(self.act(self.owner, "reopen", {"reason": "x"}).status_code, 409)
        self.assertEqual(self.act(self.owner, "unknown").status_code, 404)

    def test_final_closure_blocked_before_term_end(self):
        self.to_pending_final()
        with mock.patch("academics.closure.school_today", return_value=date(2026, 4, 29)):
            response = self.act(self.owner, "final-authorization", {"decision": "approve"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("2026-04-30", response.data["detail"])
        self.term.refresh_from_db()
        self.assertFalse(self.term.is_completed)

    def test_reopen_requires_authority_and_reason(self):
        self.to_pending_final()
        self.act(self.owner, "final-authorization", {"decision": "approve"})
        self.assertEqual(self.act(self.head, "reopen", {"reason": "x"}).status_code, 404)
        self.assertEqual(self.act(self.teacher, "reopen", {"reason": "x"}).status_code, 404)
        self.assertEqual(self.act(self.owner, "reopen", {}).status_code, 400)
        reopened = self.act(self.owner, "reopen", {"reason": "Late marks found"})
        self.assertEqual(reopened.data["status"], "reopened")
        self.assertEqual(reopened.data["history"][-1]["comments"], "Late marks found")
        self.assertEqual(reopened.data["history"][-1]["previous_status"], "completed")
        self.term.refresh_from_db()
        self.assertFalse(self.term.is_completed)
        # Approvals start again, but the earlier ones remain in the history.
        self.assertIsNone(reopened.data["closure"]["academic_approved_at"])
        self.assertEqual(self.act(self.owner, "final-authorization", {"decision": "approve"}).status_code, 409)
        self.assertEqual(self.act(self.head, "submit").status_code, 200)
        self.assertEqual(sum(1 for h in self.as_user(self.owner).get(self.url).data["history"] if h["stage"] == "academic"), 1)

    def test_other_tenant_and_unrelated_roles_cannot_see_closure(self):
        self.assertEqual(self.as_user(self.other_owner).get(self.url).status_code, 404)
        self.assertEqual(self.act(self.other_owner, "initialize").status_code, 404)
        self.assertEqual(self.as_user(self.teacher).get(self.url).status_code, 404)
        view = self.as_user(self.accountant).get(self.url)
        self.assertEqual(view.status_code, 200)
        self.assertNotIn("students", self.items(view)["marks_entered"]["details"])
        self.assertEqual(self.act(self.accountant, "initialize").status_code, 404)

    def test_finance_summary_warns_without_blocking(self):
        from finance.models import Invoice

        Invoice.objects.create(
            tenant=self.tenant, student=self.student, academic_year=self.year, period_label="Term 1",
            status="issued", due_date=date(2026, 3, 1), subtotal="500.00", total="500.00", amount_paid="100.00",
        )
        Invoice.objects.create(
            tenant=self.tenant, student=self.student, academic_year=self.year, period_label="Term 1 (supplementary 1)",
            status="draft", due_date=date(2026, 3, 1), subtotal="50.00", total="50.00",
        )
        finance = self.as_user(self.owner).get(self.url).data["finance"]
        self.assertEqual(finance["outstanding_invoice_count"], 1)
        self.assertEqual(finance["outstanding_total"], "400.00")
        self.assertEqual(finance["students_with_balances"], 1)
        self.assertEqual(finance["draft_invoice_count"], 1)
        self.assertEqual(finance["draft_invoice_total"], "50.00")
        self.complete_academics()
        self.act(self.owner, "initialize")
        self.assertEqual(self.act(self.head, "submit").status_code, 200)
