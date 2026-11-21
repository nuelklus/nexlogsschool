import hashlib
import json
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from core.enrollment import _audit, _membership
from core.models import Membership, Student, StudentGuardian

from .class_subjects import class_subject_ids
from .grading import Component, GradeBand, GradingError, GradingPolicy, Mark, MarkStatus, RoundingRule, calculate_subject
from .models import (
    AcademicTerm, Assessment, AssessmentResult, Examination, ExaminationPublication, ExaminationSession,
    GradingConfiguration, SchoolClass, StudentEnrollment, Subject, TeachingAssignment,
)
from .views import MANAGE_ROLES

LEADERS = (*MANAGE_ROLES, Membership.Role.HEADTEACHER)
STAFF = (*LEADERS, Membership.Role.TEACHER)


def _member(request, tenant_id, roles):
    member = _membership(request.user, tenant_id, roles)
    if member is None:
        raise NotFound()
    return member


def _accessible(request, tenant_id, exam_id, lock=False):
    member = _member(request, tenant_id, STAFF)
    query = Examination.objects.filter(tenant_id=tenant_id, pk=exam_id)
    if lock:
        query = query.select_for_update(of=("self",))
    exam = query.select_related("term", "school_class", "subject", "configuration").first()
    if exam is None or (
        member.role == Membership.Role.TEACHER and not TeachingAssignment.objects.filter(
            tenant_id=tenant_id, school_class=exam.school_class, subject=exam.subject, teacher=request.user
        ).exists()
    ):
        raise NotFound()
    return exam, member


def _lock_for_action(request, tenant_id, exam_id):
    initial, _ = _accessible(request, tenant_id, exam_id)
    term = AcademicTerm.objects.select_for_update().filter(pk=initial.term_id, tenant_id=tenant_id).first()
    if term is None:
        raise NotFound()
    exam, member = _accessible(request, tenant_id, exam_id, lock=True)
    if exam.term_id != term.pk:
        raise ValidationError({"detail": "The examination term changed. Reload and retry."})
    return exam, member, term


def _policy(configuration):
    config = configuration.configuration
    return GradingPolicy(
        components=tuple(Component(item["key"], Decimal(item["maximum"]), Decimal(item["weight"]))
                         for item in config["components"]),
        bands=tuple(GradeBand(Decimal(item["minimum"]), item["grade"], item["description"], item["passed"])
                    for item in config["bands"]),
        decimal_places=config["decimal_places"], rounding=RoundingRule(config["rounding"]),
        allow_decimal_scores=config["allow_decimal_scores"],
        exclude_not_applicable=config["exclude_not_applicable"],
    )


def _roster(exam):
    if exam.subject_id not in class_subject_ids(exam.tenant_id, exam.school_class_id):
        raise ValidationError({"subject": "This subject is no longer assigned to the class. Restore the assignment before managing its examination."})
    enrollments = StudentEnrollment.objects.filter(
        tenant_id=exam.tenant_id, school_class=exam.school_class,
        enrolled_on__lte=exam.start_date,
    ).filter(
        Q(status=StudentEnrollment.Status.ACTIVE, ended_on__isnull=True)
        | Q(ended_on__gte=exam.start_date)
    )
    if (enrollments.exclude(student__tenant_id=exam.tenant_id).exists()
            or enrollments.filter(campus__isnull=False).exclude(campus__tenant_id=exam.tenant_id).exists()
            or enrollments.filter(campus__isnull=False, school_class__campus__isnull=False).exclude(
                campus_id=F("school_class__campus_id")
            ).exists()):
        raise ValidationError({"detail": "The examination roster contains inconsistent tenant or campus data. Reconcile enrollments first."})
    return enrollments.distinct().select_related("student", "campus")


def _exam_data(exam):
    return {
        "id": str(exam.pk), "name": exam.name, "term": str(exam.term_id),
        "school_class": str(exam.school_class_id), "subject": str(exam.subject_id),
        "configuration": str(exam.configuration_id), "start_date": exam.start_date.isoformat(),
        "end_date": exam.end_date.isoformat(), "marks_deadline": exam.marks_deadline.isoformat(),
        "publication_date": exam.publication_date.isoformat(), "status": exam.status,
        "version": exam.version, "published_version": exam.published_version,
        "session": str(exam.session_id) if exam.session_id else None,
    }


class ExaminationCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    term = serializers.UUIDField()
    school_class = serializers.UUIDField()
    subject = serializers.UUIDField()
    configuration = serializers.UUIDField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    marks_deadline = serializers.DateField()
    publication_date = serializers.DateField()


def _create_exam(request, tenant_id, data, session=None):
    term = AcademicTerm.objects.select_for_update().filter(tenant_id=tenant_id, pk=data["term"]).first()
    school_class = SchoolClass.objects.filter(tenant_id=tenant_id, pk=data["school_class"], is_active=True).first()
    subject = Subject.objects.filter(tenant_id=tenant_id, pk=data["subject"]).first()
    config = GradingConfiguration.objects.filter(tenant_id=tenant_id, pk=data["configuration"]).first()
    if not all((term, school_class, subject, config)):
        raise ValidationError({"detail": "Select a valid term, class, subject and grading configuration."})
    if term.is_completed:
        raise ValidationError({"term": "The term is closed."})
    if (term.academic_year.tenant_id != tenant_id or school_class.academic_year.tenant_id != tenant_id
            or (school_class.campus_id and school_class.campus.tenant_id != tenant_id)
            or (config.class_level_id and config.class_level.tenant_id != tenant_id)):
        raise ValidationError({"detail": "The selected academic records have inconsistent school or campus ownership."})
    if term.academic_year_id != school_class.academic_year_id:
        raise ValidationError({"school_class": "The class must belong to the term's academic year."})
    if not term.start_date <= data["start_date"] <= data["end_date"] <= term.end_date:
        raise ValidationError({"start_date": "Examination dates must be ordered and fall within the term."})
    if data["marks_deadline"] < data["end_date"] or data["publication_date"] < data["end_date"]:
        raise ValidationError({"detail": "Deadlines and publication must be on or after the examination end date."})
    if config.class_level_id and config.class_level_id != school_class.class_level_id:
        raise ValidationError({"configuration": "The configuration does not match the class level."})
    if not TeachingAssignment.objects.filter(
        tenant_id=tenant_id, school_class=school_class, subject=subject
    ).exists():
        raise ValidationError({"subject": "Assign a teacher to this class and subject first."})
    exam = Examination.objects.create(
        tenant_id=tenant_id, term=term, school_class=school_class, subject=subject, session=session,
        configuration=config, name=data["name"], start_date=data["start_date"], end_date=data["end_date"],
        marks_deadline=data["marks_deadline"], publication_date=data["publication_date"],
    )
    for component in _policy(config).components:
        Assessment.objects.create(
            tenant_id=tenant_id, term=term, school_class=school_class, subject=subject,
            name=f"{str(exam.pk)}:{component.key}"[:120], examination=exam, component_key=component.key,
            assessment_date=exam.start_date, max_score=component.maximum,
        )
    _audit(request, tenant_id, "academic.examination.created", "Examination", exam.pk)
    return exam


def _visible_exams(request, tenant_id, member):
    exams = Examination.objects.filter(tenant_id=tenant_id)
    if member.role == Membership.Role.TEACHER:
        exams = exams.filter(
            school_class__teaching_assignments__tenant_id=tenant_id,
            school_class__teaching_assignments__subject_id=F("subject_id"),
            school_class__teaching_assignments__teacher=request.user,
        )
    return exams


class ExaminationListView(APIView):
    def get(self, request, tenant_id):
        member = _member(request, tenant_id, STAFF)
        return Response([_exam_data(exam) for exam in _visible_exams(request, tenant_id, member)])

    def post(self, request, tenant_id):
        _member(request, tenant_id, MANAGE_ROLES)
        serializer = ExaminationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            exam = _create_exam(request, tenant_id, serializer.validated_data)
        return Response(_exam_data(exam), status=status.HTTP_201_CREATED)


class SessionEntrySerializer(serializers.Serializer):
    school_class = serializers.UUIDField()
    subject = serializers.UUIDField()
    configuration = serializers.UUIDField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    marks_deadline = serializers.DateField()
    publication_date = serializers.DateField()


class SessionSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    term = serializers.UUIDField()
    entries = SessionEntrySerializer(many=True, allow_empty=False, max_length=200)

    def validate_entries(self, entries):
        pairs = [(entry["school_class"], entry["subject"]) for entry in entries]
        if len(pairs) != len(set(pairs)):
            raise serializers.ValidationError("Each class and subject combination must be unique.")
        return entries


