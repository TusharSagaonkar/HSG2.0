"""Views for the Society Creation & Accounting Migration Wizard.

All views are function-based with ``@login_required`` (per the
implementation plan §4.3). They delegate all business logic to the
service layer and never mutate models directly.

Step dispatch:
    The ``wizard_step`` view uses ``STEP_FORMS`` and ``STEP_TEMPLATES``
    dicts to map a step number to its form class and template name.
    Steps without a form (5, 9, 10–24, 26–28) render display-only
    templates or redirect to specialised views (staging, reconciliation,
    checklist, finalize, complete).
"""

from __future__ import annotations

import logging
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from onboarding.forms import (
    AccountingStartYearForm,
    FinalApprovalForm,
    MemberAssignmentForm,
    ModuleSelectionForm,
    SocietyDetailsForm,
    SocietyTypeForm,
    StructureForm,
    UnitConfigurationForm,
)
from onboarding.models import OnboardingWizard
from onboarding.services.financial_year_service import (
    FY_PATTERN_APRIL_MARCH,
    FinancialYearSetupService,
)
from onboarding.services.finalization_service import MigrationFinalizationService
from onboarding.services.module_config_service import ModuleConfigurationService
from onboarding.services.reconciliation_service import ReconciliationService
from onboarding.services.society_setup_service import SocietySetupService
from onboarding.services.staging_service import ALL_TEMPLATE_TYPES
from onboarding.services.staging_service import StagingService
from onboarding.services.validation_service import ValidationService
from onboarding.services.wizard_service import (
    STEP_NAMES,
    STEP_SOCIETY_READY,
    WizardService,
)
from societies.utils import tenant_context

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Step → Form / Template mapping
# --------------------------------------------------------------------------- #

STEP_FORMS: dict[int, type] = {
    1: SocietyDetailsForm,
    2: SocietyTypeForm,
    3: ModuleSelectionForm,
    4: AccountingStartYearForm,
    6: StructureForm,
    7: UnitConfigurationForm,
    8: MemberAssignmentForm,
    25: FinalApprovalForm,
}

STEP_TEMPLATES: dict[int, str] = {
    1: "onboarding/steps/step_society_details.html",
    2: "onboarding/steps/step_society_type.html",
    3: "onboarding/steps/step_module_selection.html",
    4: "onboarding/steps/step_accounting_start_year.html",
    5: "onboarding/steps/step_financial_year_creation.html",
    6: "onboarding/steps/step_structure.html",
    7: "onboarding/steps/step_unit_configuration.html",
    8: "onboarding/steps/step_member_assignment.html",
    9: "onboarding/steps/step_accounting_setup.html",
    10: "onboarding/steps/step_chart_of_accounts.html",
    11: "onboarding/steps/step_import_templates.html",
    12: "onboarding/steps/step_staging_area.html",
    13: "onboarding/steps/step_import_validation.html",
    14: "onboarding/steps/step_delete_reupload.html",
    15: "onboarding/steps/step_opening_trial_balance.html",
    16: "onboarding/steps/step_member_outstanding.html",
    17: "onboarding/steps/step_vendor_outstanding.html",
    18: "onboarding/steps/step_bank_opening.html",
    19: "onboarding/steps/step_cash_opening.html",
    20: "onboarding/steps/step_funds.html",
    21: "onboarding/steps/step_fixed_assets.html",
    22: "onboarding/steps/step_loans.html",
    23: "onboarding/steps/step_reconciliation_dashboard.html",
    24: "onboarding/steps/step_migration_validation_checklist.html",
    25: "onboarding/steps/step_final_approval.html",
    26: "onboarding/steps/step_create_opening_journal.html",
    27: "onboarding/steps/step_lock_migration.html",
    28: "onboarding/steps/step_society_ready.html",
}

# Steps that map to the staging area (upload / view / validate).
STAGING_STEPS = {12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22}

# Maps staging steps to their template type.
STEP_TO_TEMPLATE_TYPE: dict[int, str] = {
    12: "CHART_OF_ACCOUNTS",  # staging area overview
    15: "TRIAL_BALANCE",
    16: "MEMBER_OUTSTANDING",
    17: "VENDOR_OUTSTANDING",
    18: "BANK_OPENING",
    19: "CASH_OPENING",
    20: "FUNDS",
    21: "FIXED_ASSETS",
    22: "LOANS",
}


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #

