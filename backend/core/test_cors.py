from django.test import SimpleTestCase, override_settings


@override_settings(CORS_ALLOWED_ORIGINS=["https://school.example"])
class FinanceCorsTests(SimpleTestCase):
    def test_payment_preflight_allows_idempotency_key(self):
        response = self.client.options(
            "/api/tenants/00000000-0000-0000-0000-000000000001/finance/payments/",
            HTTP_ORIGIN="https://school.example",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization,content-type,idempotency-key",
        )

        self.assertEqual(response.status_code, 200)
        allowed_headers = response["access-control-allow-headers"].lower()
        self.assertIn("idempotency-key", allowed_headers)