class ExaminationSessionView(APIView):
    def get(self, request, tenant_id):
        member = _member(request, tenant_id, STAFF)
        exams = _visible_exams(request, tenant_id, member).filter(session__isnull=False).select_related(
            "school_class", "subject"
        )
        by_session = {}
        for exam in exams:
            by_session.setdefault(exam.session_id, []).append({
                **_exam_data(exam), "class_name": exam.school_class.name,
                "section": exam.school_class.section, "subject_name": exam.subject.name,
            })
        sessions = ExaminationSession.objects.filter(tenant_id=tenant_id, pk__in=by_session)
        return Response([{
            "id": str(session.pk), "name": session.name, "term": str(session.term_id),
            "entries": by_session[session.pk],
            "counts": {state: sum(row["status"] == state for row in by_session[session.pk])
                       for state in Examination.Status.values},
        } for session in sessions])

    def post(self, request, tenant_id):
        _member(request, tenant_id, MANAGE_ROLES)
        serializer = SessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            term = AcademicTerm.objects.select_for_update().filter(tenant_id=tenant_id, pk=data["term"]).first()
            if term is None:
                raise ValidationError({"term": "Select a valid term."})
            session = ExaminationSession.objects.create(tenant_id=tenant_id, term=term, name=data["name"])
            exams = []
            for entry in data["entries"]:
                exams.append(_create_exam(request, tenant_id, {
                    **entry, "name": data["name"], "term": data["term"],
                }, session=session))
            _audit(request, tenant_id, "academic.examination_session.created", "ExaminationSession", session.pk,
                   {"examinations": [str(exam.pk) for exam in exams]})
        return Response({"id": str(session.pk), "name": session.name, "term": str(term.pk),
                         "entries": [_exam_data(exam) for exam in exams]}, status=status.HTTP_201_CREATED)


class MarkEntrySerializer(serializers.Serializer):
    enrollment = serializers.UUIDField()
    component = serializers.CharField(max_length=80)
    status = serializers.ChoiceField(choices=AssessmentResult.Status.choices)
    score = serializers.DecimalField(max_digits=7, decimal_places=2, allow_null=True)
    remarks = serializers.CharField(max_length=240, allow_blank=True, required=False, default="")

    def validate(self, attrs):
        if (attrs["status"] == AssessmentResult.Status.SCORED) != (attrs["score"] is not None):
            raise serializers.ValidationError("Only scored marks must have a score.")
        return attrs


class MarkBatchSerializer(serializers.Serializer):
    entries = MarkEntrySerializer(many=True, allow_empty=False, max_length=5000)

    def validate_entries(self, entries):
        keys = [(item["enrollment"], item["component"]) for item in entries]
        if len(set(keys)) != len(keys):
            raise serializers.ValidationError("Each student and component must appear only once.")
        return entries


class ExaminationActionSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, allow_blank=True, required=False, default="")


