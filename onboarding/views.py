"""Views for the Society Creation & Accounting Migration Wizard.

All views are function-based with ``@login_required`` (per the
implementation plan §4.3). They delegate all business logic to the
service layer and never mutate models directly.

Step dispatch:
    The ``wizard_step`` view uses ``STEP_FORMS`` and ``STEP_TEMPLATES``
    dicts to map a step number to its form class and template name.
    Steps without a form (7, 8–23, 25–27) render display-only
    templates or redirect to specialised views (staging, reconciliation,
    checklist, finalize, complete).
"""

from __future__ import annotations

import json
import logging
import csv
import io
import os
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
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
    build_template_entry_formset,
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
    OPTIONAL_MODULES,
    STEP_NAMES,
    STEP_SOCIETY_READY,
    WizardService,
)
from societies.utils import tenant_context

try:  # pragma: no cover - optional dependency
    import openpyxl  # type: ignore
except ImportError:  # pragma: no cover
    openpyxl = None  # type: ignore

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
    5: HousingStructureForm,  # Use the housing structure form instead
    6: UnitConfigurationForm,
    24: FinalApprovalForm,
}

STEP_TEMPLATES: dict[int, str] = {
    1: "onboarding/steps/step_society_details.html",
    2: "onboarding/steps/step_society_type.html",
    3: "onboarding/steps/step_module_selection.html",
    4: "onboarding/steps/step_accounting_start_year.html",
    5: "onboarding/steps/step_structure.html",
    6: "onboarding/steps/step_unit_configuration.html",
    7: "onboarding/steps/step_member_assignment.html",
    8: "onboarding/steps/step_accounting_setup.html",
    9: "onboarding/steps/step_chart_of_accounts.html",
    10: "onboarding/steps/step_import_templates.html",
    11: "onboarding/steps/step_staging_area.html",
    12: "onboarding/steps/step_import_validation.html",
    13: "onboarding/steps/step_delete_reupload.html",
    14: "onboarding/steps/step_opening_trial_balance.html",
    15: "onboarding/steps/step_member_outstanding.html",
    16: "onboarding/steps/step_vendor_outstanding.html",
    17: "onboarding/steps/step_bank_opening.html",
    18: "onboarding/steps/step_cash_opening.html",
    19: "onboarding/steps/step_funds.html",
    20: "onboarding/steps/step_fixed_assets.html",
    21: "onboarding/steps/step_loans.html",
    22: "onboarding/steps/step_reconciliation_dashboard.html",
    23: "onboarding/steps/step_migration_validation_checklist.html",
    24: "onboarding/steps/step_final_approval.html",
    25: "onboarding/steps/step_create_opening_journal.html",
    26: "onboarding/steps/step_lock_migration.html",
    27: "onboarding/steps/step_society_ready.html",
}

# Steps that map to the staging area (upload / view / validate).
STAGING_STEPS = {11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21}

