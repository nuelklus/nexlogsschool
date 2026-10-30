from datetime import date, timedelta
from decimal import Decimal
from threading import Barrier, Thread
from unittest import skipUnless

from django.db import connection, connections
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import AcademicYear, AuditEvent
from core.tests import create_tenant

from . import ledger
from .models import (
    Account,
    FeeCategory,
    FeeSchedule,
    ImmutableError,
    Invoice,
    ServiceEnrollment,
    ServiceEnrollmentEvent,
    StudentAdjustment,
)
from .test_finance import FAST_HASH, FinanceBase, Role, make_student, make_user

D = Decimal
A = FeeSchedule.Applicability


class OptionalFeeBase(FinanceBase):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        income = Account.objects.filter(tenant=self.tenant, type="income").first()
        self.lunch = FeeCategory.objects.create(tenant=self.tenant, code="lunch", name="Lunch", income_account=income)
        self.club = FeeCategory.objects.create(tenant=self.tenant, code="club", name="Robotics Club", income_account=income)
        self.schedule()  # compulsory tuition 1000

    def optional(self, category=None, amount="200.00", **extra):
        return FeeSchedule.objects.create(
            tenant=self.tenant, academic_year=self.year, category=category or self.lunch, amount=D(amount),
            effective_from=date(2026, 1, 1), applicability=A.OPTIONAL, **extra,
        )

    def subscribe(self, student=None, category=None, start=None, end=None, expect=201):
        body = {"student": str((student or self.student).pk), "category": str((category or self.lunch).pk),
                "start_date": (start or self.today - timedelta(days=1)).isoformat()}
        if end:
            body["end_date"] = end.isoformat()
        response = self.client.post(f"{self.base}/service-enrollments/", body, format="json")
        self.assertEqual(response.status_code, expect, response.data)
        return response.data

    def preview(self, students=None, period="Term 1", **extra):
        body = {"academic_year": str(self.year.pk), "period_label": period,
                "students": [str(s.pk) for s in (students or [self.student])], **extra}
        response = self.client.post(f"{self.base}/invoices/preview/", body, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def row(self, student=None, **kw):
        return self.preview([student or self.student], **kw)["students"][0]

    def fee_names(self, row):
        return [f["description"] for f in row["calculation"]["fees"]]

    def excluded(self, row):
        return {e["description"]: e["code"] for e in row["calculation"]["excluded"]}


class ApplicabilityTests(OptionalFeeBase):
    def test_unsubscribed_student_is_not_charged_lunch(self):
        self.optional()
        row = self.row()
        self.assertEqual(self.fee_names(row), ["Tuition"])
        self.assertEqual(self.excluded(row), {"Lunch": "not_subscribed"})
        self.assertEqual(row["totals"]["total"], "1000.00")

    def test_subscribed_student_is_charged_and_sibling_is_not(self):
        self.optional()
        self.subscribe()
        self.assertEqual(self.row()["totals"]["total"], "1200.00")
        self.assertEqual(self.row(self.sibling)["totals"]["total"], "1000.00")

    def test_generated_invoice_matches_preview_and_preview_saves_nothing(self):
        self.optional()
        self.subscribe()
        before = Invoice.objects.count()
        preview = self.row()
        self.assertEqual(Invoice.objects.count(), before)
        draft = self.client.post(
            f"{self.base}/invoices/", {"student": str(self.student.pk), "academic_year": str(self.year.pk), "period_label": "Term 1"}, format="json"
        )
        self.assertEqual(draft.status_code, 201, draft.data)
        self.assertEqual(draft.data["total"], preview["totals"]["total"])
        self.assertEqual(draft.data["calculation"]["excluded"], preview["calculation"]["excluded"])

    def test_compulsory_fee_unchanged(self):
        self.assertEqual(self.row()["totals"]["total"], "1000.00")

    def test_future_ended_suspended_and_cancelled_enrollments_do_not_charge(self):
        self.optional()
        future = self.subscribe(start=self.today + timedelta(days=5))
        self.assertEqual(self.row()["totals"]["total"], "1000.00")
        self.client.patch(f"{self.base}/service-enrollments/{future['id']}/", {"start_date": (self.today - timedelta(days=3)).isoformat()}, format="json")
        self.assertEqual(self.row()["totals"]["total"], "1200.00")
        self.client.patch(f"{self.base}/service-enrollments/{future['id']}/", {"end_date": (self.today - timedelta(days=1)).isoformat()}, format="json")
        self.assertEqual(self.row()["totals"]["total"], "1000.00")
        self.client.patch(f"{self.base}/service-enrollments/{future['id']}/", {"end_date": None, "status": "suspended"}, format="json")
        self.assertEqual(self.row()["totals"]["total"], "1000.00")
        self.client.patch(f"{self.base}/service-enrollments/{future['id']}/", {"status": "cancelled"}, format="json")
        self.assertEqual(self.row()["totals"]["total"], "1000.00")

    def test_overlapping_subscription_rejected(self):
        self.optional()
        self.subscribe()
        self.subscribe(expect=409)

    def test_cannot_subscribe_to_category_without_optional_fee(self):
        self.subscribe(category=self.cat["tuition"], expect=400)
        self.subscribe(category=self.club, expect=400)

    def test_end_before_start_rejected(self):
        self.optional()
        self.subscribe(start=self.today, end=self.today - timedelta(days=2), expect=400)

    def test_individual_fee_needs_assignment(self):
        self.optional(self.club, "75.00", ).delete()
        FeeSchedule.objects.create(
            tenant=self.tenant, academic_year=self.year, category=self.club, amount=D("75.00"),
            effective_from=date(2026, 1, 1), applicability=A.INDIVIDUAL,
        )
        self.assertEqual(self.excluded(self.row()), {"Robotics Club": "not_assigned"})
        self.subscribe(category=self.club)
        self.assertEqual(self.row()["totals"]["total"], "1075.00")


class ConditionalTests(OptionalFeeBase):
    def conditional(self, category, condition_type, value="", amount="50.00"):
        return FeeSchedule.objects.create(
            tenant=self.tenant, academic_year=self.year, category=category, amount=D(amount), effective_from=date(2026, 1, 1),
            applicability=A.CONDITIONAL, condition_type=condition_type, condition_value=value,
        )

    def test_gender_condition(self):
        self.conditional(self.club, "gender", "female")
        self.assertEqual(self.excluded(self.row()), {"Robotics Club": "condition_not_met"})
        self.student.gender = "Female"
        self.student.save()
        self.assertEqual(self.row()["totals"]["total"], "1050.00")

    def test_new_student_condition(self):
        self.conditional(self.club, "new_student")
        self.student.enrolled_on = date(2026, 9, 15)
        self.student.save()
        self.assertEqual(self.row()["totals"]["total"], "1050.00")
        self.sibling.enrolled_on = date(2024, 9, 15)
        self.sibling.save()
        self.assertEqual(self.row(self.sibling)["totals"]["total"], "1000.00")

    def test_service_condition_requires_subscription(self):
        self.optional()
        self.conditional(self.club, "service", "lunch")
        self.assertEqual(self.excluded(self.row())["Robotics Club"], "condition_not_met")
        self.subscribe()
        self.assertEqual(self.row()["totals"]["total"], "1250.00")

    def test_schedule_api_validates_condition(self):
        body = {
            "academic_year": str(self.year.pk), "category": str(self.club.pk), "amount": "10.00",
            "effective_from": "2026-01-01", "applicability": "conditional", "condition_type": "service", "condition_value": "nope",
        }
        self.assertEqual(self.client.post(f"{self.base}/fee-schedules/", body, format="json").status_code, 400)
        body["applicability"] = "compulsory"
        ignored = self.client.post(f"{self.base}/fee-schedules/", body, format="json")
        self.assertEqual(ignored.status_code, 201, ignored.data)
        self.assertEqual(ignored.data["condition_type"], "")


class PerUnitTests(OptionalFeeBase):
    def setUp(self):
        super().setUp()
        self.meal = self.optional(amount="12.50", billing_basis="per_unit", unit_label="meal")
        self.subscribe()

    def usage(self, quantity, student=None, period="Term 1", expect=None):
        response = self.client.post(
            f"{self.base}/service-usage/",
            {"student": str((student or self.student).pk), "category": str(self.lunch.pk), "academic_year": str(self.year.pk),
             "period_label": period, "quantity": quantity},
            format="json",
        )
        if expect:
            self.assertEqual(response.status_code, expect, response.data)
        return response

    def test_missing_usage_blocks(self):
        row = self.row()
        self.assertFalse(row["can_generate"])
        self.assertTrue(row["issues"])
        blocked = self.client.post(
            f"{self.base}/invoices/", {"student": str(self.student.pk), "academic_year": str(self.year.pk), "period_label": "Term 1"}, format="json"
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertFalse(Invoice.objects.filter(student=self.student).exists())

    def test_quantity_times_unit_price(self):
        self.usage(37, expect=201)
        row = self.row()
        self.assertEqual(row["totals"]["total"], "1462.50")
        draft = self.client.post(
            f"{self.base}/invoices/", {"student": str(self.student.pk), "academic_year": str(self.year.pk), "period_label": "Term 1"}, format="json"
        )
        line = [l for l in draft.data["lines"] if l["description"].startswith("Lunch")][0]
        self.assertEqual((line["quantity"], line["unit_price"], line["amount"]), (37, "12.50", "462.50"))

    def test_usage_upsert_and_zero_excludes(self):
        self.usage(10, expect=201)
        self.usage(0, expect=200)
        self.assertEqual(self.excluded(self.row()), {"Lunch": "no_usage"})

    def test_usage_validation(self):
        self.usage(-1, expect=400)
        self.usage("abc", expect=400)
        self.usage(10001, expect=400)
        other = self.client.post(
            f"{self.base}/service-usage/",
            {"student": str(self.student.pk), "category": str(self.cat["tuition"].pk), "academic_year": str(self.year.pk), "period_label": "Term 1", "quantity": 3},
            format="json",
        )
        self.assertEqual(other.status_code, 400)

    def test_per_unit_requires_label(self):
        body = {
            "academic_year": str(self.year.pk), "category": str(self.club.pk), "amount": "1.00", "effective_from": "2026-01-01",
            "applicability": "optional", "billing_basis": "per_unit",
        }
        self.assertEqual(self.client.post(f"{self.base}/fee-schedules/", body, format="json").status_code, 400)


class ExemptionTests(OptionalFeeBase):
    def exempt(self, client=None, reason="Staff child", category=None):
        body = {
            "student": str(self.student.pk), "academic_year": str(self.year.pk), "kind": "exemption",
            "category": str((category or self.cat["tuition"]).pk), "reason": reason,
        }
        return (client or self.client).post(f"{self.base}/adjustments/", body, format="json")

    def test_exemption_removes_fee_and_records_reason(self):
        response = self.exempt()
        self.assertEqual(response.status_code, 201, response.data)
        row = self.row()
        self.assertEqual(row["totals"]["total"], "0.00" if row["totals"] else "0.00")
        self.assertEqual(self.excluded(row), {"Tuition": "exempt"})
        self.assertIn("Staff child", row["calculation"]["excluded"][0]["reason"])
        self.assertTrue(AuditEvent.objects.filter(tenant=self.tenant, action="finance.adjustment_approved").exists())

    def test_reason_mandatory_and_duplicate_rejected(self):
        self.assertEqual(self.exempt(reason="").status_code, 400)
        self.assertEqual(self.exempt().status_code, 201)
        self.assertEqual(self.exempt().status_code, 409)

    def test_exemption_needs_category(self):
        body = {"student": str(self.student.pk), "academic_year": str(self.year.pk), "kind": "exemption", "reason": "x"}
        self.assertEqual(self.client.post(f"{self.base}/adjustments/", body, format="json").status_code, 400)

    def test_accountant_can_adjust_with_reason_others_cannot(self):
        self.assertEqual(self.exempt(self.as_user(self.accountant)).status_code, 201)
        for user in (self.teacher, self.parent, self.student_user):
            self.assertEqual(self.exempt(self.as_user(user), category=self.lunch).status_code, 404)

    def test_revoke_requires_reason_and_restores_fee(self):
        adjustment = self.exempt().data
        accountant = self.as_user(self.accountant)
        url = f"{self.base}/adjustments/{adjustment['id']}/revoke/"
        self.assertEqual(accountant.post(url, {}, format="json").status_code, 400)
        self.assertEqual(accountant.post(url, {"reason": "Entered in error"}, format="json").status_code, 200)
        self.assertEqual(self.row()["totals"]["total"], "1000.00")
        self.assertTrue(AuditEvent.objects.filter(tenant=self.tenant, action="finance.adjustment_revoked").exists())


class HistoryTests(OptionalFeeBase):
    def test_issued_invoice_is_not_changed_by_later_changes(self):
        lunch = self.optional()
        enrollment = self.subscribe()
        invoice = self.issued_invoice()
        self.assertEqual(invoice["total"], "1200.00")
        self.client.patch(f"{self.base}/service-enrollments/{enrollment['id']}/", {"status": "cancelled"}, format="json")
        lunch.amount = D("999.00")
        lunch.save()
        FeeSchedule.objects.filter(category=self.cat["tuition"]).update(amount=D("5.00"))
        self.client.post(
            f"{self.base}/adjustments/",
            {"student": str(self.student.pk), "academic_year": str(self.year.pk), "kind": "exemption", "category": str(self.cat["tuition"].pk), "reason": "Late"},
            format="json",
        )
        stored = self.client.get(f"{self.base}/invoices/{invoice['id']}/").data
        self.assertEqual(stored["total"], "1200.00")
        amounts = {f["description"]: f["amount"] for f in stored["calculation"]["fees"]}
        self.assertEqual(amounts, {"Tuition": "1000.00", "Lunch": "200.00"})
        self.assertEqual(Invoice.objects.get(pk=invoice["id"]).lines.count(), 2)

    def test_portal_users_do_not_see_calculation_or_exemption_reasons(self):
        self.client.post(
            f"{self.base}/adjustments/",
            {"student": str(self.student.pk), "academic_year": str(self.year.pk), "kind": "exemption", "category": str(self.cat["tuition"].pk), "reason": "Staff child"},
            format="json",
        )
        self.optional()
        self.subscribe()
        invoice = self.issued_invoice()
        self.assertIn("Staff child", str(self.client.get(f"{self.base}/invoices/{invoice['id']}/").data["calculation"]))
        for user in (self.parent, self.student_user):
            detail = self.as_user(user).get(f"{self.base}/invoices/{invoice['id']}/")
            self.assertEqual(detail.status_code, 200)
            self.assertIsNone(detail.data["calculation"])
            self.assertNotIn("Staff child", str(detail.data))

    def test_enrollment_history_records_changes_and_is_immutable(self):
        self.optional()
        enrollment = self.subscribe()
        self.client.patch(f"{self.base}/service-enrollments/{enrollment['id']}/", {"status": "suspended", "reason": "Away"}, format="json")
        detail = self.client.get(f"{self.base}/service-enrollments/{enrollment['id']}/").data
        self.assertEqual([h["action"] for h in detail["history"]], ["created", "updated"] if detail["history"][0]["action"] == "created" else ["updated", "created"])
        event = ServiceEnrollmentEvent.objects.first()
        event.detail = {}
        with self.assertRaises(ImmutableError):
            event.save()
        with self.assertRaises(ImmutableError):
            event.delete()

    def test_supplementary_invoice_bills_only_new_service_once(self):
        self.optional()
        self.issued_invoice()
        self.subscribe()
        body = {
            "student": str(self.student.pk), "academic_year": str(self.year.pk), "period_label": "Term 1",
            "supplementary": True, "categories": [str(self.lunch.pk), str(self.cat["tuition"].pk)],
        }
        first = self.client.post(f"{self.base}/invoices/", body, format="json")
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data["total"], "200.00")
        self.assertEqual(first.data["period_label"], "Term 1 (supplementary 1)")
        again = self.client.post(f"{self.base}/invoices/", body, format="json")
        self.assertEqual(again.status_code, 400, again.data)

    def test_duplicate_plain_invoice_prevented(self):
        self.issued_invoice()
        again = self.client.post(
            f"{self.base}/invoices/", {"student": str(self.student.pk), "academic_year": str(self.year.pk), "period_label": "Term 1"}, format="json"
        )
        self.assertEqual(again.status_code, 409, again.data)


class BulkTests(OptionalFeeBase):
    def bulk_body(self, **extra):
        return {"academic_year": str(self.year.pk), "period_label": "Term 1", "students": [str(self.student.pk), str(self.sibling.pk)], **extra}

    def test_bulk_generate_then_issue_batch(self):
        self.optional()
        self.subscribe()
        response = self.client.post(f"{self.base}/invoices/bulk/", self.bulk_body(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual((response.data["created"], response.data["failed"]), (2, 0))
        ids = [r["invoice"] for r in response.data["results"]]
        issued = self.client.post(f"{self.base}/invoices/issue-batch/", {"invoices": ids}, format="json")
        self.assertEqual(issued.data["issued"], 2, issued.data)
        numbers = set(Invoice.objects.filter(tenant=self.tenant).values_list("number", flat=True))
        self.assertEqual(len(numbers), 2)
        again = self.client.post(f"{self.base}/invoices/issue-batch/", {"invoices": ids}, format="json")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(Invoice.objects.filter(tenant=self.tenant, number__in=numbers).count(), 2)

    def test_bulk_rerun_reports_duplicates_not_new_invoices(self):
        self.client.post(f"{self.base}/invoices/bulk/", self.bulk_body(), format="json")
        again = self.client.post(f"{self.base}/invoices/bulk/", self.bulk_body(), format="json")
        self.assertEqual(again.status_code, 400)
        self.assertEqual(again.data["failed"], 2)
        self.assertEqual(Invoice.objects.filter(tenant=self.tenant).count(), 2)

    def test_bulk_partial_failure_keeps_good_students(self):
        meal = self.optional(amount="5.00", billing_basis="per_unit", unit_label="meal")
        self.subscribe()
        self.client.post(
            f"{self.base}/service-usage/",
            {"student": str(self.sibling.pk), "category": str(self.lunch.pk), "academic_year": str(self.year.pk), "period_label": "Term 1", "quantity": 4},
            format="json",
        )
        self.subscribe(student=self.sibling)
        response = self.client.post(f"{self.base}/invoices/bulk/", self.bulk_body(), format="json")
        self.assertEqual((response.data["created"], response.data["failed"]), (1, 1))
        self.assertEqual(meal.billing_basis, "per_unit")

    def test_bulk_by_class_and_selection_required(self):
        response = self.client.post(f"{self.base}/invoices/preview/", {"academic_year": str(self.year.pk), "period_label": "T1"}, format="json")
        self.assertEqual(response.status_code, 400)
        by_class = self.client.post(
            f"{self.base}/invoices/preview/", {"academic_year": str(self.year.pk), "period_label": "T1", "grade_level": "jhs 1"}, format="json"
        )
        self.assertEqual(by_class.data["ready"], 2)

    def test_bulk_limit(self):
        for n in range(3, 303):
            make_student(self.tenant, n)
        response = self.client.post(
            f"{self.base}/invoices/preview/", {"academic_year": str(self.year.pk), "period_label": "T1", "grade_level": "JHS 1"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_drafts_invisible_to_parents_until_issued(self):
        self.client.post(f"{self.base}/invoices/bulk/", self.bulk_body(), format="json")
        self.assertEqual(self.as_user(self.parent).get(f"{self.base}/invoices/").data, [])

    def test_only_finance_roles_can_preview_or_bulk(self):
        for user in (self.teacher, self.parent, self.student_user):
            client = self.as_user(user)
            self.assertEqual(client.post(f"{self.base}/invoices/preview/", self.bulk_body(), format="json").status_code, 404)
            self.assertEqual(client.post(f"{self.base}/invoices/bulk/", self.bulk_body(), format="json").status_code, 404)
            self.assertEqual(client.post(f"{self.base}/service-enrollments/", {}, format="json").status_code, 404)
        self.assertEqual(self.as_user(self.accountant).post(f"{self.base}/invoices/preview/", self.bulk_body(), format="json").status_code, 200)


class OptionalFeeIsolationTests(OptionalFeeBase):
    def setUp(self):
        super().setUp()
        self.other_tenant = create_tenant(name="Kumasi Prep", slug="kumasi-prep", email="k@k.example", phone="2")
        self.other_owner = make_user(self.other_tenant, "owner@k.example", Role.OWNER)
        self.other_student = make_student(self.other_tenant, 1)
        self.other_year = AcademicYear.objects.create(
            tenant=self.other_tenant, name="2026/2027", start_date=date(2026, 9, 1), end_date=date(2027, 7, 31), is_current=True
        )
        ledger.ensure_ready(self.other_tenant.pk)
        self.other_client = self.as_user(self.other_owner)
        self.other_base = f"/api/tenants/{self.other_tenant.pk}/finance"
        self.optional()
        self.subscribe()

    def test_outsider_cannot_reach_tenant_services(self):
        for path in ("service-enrollments/", "service-usage/", "adjustments/"):
            self.assertEqual(self.other_client.get(f"{self.base}/{path}").status_code, 404, path)
        self.assertEqual(self.other_client.post(f"{self.base}/invoices/preview/", {}, format="json").status_code, 404)

    def test_cannot_subscribe_or_preview_other_tenants_student(self):
        self.subscribe(student=self.other_student, expect=404)
        preview = self.client.post(
            f"{self.base}/invoices/preview/",
            {"academic_year": str(self.year.pk), "period_label": "T1", "students": [str(self.other_student.pk)]}, format="json",
        )
        self.assertEqual(preview.status_code, 400)

    def test_other_tenant_url_hides_enrollments_and_categories(self):
        self.assertEqual(self.other_client.get(f"{self.other_base}/service-enrollments/").data, [])
        enrollment = ServiceEnrollment.objects.get()
        self.assertEqual(self.other_client.get(f"{self.other_base}/service-enrollments/{enrollment.pk}/").status_code, 404)
        self.assertEqual(
            self.other_client.patch(f"{self.other_base}/service-enrollments/{enrollment.pk}/", {"status": "cancelled"}, format="json").status_code, 404
        )
        body = {"student": str(self.other_student.pk), "category": str(self.lunch.pk), "start_date": "2026-09-01"}
        self.assertEqual(self.other_client.post(f"{self.other_base}/service-enrollments/", body, format="json").status_code, 404)


@FAST_HASH
@skipUnless(connection.vendor == "postgresql", "Row-level locking needs PostgreSQL")
class OptionalFeeConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.tenant = create_tenant(name="Race School", slug="race2", email="r@r.example", phone="1")
        self.owner = make_user(self.tenant, "owner@r.example", Role.OWNER)
        self.year = AcademicYear.objects.create(
            tenant=self.tenant, name="2026/2027", start_date=date(2026, 9, 1), end_date=date(2027, 7, 31), is_current=True
        )
        self.students = [make_student(self.tenant, n) for n in range(1, 5)]
        ledger.ensure_ready(self.tenant.pk)
        FeeSchedule.objects.create(
            tenant=self.tenant, academic_year=self.year, category=FeeCategory.objects.get(tenant=self.tenant, code="tuition"),
            amount=D("1000.00"), effective_from=date(2026, 1, 1),
        )
        self.base = f"/api/tenants/{self.tenant.pk}/finance"

    def parallel(self, jobs):
        barrier, errors, results = Barrier(len(jobs)), [], []

        def attempt(job):
            try:
                client = APIClient()
                client.force_authenticate(user=self.owner)
                barrier.wait(timeout=10)
                results.append(job(client))
            except BaseException as error:
                errors.append(error)
            finally:
                connections.close_all()

        threads = [Thread(target=attempt, args=(j,)) for j in jobs]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=90)
        self.assertEqual(errors, [])
        return results

    def test_concurrent_bulk_generation_creates_one_invoice_per_student(self):
        body = {"academic_year": str(self.year.pk), "period_label": "Term 1", "students": [str(s.pk) for s in self.students]}
        jobs = [lambda c: c.post(f"{self.base}/invoices/bulk/", body, format="json").data["created"]] * 4
        created = self.parallel(jobs)
        self.assertEqual(sum(created), 4)
        self.assertEqual(Invoice.objects.filter(tenant=self.tenant).count(), 4)

    def test_concurrent_subscriptions_do_not_overlap(self):
        income = Account.objects.filter(tenant=self.tenant, type="income").first()
        lunch = FeeCategory.objects.create(tenant=self.tenant, code="lunch", name="Lunch", income_account=income)
        FeeSchedule.objects.create(
            tenant=self.tenant, academic_year=self.year, category=lunch, amount=D("10.00"),
            effective_from=date(2026, 1, 1), applicability=A.OPTIONAL,
        )
        body = {"student": str(self.students[0].pk), "category": str(lunch.pk), "start_date": "2026-09-01"}
        statuses = self.parallel([lambda c: c.post(f"{self.base}/service-enrollments/", body, format="json").status_code] * 4)
        self.assertEqual(sorted(statuses), [201, 409, 409, 409])
        self.assertEqual(ServiceEnrollment.objects.filter(tenant=self.tenant).count(), 1)
