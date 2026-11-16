from django.db import IntegrityError, transaction
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .enrollment import MANAGE_ROLES, _audit, _membership
from .models import Campus


class CampusSerializer(serializers.ModelSerializer):
    class Meta:
        model = Campus
        fields = ("id", "name", "code", "address", "contact_phone", "contact_email", "status")
        read_only_fields = ("id",)
        validators = []


class CampusManagementView(APIView):
    def get(self, request, tenant_id):
        if _membership(request.user, tenant_id, MANAGE_ROLES) is None:
            raise NotFound()
        campuses = Campus.objects.filter(tenant_id=tenant_id).order_by("name", "pk")
        return Response(CampusSerializer(campuses, many=True).data)

    def post(self, request, tenant_id):
        if _membership(request.user, tenant_id, MANAGE_ROLES) is None:
            raise NotFound()
        serializer = CampusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                campus = serializer.save(tenant_id=tenant_id)
                _audit(request, tenant_id, "campus.created", "Campus", campus.pk)
        except IntegrityError as exc:
            if Campus.objects.filter(tenant_id=tenant_id, code=serializer.validated_data["code"]).exists():
                raise ValidationError({"code": "A campus with this code already exists in this school."}) from exc
            raise
        return Response(CampusSerializer(campus).data, status=status.HTTP_201_CREATED)


class CampusManagementDetailView(APIView):
    def patch(self, request, tenant_id, campus_id):
        if _membership(request.user, tenant_id, MANAGE_ROLES) is None:
            raise NotFound()
        try:
            with transaction.atomic():
                campus = Campus.objects.select_for_update().filter(tenant_id=tenant_id, pk=campus_id).first()
                if campus is None:
                    raise NotFound()
                serializer = CampusSerializer(campus, data=request.data, partial=True)
                serializer.is_valid(raise_exception=True)
                changed = list(serializer.validated_data)
                campus = serializer.save()
                _audit(request, tenant_id, "campus.updated", "Campus", campus.pk, {"fields": changed})
        except IntegrityError as exc:
            if "code" in request.data and Campus.objects.filter(
                tenant_id=tenant_id, code=request.data["code"]
            ).exclude(pk=campus_id).exists():
                raise ValidationError({"code": "A campus with this code already exists in this school."}) from exc
            raise
        return Response(CampusSerializer(campus).data)
