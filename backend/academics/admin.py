from django.contrib import admin

from .models import ClassLevelCatalogue


@admin.register(ClassLevelCatalogue)
class ClassLevelCatalogueAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "education_stage", "country_code", "sort_order", "is_active")
    list_filter = ("country_code", "education_stage", "is_active")
    search_fields = ("code", "name")

    def get_readonly_fields(self, request, obj=None):
        return ("code",) if obj else ()

    def has_delete_permission(self, request, obj=None):
        return False