# Maps staging steps to their template type.
STEP_TO_TEMPLATE_TYPE: dict[int, str] = {
    11: "CHART_OF_ACCOUNTS",  # staging area overview
    14: "TRIAL_BALANCE",
    15: "MEMBER_OUTSTANDING",
    16: "VENDOR_OUTSTANDING",
    17: "BANK_OPENING",
    18: "CASH_OPENING",
    19: "FUNDS",
    20: "FIXED_ASSETS",
    21: "LOANS",
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
    wizard_data = wizard.wizard_data or {}
    initial: dict[str, Any] = {}

    if step_number == 1:
        # Society's main fields live on Society; the remaining form values
        # are retained as a wizard snapshot by the setup service.
        initial.update(wizard_data.get("society_details", {}))
        if wizard.society_id:
            initial.update({
                "name": wizard.society.name,
                "registration_number": wizard.society.registration_number or "",
                "address": wizard.society.address or "",
            })
        if "timezone" in initial and "time_zone" not in initial:
            initial["time_zone"] = initial["timezone"]
    elif step_number == 2 and wizard.society_type:
        initial["society_type"] = wizard.society_type
    elif step_number == 3:
        selected = set(wizard.selected_modules or [])
        initial.update({key: key in selected for key in OPTIONAL_MODULES})
    elif step_number == 4:
        initial.update(wizard_data.get("Accounting Start Year", {}))
    elif step_number == 24:
        initial.update(wizard_data.get("Final Approval", {}))

    if data is not None:
        kwargs["data"] = data
    if files is not None:
        kwargs["files"] = files

    if initial:
        kwargs["initial"] = initial

    # Step 4 needs the FY pattern from Step 1 data.
    if step_number == 4:
        fy_pattern = FY_PATTERN_APRIL_MARCH
        step1 = wizard_data.get(
            "Society Details", wizard_data.get("society_details", {})
        )
        fy_pattern = step1.get("financial_year_pattern", FY_PATTERN_APRIL_MARCH)
        kwargs["fy_pattern"] = fy_pattern

    # Step 6 needs society ID for structure form initialization
    elif step_number == 5 and wizard.society:
        initial["society"] = wizard.society.id

    # Step 7 needs society ID for structure queryset in bulk generation
    elif step_number == 6 and wizard.society:
        initial["society"] = wizard.society.id
        initial["units_json"] = _step7_units_json(wizard)

    if initial:
        kwargs["initial"] = initial

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
    visible_steps = WizardService.visible_steps(wizard)
    progress_index = visible_steps.index(step_number) + 1 if step_number in visible_steps else 1
    return {
        "wizard": wizard,
        "step_number": step_number,
        "step_name": STEP_NAMES.get(step_number, f"Step {step_number}"),
        "state": state,
        "step_names": STEP_NAMES,
        "total_steps": 9 if wizard.society_type == OnboardingWizard.SocietyType.NEW else 27,
        "progress_index": progress_index,
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


def _step7_units_json(wizard: OnboardingWizard) -> str:
    """Serialize saved units for the editable Step 7 grid."""
    if not wizard.society_id:
        return "[]"

    from members.models import Unit

    metadata = {
        (str(row.get("structure_id")), str(row.get("flat_number"))): row
        for row in (wizard.wizard_data or {}).get("units_metadata", [])
        if isinstance(row, dict)
    }
    units = []
    for unit in Unit.objects.filter(structure__society_id=wizard.society_id).select_related(
        "structure"
    ).order_by("structure_id", "identifier"):
        saved = metadata.get((str(unit.structure_id), unit.identifier), {})
        usage_type = {
            "FLAT": "RESIDENTIAL",
            "SHOP": "COMMERCIAL",
            "OFFICE": "COMMERCIAL",
            "OTHER": "MIXED",
        }.get(unit.unit_type, "RESIDENTIAL")
        units.append({
            "flat_number": unit.identifier,
            "structure_id": unit.structure_id,
            "building": unit.structure.name,
            "floor": saved.get("floor"),
            "unit_type": unit.unit_type,
            "usage_type": usage_type,
            "area": str(unit.area_sqft) if unit.area_sqft is not None else "",
            "parking_allocation": saved.get("parking_allocation", ""),
        })
    return json.dumps(units)


def _step8_member_context(wizard: OnboardingWizard) -> dict[str, Any]:
    """Context for Step 7: structures with their units and existing members.

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


def _step9_account_context(wizard: OnboardingWizard) -> dict[str, Any]:
    """Context for Step 8: accounting bootstrap confirmation."""
    return {
        "has_society": bool(wizard.society),
    }


def _step8_summary_context(wizard: OnboardingWizard) -> dict[str, Any]:
    """Build the full review of information collected before Step 8."""
    wizard_data = wizard.wizard_data or {}
    society_details = wizard_data.get(
        "Society Details", wizard_data.get("society_details", {})
    )
    labels = {
        "name": _("Society Name"), "registration_number": _("Registration Number"),
        "registration_date": _("Registration Date"), "society_type": _("Society Category"),
        "address": _("Address"), "city": _("City"), "state": _("State"),
        "country": _("Country"), "pin_code": _("PIN Code"), "pan": _("PAN"),
        "gst_number": _("GST Number"), "tan": _("TAN"), "email": _("Contact Email"),
        "phone": _("Phone"), "time_zone": _("Time Zone"), "currency": _("Currency"),
        "financial_year_pattern": _("Financial Year Pattern"),
    }
    details = dict(society_details)
    if wizard.society:
        details.update({key: value for key, value in {
            "name": wizard.society.name,
            "registration_number": wizard.society.registration_number,
            "address": wizard.society.address,
        }.items() if value})
    society_rows = [
        {"label": labels.get(key, key.replace("_", " ").title()), "value": value}
        for key, value in details.items() if value not in (None, "")
    ]
    modules = wizard.selected_modules or []
    from onboarding.services.module_config_service import MODULE_DISPLAY_NAMES
    structure_data = _step8_member_context(wizard)
    structures = structure_data["structures"]
    members_by_unit = structure_data["members_by_unit"]
    for structure in structures:
        for unit in structure["units"]:
            unit["members"] = members_by_unit.get(str(unit["id"]), [])
    return {
        "summary_society_rows": society_rows,
        "summary_society_type": dict(OnboardingWizard.SocietyType.choices).get(
            wizard.society_type, wizard.society_type or ""
        ),
        "summary_modules": [MODULE_DISPLAY_NAMES.get(
            module, module.replace("_", " ").title()
        ) for module in modules],
        "summary_financial_year": _get_selected_fy_label(wizard),
        "summary_structures": structures,
        "summary_members_by_unit": members_by_unit,
        "summary_structure_count": len(structures),
        "summary_unit_count": sum(len(item["units"]) for item in structures),
        "summary_member_count": sum(len(items) for items in members_by_unit.values()),
    }


def _step10_account_context(
    wizard: OnboardingWizard, search_query: str = ""
) -> dict[str, Any]:
    """Context for Step 9: chart of accounts tree and smart search."""
    empty = {
        "accounts": [], "account_count": 0, "account_matching_count": 0,
        "account_search_query": search_query, "root_accounts": [],
        "account_tree_nodes": [], "has_society": False,
    }
    if not wizard.society:
        return empty

    from django.db.models import Q, Value
    from django.db.models.functions import Replace
    from accounting.models import Account
    from accounting.views import _build_account_tree
    from societies.permissions import has_permission

    accounts = list(
        Account.objects.filter(society=wizard.society)
        .select_related("category", "parent")
        .order_by("code", "name", "pk")
    )
    search_query = (search_query or "").strip()
    visible_accounts = accounts
    matching_count = len(accounts)
    if search_query:
        compact_query = search_query.replace(".", "").strip()
        matches = Account.objects.filter(society=wizard.society).filter(
            Q(name__icontains=search_query) | Q(code__icontains=search_query)
        )
        if compact_query:
            matches = matches.annotate(
                compact_account_code=Replace("code", Value("."), Value(""))
            ).filter(
                Q(name__icontains=search_query)
                | Q(code__icontains=search_query)
                | Q(compact_account_code__icontains=compact_query)
            )
        match_ids = set(matches.values_list("pk", flat=True))
        matching_count = len(match_ids)
        visible_ids = set(match_ids)
        account_by_id = {account.pk: account for account in accounts}
        for account_id in match_ids:
            current = account_by_id.get(account_id)
            parent_id = current.parent_id if current else None
            while parent_id and parent_id in account_by_id and parent_id not in visible_ids:
                visible_ids.add(parent_id)
                parent_id = account_by_id[parent_id].parent_id
        visible_accounts = [account for account in accounts if account.pk in visible_ids]

    return {
        "accounts": accounts,
        "account_count": len(accounts),
        "account_matching_count": matching_count,
        "account_search_query": search_query,
        "root_accounts": [account for account in accounts if account.parent_id is None],
        "account_tree_nodes": _build_account_tree(visible_accounts),
        "can_create_account": has_permission(
            wizard.created_by, "accounting.account.create", wizard.society
        ),
        "can_edit_account": has_permission(
            wizard.created_by, "accounting.account.edit", wizard.society
        ),
        "can_delete_account": has_permission(
            wizard.created_by, "accounting.account.delete", wizard.society
        ),
        "has_society": True,
    }


def _build_sample_template_rows(wizard: OnboardingWizard) -> dict[str, list[dict[str, Any]]]:
    """Build prefilled sample rows for the Step 10 template downloads."""
    rows: dict[str, list[dict[str, Any]]] = {}
    if wizard is None or not getattr(wizard, "society", None):
        return rows

    from accounting.models import Account
    from members.models import Member
    from members.models import Unit
    accounts = list(
        Account.objects.filter(society=wizard.society)
        .select_related("category", "parent")
        .order_by("code", "name", "pk")
    )
    units = list(
        Unit.objects.filter(structure__society=wizard.society)
        .select_related("structure")
        .order_by("structure__name", "identifier", "pk")
    )
    members = list(
        Member.objects.filter(society=wizard.society)
        .select_related("unit")
        .order_by("unit__identifier", "full_name", "pk")
    )

    account_by_code = {a.code: a for a in accounts if a.code}
    unit_by_identifier = {u.identifier: u for u in units if u.identifier}
    member_by_unit: dict[str, list[Member]] = {}
    for member in members:
        if member.unit_id:
            member_by_unit.setdefault(member.unit.identifier, []).append(member)

    rows["CHART_OF_ACCOUNTS"] = [
        {
            "account_code": account.code,
            "account_name": account.name,
            "account_group": account.category.name if account.category_id else "",
            "account_type": account.account_type,
            "parent_code": account.parent.code if account.parent_id else "",
            "nature": account.account_type or "",
            "opening_debit": "",
            "opening_credit": "",
        }
        for account in accounts
        if account.is_posting and account.is_active
    ]

    trial_balance_rows: list[dict[str, Any]] = []
    for account in accounts[:25]:
        trial_balance_rows.append(
            {
                "account_code": account.code,
                "account_name": account.name,
                "debit": "",
                "credit": "",
            }
        )
    rows["TRIAL_BALANCE"] = trial_balance_rows

    rows["MEMBER_OUTSTANDING"] = [
        {
            "unit_identifier": unit.identifier,
            "member_name": (
                member_by_unit.get(unit.identifier)[0].full_name
                if member_by_unit.get(unit.identifier)
                else ""
            ),
            "outstanding_amount": "",
            "advance_maintenance": "",
            "credit_balance": "",
            "late_fees": "",
            "interest_receivable": "",
        }
        for unit in units[:25]
    ]

    rows["VENDOR_OUTSTANDING"] = [
        {
            "vendor_name": "Vendor Name",
            "outstanding_amount": "",
            "advance_paid": "",
            "retention": "",
            "security_deposit": "",
        }
    ]

    bank_accounts = [a for a in accounts if getattr(a, "is_bank", False)]
    rows["BANK_OPENING"] = [
        {
            "bank_name": account.name,
            "account_number": "",
            "ifsc": "",
            "branch": "",
            "opening_balance": "",
            "account_code": account.code,
        }
        for account in bank_accounts[:15]
    ]

    cash_account = next(
        (a for a in accounts if a.code in {"1.4.1", "1.4.1.1", "1.4"}),
        None,
    )
    rows["CASH_OPENING"] = [
        {
            "opening_balance": "",
        }
    ]
    if cash_account is not None:
        rows["CASH_OPENING"][0]["opening_balance"] = ""

    asset_accounts = [
        a for a in accounts
        if a.account_type == Account.AccountType.ASSET and not getattr(a, "is_bank", False)
    ]
    rows["FIXED_ASSETS"] = [
        {
            "asset_name": "Lift",
            "asset_category": "Building",
            "gross_value": "",
            "depreciation": "",
            "net_value": "",
            "account_code": asset_accounts[0].code if asset_accounts else "",
        }
    ]

    rows["SECURITY_DEPOSITS"] = [
        {
            "description": "Security deposit",
            "amount": "",
            "against_account": account_by_code.get("2.1.4").code if account_by_code.get("2.1.4") else "",
        }
    ]

    rows["LOANS"] = [
        {
            "loan_name": "Bank Loan",
            "loan_type": "BANK",
            "outstanding_principal": "",
            "interest": "",
            "account_code": account_by_code.get("2.6.1").code if account_by_code.get("2.6.1") else "",
        }
    ]

    rows["FUNDS"] = [
        {
            "fund_name": "Repair Fund",
            "fund_type": "REPAIR",
            "balance": "",
            "account_code": account_by_code.get("5.1.2").code if account_by_code.get("5.1.2") else "",
        }
    ]

    return rows


def _get_template_manual_seed_rows(
    wizard: OnboardingWizard,
    template_type: str,
) -> list[dict[str, Any]]:
    """Return the prefilled rows shown as locked reference rows."""
    seed_rows = _build_sample_template_rows(wizard).get(template_type, [])
    if seed_rows:
        columns = StagingService.get_template_columns(template_type)
        return [
            {column: row.get(column, "") for column in columns}
            for row in seed_rows
        ]
    columns = StagingService.get_template_columns(template_type)
    return [{column: "" for column in columns}]


def _get_template_manual_existing_rows(
    wizard: OnboardingWizard,
    template_type: str,
) -> list[dict[str, Any]]:
    """Return the last saved rows for a manual-entry template, if any."""
    try:
        staging_data = StagingService.get_staging_data(wizard, template_type)
    except (ValidationError, ValueError):
        return []

    columns = StagingService.get_template_columns(template_type)
    return [
        {column: row.get(column, "") for column in columns}
        for row in staging_data.get("rows", [])
    ]


def _manual_coa_account_menu(wizard: OnboardingWizard) -> list[dict[str, Any]]:
    """Build grouped posting-account choices with rolled-up posted balances."""
    from django.db.models import Sum
    from accounting.models import Account, LedgerEntry

    accounts = list(
        Account.objects.filter(society=wizard.society)
        .select_related("category")
        .order_by("code", "name", "pk")
    )
    account_ids = [account.pk for account in accounts]
    balances = {
        row["account_id"]: (row["debit"] or 0, row["credit"] or 0)
        for row in LedgerEntry.objects.filter(
            account_id__in=account_ids,
            voucher__society=wizard.society,
            voucher__posted_at__isnull=False,
        ).values("account_id").annotate(debit=Sum("debit"), credit=Sum("credit"))
    }
    children: dict[int | None, list[Any]] = {}
    for account in accounts:
        children.setdefault(account.parent_id, []).append(account)

    def summarize(account, path=None):
        path = path or set()
        if account.pk in path:
            return None
        next_path = path | {account.pk}
        child_nodes = [
            node for child in children.get(account.pk, [])
            if (node := summarize(child, next_path)) is not None
        ]
        debit, credit = balances.get(account.pk, (0, 0))
        debit = debit + sum((node["debit"] for node in child_nodes), 0)
        credit = credit + sum((node["credit"] for node in child_nodes), 0)
        return {
            "account": account, "children": child_nodes,
            "debit": debit, "credit": credit,
        }

    tree = [
        node for account in children.get(None, [])
        if (node := summarize(account)) is not None
    ]
    return tree


def _merge_fixed_rows_by_index(
    seed_rows: list[dict[str, Any]],
    existing_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge saved values onto the current fixed template rows by position."""

    merged_rows: list[dict[str, Any]] = []
    for index, seed_row in enumerate(seed_rows):
        merged = dict(seed_row)
        saved_row = existing_rows[index] if index < len(existing_rows) else None
        if saved_row:
            for column, value in saved_row.items():
                if column not in merged:
                    continue
                if value not in ("", None):
                    merged[column] = value
        merged_rows.append(merged)
    return merged_rows


def _build_template_payload(headers: tuple[str, ...], rows: list[dict[str, Any]]) -> list[list[str]]:
    payload: list[list[str]] = [list(headers)]
    for row in rows:
        payload.append([str(row.get(col, "") or "") for col in headers])
    if len(payload) == 1:
        payload.append(["" for _ in headers])
    return payload


def _render_template_download(template_type: str, rows: list[dict[str, Any]], *, file_format: str):
    """Render a CSV or XLSX download for a staging template."""
    headers = StagingService.get_template_columns(template_type)
    safe_name = StagingService._normalize_template_type(template_type).lower()

    if file_format == "xlsx":
        if openpyxl is None:
            raise ValidationError(_("Excel downloads require the 'openpyxl' package."))
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "Template"
        for row in _build_template_payload(headers, rows):
            sheet.append(row)
        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)
        response = HttpResponse(
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{safe_name}_template.xlsx"'
        response["X-Content-Type-Options"] = "nosniff"
        return response

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerows(_build_template_payload(headers, rows))
    response = HttpResponse(buffer.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{safe_name}_template.csv"'
    response["X-Content-Type-Options"] = "nosniff"
    return response

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

    # Step 22 → reconciliation dashboard.
    if step_number == 22:
        return redirect("onboarding:reconciliation-dashboard", wizard_id=wizard.pk)

    # Step 23 → validation checklist.
    if step_number == 23:
        return redirect("onboarding:validation-checklist", wizard_id=wizard.pk)

    # Step 27 → complete page.
    if step_number == STEP_SOCIETY_READY:
        return redirect("onboarding:wizard-complete", wizard_id=wizard.pk)

    context = _base_context(wizard, step_number)

    # Step 4 now selects and creates the first financial year in one action.
    if step_number == 4:
        context["selected_fy"] = _get_selected_fy_label(wizard)
        from accounting.models import FinancialYear
        context["financial_years"] = (
            FinancialYear.objects.filter(society=wizard.society).order_by("-start_date")
            if wizard.society_id else []
        )
    # Special context for display-only steps.
    elif step_number == 5:
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
    elif step_number == 6:
        context.update(_step7_structure_context(wizard))
    elif step_number == 7:
        context.update(_step8_member_context(wizard))
    elif step_number == 8:
        context["society"] = wizard.society
        context.update(_step9_account_context(wizard))
        context.update(_step8_summary_context(wizard))
    elif step_number == 9:
        context.update(_step10_account_context(wizard, request.GET.get("q", "")))
        context["account_return_url"] = request.get_full_path()
    elif step_number == 10:
        context["upload_summary"] = _safe_get_upload_summary(wizard)
        context["template_types"] = ALL_TEMPLATE_TYPES
        context["template_columns"] = _get_template_columns()

    form = _get_step_form(step_number, wizard)
    if form is not None:
        context["form"] = form

    return render(request, _get_step_template(step_number), context)


@login_required
@require_http_methods(["POST"])
def wizard_reset_accounts(request: HttpRequest, wizard_id: int) -> HttpResponse:
    """Replace a wizard society's chart with the system standard accounts."""
    wizard = _get_wizard(wizard_id, request.user)
    if wizard.current_step < 9 or not wizard.society:
        messages.error(request, _("The chart of accounts is not available to reset yet."))
        return redirect("onboarding:wizard-step", wizard_id=wizard.pk, step_number=wizard.current_step)

    from accounting.services.standard_accounts import rebuild_accounts_for_society

    try:
        with transaction.atomic():
            rebuild_accounts_for_society(wizard.society)
    except (ProtectedError, ValidationError):
        messages.error(
            request,
            _("Accounts could not be reset because other accounting records depend on them."),
        )
    else:
        messages.success(request, _("Accounts reset to the system standard chart."))

    return redirect("onboarding:wizard-step", wizard_id=wizard.pk, step_number=9)


@login_required
def wizard_units_delete(
    request: HttpRequest,
    wizard_id: int,
    structure_id: int,
) -> HttpResponse:
    """Delete all units on a structure from onboarding Step 6 summary."""
    if request.method != "POST":
        messages.error(request, _("Invalid request method."))
        return redirect("onboarding:wizard-step", wizard_id=wizard_id, step_number=6)

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

    return redirect("onboarding:wizard-step", wizard_id=wizard.pk, step_number=6)


@login_required
def wizard_structure_delete(
    request: HttpRequest,
    wizard_id: int,
    structure_id: int,
) -> HttpResponse:
    """Delete a structure and its descendants during onboarding Step 6."""
    if request.method != "POST":
        messages.error(request, _("Invalid request method."))
        return redirect(
            "onboarding:wizard-step", wizard_id=wizard_id, step_number=5
        )

    wizard = _get_wizard(wizard_id, request.user)
    if wizard.status != OnboardingWizard.Status.IN_PROGRESS:
        messages.error(request, _("This wizard is no longer in progress."))
        return redirect("onboarding:wizard-list")

    if not wizard.society_id:
        messages.error(request, _("This wizard has no society to manage."))
        return redirect(
            "onboarding:wizard-step", wizard_id=wizard.pk, step_number=5
        )

    from members.models import Structure

    structure = Structure.objects.filter(
        pk=structure_id, society_id=wizard.society_id
    ).first()
    if structure is None:
        messages.error(request, _("Structure not found in this society."))
    else:
        name = structure.name
        structure.delete()  # Cascades to nested structures and their units.
        messages.success(
            request,
            _("Structure '%(name)s' and its nested data were deleted.")
            % {"name": name},
        )

    return redirect(
        "onboarding:wizard-step", wizard_id=wizard.pk, step_number=5
    )


# --------------------------------------------------------------------------- #
# Step 7 — Member CRUD JSON API (scoped to the wizard's society)
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

    # Step 5's form is for adding a structure. Continuing should not require
    # the add-structure fields to be filled in a second time.
    if step_number == 5 and "continue_wizard" in request.POST:
        from members.models import Structure

        if not wizard.society or not Structure.objects.filter(
            society=wizard.society
        ).exists():
            message = str(_("Add at least one structure before continuing."))
            messages.error(request, message)
            if request.headers.get("HX-Request") == "true":
                list(messages.get_messages(request))
                response = HttpResponse(status=204)
                response["HX-Trigger"] = json.dumps(
                    {"showToast": {"message": message, "type": "error"}}
                )
                return response
            return redirect(
                "onboarding:wizard-step", wizard_id=wizard.pk, step_number=5
            )

        response = _advance_and_redirect(request, wizard, step_number)
        if request.headers.get("HX-Request") == "true":
            destination = response["Location"]
            list(messages.get_messages(request))
            htmx_response = HttpResponse(status=204)
            htmx_response["HX-Location"] = json.dumps(
                {
                    "path": destination,
                    "target": "#workspace",
                    "select": "#workspace",
                    "swap": "innerHTML",
                    "push": destination,
                }
            )
            htmx_response["HX-Trigger"] = json.dumps(
                {
                    "showToast": {
                        "message": str(
                            _("Step %(step)s completed.") % {"step": step_number}
                        ),
                        "type": "success",
                    }
                }
            )
            return htmx_response
        return response

    if step_number == 4 and "continue_wizard" in request.POST:
        from accounting.models import FinancialYear
        if not wizard.society_id or not FinancialYear.objects.filter(society_id=wizard.society_id).exists():
            messages.error(request, _("Add at least one financial year before continuing."))
            if request.headers.get("HX-Request") == "true":
                list(messages.get_messages(request))
                response = HttpResponse(status=204)
                response["HX-Trigger"] = json.dumps(
                    {
                        "showToast": {
                            "message": str(_("Add at least one financial year before continuing.")),
                            "type": "error",
                        }
                    }
                )
                return response
            return redirect("onboarding:wizard-step", wizard_id=wizard.pk, step_number=4)
        if request.headers.get("HX-Request") == "true":
            redirect_response = _advance_and_redirect(request, wizard, step_number)
            destination = redirect_response["Location"]
            list(messages.get_messages(request))
            response = HttpResponse(status=204)
            response["HX-Location"] = json.dumps(
                {
                    "path": destination,
                    "target": "#workspace",
                    "select": "#workspace",
                    "swap": "innerHTML",
                    "push": destination,
                }
            )
            response["HX-Trigger"] = json.dumps(
                {
                    "showToast": {
                        "message": str(_("Step %(step)s completed.") % {"step": step_number}),
                        "type": "success",
                    }
                }
            )
            return response
        return _advance_and_redirect(request, wizard, step_number)

    form = _get_step_form(step_number, wizard, data=request.POST, files=request.FILES)

    # Steps without a form (5, 8, 9, 10, 11) just advance.
    if form is None:
        try:
            response = _handle_no_form_step(request, wizard, step_number)
        except ValidationError as exc:
            messages.error(request, str(exc))
            return redirect(
                "onboarding:wizard-step",
                wizard_id=wizard.pk,
                step_number=step_number,
            )
        if response is not None:
            return response
        return _advance_and_redirect(request, wizard, step_number)

    # Step 6 Continue with an empty grid: allow advance when units already
    # exist on the society (user may have saved in a previous visit).
    if step_number == 6 and "continue_wizard" in request.POST:
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
        if step_number == 4 and request.headers.get("HX-Request") == "true":
            messages.error(request, _("Please select a financial year."))
            list(messages.get_messages(request))
            from accounting.models import FinancialYear

            response = render(
                request,
                "onboarding/partials/financial_year_list.html",
                {
                    "wizard": wizard,
                    "financial_years": FinancialYear.objects.filter(
                        society_id=wizard.society_id
                    ).order_by("-start_date"),
                },
            )
            response["HX-Trigger"] = json.dumps(
                {"showToast": {"message": str(_("Please select a financial year.")), "type": "error"}}
            )
            return response
        context = _base_context(wizard, step_number)
        context["form"] = form
        if step_number == 6:
            context.update(_step7_structure_context(wizard))
        messages.error(request, _("Please correct the errors below."))
        return render(request, _get_step_template(step_number), context)

    try:
        result = _handle_valid_step(request, wizard, step_number, form)
    except ValidationError as exc:
        messages.error(request, str(exc))
        context = _base_context(wizard, step_number)
        context["form"] = form
        if step_number == 6:
            context.update(_step7_structure_context(wizard))
        return render(request, _get_step_template(step_number), context)

    # Handlers may return a redirect to stay on the same step (e.g. Save Units).
    if result is not None:
        return result

    if step_number == 4:
        messages.success(request, _("Financial year added."))
        if request.headers.get("HX-Request") == "true":
            list(messages.get_messages(request))
            from accounting.models import FinancialYear

            response = render(
                request,
                "onboarding/partials/financial_year_list.html",
                {
                    "wizard": wizard,
                    "financial_years": FinancialYear.objects.filter(
                        society_id=wizard.society_id
                    ).order_by("-start_date"),
                },
            )
            response["HX-Trigger"] = json.dumps(
                {"showToast": {"message": str(_("Financial year added.")), "type": "success"}}
            )
            return response
        return redirect("onboarding:wizard-step", wizard_id=wizard.pk, step_number=4)

    return _advance_and_redirect(request, wizard, step_number)


@login_required
@require_http_methods(["POST"])
def wizard_financial_year_delete(request: HttpRequest, wizard_id: int, financial_year_id: int) -> HttpResponse:
    """Delete a financial year belonging to the society in this active wizard."""
    wizard = _get_wizard(wizard_id, request.user)
    if wizard.status != OnboardingWizard.Status.IN_PROGRESS or not wizard.society_id:
        messages.error(request, _("This wizard cannot manage financial years."))
        return redirect("onboarding:wizard-list")

    from accounting.models import FinancialYear
    from accounting.models.model_Voucher import Voucher

    financial_year = FinancialYear.objects.filter(
        pk=financial_year_id, society_id=wizard.society_id
    ).first()
    toast_message = ""
    toast_type = "success"
    if financial_year is None:
        toast_message = str(_("Financial year not found in this society."))
        toast_type = "error"
        messages.error(request, toast_message)
    elif Voucher.objects.filter(
        society_id=wizard.society_id,
        voucher_date__gte=financial_year.start_date,
        voucher_date__lte=financial_year.end_date,
    ).exists():
        toast_message = str(_("This financial year has vouchers and cannot be deleted."))
        toast_type = "error"
        messages.error(request, toast_message)
    else:
        financial_year.delete()
        data = dict(wizard.wizard_data or {})
        if data.get("financial_year_id") == str(financial_year_id):
            data.pop("financial_year_id", None)
            data.pop("accounting_start_year", None)
            step4_data = dict(data.get("Accounting Start Year", {}))
            step4_data.pop("accounting_start_year", None)
            data["Accounting Start Year"] = step4_data
            wizard.wizard_data = data
            wizard.save(update_fields=["wizard_data"])
        toast_message = str(_("Financial year deleted."))
        messages.success(request, toast_message)
    if request.headers.get("HX-Request") == "true":
        # This partial does not render Django's normal message area, so show
        # the result immediately as a toast and consume the queued message.
        list(messages.get_messages(request))
        response = render(
            request,
            "onboarding/partials/financial_year_list.html",
            {
                "wizard": wizard,
                "financial_years": FinancialYear.objects.filter(
                    society_id=wizard.society_id
                ).order_by("-start_date"),
            },
        )
        response["HX-Trigger"] = json.dumps(
            {"showToast": {"message": toast_message, "type": toast_type}}
        )
        return response
    return redirect("onboarding:wizard-step", wizard_id=wizard.pk, step_number=4)


def _handle_no_form_step(request, wizard: OnboardingWizard, step_number: int):
    """Handle steps that don't have a form (auto-advance steps)."""
    if step_number == 8:
        # Accounting Setup — create standard accounts for the society.
        if wizard.society is None:
            raise ValidationError(_("Society must be created before accounting setup."))
        from accounting.services.standard_accounts import ensure_standard_accounts
        from accounting.models import AccountMapping

        with tenant_context(wizard.society):
            ensure_standard_accounts(wizard.society)
            AccountMapping.ensure_for_society(wizard.society)

    elif step_number == 7:
        # Member Assignment — members are persisted via AJAX in the UI.
        # Only guard the advance: require at least one active member.
        from members.models import Member

        if not wizard.society or not Member.objects.filter(
            society=wizard.society, status=Member.MemberStatus.ACTIVE
        ).exists():
            raise ValidationError(
                _("Please add at least one active member before continuing.")
            )

    elif step_number in (9, 10):
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
        # Select the accounting start year and create its financial year.
        WizardService.update_wizard_data(
            wizard=wizard,
            key="Accounting Start Year",
            value=form.cleaned_data,
        )
        FinancialYearSetupService.create_financial_years_through_current(
            wizard=wizard,
            start_year=form.cleaned_data["accounting_start_year"],
            user=request.user,
        )

    elif step_number == 5:
        # Society Structure — create individual structure using housing form.
        if 'add_structure' in request.POST:
            # User clicked "Add Structure" - create the structure and stay on step 5
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
            response = redirect(
                "onboarding:wizard-step",
                wizard_id=wizard.pk,
                step_number=step_number,
            )
            if request.headers.get("HX-Request") == "true":
                destination = response["Location"]
                list(messages.get_messages(request))
                htmx_response = HttpResponse(status=204)
                htmx_response["HX-Location"] = json.dumps(
                    {
                        "path": destination,
                        "target": "#workspace",
                        "select": "#workspace",
                        "swap": "innerHTML",
                        "push": destination,
                    }
                )
                htmx_response["HX-Trigger"] = json.dumps(
                    {
                        "showToast": {
                            "message": str(
                                _("Structure '%(name)s' created successfully.")
                                % {"name": structure.name}
                            ),
                            "type": "success",
                        }
                    }
                )
                return htmx_response
            return response
        elif 'continue_wizard' in request.POST:
            # User clicked "Continue to Next Step" - advance to step 6
            pass  # This will fall through to normal wizard advancement

    elif step_number == 6:
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

    elif step_number == 24:
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

    # If the next step is 27 (society ready), go to the complete page.
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
    try:
        file_format = (request.GET.get("format") or "csv").strip().lower()
        if file_format in {"excel", "xlsx"}:
            file_format = "xlsx"
        else:
            file_format = "csv"
        wizard_id = request.GET.get("wizard_id")
        wizard = _get_wizard(int(wizard_id), request.user) if wizard_id else None
        rows = _build_sample_template_rows(wizard)
        canonical = StagingService._normalize_template_type(template_type)
    except (ValueError, ValidationError) as exc:
        messages.error(request, str(exc))
        return redirect("onboarding:wizard-list")

    return _render_template_download(
        template_type,
        rows.get(canonical, []),
        file_format=file_format,
    )


@login_required
def template_manual_entry(
    request: HttpRequest,
    wizard_id: int,
    template_type: str,
) -> HttpResponse:
    """Render and submit a direct typed-entry form for a staging template."""
    wizard = _get_wizard(wizard_id, request.user)

    if wizard.current_step < 10:
        messages.error(request, _("Please complete step 10 before manual entry."))
        return redirect(
            "onboarding:wizard-step",
            wizard_id=wizard.pk,
            step_number=wizard.current_step,
        )

    try:
        canonical = StagingService._normalize_template_type(template_type)
        columns = StagingService.get_template_columns(canonical)
        seed_rows = _get_template_manual_seed_rows(wizard, canonical)
        existing_rows = _get_template_manual_existing_rows(wizard, canonical)
    except (ValidationError, ValueError) as exc:
        messages.error(request, str(exc))
        return redirect("onboarding:wizard-step", wizard_id=wizard.pk, step_number=10)

    fixed_template_rows = True
    initial_rows = _merge_fixed_rows_by_index(seed_rows, existing_rows)
    if canonical == "CHART_OF_ACCOUNTS":
        saved_by_code = {
            str(row.get("account_code") or ""): row for row in existing_rows
        }
        initial_rows = []
        for seed in seed_rows:
            merged = dict(seed)
            saved = saved_by_code.get(str(seed.get("account_code") or ""), {})
            for column in ("opening_debit", "opening_credit"):
                if saved.get(column) not in (None, ""):
                    merged[column] = saved[column]
            initial_rows.append(merged)
    reference_rows = seed_rows
    locked_columns_by_row: list[set[str]] = [
        {
            column
            for column, value in row.items()
            if str(value or "").strip()
        }
        for row in reference_rows
    ]

    account_tree: list[dict[str, Any]] = []
    posting_accounts_by_code = {}
    if canonical == "CHART_OF_ACCOUNTS" and wizard.society:
        from accounting.models import Account

        account_tree = _manual_coa_account_menu(wizard)
        posting_accounts_by_code = {
            account.code: account
            for account in Account.objects.filter(
                society=wizard.society, is_active=True, is_posting=True
            ).exclude(code__isnull=True).exclude(code="").select_related("category", "parent")
        }

    formset_extra = 0
    if request.method == "POST":
        formset = build_template_entry_formset(
            canonical,
            data=request.POST,
            initial=initial_rows,
            extra=formset_extra,
        )
        fixed_row_count_mismatch = False
        posted_total_forms = request.POST.get("rows-TOTAL_FORMS")
        expected_total_forms = str(len(initial_rows))
        if posted_total_forms != expected_total_forms:
            formset._non_form_errors = formset.error_class(
                [
                    _(
                        "This template uses fixed rows. "
                        "Update the source data in the previous step, then return here.",
                    ),
                ]
            )
            fixed_row_count_mismatch = True
        if not fixed_row_count_mismatch and formset.is_valid():
            rows: list[dict[str, Any]] = []
            for index, form in enumerate(formset):
                if not hasattr(form, "cleaned_data"):
                    continue
                row = {column: form.cleaned_data.get(column, "") for column in columns}
                if index < len(locked_columns_by_row):
                    for column in locked_columns_by_row[index]:
                        row[column] = seed_rows[index].get(column, "")
                if canonical == "CHART_OF_ACCOUNTS":
                    account = posting_accounts_by_code.get(
                        str(row.get("account_code") or "").strip()
                    )
                    if account is None:
                        continue
                    row.update({
                        "account_name": account.name,
                        "account_group": account.category.name,
                        "account_type": account.account_type,
                        "parent_code": "",
                        "nature": account.account_type,
                    })
                if any(str(value).strip() for value in row.values()):
                    rows.append(row)

            if not rows:
                formset._non_form_errors = formset.error_class(
                    [_("Add at least one row before saving.")]
                )
            else:
                try:
                    StagingService.save_rows(
                        wizard=wizard,
                        template_type=canonical,
                        rows=rows,
                        user=request.user,
                    )
                    ValidationService.validate_batch(
                        wizard=wizard,
                        template_type=canonical,
                        user=request.user,
                    )
                except (ValidationError, ValueError) as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(
                        request,
                        _("Manual data saved and validated for %(t)s.")
                        % {"t": canonical.replace("_", " ").title()},
                    )
                    return redirect(
                        "onboarding:staging-view",
                        wizard_id=wizard.pk,
                        template_type=canonical,
                    )
    else:
        formset = build_template_entry_formset(
            canonical,
            initial=initial_rows,
            extra=formset_extra,
        )

    if canonical == "CHART_OF_ACCOUNTS":
        forms_by_code = {
            str(form.initial.get("account_code") or ""): form
            for form in formset.forms
        }

        def attach_coa_forms(nodes):
            for node in nodes:
                node["entry_form"] = forms_by_code.get(
                    str(node["account"].code or "")
                )
                attach_coa_forms(node["children"])

        attach_coa_forms(account_tree)

    # Lock the prefilled cells only, so users can continue editing their own
    # previously-entered values on repeat visits.
    for index, form in enumerate(formset.forms):
        if index < len(locked_columns_by_row):
            for column in locked_columns_by_row[index]:
                field = form.fields.get(column)
                if field is None:
                    continue
                field.widget.attrs["readonly"] = "readonly"
                field.widget.attrs["aria-readonly"] = "true"
                field.widget.attrs["data-prefilled"] = "true"
                existing = field.widget.attrs.get("class", "")
                field.widget.attrs["class"] = f"{existing} bg-light text-muted".strip()

    context = _base_context(wizard, 11)
    context.update(
        {
            "template_type": canonical,
            "template_columns": columns,
            "template_entry_formset": formset,
            "seed_row_count": len(seed_rows),
            "existing_row_count": len(existing_rows),
            "fixed_template_rows": fixed_template_rows,
            "step_10_url": reverse(
                "onboarding:wizard-step",
                kwargs={"wizard_id": wizard.pk, "step_number": 10},
            ),
            "manual_coa_tree": account_tree,
        }
    )
    return render(
        request,
        "onboarding/steps/step_manual_template_entry.html",
        context,
    )


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
# Reconciliation Dashboard (Step 22)
# --------------------------------------------------------------------------- #

@login_required
def reconciliation_dashboard(
    request: HttpRequest,
    wizard_id: int,
) -> HttpResponse:
    """Render the reconciliation dashboard (Step 22)."""
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

    context = _base_context(wizard, 22)
    context["dashboard"] = dashboard
    return render(
        request,
        "onboarding/steps/step_reconciliation_dashboard.html",
        context,
    )


# --------------------------------------------------------------------------- #
# Validation Checklist (Step 23)
# --------------------------------------------------------------------------- #

@login_required
def validation_checklist(
    request: HttpRequest,
    wizard_id: int,
) -> HttpResponse:
    """Render the 9-check validation checklist (Step 23)."""
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

    context = _base_context(wizard, 23)
    context["checklist"] = checklist
    context["can_finalize"] = checklist.get("can_finalize", False)
    return render(
        request,
        "onboarding/steps/step_migration_validation_checklist.html",
        context,
    )


# --------------------------------------------------------------------------- #
# Finalize Migration (Steps 24–26)
# --------------------------------------------------------------------------- #

@login_required
def finalize_migration(
    request: HttpRequest,
    wizard_id: int,
) -> HttpResponse:
    """Handle final approval and trigger migration finalization (Steps 24–26)."""
    wizard = _get_wizard(wizard_id, request.user)

    if request.method == "POST":
        form = FinalApprovalForm(request.POST)
        if not form.is_valid():
            context = _base_context(wizard, 24)
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

        WizardService.update_wizard_data(
            wizard=wizard,
            key="Final Approval",
            value={
                "confirmed": form.cleaned_data["confirm"],
                "reason": form.cleaned_data.get("reason", ""),
            },
        )

        try:
            with transaction.atomic():
                # Step 25: Create the opening journal.
                voucher = MigrationFinalizationService.create_opening_journal(
                    wizard=wizard,
                    society=wizard.society,
                    user=request.user,
                )
                # Step 26: Lock the migration.
                MigrationFinalizationService.lock_migration(
                    wizard=wizard,
                    society=wizard.society,
                    user=request.user,
                )
                # Mark the wizard as complete.
                WizardService.complete_wizard(wizard, user=request.user)
        except ValidationError as exc:
            messages.error(request, str(exc))
            context = _base_context(wizard, 24)
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
    context = _base_context(wizard, 24)
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
# Wizard Complete (Step 27)
# --------------------------------------------------------------------------- #

@login_required
def wizard_complete(
    request: HttpRequest,
    wizard_id: int,
) -> HttpResponse:
    """Render the success / summary page (Step 27)."""
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
