from copy import deepcopy
from unittest.mock import patch

from django.test import TestCase

from core.models import AuditEvent, Membership

from .models import AssessmentResult, GradingConfiguration, SchoolClassLevel
from .tests import AcademicFixture


class GradingConfigurationTests(AcademicFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.url = self.base + "grading-configurations/"
        self.payload = {
            "name": "Primary end of term",
            "examination_type": "end-of-term",
            "configuration": {
                "components": [
                    {"key": "coursework", "maximum": "20.00", "weight": "40.00"},
                    {"key": "exam", "maximum": "100.00", "weight": "60.00"},
                ],
                "bands": [
                    {"minimum": "0.00", "grade": "F", "description": "Needs work", "passed": False},
                    {"minimum": "50.00", "grade": "P", "description": "Pass", "passed": True},
                ],
                "decimal_places": 2,
                "rounding": "half_up",
                "allow_decimal_scores": True,
                "exclude_not_applicable": False,
            },
        }

    def test_create_and_revision_preserve_previous_configuration(self):
        legacy = self.client.post(self.base + "results/", {
            "assessment": self.assessment["id"], "enrollment": self.enrollment["id"],
            "score": "87.5", "remarks": "Original",
        }, format="json")
        self.assertEqual(legacy.status_code, 201)
        first = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(first.status_code, 201, first.data)
        first_id = first.data["id"]
        revised = deepcopy(self.payload)
        revised["configuration"]["bands"][1]["minimum"] = "60"
        second = self.client.post(self.url + first_id + "/revisions/", revised, format="json")
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(second.data["family"], first.data["family"])
        self.assertEqual(second.data["version"], 2)
        self.assertEqual(GradingConfiguration.objects.get(pk=first_id).configuration["bands"][1]["minimum"], "50.00")
        self.assertEqual(second.data["configuration"]["bands"][1]["minimum"], "60.00")
        self.assertEqual(self.client.post(self.url + first_id + "/revisions/", revised, format="json").status_code, 400)
        self.assertEqual(self.client.patch(self.url + first_id + "/", revised, format="json").status_code, 404)
        self.assertEqual(AuditEvent.objects.filter(
            tenant=self.tenant, action__startswith="academic.grading_configuration."
        ).count(), 2)
        result = AssessmentResult.objects.get(pk=legacy.data["id"])
        self.assertEqual(str(result.score), "87.50")
        self.assertEqual(result.remarks, "Original")

    def test_creation_rolls_back_if_audit_fails(self):
        with patch("academics.grading_configurations._audit", side_effect=RuntimeError("audit failure")):
            with self.assertRaisesRegex(RuntimeError, "audit failure"):
                self.client.post(self.url, self.payload, format="json")
        self.assertEqual(GradingConfiguration.objects.count(), 0)

    def test_invalid_configuration_is_rejected_without_writing(self):
        payloads = []
        bad = deepcopy(self.payload)
        bad["configuration"]["components"][0]["weight"] = "39"
        payloads.append(bad)
        bad = deepcopy(self.payload)
        bad["configuration"]["bands"][0]["minimum"] = "1"
        payloads.append(bad)
        bad = deepcopy(self.payload)
        bad["configuration"]["components"][1]["key"] = "coursework"
        payloads.append(bad)
        bad = deepcopy(self.payload)
        del bad["configuration"]["exclude_not_applicable"]
        payloads.append(bad)
        for payload in payloads:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post(self.url, payload, format="json").status_code, 400)
        self.assertEqual(GradingConfiguration.objects.count(), 0)

    def test_roles_and_tenant_isolation(self):
        foreign_level = SchoolClassLevel.objects.create(tenant=self.other_tenant, display_name="Foreign")
        payload = {**self.payload, "class_level": str(foreign_level.pk)}
        self.assertEqual(self.client.post(self.url, payload, format="json").status_code, 400)
        created = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(created.status_code, 201)
        config_id = created.data["id"]
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(len(self.client.get(self.url).data), 1)
        self.assertEqual(self.client.post(self.url, self.payload, format="json").status_code, 404)
        self.assertEqual(self.client.post(self.url + config_id + "/revisions/", self.payload, format="json").status_code, 404)
        head = self.make_member(self.tenant, "head@northstar.example", Membership.Role.HEADTEACHER)
        self.client.force_authenticate(user=head)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url, self.payload, format="json").status_code, 404)
        self.client.force_authenticate(user=self.other_owner)
        other_url = f"/api/tenants/{self.other_tenant.pk}/academics/grading-configurations/"
        self.assertEqual(self.client.get(other_url).data, [])
        self.assertEqual(self.client.post(other_url + config_id + "/revisions/", self.payload, format="json").status_code, 404)
        self.assertEqual(self.client.get(self.url).status_code, 404)
