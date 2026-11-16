from core.models import Campus, Membership
from core.tests import create_tenant
from finance.models import FinanceCampusGrant, Invoice
from finance.test_finance import FinanceBase, make_student

from .models import SchoolClass, SchoolClassLevel


class ClassOptionsTests(FinanceBase):
    def setUp(self):
        super().setUp()
        self.url = f"/api/tenants/{self.tenant.pk}/academics/class-options/"

    def test_default_and_legacy_names_are_distinct(self):
        self.student.grade_level = "Custom legacy"
        self.student.save(update_fields=("grade_level",))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue({"Custom legacy", "Grade 1", "Basic 1", "SHS 3"}.issubset(response.data))
        self.assertEqual(len(response.data), len(set(response.data)))

    def test_configured_levels_and_historical_finance_names(self):
        SchoolClassLevel.objects.create(tenant=self.tenant, display_name="Custom level")
        SchoolClassLevel.objects.create(tenant=self.tenant, display_name="Archived level", is_active=False)
        SchoolClass.objects.create(tenant=self.tenant, academic_year=self.year, name="Custom group")
        self.schedule(grade="Old schedule")
        self.schedule()
        invoice = self.issued_invoice()
        Invoice.objects.filter(pk=invoice["id"]).update(grade_level="Historical invoice")
        admission = self.client.get(self.url).data
        finance = self.client.get(self.url, {"purpose": "finance"}).data
        self.assertTrue({"Custom level", "Custom group", "JHS 1"}.issubset(admission))
        self.assertNotIn("Archived level", admission)
        self.assertNotIn("Old schedule", admission)
        self.assertTrue({"Old schedule", "Historical invoice"}.issubset(finance))

    def test_tenant_and_accountant_scope(self):
        other = create_tenant(name="Other", slug="other-options", email="other@example.com", phone="2")
        make_student(other, grade="Other school only")
        second = Campus.objects.create(tenant=self.tenant, name="Second", code="SECOND")
        self.schedule()
        first = self.issued_invoice(period="First")
        self.student.campus = second
        self.student.save(update_fields=("campus",))
        second_invoice = self.issued_invoice(period="Second")
        Invoice.objects.filter(pk=first["id"]).update(grade_level="First only")
        Invoice.objects.filter(pk=second_invoice["id"]).update(grade_level="Second only")
        accountant = self.as_user(self.accountant)
        url = f"{self.url}?purpose=finance"
        self.assertEqual(accountant.get(url).data, [])
        member = Membership.objects.get(tenant=self.tenant, user=self.accountant)
        grant = FinanceCampusGrant.objects.create(membership=member, campus=second)
        self.assertEqual(accountant.get(url).data, ["Second only"])
        grant.delete()
        self.assertEqual(accountant.get(url).data, [])
        self.assertEqual(accountant.get(self.url).status_code, 404)
        self.assertEqual(self.as_user(self.teacher).get(url).status_code, 404)
        self.assertNotIn("Other school only", self.client.get(url).data)
        self.assertEqual(self.client.get(self.url, {"purpose": "invalid"}).status_code, 400)