def _get_wizard(wizard_id: int, user) -> OnboardingWizard:
    """Retrieve a wizard scoped to the requesting user.

    Uses ``.unscoped()`` so the wizard can be fetched even before a
    society is associated (the TenantManager would otherwise filter
    it out when no tenant contextvar is set).
    """
    return get_object_or_404(
        OnboardingWizard.objects.unscoped().filter(created_by=user),
        pk=wizard_id,
    )


def _get_step_form(step_number: int, wizard: OnboardingWizard, data=None, files=None):
    """Instantiate the form for the given step, or return ``None``.

    For steps that have no form (display-only), ``None`` is returned.
    """
    form_class = STEP_FORMS.get(step_number)
    if form_class is None:
        return None

    kwargs: dict[str, Any] = {}
    if data is not None:
        kwargs["data"] = data
    if files is not None:
        kwargs["files"] = files

    # Step 4 needs the FY pattern from Step 1 data.
    if step_number == 4:
        fy_pattern = FY_PATTERN_APRIL_MARCH
        wizard_data = wizard.wizard_data or {}
        step1 = wizard_data.get("Society Details", {})
        fy_pattern = step1.get("financial_year_pattern", FY_PATTERN_APRIL_MARCH)
        kwargs["fy_pattern"] = fy_pattern

    return form_class(**kwargs)


def _get_step_template(step_number: int) -> str:
    """Return the template path for the given step."""
    return STEP_TEMPLATES.get(
        step_number,
        "onboarding/steps/step_society_details.html",
    )


def _base_context(wizard: OnboardingWizard, step_number: int) -> dict[str, Any]:
    """Build the common context for all step views."""
    state = WizardService.get_wizard_state(wizard)
    return {
        "wizard": wizard,
        "step_number": step_number,
        "step_name": STEP_NAMES.get(step_number, f"Step {step_number}"),
        "state": state,
        "step_names": STEP_NAMES,
        "total_steps": 28,
    }


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #

@login_required
def wizard_list(request: HttpRequest) -> HttpResponse:
    """List all wizards created by the current user."""
    wizards = (
        OnboardingWizard.objects.unscoped()
        .filter(created_by=request.user)
        .order_by("-started_at")
        .select_related("society")
    )
    return render(
        request,
        "onboarding/wizard_list.html",
        {"wizards": wizards},
    )


@login_required
def wizard_start(request: HttpRequest) -> HttpResponse:
    """Create a new wizard and redirect to Step 1."""
    if request.method != "POST":
        messages.error(request, _("Invalid request method."))
        return redirect("onboarding:wizard-list")

    try:
        wizard = WizardService.create_wizard(user=request.user)
    except ValidationError as exc:
        messages.error(request, _("Could not start wizard: %(err)s") % {"err": exc})
        return redirect("onboarding:wizard-list")

    messages.success(request, _("Wizard started. Let's set up your society."))
    return redirect(
        "onboarding:wizard-step",
        wizard_id=wizard.pk,
        step_number=1,
    )


@login_required
def wizard_detail(request: HttpRequest, wizard_id: int) -> HttpResponse:
    """Redirect to the wizard's current step."""
    wizard = _get_wizard(wizard_id, request.user)

    if wizard.status == OnboardingWizard.Status.COMPLETED:
        return redirect("onboarding:wizard-complete", wizard_id=wizard.pk)

    if wizard.status == OnboardingWizard.Status.ABANDONED:
        WizardService.resume_wizard(wizard, user=request.user)
        messages.info(request, _("Resumed an abandoned wizard."))

    return redirect(
        "onboarding:wizard-step",
        wizard_id=wizard.pk,
        step_number=wizard.current_step,
    )


