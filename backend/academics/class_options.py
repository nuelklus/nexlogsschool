from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from core.enrollment import _membership
from core.models import Membership, Student
from finance.models import FeeSchedule, FinanceCampusGrant, Invoice

from .models import ClassLevelCatalogue, SchoolClass, SchoolClassLevel
from .views import MANAGE_ROLES


class ClassOptionsView(APIView):
    def get(self, request, tenant_id):
        purpose = request.query_params.get("purpose", "admission")
        if purpose not in ("admission", "finance"):
            raise ValidationError({"purpose": "Choose admission or finance."})
        roles = MANAGE_ROLES if purpose == "admission" else MANAGE_ROLES + (Membership.Role.ACCOUNTANT,)
        member = _membership(request.user, tenant_id, roles)
        if member is None:
            raise NotFound()
        if member.role == Membership.Role.ACCOUNTANT:
            grants = FinanceCampusGrant.objects.filter(
                membership=member, campus__tenant_id=tenant_id
            ).values("campus_id")
            names = Invoice.objects.filter(tenant_id=tenant_id, campus_id__in=grants).values_list("grade_level", flat=True)
            return Response(sorted({name for name in names if name and name.strip()}, key=str.casefold))

        levels = SchoolClassLevel.objects.filter(tenant_id=tenant_id, is_active=True)
        names = set(levels.values_list("display_name", flat=True))
        names.update(SchoolClass.objects.filter(tenant_id=tenant_id, is_active=True).values_list("name", flat=True))
        names.update(Student.objects.filter(tenant_id=tenant_id).values_list("grade_level", flat=True))
        if not SchoolClassLevel.objects.filter(tenant_id=tenant_id).exists():
            catalogue = ClassLevelCatalogue.objects.filter(country_code="GH", is_active=True)
            for level in catalogue:
                names.add(level.name)
                if level.code.startswith("gh-basic-"):
                    names.add(f"Grade {level.code.removeprefix('gh-basic-')}")
        if purpose == "finance":
            names.update(FeeSchedule.objects.filter(tenant_id=tenant_id).values_list("grade_level", flat=True))
            names.update(Invoice.objects.filter(tenant_id=tenant_id).values_list("grade_level", flat=True))
        return Response(sorted({name for name in names if name and name.strip()}, key=str.casefold))
