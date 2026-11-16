from django.urls import path

from .promotion import PromotionView
from .closure_views import TermClosureActionView, TermClosureView
from .views import AcademicCollectionView, ClassLevelAssignmentView, ClassManagementView, TermDetailView
from .level_views import CatalogueListView, SchoolClassLevelDetailView, SchoolClassLevelListView
from .reconciliation import ClassReconciliationView
from .transfers import ClassTransferView
from .class_options import ClassOptionsView
from .attendance import ClassAttendanceView
from .grading_configurations import GradingConfigurationListView, GradingConfigurationRevisionView
from .examinations import ExaminationActionView, ExaminationDetailView, ExaminationListView, ExaminationSessionView, PublishedResultsView
from .report_card_preview import ReportCardPreviewView
from .report_cards import ReportCardListView, ReportCardPdfView, ReportCardPublishView, ReportCardWithdrawView
from .report_card_class_export import ClassReportExportView
from .report_card_batch import ClassReportRosterView, ClassReportReviewView
from .provenance import ProvenanceAttestationView, ProvenanceReportView, ProvenanceRevocationView

urlpatterns = [
    path("tenants/<uuid:tenant_id>/academics/enrollment-provenance/report/", ProvenanceReportView.as_view(), name="academic-enrollment-provenance-report"),
    path("tenants/<uuid:tenant_id>/academics/enrollment-provenance/attest/", ProvenanceAttestationView.as_view(), name="academic-enrollment-provenance-attest"),
    path("tenants/<uuid:tenant_id>/academics/enrollment-provenance/<uuid:attestation_id>/revoke/", ProvenanceRevocationView.as_view(), name="academic-enrollment-provenance-revoke"),
    path("tenants/<uuid:tenant_id>/academics/report-cards/class-roster/", ClassReportRosterView.as_view(), name="academic-report-card-class-roster"),
    path("tenants/<uuid:tenant_id>/academics/report-cards/class-review/", ClassReportReviewView.as_view(), name="academic-report-card-class-review"),
    path("tenants/<uuid:tenant_id>/academics/report-cards/class-export/", ClassReportExportView.as_view(), name="academic-report-card-class-export"),
    path("tenants/<uuid:tenant_id>/academics/report-cards/", ReportCardListView.as_view(), name="academic-report-cards"),
    path("tenants/<uuid:tenant_id>/academics/report-cards/<uuid:report_id>/publish/", ReportCardPublishView.as_view(), name="academic-report-card-publish"),
    path("tenants/<uuid:tenant_id>/academics/report-cards/<uuid:report_id>/withdraw/", ReportCardWithdrawView.as_view(), name="academic-report-card-withdraw"),
    path("tenants/<uuid:tenant_id>/academics/report-cards/<uuid:report_id>/pdf/", ReportCardPdfView.as_view(), name="academic-report-card-pdf"),
    path("tenants/<uuid:tenant_id>/academics/report-card-preview/", ReportCardPreviewView.as_view(), name="academic-report-card-preview"),
    path("tenants/<uuid:tenant_id>/academics/examination-sessions/", ExaminationSessionView.as_view(), name="academic-examination-sessions"),
    path("tenants/<uuid:tenant_id>/academics/examinations/", ExaminationListView.as_view(), name="academic-examinations"),
    path("tenants/<uuid:tenant_id>/academics/examinations/<uuid:exam_id>/", ExaminationDetailView.as_view(), name="academic-examination-detail"),
    path("tenants/<uuid:tenant_id>/academics/examinations/<uuid:exam_id>/<str:action>/", ExaminationActionView.as_view(), name="academic-examination-action"),
    path("tenants/<uuid:tenant_id>/academics/published-results/", PublishedResultsView.as_view(), name="academic-published-results"),
    path("tenants/<uuid:tenant_id>/academics/grading-configurations/", GradingConfigurationListView.as_view(), name="academic-grading-configurations"),
    path("tenants/<uuid:tenant_id>/academics/grading-configurations/<uuid:config_id>/revisions/", GradingConfigurationRevisionView.as_view(), name="academic-grading-configuration-revision"),
    path("tenants/<uuid:tenant_id>/academics/attendance/register/", ClassAttendanceView.as_view(), name="academic-attendance-register"),
    path("tenants/<uuid:tenant_id>/academics/class-options/", ClassOptionsView.as_view(), name="academic-class-options"),
    path("tenants/<uuid:tenant_id>/academics/transfers/", ClassTransferView.as_view(), name="academic-class-transfer"),
    path("tenants/<uuid:tenant_id>/academics/class-reconciliation/", ClassReconciliationView.as_view(), name="academic-class-reconciliation"),
    path("tenants/<uuid:tenant_id>/academics/class-level-catalogue/", CatalogueListView.as_view(), name="class-level-catalogue"),
    path("tenants/<uuid:tenant_id>/academics/class-levels/", SchoolClassLevelListView.as_view(), name="school-class-levels"),
    path("tenants/<uuid:tenant_id>/academics/class-levels/<uuid:level_id>/", SchoolClassLevelDetailView.as_view(), name="school-class-level-detail"),
    path("tenants/<uuid:tenant_id>/academics/classes/<uuid:class_id>/level/", ClassLevelAssignmentView.as_view(), name="academic-class-level-assignment"),
    path("tenants/<uuid:tenant_id>/academics/classes/<uuid:class_id>/management/", ClassManagementView.as_view(), name="academic-class-management"),
    path("tenants/<uuid:tenant_id>/academics/promotion/", PromotionView.as_view(), name="academic-promotion"),
    path("tenants/<uuid:tenant_id>/academics/terms/<uuid:term_id>/", TermDetailView.as_view(), name="academic-term-detail"),
    path(
        "tenants/<uuid:tenant_id>/academics/terms/<uuid:term_id>/closure/",
        TermClosureView.as_view(),
        name="academic-term-closure",
    ),
    path(
        "tenants/<uuid:tenant_id>/academics/terms/<uuid:term_id>/closure/exceptions/<uuid:exception_id>/decision/",
        TermClosureActionView.as_view(),
        {"action": "exception-decision"},
        name="academic-term-closure-exception",
    ),
    path(
        "tenants/<uuid:tenant_id>/academics/terms/<uuid:term_id>/closure/<str:action>/",
        TermClosureActionView.as_view(),
        name="academic-term-closure-action",
    ),
    path(
        "tenants/<uuid:tenant_id>/academics/<str:collection>/",
        AcademicCollectionView.as_view(),
        name="academic-collection",
    ),
]
