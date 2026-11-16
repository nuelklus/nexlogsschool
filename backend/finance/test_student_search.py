from .test_finance import FinanceBase, Role


class StudentSearchTests(FinanceBase):
    def test_search_returns_matches_with_amount_owed(self):
        self.schedule(amount="1000.00")
        self.issued_invoice()
        data = self.client.get(f"{self.base}/students/search/?q=first1").data
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], str(self.student.pk))
        self.assertEqual(data[0]["amount_owed"], "1000.00")
        self.assertEqual(data[0]["advance_credit"], "0.00")

    def test_search_by_admission_number_and_empty_query(self):
        self.assertEqual(len(self.client.get(f"{self.base}/students/search/?q=S-0002").data), 1)
        self.assertEqual(self.client.get(f"{self.base}/students/search/").data, [])

    def test_parents_cannot_search(self):
        response = self.as_user(self.parent).get(f"{self.base}/students/search/?q=first")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.as_user(self.accountant).get(f"{self.base}/students/search/?q=first").status_code, 403)


class BroughtForwardTests(FinanceBase):
    def test_new_invoice_carries_earlier_unpaid_balance(self):
        self.schedule(amount="1000.00")
        first = self.issued_invoice(period="Term 1")
        self.assertEqual(self.pay("400.00", auto_allocate=True).status_code, 201)
        second = self.issued_invoice(period="Term 2")
        detail = self.client.get(f"{self.base}/invoices/{second['id']}/").data
        self.assertEqual(detail["brought_forward"], "600.00")
        self.assertEqual(detail["total_due"], "1600.00")
        first_detail = self.client.get(f"{self.base}/invoices/{first['id']}/").data
        self.assertEqual(first_detail["brought_forward"], "0.00")