@login_required
def wizard_step(
    request: HttpRequest,
    wizard_id: int,
    step_number: int,
) -> HttpResponse:
    """Render a specific wizard step.

    Validates that the step is accessible (not a future step) and
    dispatches to the appropriate form and template.
    """
    wizard = _get_wizard(wizard_id, request.user)

    # Prevent accessing future steps.
    if step_number > wizard.current_step:
        messages.error(request, _("Cannot access future steps."))
        return redirect(
            "onboarding:wizard-step",
            wizard_id=wizard.pk,
            step_number=wizard.current_step,
        )

    # Steps 12–22 (staging) redirect to the staging view.
    if step_number in STAGING_STEPS:
        template_type = STEP_TO_TEMPLATE_TYPE.get(step_number, "CHART_OF_ACCOUNTS")
        return redirect(
            "onboarding:staging-view",
            wizard_id=wizard.pk,
            template_type=template_type,
        )

    # Step 23 → reconciliation dashboard.
    if step_number == 23:
        return redirect("onboarding:reconciliation-dashboard", wizard_id=wizard.pk)

    # Step 24 → validation checklist.
    if step_number == 24:
        return redirect("onboarding:validation-checklist", wizard_id=wizard.pk)

    # Step 28 → complete page.
    if step_number == STEP_SOCIETY_READY:
        return redirect("onboarding:wizard-complete", wizard_id=wizard.pk)

    context = _base_context(wizard, step_number)

    # Special context for display-only steps.
    if step_number == 5:
        context["fy_options"] = _get_fy_options(wizard)
    elif step_number == 9:
        context["society"] = wizard.society
    elif step_number == 10:
        context["upload_summary"] = _safe_get_upload_summary(wizard)
    elif step_number == 11:
        context["template_types"] = ALL_TEMPLATE_TYPES
        context["template_columns"] = _get_template_columns()

    form = _get_step_form(step_number, wizard)
    if form is not None:
        context["form"] = form

    return render(request, _get_step_template(step_number), context)


@login_required
def wizard_step_save(
    request: HttpRequest,
    wizard_id: int,
    step_number: int,
) -> HttpResponse:
    """Save step data and advance the wizard.

    Handles each step's specific service call before advancing.
    """
    if request.method != "POST":
        messages.error(request, _("Invalid request method."))
        return redirect(
            "onboarding:wizard-step",
            wizard_id=wizard_id,
            step_number=step_number,
        )

    wizard = _get_wizard(wizard_id, request.user)

    if wizard.status != OnboardingWizard.Status.IN_PROGRESS:
        messages.error(request, _("This wizard is no longer in progress."))
        return redirect("onboarding:wizard-list")

    form = _get_step_form(step_number, wizard, data=request.POST, files=request.FILES)

    # Steps without a form (5, 9, 10, 11) just advance.
    if form is None:
        try:
            _handle_no_form_step(request, wizard, step_number)
        except ValidationError as exc:
            messages.error(request, str(exc))
            return redirect(
                "onboarding:wizard-step",
                wizard_id=wizard.pk,
                step_number=step_number,
            )
        return _advance_and_redirect(request, wizard, step_number)

    if not form.is_valid():
        context = _base_context(wizard, step_number)
        context["form"] = form
        messages.error(request, _("Please correct the errors below."))
        return render(request, _get_step_template(step_number), context)

    try:
        _handle_valid_step(request, wizard, step_number, form)
    except ValidationError as exc:
        messages.error(request, str(exc))
        context = _base_context(wizard, step_number)
        context["form"] = form
        return render(request, _get_step_template(step_number), context)

    return _advance_and_redirect(request, wizard, step_number)


def _handle_no_form_step(request, wizard: OnboardingWizard, step_number: int):
    """Handle steps that don't have a form (auto-advance steps)."""
    if step_number == 5:
        # Financial Year Creation — auto-create FY from Step 4 data.
        wizard_data = wizard.wizard_data or {}
        step4 = wizard_data.get("Accounting Start Year", {})
        fy_label = step4.get("accounting_start_year")
        if not fy_label:
            raise ValidationError(_("No accounting start year selected."))
        step1 = wizard_data.get("Society Details", {})
        fy_pattern = step1.get("financial_year_pattern", FY_PATTERN_APRIL_MARCH)
        FinancialYearSetupService.create_financial_year(
            wizard=wizard,
            start_year=fy_label,
            user=request.user,
        )

    elif step_number == 9:
        # Accounting Setup — create standard accounts for the society.
        if wizard.society is None:
            raise ValidationError(_("Society must be created before accounting setup."))
        from accounting.services.standard_accounts import ensure_standard_accounts
        from accounting.models import AccountMapping

        with tenant_context(wizard.society):
            ensure_standard_accounts(wizard.society)
            AccountMapping.ensure_for_society(wizard.society)

    elif step_number in (10, 11):
        # Chart of Accounts / Import Templates — display-only, just advance.
        pass