class ExaminationDetailView(APIView):
    def get(self, request, tenant_id, exam_id):
        exam, _ = _accessible(request, tenant_id, exam_id)
        roster = list(_roster(exam))
        marks = AssessmentResult.objects.filter(
            tenant_id=tenant_id, assessment__examination=exam
        ).select_related("assessment")
        marks = list(marks)
        calculations = []
        policy = _policy(exam.configuration)
        student_marks = {}
        for item in marks:
            student_marks.setdefault(item.enrollment_id, []).append(
                Mark(item.assessment.component_key, MarkStatus(item.mark_status), item.score)
            )
        for student in roster:
            try:
                result = calculate_subject(policy, tuple(student_marks.get(student.pk, [])))
                calculations.append({"enrollment": str(student.pk), "score": str(result.score),
                                     "grade": result.grade, "description": result.description,
                                     "passed": result.passed, "error": None})
            except GradingError as exc:
                calculations.append({"enrollment": str(student.pk), "score": None, "grade": None,
                                     "description": None, "passed": None, "error": str(exc)})
        return Response({
            **_exam_data(exam), "policy": exam.configuration.configuration,
            "students": [{"enrollment": str(item.pk), "student": str(item.student_id),
                          "name": f"{item.student.first_name} {item.student.last_name}",
                          "admission_number": item.student.admission_number} for item in roster],
            "marks": [{"enrollment": str(item.enrollment_id), "component": item.assessment.component_key,
                       "status": item.mark_status, "score": str(item.score) if item.score is not None else None,
                       "remarks": item.remarks} for item in marks],
            "calculations": calculations,
            "publication_history": [
                {"version": item.version, "published_at": item.published_at.isoformat(), "result": item.snapshot}
                for item in exam.publications.order_by("-version", "published_at")
            ],
        })

    def post(self, request, tenant_id, exam_id):
        serializer = MarkBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            exam, member, term = _lock_for_action(request, tenant_id, exam_id)
            if member.role != Membership.Role.TEACHER:
                raise NotFound()
            if term.is_completed or exam.status != Examination.Status.DRAFT:
                raise ValidationError({"detail": "Marks can only be edited while the examination is open and in draft."})
            roster = {item.pk: item for item in _roster(exam)}
            components = {item.component_key: item for item in exam.components.all()}
            policy = _policy(exam.configuration)
            for entry in serializer.validated_data["entries"]:
                enrollment = roster.get(entry["enrollment"])
                component = components.get(entry["component"])
                if not enrollment or not component:
                    raise ValidationError({"entries": "Each mark must refer to a student in the class on the examination date and a configured component."})
                score = entry["score"]
                if score is not None and (score < 0 or score > component.max_score or (
                    not policy.allow_decimal_scores and score != score.to_integral_value()
                )):
                    raise ValidationError({"entries": f"Invalid score for {component.component_key}."})
                old = AssessmentResult.objects.filter(assessment=component, enrollment=enrollment).first()
                before = None if old is None else {
                    "status": old.mark_status, "score": str(old.score) if old.score is not None else None, "remarks": old.remarks,
                }
                result, _ = AssessmentResult.objects.update_or_create(
                    assessment=component, enrollment=enrollment,
                    defaults={"tenant_id": tenant_id, "mark_status": entry["status"], "score": score, "remarks": entry["remarks"]},
                )
                _audit(request, tenant_id, "academic.examination.mark_saved", "AssessmentResult", result.pk, {
                    "before": before, "after": {"status": entry["status"], "score": str(score) if score is not None else None,
                                                "remarks": entry["remarks"]}, "version": exam.version,
                })
        return Response({"saved": len(serializer.validated_data["entries"])})


def _results(exam):
    roster = list(_roster(exam))
    if not roster:
        raise ValidationError({"detail": "There are no students enrolled on the examination start date."})
    components = list(exam.components.all())
    marks = AssessmentResult.objects.filter(assessment__in=components).select_related("assessment")
    by_student = {}
    for mark in marks:
        by_student.setdefault(mark.enrollment_id, []).append(
            Mark(mark.assessment.component_key, MarkStatus(mark.mark_status), mark.score)
        )
    results = []
    for enrollment in roster:
        try:
            result = calculate_subject(_policy(exam.configuration), tuple(by_student.get(enrollment.pk, [])))
        except GradingError as exc:
            raise ValidationError({"detail": f"{enrollment.student.admission_number}: {exc}"}) from exc
        results.append((enrollment, result))
    return results


