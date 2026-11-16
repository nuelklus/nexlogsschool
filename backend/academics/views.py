from collections.abc import Mapping

from django.db import IntegrityError, transaction
from django.db.models import Q
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from core.enrollment import _audit, _membership
from core.models import AcademicYear, Campus, Membership, Student, User

from .models import (
    AcademicTerm,
    Assessment,
    AssessmentResult,
    AttendanceRecord,
    SchoolClass,
    SchoolClassLevel,
    StudentEnrollment,
    Subject,
    TeachingAssignment,
    TimetableEntry,
)

MANAGE_ROLES = (Membership.Role.OWNER, Membership.Role.SCHOOL_ADMIN)
READ_ROLES = MANAGE_ROLES + (Membership.Role.ACCOUNTANT, Membership.Role.TEACHER, Membership.Role.HEADTEACHER)


class TenantSerializer(serializers.ModelSerializer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tenant_id = self.context["tenant_id"]
        for field in self.fields.values():
            queryset = getattr(field, "queryset", None)
            if queryset is not None and hasattr(queryset.model, "tenant_id"):
                field.queryset = queryset.filter(tenant_id=tenant_id)

    # User ids are integers; the API exposes every academic reference as a string.
    def to_representation(self, instance):
        data = super().to_representation(instance)
        for name in ("class_teacher", "teacher"):
            if data.get(name) is not None:
                data[name] = str(data[name])
        return data

    def validate(self, attrs):
        return attrs


class AcademicYearSerializer(TenantSerializer):
    class Meta:
        model = AcademicYear
        fields = ("id", "name", "start_date", "end_date", "status", "is_current")
        read_only_fields = ("id",)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        start = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if start and end and end <= start:
            raise serializers.ValidationError({"end_date": "The end date must be after the start date."})
        return attrs


class AcademicTermSerializer(TenantSerializer):
    class Meta:
        model = AcademicTerm
        fields = ("id", "academic_year", "name", "start_date", "end_date", "is_current", "is_completed")
        read_only_fields = ("id", "is_completed")

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance and self.instance.is_completed:
            raise serializers.ValidationError("A completed term cannot be edited. Reopen it first.")
        year = attrs.get("academic_year", getattr(self.instance, "academic_year", None))
        start = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if start and end and end <= start:
            raise serializers.ValidationError({"end_date": "The end date must be after the start date."})
        if year and start and end and (start < year.start_date or end > year.end_date):
            raise serializers.ValidationError({"start_date": "Term dates must fall within the academic year."})
        if year and start and end:
            clash = AcademicTerm.objects.filter(
                tenant_id=self.context["tenant_id"], academic_year=year, start_date__lte=end, end_date__gte=start
            )
            if self.instance:
                clash = clash.exclude(pk=self.instance.pk)
            other = clash.first()
            if other:
                raise serializers.ValidationError({"start_date": f"These dates overlap {other.name}."})
        return attrs


class SchoolClassSerializer(TenantSerializer):
    class_level = serializers.PrimaryKeyRelatedField(
        queryset=SchoolClassLevel.objects.none(), allow_null=True, required=False
    )

    class Meta:
        model = SchoolClass
        fields = (
            "id", "academic_year", "campus", "name", "section", "room", "class_level",
            "class_teacher", "is_active",
        )
        read_only_fields = ("id", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["class_level"].queryset = SchoolClassLevel.objects.filter(
            tenant_id=self.context["tenant_id"], is_active=True
        )
        self.fields["campus"].queryset = Campus.objects.filter(
            tenant_id=self.context["tenant_id"], status=Campus.Status.ACTIVE
        )
        self.fields["class_teacher"].queryset = User.objects.filter(
            memberships__tenant_id=self.context["tenant_id"],
            memberships__role=Membership.Role.TEACHER,
            memberships__status=Membership.Status.ACTIVE,
            is_active=True,
        )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance is None and not attrs.get("campus"):
            raise serializers.ValidationError({"campus": "Choose an active campus before creating a class."})
        return attrs


class SubjectSerializer(TenantSerializer):
    class Meta:
        model = Subject
        fields = ("id", "name", "code")
        read_only_fields = ("id",)

    def validate_code(self, value):
        return value.strip().upper()


class TeachingAssignmentSerializer(TenantSerializer):
    class Meta:
        model = TeachingAssignment
        fields = ("id", "school_class", "subject", "teacher")
        read_only_fields = ("id",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tenant_id = self.context["tenant_id"]
        self.fields["teacher"].queryset = User.objects.filter(
            memberships__tenant_id=tenant_id,
            memberships__role=Membership.Role.TEACHER,
            memberships__status=Membership.Status.ACTIVE,
            is_active=True,
        )


class StudentEnrollmentSerializer(TenantSerializer):
    subjects = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Subject.objects.none(), required=False
    )

    class Meta:
        model = StudentEnrollment
        fields = ("id", "student", "campus", "school_class", "subjects", "enrolled_on", "ended_on", "status")
        read_only_fields = ("id", "campus", "ended_on")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tenant_id = self.context["tenant_id"]
        self.fields["student"].queryset = Student.objects.filter(tenant_id=tenant_id)
        self.fields["subjects"].child_relation.queryset = Subject.objects.filter(tenant_id=tenant_id)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if not self.instance and attrs.get("status") == StudentEnrollment.Status.TRANSFERRED:
            raise serializers.ValidationError({"status": "Use the transfer workflow to transfer an enrollment."})
        school_class = attrs.get("school_class", getattr(self.instance, "school_class", None))
        student = attrs.get("student", getattr(self.instance, "student", None))
        subjects = attrs.get("subjects", list(self.instance.subjects.all()) if self.instance else [])
        if school_class and student and StudentEnrollment.objects.filter(
            tenant_id=self.context["tenant_id"],
            student=student,
            school_class__academic_year=school_class.academic_year,
            status=StudentEnrollment.Status.ACTIVE,
        ).exclude(pk=getattr(self.instance, "pk", None)).exists():
            raise serializers.ValidationError(
                {"student": "This student already has an active class enrollment for the academic year."}
            )
        if school_class and subjects:
            assigned = set(
                TeachingAssignment.objects.filter(
                    tenant_id=self.context["tenant_id"], school_class=school_class,
                    subject__in=subjects,
                ).values_list("subject_id", flat=True)
            )
            if assigned != {subject.pk for subject in subjects}:
                raise serializers.ValidationError(
                    {"subjects": "Select only subjects assigned to this class."}
                )
        if school_class and student:
            if (
                school_class.campus_id
                and student.campus_id != school_class.campus_id
            ):
                raise serializers.ValidationError(
                    {"school_class": "The student's campus does not match this class."}
                )
            enrolled_on = attrs.get("enrolled_on", getattr(self.instance, "enrolled_on", None))
            if enrolled_on and not (
                school_class.academic_year.start_date <= enrolled_on <= school_class.academic_year.end_date
            ):
                raise serializers.ValidationError(
                    {"enrolled_on": "The enrollment date must be within the academic year."}
                )
            if school_class.campus_id and student.campus_id not in (None, school_class.campus_id):
                raise serializers.ValidationError(
                    {"school_class": "The student's campus does not match this class."}
                )
        return attrs


class AttendanceRecordSerializer(TenantSerializer):
    class Meta:
        model = AttendanceRecord
        fields = ("id", "enrollment", "date", "status", "notes")
        read_only_fields = ("id",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["enrollment"].queryset = StudentEnrollment.objects.filter(
            tenant_id=self.context["tenant_id"], status=StudentEnrollment.Status.ACTIVE
        )


class TimetableEntrySerializer(TenantSerializer):
    class Meta:
        model = TimetableEntry
        fields = ("id", "school_class", "subject", "teacher", "weekday", "start_time", "end_time", "room")
        read_only_fields = ("id",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tenant_id = self.context["tenant_id"]
        self.fields["teacher"].queryset = User.objects.filter(
            memberships__tenant_id=tenant_id,
            memberships__role=Membership.Role.TEACHER,
            memberships__status=Membership.Status.ACTIVE,
            is_active=True,
        )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        school_class = attrs.get("school_class", getattr(self.instance, "school_class", None))
        subject = attrs.get("subject", getattr(self.instance, "subject", None))
        teacher = attrs.get("teacher", getattr(self.instance, "teacher", None))
        weekday = attrs.get("weekday", getattr(self.instance, "weekday", None))
        start = attrs.get("start_time", getattr(self.instance, "start_time", None))
        end = attrs.get("end_time", getattr(self.instance, "end_time", None))
        if start and end and end <= start:
            raise serializers.ValidationError({"end_time": "The end time must be after the start time."})
        tenant_id = self.context["tenant_id"]
        if school_class and subject and teacher and not TeachingAssignment.objects.filter(
            tenant_id=tenant_id, school_class=school_class, subject=subject, teacher=teacher
        ).exists():
            raise serializers.ValidationError(
                {"teacher": "Assign this teacher to the class and subject first."}
            )
        if weekday is not None and start and end:
            conflicts = TimetableEntry.objects.filter(
                tenant_id=tenant_id,
                weekday=weekday,
                start_time__lt=end,
                end_time__gt=start,
            ).filter(Q(school_class=school_class) | Q(teacher=teacher))
            if self.instance:
                conflicts = conflicts.exclude(pk=self.instance.pk)
            if conflicts.exists():
                raise serializers.ValidationError(
                    {"start_time": "This class or teacher already has a lesson at that time."}
                )
        return attrs


class AssessmentSerializer(TenantSerializer):
    class Meta:
        model = Assessment
        fields = ("id", "term", "school_class", "subject", "name", "assessment_date", "max_score")
        read_only_fields = ("id",)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        term = attrs.get("term")
        school_class = attrs.get("school_class")
        subject = attrs.get("subject")
        if term and school_class and term.academic_year_id != school_class.academic_year_id:
            raise serializers.ValidationError({"term": "Choose a term in the class's academic year."})
        assessment_date = attrs.get("assessment_date")
        if term and assessment_date and not term.start_date <= assessment_date <= term.end_date:
            raise serializers.ValidationError({"assessment_date": "The assessment date must fall within the selected term."})
        if school_class and subject and not TeachingAssignment.objects.filter(
            tenant_id=self.context["tenant_id"], school_class=school_class, subject=subject
        ).exists():
            raise serializers.ValidationError({"subject": "This subject is not assigned to the class."})
        return attrs


class AssessmentResultSerializer(TenantSerializer):
    score = serializers.DecimalField(max_digits=7, decimal_places=2, min_value=0)

    class Meta:
        model = AssessmentResult
        fields = ("id", "assessment", "enrollment", "score", "remarks")
        read_only_fields = ("id",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tenant_id = self.context["tenant_id"]
        self.fields["enrollment"].queryset = StudentEnrollment.objects.filter(
            tenant_id=tenant_id, status=StudentEnrollment.Status.ACTIVE
        )
        self.fields["assessment"].queryset = Assessment.objects.filter(
            tenant_id=tenant_id, examination__isnull=True
        )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        assessment = attrs.get("assessment")
        enrollment = attrs.get("enrollment")
        score = attrs.get("score")
        if assessment and enrollment and assessment.school_class_id != enrollment.school_class_id:
            raise serializers.ValidationError({"enrollment": "The student is not enrolled in this assessment's class."})
        if assessment and score is not None and score > assessment.max_score:
            raise serializers.ValidationError({"score": "The score cannot exceed the assessment maximum."})
        return attrs


SERIALIZERS = {
    "years": AcademicYearSerializer,
    "terms": AcademicTermSerializer,
    "classes": SchoolClassSerializer,
    "subjects": SubjectSerializer,
    "teacher-assignments": TeachingAssignmentSerializer,
    "enrollments": StudentEnrollmentSerializer,
    "attendance": AttendanceRecordSerializer,
    "timetable": TimetableEntrySerializer,
    "assessments": AssessmentSerializer,
    "results": AssessmentResultSerializer,
}


class AcademicCollectionView(APIView):
    def get(self, request, tenant_id, collection):
        if collection == "teachers":
            if _membership(request.user, tenant_id, READ_ROLES) is None:
                raise NotFound()
            teachers = User.objects.filter(
                memberships__tenant_id=tenant_id,
                memberships__role=Membership.Role.TEACHER,
                memberships__status=Membership.Status.ACTIVE,
                is_active=True,
            ).order_by("full_name", "email").distinct()
            return Response([
                {"id": str(teacher.pk), "name": teacher.full_name, "email": teacher.email}
                for teacher in teachers
            ])
        serializer_class = SERIALIZERS.get(collection)
        if serializer_class is None:
            raise NotFound()
        if _membership(request.user, tenant_id, READ_ROLES) is None:
            raise NotFound()
        model = serializer_class.Meta.model
        queryset = model.objects.filter(tenant_id=tenant_id)
        if collection == "assessments":
            queryset = queryset.filter(examination__isnull=True)
        elif collection == "results":
            queryset = queryset.filter(assessment__examination__isnull=True)
        if collection == "enrollments":
            queryset = queryset.select_related("student", "school_class")
        elif collection == "attendance":
            queryset = queryset.select_related("enrollment__student", "enrollment__school_class")
        elif collection == "teacher-assignments":
            queryset = queryset.select_related("school_class", "subject", "teacher")
        return Response(
            serializer_class(queryset, many=True, context={"tenant_id": tenant_id}).data
        )

    def post(self, request, tenant_id, collection):
        serializer_class = SERIALIZERS.get(collection)
        if serializer_class is None:
            raise NotFound()
        membership = _membership(request.user, tenant_id, READ_ROLES)
        if membership is None:
            raise NotFound()
        is_manager = membership.role in MANAGE_ROLES
        teacher_writes = collection in ("attendance", "results") and membership.role == Membership.Role.TEACHER
        if not is_manager and not teacher_writes:
            raise NotFound()

        serializer = serializer_class(data=request.data, context={"tenant_id": tenant_id})
        serializer.is_valid(raise_exception=True)
        if teacher_writes:
            if collection == "attendance":
                assigned = TeachingAssignment.objects.filter(
                    tenant_id=tenant_id,
                    school_class=serializer.validated_data["enrollment"].school_class,
                    teacher=request.user,
                ).exists()
            else:
                assessment = serializer.validated_data["assessment"]
                assigned = TeachingAssignment.objects.filter(
                    tenant_id=tenant_id,
                    school_class=assessment.school_class,
                    subject=assessment.subject,
                    teacher=request.user,
                ).exists()
            if not assigned:
                raise NotFound()
        try:
            with transaction.atomic():
                if collection == "classes":
                    campus = Campus.objects.select_for_update().filter(
                        pk=serializer.validated_data["campus"].pk, tenant_id=tenant_id,
                        status=Campus.Status.ACTIVE,
                    ).first()
                    if campus is None:
                        raise ValidationError({"campus": "This campus is no longer active. Choose an active campus."})
                    serializer.validated_data["campus"] = campus
                if collection in ("attendance", "results"):
                    enrollment = serializer.validated_data["enrollment"]
                    Student.objects.select_for_update().get(pk=enrollment.student_id, tenant_id=tenant_id)
                    if not StudentEnrollment.objects.filter(
                        pk=enrollment.pk, tenant_id=tenant_id, status=StudentEnrollment.Status.ACTIVE
                    ).exists():
                        raise ValidationError({"enrollment": "This enrollment is no longer active."})
                if collection == "classes" and serializer.validated_data.get("class_level"):
                    level = SchoolClassLevel.objects.select_for_update().get(
                        pk=serializer.validated_data["class_level"].pk, tenant_id=tenant_id
                    )
                    if not level.is_active:
                        raise ValidationError({"class_level": "This level is archived."})
                if collection == "enrollments":
                    student = Student.objects.select_for_update().get(
                        pk=serializer.validated_data["student"].pk, tenant_id=tenant_id
                    )
                    year = serializer.validated_data["school_class"].academic_year
                    school_class = SchoolClass.objects.select_for_update().get(
                        pk=serializer.validated_data["school_class"].pk, tenant_id=tenant_id
                    )
                    if not school_class.is_active:
                        raise ValidationError({"school_class": "This class is archived."})
                    if school_class.campus_id and student.campus_id != school_class.campus_id:
                        raise ValidationError({"school_class": "The student's campus does not match this class."})
                    if serializer.validated_data.get("status", StudentEnrollment.Status.ACTIVE) == StudentEnrollment.Status.ACTIVE and StudentEnrollment.objects.filter(
                        tenant_id=tenant_id, student=student, school_class__academic_year=year,
                        status=StudentEnrollment.Status.ACTIVE,
                    ).exists():
                        raise ValidationError({"student": "This student already has an active class enrollment for the academic year."})
                    serializer.validated_data["student"] = student
                    serializer.validated_data["school_class"] = school_class
                if collection == "years" and serializer.validated_data.get("is_current"):
                    AcademicYear.objects.filter(tenant_id=tenant_id, is_current=True).update(is_current=False)
                if collection == "terms" and serializer.validated_data.get("is_current"):
                    AcademicTerm.objects.filter(tenant_id=tenant_id, is_current=True).update(is_current=False)
                save_kwargs = {"tenant_id": tenant_id}
                if collection == "attendance":
                    save_kwargs["recorded_by"] = request.user
                if collection == "enrollments":
                    save_kwargs["campus"] = school_class.campus or student.campus
                record = serializer.save(**save_kwargs)
                if collection == "enrollments" and record.school_class.academic_year.is_current:
                    student = record.student
                    student.grade_level = record.school_class.name
                    changed_fields = ["grade_level", "updated_at"]
                    if record.campus_id:
                        student.campus_id = record.campus_id
                        changed_fields.append("campus")
                    student.save(update_fields=changed_fields)
                    _audit(
                        request,
                        tenant_id,
                        "student.current_class_assigned",
                        "Student",
                        record.student_id,
                        {"class": record.school_class.name},
                    )
                _audit(request, tenant_id, f"academic.{collection.replace('-', '_')}.created", record._meta.object_name, record.pk)
        except IntegrityError:
            return Response(
                {"detail": "A matching academic record already exists."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(serializer_class(record, context={"tenant_id": tenant_id}).data, status=status.HTTP_201_CREATED)



class ClassLevelAssignmentView(APIView):
    def patch(self, request, tenant_id, class_id):
        if _membership(request.user, tenant_id, MANAGE_ROLES) is None:
            raise NotFound()
        if not isinstance(request.data, Mapping) or set(request.data) != {"class_level"}:
            raise ValidationError({"detail": "Provide only class_level; use null to remove an assignment."})
        with transaction.atomic():
            school_class = SchoolClass.objects.select_for_update().filter(
                pk=class_id, tenant_id=tenant_id
            ).first()
            if school_class is None:
                raise NotFound()
            serializer = SchoolClassSerializer(
                school_class, data=request.data, partial=True, context={"tenant_id": tenant_id}
            )
            serializer.is_valid(raise_exception=True)
            level = serializer.validated_data["class_level"]
            if level is not None:
                level = SchoolClassLevel.objects.select_for_update().get(pk=level.pk, tenant_id=tenant_id)
                if not level.is_active:
                    raise ValidationError({"class_level": "This level is archived."})
            previous = school_class.class_level_id
            school_class.class_level = level
            school_class.save(update_fields=("class_level",))
            if previous != school_class.class_level_id:
                _audit(request, tenant_id, "academic.class_level_assigned", "SchoolClass", school_class.pk, {
                    "previous_level": str(previous) if previous else None,
                    "new_level": str(level.pk) if level else None,
                })
        return Response(SchoolClassSerializer(school_class, context={"tenant_id": tenant_id}).data)


class ClassManagementView(APIView):
    def patch(self, request, tenant_id, class_id):
        if _membership(request.user, tenant_id, MANAGE_ROLES) is None:
            raise NotFound()
        if not isinstance(request.data, Mapping) or not request.data or set(request.data) - {"class_teacher", "is_active", "campus"}:
            raise ValidationError({"detail": "Provide class_teacher, is_active, campus, or any combination."})
        with transaction.atomic():
            school_class = SchoolClass.objects.select_for_update().filter(tenant_id=tenant_id, pk=class_id).first()
            if school_class is None:
                raise NotFound()
            fields = SchoolClassSerializer(context={"tenant_id": tenant_id}).fields
            teacher_field = fields["class_teacher"]
            teacher = teacher_field.run_validation(request.data["class_teacher"]) if "class_teacher" in request.data else school_class.class_teacher
            active = serializers.BooleanField().run_validation(request.data["is_active"]) if "is_active" in request.data else school_class.is_active
            campus = fields["campus"].run_validation(request.data["campus"]) if "campus" in request.data else school_class.campus
            if "campus" in request.data and school_class.campus_id and campus is None:
                raise ValidationError({"campus": "A campus cannot be removed from a class."})
            if (
                (campus.pk if campus else None) != school_class.campus_id
                and StudentEnrollment.objects.filter(school_class=school_class).exists()
            ):
                raise ValidationError({"campus": "The campus cannot be changed after students have enrolled."})
            previous = {
                "class_teacher": str(school_class.class_teacher_id) if school_class.class_teacher_id else None,
                "is_active": school_class.is_active,
                "campus": str(school_class.campus_id) if school_class.campus_id else None,
            }
            school_class.class_teacher = teacher
            school_class.is_active = active
            school_class.campus = campus
            try:
                with transaction.atomic():
                    school_class.save(update_fields=("class_teacher", "is_active", "campus"))
            except IntegrityError as exc:
                if getattr(getattr(exc.__cause__, "diag", None), "constraint_name", None) == "class_campus_frozen":
                    raise ValidationError({"campus": "The campus cannot be changed after students have enrolled."}) from exc
                raise
            current = {
                "class_teacher": str(school_class.class_teacher_id) if school_class.class_teacher_id else None,
                "is_active": school_class.is_active,
                "campus": str(school_class.campus_id) if school_class.campus_id else None,
            }
            if previous != current:
                _audit(request, tenant_id, "academic.class_group.updated", "SchoolClass", school_class.pk, {"previous": previous, "new": current})
        return Response(SchoolClassSerializer(school_class, context={"tenant_id": tenant_id}).data)


class TermDetailView(APIView):
    """Schools set and adjust their own term names and dates."""

    def patch(self, request, tenant_id, term_id):
        if _membership(request.user, tenant_id, MANAGE_ROLES) is None:
            raise NotFound()
        term = AcademicTerm.objects.filter(pk=term_id, tenant_id=tenant_id).first()
        if term is None:
            raise NotFound()
        data = {key: request.data[key] for key in ("name", "start_date", "end_date") if key in request.data}
        serializer = AcademicTermSerializer(term, data=data, partial=True, context={"tenant_id": tenant_id})
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                record = serializer.save()
                _audit(request, tenant_id, "academic.terms.updated", "AcademicTerm", record.pk, {"fields": sorted(data)})
        except IntegrityError:
            return Response({"detail": "A term with that name already exists in this year."}, status=status.HTTP_409_CONFLICT)
        return Response(AcademicTermSerializer(record, context={"tenant_id": tenant_id}).data)