def _handle_valid_step(request, wizard: OnboardingWizard, step_number: int, form):
    """Dispatch to the appropriate service for each form-based step."""
    if step_number == 1:
        # Society Details — create the society.
        society = SocietySetupService.create_society(
            wizard=wizard,
            society_data=form.cleaned_data,
            user=request.user,
        )
        # Persist the society selection in the session so middleware picks it up.
        from housing_accounting.selection import _persist_selection
        _persist_selection(request, society=society, financial_year=None)

    elif step_number == 2:
        # Society Type — set on the wizard.
        WizardService.set_society_type(
            wizard=wizard,
            society_type=form.cleaned_data["society_type"],
            user=request.user,
        )

    elif step_number == 3:
        # Module Selection — configure modules.
        ModuleConfigurationService.configure_modules(
            wizard=wizard,
            selected_modules=form.get_selected_modules(),
            user=request.user,
        )

    elif step_number == 4:
        # Accounting Start Year — store the selection in wizard_data.
        WizardService.update_wizard_data(
            wizard=wizard,
            key="Accounting Start Year",
            value=form.cleaned_data,
        )

    elif step_number == 6:
        # Society Structure — create structures.
        SocietySetupService.create_structure(
            wizard=wizard,
            structures_data=form.cleaned_data["structures_json"],
            user=request.user,
        )

    elif step_number == 7:
        # Unit Configuration — create units.
        SocietySetupService.create_units(
            wizard=wizard,
            units_data=form.cleaned_data["units_json"],
            user=request.user,
        )

    elif step_number == 8:
        # Member Assignment — assign members.
        SocietySetupService.assign_members(
            wizard=wizard,
            members_data=form.cleaned_data["members_json"],
            user=request.user,
        )

    elif step_number == 25:
        # Final Approval — store the approval in wizard_data.
        WizardService.update_wizard_data(
            wizard=wizard,
            key="Final Approval",
            value={
                "confirmed": True,
                "reason": form.cleaned_data.get("reason", ""),
            },
        )


def _advance_and_redirect(request, wizard: OnboardingWizard, step_number: int):
    """Advance the wizard and redirect to the next step."""
    WizardService.advance_step(
        wizard=wizard,
        step_data=None,
        user=request.user,
    )
    wizard.refresh_from_db()
    next_step = wizard.current_step

    messages.success(
        request,
        _("Step %(step)s completed.") % {"step": step_number},
    )

    # If the next step is 28 (society ready), go to the complete page.
    if next_step == STEP_SOCIETY_READY:
        return redirect("onboarding:wizard-complete", wizard_id=wizard.pk)

    return redirect(
        "onboarding:wizard-step",
        wizard_id=wizard.pk,
        step_number=next_step,
    )


# --------------------------------------------------------------------------- #
# Staging views (Steps 12–22)
# --------------------------------------------------------------------------- #

@login_required
def staging_upload(
    request: HttpRequest,
    wizard_id: int,
    template_type: str,
) -> HttpResponse:
    """Upload a file to the staging area."""
    wizard = _get_wizard(wizard_id, request.user)

    if request.method == "POST" and request.FILES.get("file"):
        try:
            StagingService.upload_file(
                wizard=wizard,
                template_type=template_type,
                file=request.FILES["file"],
                user=request.user,
            )
            messages.success(
                request,
                _("File uploaded for %(t)s.") % {"t": template_type},
            )
        except (ValidationError, ValueError) as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, _("No file provided."))

    return redirect(
        "onboarding:staging-view",
        wizard_id=wizard.pk,
        template_type=template_type,
    )