def _fingerprint(exam, results):
    rows = AssessmentResult.objects.filter(assessment__examination=exam).order_by(
        "enrollment_id", "assessment__component_key"
    ).values_list("enrollment_id", "assessment__component_key", "mark_status", "score", "remarks")
    data = {
        "roster": sorted(
            (str(enrollment.pk), str(enrollment.student_id), enrollment.student.first_name,
             enrollment.student.last_name, enrollment.student.admission_number,
             str(enrollment.campus_id) if enrollment.campus_id else "",
             enrollment.campus.name if enrollment.campus_id else "")
            for enrollment, _ in results
        ),
        "marks": [[str(enrollment), component, mark_status, str(score) if score is not None else None, remarks]
                  for enrollment, component, mark_status, score, remarks in rows],
        "configuration": exam.configuration.configuration,
        "context": {
            "class": exam.school_class.name, "section": exam.school_class.section,
            "class_campus": exam.school_class.campus.name if exam.school_class.campus_id else None,
            "year": exam.term.academic_year.name, "term": exam.term.name,
            "examination": exam.name, "subject": exam.subject.name,
        },
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ExaminationActionView(APIView):
    def post(self, request, tenant_id, exam_id, action):
        serializer = ExaminationActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            exam, member, term = _lock_for_action(request, tenant_id, exam_id)
            if term.is_completed:
                raise ValidationError({"detail": "The term is closed. Reopen it before changing results."})
            transitions = {
                "submit": (Membership.Role.TEACHER, Examination.Status.DRAFT, Examination.Status.SUBMITTED),
                "approve": (Membership.Role.HEADTEACHER, Examination.Status.SUBMITTED, Examination.Status.APPROVED),
                "publish": (MANAGE_ROLES, Examination.Status.APPROVED, Examination.Status.PUBLISHED),
                "return": (Membership.Role.HEADTEACHER, Examination.Status.SUBMITTED, Examination.Status.DRAFT),
                "correct": (MANAGE_ROLES, Examination.Status.PUBLISHED, Examination.Status.DRAFT),
            }
            if action not in transitions:
                raise NotFound()
            role, previous, target = transitions[action]
            roles = role if isinstance(role, tuple) else (role,)
            if member.role not in roles:
                raise NotFound()
            if exam.status != previous and not (
                action == "return" and exam.status == Examination.Status.APPROVED
            ):
                raise ValidationError({"detail": "This action is not available in the current status."})
            previous = exam.status
            reason = serializer.validated_data["reason"].strip()
            if action in ("return", "correct") and not reason:
                raise ValidationError({"reason": "A reason of up to 500 characters is required."})
            if action == "submit" and timezone.localdate() < exam.end_date:
                raise ValidationError({"detail": "Results cannot be submitted before the examination ends."})
            results = _results(exam) if action in ("submit", "approve", "publish") else []
            if action == "submit":
                exam.submitted_fingerprint = _fingerprint(exam, results)
            elif action in ("approve", "publish") and _fingerprint(exam, results) != exam.submitted_fingerprint:
                raise ValidationError({"detail": "The roster or marks changed since submission. Return the results for review."})
            elif action in ("return", "correct"):
                exam.submitted_fingerprint = ""
                if action == "correct":
                    exam.version += 1
            if action == "publish":
                if timezone.localdate() < exam.publication_date:
                    raise ValidationError({"detail": "Results cannot be published before the publication date."})
                for enrollment, result in results:
                    ExaminationPublication.objects.create(
                        tenant_id=tenant_id, examination=exam, enrollment=enrollment, published_by=request.user,
                        version=exam.version,
                        snapshot={
                            "student_name": f"{enrollment.student.first_name} {enrollment.student.last_name}",
                            "admission_number": enrollment.student.admission_number,
                            "class": exam.school_class.name, "section": exam.school_class.section,
                            "campus": enrollment.campus.name if enrollment.campus_id else None,
                            "class_campus": exam.school_class.campus.name if exam.school_class.campus_id else None,
                            "year": exam.term.academic_year.name, "term": exam.term.name,
                            "examination": exam.name, "subject": exam.subject.name,
                            "score": str(result.score), "grade": result.grade,
                            "description": result.description, "passed": result.passed,
                            "configuration": exam.configuration.configuration,
                            "marks": [
                                {"component": mark.assessment.component_key, "status": mark.mark_status,
                                 "score": str(mark.score) if mark.score is not None else None, "remarks": mark.remarks}
                                for mark in AssessmentResult.objects.filter(
                                    assessment__examination=exam, enrollment=enrollment
                                ).select_related("assessment")
                            ],
                        },
                    )
                exam.published_version = exam.version
            exam.status = target
            exam.save(update_fields=("status", "submitted_fingerprint", "version", "published_version"))
            _audit(request, tenant_id, f"academic.examination.{action}", "Examination", exam.pk, {
                "from": previous, "to": target, "reason": reason, "version": exam.version,
            })
        return Response(_exam_data(exam))


class PublishedResultsView(APIView):
    def get(self, request, tenant_id):
        member = _member(request, tenant_id, (Membership.Role.PARENT, Membership.Role.STUDENT))
        if member.role == Membership.Role.PARENT:
            student_ids = StudentGuardian.objects.filter(
                tenant_id=tenant_id, user=request.user
            ).values_list("student_id", flat=True)
        else:
            student_ids = Student.objects.filter(
                tenant_id=tenant_id, user=request.user
            ).values_list("pk", flat=True)
        publications = ExaminationPublication.objects.filter(
            tenant_id=tenant_id, enrollment__student_id__in=student_ids,
            enrollment__tenant_id=tenant_id, enrollment__student__tenant_id=tenant_id,
            version=F("examination__published_version"),
        ).order_by("-published_at")
        return Response([{"id": str(item.pk), "student": str(item.enrollment.student_id),
                          "published_at": item.published_at.isoformat(), "result": item.snapshot}
                         for item in publications.select_related("enrollment")])
