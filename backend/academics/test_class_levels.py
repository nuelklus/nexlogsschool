from django.contrib.admin.sites import AdminSite
from django.test import TestCase

from core.models import AuditEvent

from .admin import ClassLevelCatalogueAdmin
from .models import ClassLevelCatalogue, SchoolClassLevel
from .tests import AcademicFixture


class ClassLevelTests(AcademicFixture, TestCase):
    def test_seed_catalogue_is_ghana_first_and_not_selected_for_schools(self):
        response = self.client.get(self.base + "class-level-catalogue/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["name"] for row in response.data],
            [
                "Creche", "Nursery 1", "Nursery 2", "KG 1", "KG 2",
                "Basic 1", "Basic 2", "Basic 3", "Basic 4", "Basic 5", "Basic 6",
                "JHS 1", "JHS 2", "JHS 3", "SHS 1", "SHS 2", "SHS 3",
            ],
        )
        self.assertTrue(all(row["country_code"] == "GH" and row["is_active"] for row in response.data))
        self.assertEqual(len({row["code"] for row in response.data}), 17)
        self.assertEqual(self.client.get(self.base + "class-levels/").data, [])

    def test_configure_archive_and_restore_without_touching_existing_class(self):
        response = self.client.post(self.base + "class-levels/", {
            "catalogue_level": "gh-basic-1", "display_name": "Grade 1", "sort_order": 5,
        }, format="json")
        self.assertEqual(response.status_code, 201)
        level_id = response.data["id"]
        response = self.client.patch(self.base + f"class-levels/{level_id}/", {
            "display_name": "Primary One", "sort_order": 7, "is_active": False,
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_active"])
        self.assertEqual(response.data["catalogue_name"], "Basic 1")
        self.assertEqual(self.client.patch(
            self.base + f"class-levels/{level_id}/", {"is_active": True}, format="json"
        ).status_code, 200)
        self.school_class.refresh_from_db()
        self.student.refresh_from_db()
        self.assertEqual(self.school_class.name, "Grade 1")
        self.assertEqual(self.student.grade_level, "Grade 1")
        self.assertEqual(
            AuditEvent.objects.filter(tenant=self.tenant, entity_id=level_id).count(), 3
        )

    def test_custom_levels_uniqueness_and_validation(self):
        url = self.base + "class-levels/"
        first = self.client.post(url, {"display_name": "Bridge Class"}, format="json")
        self.assertEqual(first.status_code, 201)
        self.assertIsNone(first.data["catalogue_level"])
        self.assertEqual(self.client.post(url, {"display_name": "bridge class"}, format="json").status_code, 409)
        self.assertEqual(self.client.post(url, {"display_name": " "}, format="json").status_code, 400)
        self.assertEqual(self.client.post(url, {
            "display_name": "Other", "sort_order": -1,
        }, format="json").status_code, 400)
        self.assertEqual(self.client.patch(url + first.data["id"] + "/", {
            "catalogue_level": "gh-basic-1",
        }, format="json").status_code, 400)

    def test_inactive_catalogue_and_duplicate_selections(self):
        catalogue = ClassLevelCatalogue.objects.get(code="gh-basic-1")
        url = self.base + "class-levels/"
        self.assertEqual(self.client.post(url, {
            "catalogue_level": catalogue.code, "display_name": "Grade One",
        }, format="json").status_code, 201)
        self.assertEqual(self.client.post(url, {
            "catalogue_level": catalogue.code, "display_name": "Different name",
        }, format="json").status_code, 409)
        catalogue.is_active = False
        catalogue.save(update_fields=("is_active",))
        self.assertNotIn(catalogue.code, [row["code"] for row in self.client.get(
            self.base + "class-level-catalogue/"
        ).data])
        self.assertEqual(self.client.post(url, {
            "catalogue_level": catalogue.code, "display_name": "Again",
        }, format="json").status_code, 400)
        other_country = ClassLevelCatalogue.objects.create(
            code="other-level", name="Other", country_code="NG",
            education_stage="primary", sort_order=1,
        )
        self.assertEqual(self.client.post(url, {
            "catalogue_level": other_country.code, "display_name": "Other",
        }, format="json").status_code, 400)

    def test_role_tenant_and_global_catalogue_isolation(self):
        foreign = SchoolClassLevel.objects.create(tenant=self.other_tenant, display_name="Foreign")
        self.assertEqual(self.client.patch(
            self.base + f"class-levels/{foreign.pk}/", {"display_name": "Changed"}, format="json"
        ).status_code, 404)
        self.assertEqual(self.client.post(
            self.base + "class-level-catalogue/", {"name": "Changed"}, format="json"
        ).status_code, 405)
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(self.client.get(self.base + "class-level-catalogue/").status_code, 200)
        self.assertEqual(self.client.post(
            self.base + "class-levels/", {"display_name": "No"}, format="json"
        ).status_code, 404)
        self.client.force_authenticate(user=self.other_owner)
        self.assertEqual(self.client.get(self.base + "class-levels/").status_code, 404)
        admin = ClassLevelCatalogueAdmin(ClassLevelCatalogue, AdminSite())
        self.assertIn("code", admin.get_readonly_fields(None, ClassLevelCatalogue.objects.first()))
        self.assertFalse(admin.has_delete_permission(None))

    def test_only_one_active_terminal_level_per_school(self):
        first = self.client.post(self.base + "class-levels/", {
            "display_name": "JHS 3", "is_terminal": True,
        }, format="json")
        self.assertEqual(first.status_code, 201)
        second = self.client.post(self.base + "class-levels/", {
            "display_name": "SHS 3", "is_terminal": True,
        }, format="json")
        self.assertEqual(second.status_code, 409)
        archived = self.client.patch(self.base + f"class-levels/{first.data['id']}/", {
            "is_active": False,
        }, format="json")
        self.assertEqual(archived.status_code, 400)
        cleared = self.client.patch(self.base + f"class-levels/{first.data['id']}/", {
            "is_terminal": False, "is_active": False,
        }, format="json")
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(self.client.post(self.base + "class-levels/", {
            "display_name": "SHS 3", "is_terminal": True,
        }, format="json").status_code, 201)