@login_required
def staging_view(
    request: HttpRequest,
    wizard_id: int,
    template_type: str,
) -> HttpResponse:
    """View staging data and validation errors for a template type."""
    wizard = _get_wizard(wizard_id, request.user)

    try:
        staging_data = StagingService.get_staging_data(
            wizard=wizard,
            template_type=template_type,
        )
    except (ValidationError, ValueError) as exc:
        messages.error(request, str(exc))
        staging_data = {"rows": [], "summary": {}}

    try:
        validation_report = ValidationService.validate_batch(
            wizard=wizard,
            template_type=template_type,
            user=request.user,
        )
    except (ValidationError, ValueError) as exc:
        messages.error(request, str(exc))
        validation_report = {"errors": [], "summary": {}}

    context = _base_context(wizard, wizard.current_step)
    context.update({
        "template_type": template_type,
        "staging_data": staging_data,
        "validation_report": validation_report,
        "template_columns": StagingService.get_template_columns(template_type),
        "upload_summary": _safe_get_upload_summary(wizard),
    })
    return render(request, "onboarding/steps/step_staging_area.html", context)


@login_required
def staging_delete(
    request: HttpRequest,
    wizard_id: int,
    template_type: str,
) -> HttpResponse:
    """Delete a staging batch (supports delete & re-upload)."""
    if request.method != "POST":
        messages.error(request, _("Invalid request method."))
        return redirect(
            "onboarding:staging-view",
            wizard_id=wizard_id,
            template_type=template_type,
        )

    wizard = _get_wizard(wizard_id, request.user)
    try:
        StagingService.delete_batch(
            wizard=wizard,
            template_type=template_type,
            user=request.user,
        )
        messages.success(request, _("Staging data deleted. You can re-upload."))
    except (ValidationError, ValueError) as exc:
        messages.error(request, str(exc))

    return redirect(
        "onboarding:staging-view",
        wizard_id=wizard.pk,
        template_type=template_type,
    )


@login_required
def staging_approve(
    request: HttpRequest,
    wizard_id: int,
    template_type: str,
) -> HttpResponse:
    """Approve a staging batch (locks it as approved)."""
    if request.method != "POST":
        messages.error(request, _("Invalid request method."))
        return redirect(
            "onboarding:staging-view",
            wizard_id=wizard_id,
            template_type=template_type,
        )

    wizard = _get_wizard(wizard_id, request.user)
    try:
        StagingService.approve_batch(
            wizard=wizard,
            template_type=template_type,
            user=request.user,
        )
        messages.success(request, _("Staging batch approved."))
    except (ValidationError, ValueError) as exc:
        messages.error(request, str(exc))

    return redirect(
        "onboarding:staging-view",
        wizard_id=wizard.pk,
        template_type=template_type,
    )


# --------------------------------------------------------------------------- #
# Reconciliation Dashboard (Step 23)
# --------------------------------------------------------------------------- #

@login_required
def reconciliation_dashboard(
    request: HttpRequest,
    wizard_id: int,
) -> HttpResponse:
    """Render the reconciliation dashboard (Step 23)."""
    wizard = _get_wizard(wizard_id, request.user)

    dashboard = {}
    if wizard.society:
        try:
            dashboard = ReconciliationService.generate_full_dashboard(
                wizard=wizard,
                society=wizard.society,
            )
        except (ValidationError, ValueError) as exc:
            messages.error(request, str(exc))

    context = _base_context(wizard, 23)
    context["dashboard"] = dashboard
    return render(
        request,
        "onboarding/steps/step_reconciliation_dashboard.html",
        context,
    )


# --------------------------------------------------------------------------- #
# Validation Checklist (Step 24)
# --------------------------------------------------------------------------- #

@login_required
def validation_checklist(
    request: HttpRequest,
    wizard_id: int,
) -> HttpResponse:
    """Render the 9-check validation checklist (Step 24)."""
    wizard = _get_wizard(wizard_id, request.user)

    checklist = {}
    if wizard.society:
        try:
            checklist = ReconciliationService.run_checklist(
                wizard=wizard,
                society=wizard.society,
            )
        except (ValidationError, ValueError) as exc:
            messages.error(request, str(exc))

    context = _base_context(wizard, 24)
    context["checklist"] = checklist
    context["can_finalize"] = checklist.get("can_finalize", False)
    return render(
        request,
        "onboarding/steps/step_migration_validation_checklist.html",
        context,
    )


# --------------------------------------------------------------------------- #
# Finalize Migration (Steps 25–27)
# --------------------------------------------------------------------------- #

