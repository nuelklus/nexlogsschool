from django.db import IntegrityError, transaction
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from core.enrollment import _audit, _membership

from .models import ClassLevelCatalogue, SchoolClassLevel
from .views import MANAGE_ROLES, READ_ROLES


class CatalogueSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClassLevelCatalogue
        fields = ("code", "name", "education_stage", "country_code", "sort_order", "is_active")


class SchoolClassLevelSerializer(serializers.ModelSerializer):
    catalogue_level = serializers.SlugRelatedField(
        slug_field="code", queryset=ClassLevelCatalogue.objects.filter(country_code="GH", is_active=True),
        allow_null=True, required=False,
    )
    catalogue_name = serializers.CharField(source="catalogue_level.name", read_only=True, default=None)

    class Meta:
        model = SchoolClassLevel
        fields = ("id", "catalogue_level", "catalogue_name", "display_name", "sort_order", "is_active", "is_terminal")
        read_only_fields = ("id", "catalogue_name")

    def validate_catalogue_level(self, value):
        if self.instance and value != self.instance.catalogue_level:
            raise serializers.ValidationError("The catalogue level cannot be changed after creation.")
        return value

    def validate_display_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Enter a class level name.")
        return value

    def validate(self, attrs):
        terminal = attrs.get("is_terminal", getattr(self.instance, "is_terminal", False))
        active = attrs.get("is_active", getattr(self.instance, "is_active", True))
        if terminal and not active:
            raise serializers.ValidationError({"is_terminal": "A terminal level must be active."})
        return attrs


def _member(request, tenant_id, roles):
    if _membership(request.user, tenant_id, roles) is None:
        raise NotFound()


class CatalogueListView(APIView):
    def get(self, request, tenant_id):
        _member(request, tenant_id, READ_ROLES)
        catalogue = ClassLevelCatalogue.objects.filter(country_code="GH", is_active=True)
        return Response(CatalogueSerializer(catalogue, many=True).data)


class SchoolClassLevelListView(APIView):
    def get(self, request, tenant_id):
        _member(request, tenant_id, READ_ROLES)
        levels = SchoolClassLevel.objects.filter(tenant_id=tenant_id).select_related("catalogue_level")
        return Response(SchoolClassLevelSerializer(levels, many=True).data)

    def post(self, request, tenant_id):
        _member(request, tenant_id, MANAGE_ROLES)
        serializer = SchoolClassLevelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                level = serializer.save(tenant_id=tenant_id)
                _audit(request, tenant_id, "academic.class_level.created", "SchoolClassLevel", level.pk)
        except IntegrityError:
            return Response({"detail": "This level or name already exists, or another level is set as terminal."}, status=status.HTTP_409_CONFLICT)
        return Response(SchoolClassLevelSerializer(level).data, status=status.HTTP_201_CREATED)


class SchoolClassLevelDetailView(APIView):
    def patch(self, request, tenant_id, level_id):
        _member(request, tenant_id, MANAGE_ROLES)
        try:
            with transaction.atomic():
                level = SchoolClassLevel.objects.select_for_update().filter(tenant_id=tenant_id, pk=level_id).first()
                if level is None:
                    raise NotFound()
                serializer = SchoolClassLevelSerializer(level, data=request.data, partial=True)
                serializer.is_valid(raise_exception=True)
                previous = dict(SchoolClassLevelSerializer(level).data)
                serializer.save()
                _audit(request, tenant_id, "academic.class_level.updated", "SchoolClassLevel", level.pk, {
                    "previous": previous, "new": dict(serializer.data),
                })
        except IntegrityError:
            return Response({"detail": "This level name already exists, or another level is set as terminal."}, status=status.HTTP_409_CONFLICT)
        return Response(serializer.data)
