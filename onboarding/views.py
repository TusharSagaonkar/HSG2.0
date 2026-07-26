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

import json
import logging
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from onboarding.forms import (
    AccountingStartYearForm,
    FinalApprovalForm,
    ModuleSelectionForm,
    SocietyDetailsForm,
    SocietyTypeForm,
    UnitConfigurationForm,
)
from housing.forms import StructureForm as HousingStructureForm
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
_UNSET = object()


# --------------------------------------------------------------------------- #
# Step → Form / Template mapping
# --------------------------------------------------------------------------- #

STEP_FORMS: dict[int, type] = {
    1: SocietyDetailsForm,
    2: SocietyTypeForm,
    3: ModuleSelectionForm,
    4: AccountingStartYearForm,
    6: HousingStructureForm,  # Use the housing structure form instead
    7: UnitConfigurationForm,
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

    # Step 6 needs society ID for structure form initialization
    elif step_number == 6 and wizard.society:
        kwargs["initial"] = {"society": wizard.society.id}

    # Step 7 needs society ID for structure queryset in bulk generation
    elif step_number == 7 and wizard.society:
        kwargs["initial"] = {"society": wizard.society.id}

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
    nav = WizardService.get_step_navigation(wizard, step_number)
    return {
        "wizard": wizard,
        "step_number": step_number,
        "step_name": STEP_NAMES.get(step_number, f"Step {step_number}"),
        "state": state,
        "step_names": STEP_NAMES,
        "total_steps": 28,
        "previous_step": nav["previous_step"],
        "next_step": nav["next_step"],
        "next_step_enabled": nav["next_step_enabled"],
    }


def _step7_structure_context(wizard: OnboardingWizard) -> dict[str, Any]:
    """Context for Step 7: all structures + per-structure unit summary.

    Units can be attached to any structure (building, wing, block, tower,
    floor), so the grid and bulk-generate modal need the full hierarchy.
    """
    if not wizard.society:
        return {
            "structure_options": [],
            "unit_summary": [],
            "unit_summary_total": 0,
        }

    from django.db.models import Count

    from members.models import Structure, Unit

    all_structures = list(
        Structure.objects.filter(society=wizard.society)
        .order_by("display_order", "name", "id")
        .values("id", "name", "structure_type", "parent", "display_order")
    )
    by_id = {s["id"]: s for s in all_structures}
    type_label = dict(Structure.StructureType.choices)

    def _ancestor_path(sid: int) -> list[int]:
        path: list[int] = []
        cur = by_id.get(sid)
        seen: set[int] = set()
        while cur and cur["parent"] and cur["parent"] not in seen:
            path.append(cur["parent"])
            seen.add(cur["parent"])
            cur = by_id.get(cur["parent"])
        return list(reversed(path))

    def _path_label(sid: int) -> str:
        ids = [*_ancestor_path(sid), sid]
        return " > ".join(by_id[item]["name"] for item in ids if item in by_id)

    # Render parents before their descendants instead of using a flat
    # alphabetical list. Structures with a missing parent are treated as roots.
    children_by_parent: dict[int | None, list[dict]] = {}
    for structure in all_structures:
        parent_id = structure["parent"]
        if parent_id not in by_id:
            parent_id = None
        children_by_parent.setdefault(parent_id, []).append(structure)

    for children in children_by_parent.values():
        children.sort(key=lambda item: (item["display_order"], item["name"], item["id"]))

    ordered_structures: list[dict] = []

    def _append_branch(parent_id: int | None) -> None:
        for child in children_by_parent.get(parent_id, []):
            ordered_structures.append(child)
            _append_branch(child["id"])

    _append_branch(None)

    structure_options: list[dict] = []
    for s in ordered_structures:
        depth = len(_ancestor_path(s["id"]))
        indent = "\u00A0\u00A0" * depth + ("\u2014\u00A0" if depth else "")
        structure_options.append(
            {
                "id": s["id"],
                "name": s["name"],
                "label": (
                    f"{indent}{s['name']} "
                    f"({type_label.get(s['structure_type'], s['structure_type'])})"
                ),
                "type": s["structure_type"],
                "depth": depth,
                "path_label": _path_label(s["id"]),
            }
        )

    counts_by_struct = {
        row["structure"]: row["c"]
        for row in Unit.objects.filter(structure__society=wizard.society)
        .values("structure")
        .annotate(c=Count("id"))
    }

    unit_summary: list[dict] = []
    for s in ordered_structures:
        ids = list(
            Unit.objects.filter(structure_id=s["id"])
            .order_by("identifier")
            .values_list("identifier", flat=True)[:2000]
        )
        unit_summary.append(
            {
                "id": s["id"],
                "name": s["name"],
                "type": s["structure_type"],
                "type_label": type_label.get(
                    s["structure_type"], s["structure_type"]
                ),
                "depth": len(_ancestor_path(s["id"])),
                "path_label": _path_label(s["id"]),
                "total": counts_by_struct.get(s["id"], 0),
                "sample": ids[:5],
                "identifiers": ids,
            }
        )

    return {
        "structure_options": structure_options,
        "unit_summary": unit_summary,
        "unit_summary_total": sum(u["total"] for u in unit_summary),
    }


def _step8_member_context(wizard: OnboardingWizard) -> dict[str, Any]:
    """Context for Step 8: structures with their units and existing members.

    Returns a JSON-serialisable snapshot so the two-pane UI can render
    without an extra AJAX round-trip on page load. Members are grouped by
    unit id for easy lookup by the template/JS.
    """
    empty = {
        "structures": [],
        "structures_json": "[]",
        "members_by_unit": {},
        "members_by_unit_json": "{}",
        "has_society": False,
    }
    if not wizard.society:
        return empty

    from members.models import Member, Structure, Unit

    structures = list(
        Structure.objects.filter(society=wizard.society)
        .order_by("display_order", "name", "id")
        .values("id", "name", "structure_type", "parent", "display_order")
    )
    by_id = {s["id"]: s for s in structures}
    type_label = dict(Structure.StructureType.choices)

    # Order parents before descendants (mirrors Step 7 logic).
    children_by_parent: dict[int | None, list[dict]] = {}
    for structure in structures:
        parent_id = structure["parent"]
        if parent_id not in by_id:
            parent_id = None
        children_by_parent.setdefault(parent_id, []).append(structure)
    for children in children_by_parent.values():
        children.sort(
            key=lambda item: (item["display_order"], item["name"], item["id"])
        )

    ordered: list[dict] = []

    def _append_branch(parent_id: int | None) -> None:
        for child in children_by_parent.get(parent_id, []):
            ordered.append(child)
            _append_branch(child["id"])

    _append_branch(None)

    def _ancestor_path(sid: int) -> list[int]:
        path: list[int] = []
        cur = by_id.get(sid)
        seen: set[int] = set()
        while cur and cur["parent"] and cur["parent"] not in seen:
            path.append(cur["parent"])
            seen.add(cur["parent"])
            cur = by_id.get(cur["parent"])
        return list(reversed(path))

    units_qs = (
        Unit.objects.filter(structure__society=wizard.society)
        .select_related("structure")
        .order_by("structure_id", "identifier")
        .values("id", "identifier", "unit_type", "structure_id", "area_sqft")
    )

    unit_label = dict(Unit.UnitType.choices)
    units_by_structure: dict[int, list[dict]] = {}
    for u in units_qs:
        units_by_structure.setdefault(u["structure_id"], []).append(
            {
                "id": u["id"],
                "identifier": u["identifier"],
                "unit_type": u["unit_type"],
                "unit_type_label": unit_label.get(
                    u["unit_type"], u["unit_type"]
                ),
                "area_sqft": str(u["area_sqft"]) if u["area_sqft"] else "",
            }
        )

    structures_json: list[dict] = []
    for s in ordered:
        depth = len(_ancestor_path(s["id"]))
        path_ids = [*_ancestor_path(s["id"]), s["id"]]
        path_label = " > ".join(
            by_id[i]["name"] for i in path_ids if i in by_id
        )
        structures_json.append(
            {
                "id": s["id"],
                "name": s["name"],
                "structure_type": s["structure_type"],
                "structure_type_label": type_label.get(
                    s["structure_type"], s["structure_type"]
                ),
                "depth": depth,
                "path_label": path_label,
                "units": units_by_structure.get(s["id"], []),
            }
        )

    role_label = dict(Member.MemberRole.choices)
    status_label = dict(Member.MemberStatus.choices)
    members = (
        Member.objects.filter(society=wizard.society)
        .select_related("unit")
        .order_by("unit_id", "full_name")
        .values(
            "id",
            "full_name",
            "email",
            "phone",
            "role",
            "status",
            "start_date",
            "unit_id",
        )
    )
    members_by_unit: dict[str, list[dict]] = {}
    for m in members:
        members_by_unit.setdefault(str(m["unit_id"]), []).append(
            {
                "id": m["id"],
                "full_name": m["full_name"],
                "email": m["email"] or "",
                "phone": m["phone"] or "",
                "role": m["role"],
                "role_label": role_label.get(m["role"], m["role"]),
                "status": m["status"],
                "status_label": status_label.get(m["status"], m["status"]),
                "start_date": (
                    m["start_date"].isoformat() if m["start_date"] else ""
                ),
            }
        )

    return {
        "structures": structures_json,
        "structures_json": json.dumps(structures_json),
        "members_by_unit": members_by_unit,
        "members_by_unit_json": json.dumps(members_by_unit),
        "has_society": True,
    }


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #

@login_required
def wizard_list(request: HttpRequest) -> HttpResponse:
    """List all wizards created by the current user."""
    wizards = list(
        OnboardingWizard.objects.unscoped()
        .filter(created_by=request.user)
        .order_by("-started_at")
        .select_related("society")
    )

    # Summary stats for the dashboard cards.
    in_progress_count = sum(
        1 for w in wizards if w.status == OnboardingWizard.Status.IN_PROGRESS
    )
    completed_count = sum(
        1 for w in wizards if w.status == OnboardingWizard.Status.COMPLETED
    )
    abandoned_count = sum(
        1 for w in wizards if w.status == OnboardingWizard.Status.ABANDONED
    )

    return render(
        request,
        "onboarding/wizard_list.html",
        {
            "wizards": wizards,
            "in_progress_count": in_progress_count,
            "completed_count": completed_count,
            "abandoned_count": abandoned_count,
        },
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
        context["selected_fy"] = _get_selected_fy_label(wizard)
    elif step_number == 6:
        # Add existing structures for the structure tree display
        if wizard.society:
            from members.models import Structure
            existing_structures = Structure.objects.filter(
                society=wizard.society,
                parent__isnull=True
            ).prefetch_related('children').order_by('display_order', 'name')
            context["existing_structures"] = existing_structures
        else:
            context["existing_structures"] = []
    elif step_number == 7:
        context.update(_step7_structure_context(wizard))
    elif step_number == 8:
        context.update(_step8_member_context(wizard))
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
def wizard_units_delete(
    request: HttpRequest,
    wizard_id: int,
    structure_id: int,
) -> HttpResponse:
    """Delete all units on a structure from onboarding Step 7 summary."""
    if request.method != "POST":
        messages.error(request, _("Invalid request method."))
        return redirect("onboarding:wizard-step", wizard_id=wizard_id, step_number=7)

    wizard = _get_wizard(wizard_id, request.user)
    if wizard.status != OnboardingWizard.Status.IN_PROGRESS:
        messages.error(request, _("This wizard is no longer in progress."))
        return redirect("onboarding:wizard-list")

    try:
        deleted = SocietySetupService.delete_units_for_structure(
            wizard=wizard,
            structure_id=structure_id,
            user=request.user,
        )
        if deleted:
            messages.success(
                request,
                _("%(count)s unit(s) deleted.") % {"count": deleted},
            )
        else:
            messages.info(request, _("No units to delete on that structure."))
    except ValidationError as exc:
        messages.error(request, str(exc))

    return redirect("onboarding:wizard-step", wizard_id=wizard.pk, step_number=7)


# --------------------------------------------------------------------------- #
# Step 8 — Member CRUD JSON API (scoped to the wizard's society)
# --------------------------------------------------------------------------- #

def _serialize_member(member) -> dict:
    """Return a JSON-safe representation of a :class:`Member`."""
    role_label = dict(member.MemberRole.choices)
    status_label = dict(member.MemberStatus.choices)
    return {
        "id": member.id,
        "full_name": member.full_name,
        "email": member.email or "",
        "phone": member.phone or "",
        "role": member.role,
        "role_label": role_label.get(member.role, member.role),
        "status": member.status,
        "status_label": status_label.get(member.status, member.status),
        "start_date": (
            member.start_date.isoformat() if member.start_date else ""
        ),
        "unit_id": member.unit_id,
    }


def _member_lifecycle_user(member):
    """Resolve the user used by membership lifecycle synchronization."""
    if member.user_id:
        return member.user

    email = (member.email or "").strip()
    if not email:
        return None

    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(email__iexact=email).first()


def _end_member_lifecycle(
    member,
    *,
    end_date=None,
    lifecycle_user=_UNSET,
    lifecycle_role=None,
) -> None:
    """End active ownership and occupancy rows associated with a member."""
    from datetime import timedelta

    from members.models import UnitOccupancy, UnitOwnership

    role = lifecycle_role or member.role
    occupancy_type = {
        member.MemberRole.OWNER: UnitOccupancy.OccupancyType.OWNER,
        member.MemberRole.TENANT: UnitOccupancy.OccupancyType.TENANT,
    }.get(role)
    if occupancy_type is None:
        return

    if lifecycle_user is _UNSET:
        lifecycle_user = _member_lifecycle_user(member)
    effective_end_date = (end_date or timezone.localdate()) - timedelta(days=1)
    if lifecycle_user is None:
        UnitOccupancy.objects.filter(
            unit=member.unit,
            occupant__isnull=True,
            occupancy_type=occupancy_type,
            end_date__isnull=True,
        ).update(end_date=effective_end_date)
        return

    if role == member.MemberRole.OWNER:
        UnitOwnership.objects.filter(
            unit=member.unit,
            owner=lifecycle_user,
            end_date__isnull=True,
        ).update(end_date=effective_end_date)
    UnitOccupancy.objects.filter(
        unit=member.unit,
        occupant=lifecycle_user,
        occupancy_type=occupancy_type,
        end_date__isnull=True,
    ).update(end_date=effective_end_date)


def _step8_api_get_member(wizard: OnboardingWizard, member_id: int):
    """Fetch a member scoped to the wizard's society or return None."""
    if not wizard.society:
        return None
    from members.models import Member

    return (
        Member.objects.filter(society=wizard.society, pk=member_id)
        .select_related("unit")
        .first()
    )


@login_required
def wizard_member_list_api(
    request: HttpRequest, wizard_id: int
) -> JsonResponse:
    """GET: return structures (with units) + members grouped by unit."""
    wizard = _get_wizard(wizard_id, request.user)
    if wizard.status != OnboardingWizard.Status.IN_PROGRESS:
        return JsonResponse(
            {"error": "This wizard is no longer in progress."}, status=403
        )
    if not wizard.society:
        return JsonResponse(
            {"error": "Society has not been created yet."}, status=400
        )

    context = _step8_member_context(wizard)
    return JsonResponse(
        {
            "structures": json.loads(context["structures_json"]),
            "members_by_unit": json.loads(context["members_by_unit_json"]),
        }
    )


@login_required
@require_http_methods(["POST"])
def wizard_member_create_api(
    request: HttpRequest, wizard_id: int
) -> JsonResponse:
    """POST: create a member via :class:`MemberForm` and sync lifecycle."""
    wizard = _get_wizard(wizard_id, request.user)
    if wizard.status != OnboardingWizard.Status.IN_PROGRESS:
        return JsonResponse(
            {"error": "This wizard is no longer in progress."}, status=403
        )
    if not wizard.society:
        return JsonResponse(
            {"error": "Society has not been created yet."}, status=400
        )

    from members.models import Unit
    from housing.forms import MemberForm
    from housing.services.membership_lifecycle import sync_member_unit_lifecycle

    try:
        unit = Unit.objects.get(
            pk=request.POST.get("unit_id"),
            structure__society=wizard.society,
        )
    except (Unit.DoesNotExist, ValueError, TypeError):
        return JsonResponse(
            {"errors": {"unit_id": ["Unit not found in this society."]}},
            status=400,
        )

    data = request.POST.copy()
    data["society"] = wizard.society.id
    data["unit"] = unit.id

    form = MemberForm(
        data=data,
        society=wizard.society,
        current_user=request.user,
    )
    if not form.is_valid():
        return JsonResponse({"errors": form.errors}, status=400)

    try:
        with transaction.atomic():
            member = form.save()
            if member.status == member.MemberStatus.ACTIVE:
                sync_member_unit_lifecycle(member)
    except Exception as exc:
        logger.exception("Failed to create member in step 8 API")
        return JsonResponse(
            {"errors": {"__all__": [str(exc)]}}, status=400
        )

    return JsonResponse(
        {"ok": True, "member": _serialize_member(member)}, status=201
    )


@login_required
@require_http_methods(["POST"])
def wizard_member_update_api(
    request: HttpRequest, wizard_id: int, member_id: int
) -> JsonResponse:
    """POST (PATCH semantics): update an existing member."""
    wizard = _get_wizard(wizard_id, request.user)
    if wizard.status != OnboardingWizard.Status.IN_PROGRESS:
        return JsonResponse(
            {"error": "This wizard is no longer in progress."}, status=403
        )

    member = _step8_api_get_member(wizard, member_id)
    if member is None:
        return JsonResponse({"error": "Member not found."}, status=404)

    from housing.forms import MemberForm
    from housing.services.membership_lifecycle import sync_member_unit_lifecycle

    old_role = member.role
    old_email = (member.email or "").strip().casefold()
    old_status = member.status
    old_start_date = member.start_date
    old_lifecycle_user = _member_lifecycle_user(member)

    data = request.POST.copy()
    data["society"] = wizard.society.id
    data["unit"] = member.unit_id
    # The onboarding modal intentionally exposes a lean subset of MemberForm.
    # Preserve fields managed elsewhere instead of clearing them on update.
    data["user"] = member.user_id or ""
    data["receivable_account"] = member.receivable_account_id or ""
    data["end_date"] = member.end_date.isoformat() if member.end_date else ""

    form = MemberForm(
        data=data,
        instance=member,
        society=wizard.society,
        current_user=request.user,
    )
    if not form.is_valid():
        return JsonResponse({"errors": form.errors}, status=400)

    try:
        with transaction.atomic():
            new_role = form.cleaned_data["role"]
            new_email = (form.cleaned_data.get("email") or "").strip()
            lifecycle_changed = (
                old_role != new_role
                or old_email != new_email.casefold()
                or old_status != form.cleaned_data["status"]
                or old_start_date != form.cleaned_data.get("start_date")
            )
            if lifecycle_changed:
                _end_member_lifecycle(
                    member,
                    end_date=form.cleaned_data.get("start_date"),
                    lifecycle_user=old_lifecycle_user,
                    lifecycle_role=old_role,
                )
            member = form.save()
            if lifecycle_changed and member.status == member.MemberStatus.ACTIVE:
                sync_member_unit_lifecycle(member)
    except Exception as exc:
        logger.exception("Failed to update member %s in step 8 API", member_id)
        return JsonResponse(
            {"errors": {"__all__": [str(exc)]}}, status=400
        )

    return JsonResponse({"ok": True, "member": _serialize_member(member)})


@login_required
@require_http_methods(["POST"])
def wizard_member_delete_api(
    request: HttpRequest, wizard_id: int, member_id: int
) -> JsonResponse:
    """POST (DELETE semantics): hard-delete a member + end-date lifecycle rows."""
    wizard = _get_wizard(wizard_id, request.user)
    if wizard.status != OnboardingWizard.Status.IN_PROGRESS:
        return JsonResponse(
            {"error": "This wizard is no longer in progress."}, status=403
        )

    member = _step8_api_get_member(wizard, member_id)
    if member is None:
        return JsonResponse({"error": "Member not found."}, status=404)

    try:
        with transaction.atomic():
            _end_member_lifecycle(member)
            member.delete()
    except Exception as exc:
        logger.exception(
            "Failed to delete member %s in step 8 API", member_id
        )
        return JsonResponse(
            {"errors": {"__all__": [str(exc)]}}, status=400
        )

    return JsonResponse({"ok": True})


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

    # Steps without a form (5, 8, 9, 10, 11) just advance.
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

    # Step 7 Continue with an empty grid: allow advance when units already
    # exist on the society (user may have saved in a previous visit).
    if step_number == 7 and "continue_wizard" in request.POST:
        import json as _json

        from members.models import Unit

        raw_units = (request.POST.get("units_json") or "").strip()
        try:
            parsed_units = _json.loads(raw_units) if raw_units else []
        except (TypeError, ValueError):
            parsed_units = None
        grid_empty = isinstance(parsed_units, list) and len(parsed_units) == 0
        if grid_empty:
            has_units = bool(
                wizard.society
                and Unit.objects.filter(structure__society=wizard.society).exists()
            )
            if has_units:
                return _advance_and_redirect(request, wizard, step_number)
            messages.error(
                request,
                _("Please add and save at least one unit before continuing."),
            )
            context = _base_context(wizard, step_number)
            context["form"] = form
            context.update(_step7_structure_context(wizard))
            return render(request, _get_step_template(step_number), context)

    if not form.is_valid():
        context = _base_context(wizard, step_number)
        context["form"] = form
        if step_number == 7:
            context.update(_step7_structure_context(wizard))
        messages.error(request, _("Please correct the errors below."))
        return render(request, _get_step_template(step_number), context)

    try:
        result = _handle_valid_step(request, wizard, step_number, form)
    except ValidationError as exc:
        messages.error(request, str(exc))
        context = _base_context(wizard, step_number)
        context["form"] = form
        if step_number == 7:
            context.update(_step7_structure_context(wizard))
        return render(request, _get_step_template(step_number), context)

    # Handlers may return a redirect to stay on the same step (e.g. Save Units).
    if result is not None:
        return result

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

    elif step_number == 8:
        # Member Assignment — members are persisted via AJAX in the UI.
        # Only guard the advance: require at least one active member.
        from members.models import Member

        if not wizard.society or not Member.objects.filter(
            society=wizard.society, status=Member.MemberStatus.ACTIVE
        ).exists():
            raise ValidationError(
                _("Please add at least one active member before continuing.")
            )

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
        # Society Structure — create individual structure using housing form.
        if 'add_structure' in request.POST:
            # User clicked "Add Structure" - create the structure and stay on step 6
            from members.models import Structure
            structure = Structure.objects.create(
                society=form.cleaned_data['society'],
                parent=form.cleaned_data.get('parent'),
                structure_type=form.cleaned_data['structure_type'],
                name=form.cleaned_data['name'],
                display_order=form.cleaned_data.get('display_order', 0)
            )
            messages.success(request, f"Structure '{structure.name}' created successfully.")
            # Don't advance the wizard, just return to the same step to add more structures
            return redirect(
                "onboarding:wizard-step",
                wizard_id=wizard.pk,
                step_number=step_number,
            )
        elif 'continue_wizard' in request.POST:
            # User clicked "Continue to Next Step" - advance to step 7
            pass  # This will fall through to normal wizard advancement

    elif step_number == 7:
        # Unit Configuration — create units from the grid.
        # "Save Units" stays on this step; "Continue" advances afterward.
        units_data = form.cleaned_data["units_json"]
        created = SocietySetupService.create_units(
            wizard=wizard,
            units_data=units_data,
            user=request.user,
        )
        messages.success(
            request,
            _("%(count)s unit(s) saved.") % {"count": len(created)},
        )
        if "save_units" in request.POST and "continue_wizard" not in request.POST:
            return redirect(
                "onboarding:wizard-step",
                wizard_id=wizard.pk,
                step_number=step_number,
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
    if step_number < wizard.current_step:
        messages.success(
            request,
            _("Step %(step)s updated.") % {"step": step_number},
        )
        return redirect(
            "onboarding:wizard-step",
            wizard_id=wizard.pk,
            step_number=wizard.current_step,
        )

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
def template_download(
    request: HttpRequest,
    template_type: str,
) -> HttpResponse:
    """Download a blank CSV template for the given template type.

    Generates a CSV file with the expected column headers so users can
    fill in their data and upload it to the staging area.
    """
    import csv
    from django.http import HttpResponse as DjangoHttpResponse

    try:
        columns = StagingService.get_template_columns(template_type)
    except (ValueError, ValidationError) as exc:
        messages.error(request, str(exc))
        return redirect("onboarding:wizard-list")

    canonical = StagingService._normalize_template_type(template_type)
    filename = f"{canonical.lower()}_template.csv"

    response = DjangoHttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow(columns)
    # Add one empty row as a placeholder
    writer.writerow(["" for _ in columns])

    return response


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


def _get_selected_fy_label(wizard: OnboardingWizard) -> str | None:
    """Return the FY label selected in Step 4, or None if not selected."""
    wizard_data = wizard.wizard_data or {}
    step4 = wizard_data.get("Accounting Start Year", {})
    return step4.get("accounting_start_year")


def _get_template_columns() -> dict[str, tuple[str, ...]]:
    """Return template column definitions for the import templates step."""
    from onboarding.services.staging_service import TEMPLATE_COLUMNS
    return TEMPLATE_COLUMNS