@login_required
def finalize_migration(
    request: HttpRequest,
    wizard_id: int,
) -> HttpResponse:
    """Handle final approval and trigger migration finalization (Steps 25–27)."""
    wizard = _get_wizard(wizard_id, request.user)

    if request.method == "POST":
        form = FinalApprovalForm(request.POST)
        if not form.is_valid():
            context = _base_context(wizard, 25)
            context["form"] = form
            messages.error(request, _("Please confirm the final approval."))
            return render(
                request,
                "onboarding/steps/step_final_approval.html",
                context,
            )

        if wizard.society is None:
            messages.error(request, _("Society must be created before finalizing."))
            return redirect(
                "onboarding:wizard-step",
                wizard_id=wizard.pk,
                step_number=wizard.current_step,
            )

        try:
            with transaction.atomic():
                # Step 26: Create the opening journal.
                voucher = MigrationFinalizationService.create_opening_journal(
                    wizard=wizard,
                    society=wizard.society,
                    user=request.user,
                )
                # Step 27: Lock the migration.
                MigrationFinalizationService.lock_migration(
                    wizard=wizard,
                    society=wizard.society,
                    user=request.user,
                )
                # Mark the wizard as complete.
                WizardService.complete_wizard(wizard, user=request.user)
        except ValidationError as exc:
            messages.error(request, str(exc))
            context = _base_context(wizard, 25)
            context["form"] = form
            return render(
                request,
                "onboarding/steps/step_final_approval.html",
                context,
            )

        messages.success(
            request,
            _("Migration finalized. Opening journal created and migration locked."),
        )
        return redirect("onboarding:wizard-complete", wizard_id=wizard.pk)

    # GET — render the final approval form.
    form = FinalApprovalForm()
    context = _base_context(wizard, 25)
    context["form"] = form

    # Provide finalization summary for display.
    if wizard.society:
        try:
            context["finalization_summary"] = (
                MigrationFinalizationService.get_finalization_summary(
                    wizard=wizard,
                    society=wizard.society,
                )
            )
        except (ValidationError, ValueError):
            context["finalization_summary"] = {}

    return render(
        request,
        "onboarding/steps/step_final_approval.html",
        context,
    )


# --------------------------------------------------------------------------- #
# Wizard Complete (Step 28)
# --------------------------------------------------------------------------- #

@login_required
def wizard_complete(
    request: HttpRequest,
    wizard_id: int,
) -> HttpResponse:
    """Render the success / summary page (Step 28)."""
    wizard = _get_wizard(wizard_id, request.user)

    finalization_summary = {}
    if wizard.society:
        try:
            finalization_summary = (
                MigrationFinalizationService.get_finalization_summary(
                    wizard=wizard,
                    society=wizard.society,
                )
            )
        except (ValidationError, ValueError):
            pass

    context = _base_context(wizard, STEP_SOCIETY_READY)
    context["finalization_summary"] = finalization_summary
    context["society"] = wizard.society
    return render(request, "onboarding/wizard_complete.html", context)


# --------------------------------------------------------------------------- #
# Safe helper wrappers (never raise)
# --------------------------------------------------------------------------- #

def _safe_get_upload_summary(wizard: OnboardingWizard) -> dict:
    """Return the upload summary, or an empty dict on error."""
    try:
        return StagingService.get_upload_summary(wizard)
    except Exception:  # noqa: BLE001
        logger.debug("Failed to get upload summary for wizard %s", wizard.pk)
        return {}


def _get_fy_options(wizard: OnboardingWizard) -> list[str]:
    """Return FY options based on the wizard's Step 1 data."""
    wizard_data = wizard.wizard_data or {}
    step1 = wizard_data.get("Society Details", {})
    fy_pattern = step1.get("financial_year_pattern", FY_PATTERN_APRIL_MARCH)
    try:
        return FinancialYearSetupService.get_fy_options(fy_pattern=fy_pattern)
    except Exception:  # noqa: BLE001
        return []


def _get_template_columns() -> dict[str, tuple[str, ...]]:
    """Return template column definitions for the import templates step."""
    from onboarding.services.staging_service import TEMPLATE_COLUMNS
    return TEMPLATE_COLUMNS
