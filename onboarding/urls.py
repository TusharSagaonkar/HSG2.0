"""URL configuration for the Society Creation & Accounting Migration Wizard.

URL structure (all under ``/onboarding/``):

    GET   ""                                          → wizard_list
    POST  "start/"                                    → wizard_start
    GET   "<int:wizard_id>/"                          → wizard_detail
    GET   "<int:wizard_id>/step/<int:step_number>/"  → wizard_step
    POST  "<int:wizard_id>/step/<int:step_number>/save/" → wizard_step_save
    POST  "<int:wizard_id>/upload/<str:template_type>/"  → staging_upload
    GET   "<int:wizard_id>/staging/<str:template_type>/" → staging_view
    POST  "<int:wizard_id>/staging/<str:template_type>/delete/" → staging_delete
    POST  "<int:wizard_id>/staging/<str:template_type>/approve/" → staging_approve
    GET   "<int:wizard_id>/reconciliation/"          → reconciliation_dashboard
    GET   "<int:wizard_id>/checklist/"               → validation_checklist
    POST  "<int:wizard_id>/finalize/"                → finalize_migration
    GET   "<int:wizard_id>/complete/"                → wizard_complete
"""

from django.urls import path

from onboarding import views

app_name = "onboarding"

urlpatterns = [
    path("", views.wizard_list, name="wizard-list"),
    path("start/", views.wizard_start, name="wizard-start"),
    path("<int:wizard_id>/", views.wizard_detail, name="wizard-detail"),
    path(
        "<int:wizard_id>/step/<int:step_number>/",
        views.wizard_step,
        name="wizard-step",
    ),
    path(
        "<int:wizard_id>/step/<int:step_number>/save/",
        views.wizard_step_save,
        name="wizard-step-save",
    ),
    path(
        "<int:wizard_id>/upload/<str:template_type>/",
        views.staging_upload,
        name="staging-upload",
    ),
    path(
        "<int:wizard_id>/staging/<str:template_type>/",
        views.staging_view,
        name="staging-view",
    ),
    path(
        "<int:wizard_id>/staging/<str:template_type>/delete/",
        views.staging_delete,
        name="staging-delete",
    ),
    path(
        "<int:wizard_id>/staging/<str:template_type>/approve/",
        views.staging_approve,
        name="staging-approve",
    ),
    path(
        "<int:wizard_id>/reconciliation/",
        views.reconciliation_dashboard,
        name="reconciliation-dashboard",
    ),
    path(
        "<int:wizard_id>/checklist/",
        views.validation_checklist,
        name="validation-checklist",
    ),
    path(
        "<int:wizard_id>/finalize/",
        views.finalize_migration,
        name="finalize-migration",
    ),
    path(
        "<int:wizard_id>/complete/",
        views.wizard_complete,
        name="wizard-complete",
    ),
]
