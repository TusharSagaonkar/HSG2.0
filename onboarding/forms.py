"""Forms for the Society Creation & Accounting Migration Wizard.

All forms follow the project's established pattern (see
``housing/forms.py``): a ``BootstrapForm`` base class that applies
Bootstrap 5 widget classes, plus per-form validation that mirrors the
service-layer expectations.

Step → Form mapping:
    1  SocietyDetailsForm
    2  SocietyTypeForm
    3  ModuleSelectionForm
    4  AccountingStartYearForm
    6  StructureForm
    7  UnitConfigurationForm
    8  MemberAssignmentForm
    25 FinalApprovalForm

Steps 5, 9, 10–24, 26–28 do not use a dedicated form (they are either
auto-generated or use inline / display-only templates).
"""

from __future__ import annotations

import json
import re
from datetime import date

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from onboarding.services.financial_year_service import (
    FY_PATTERN_APRIL_MARCH,
    FY_PATTERN_JAN_DEC,
    FY_PATTERN_JUL_JUN,
    FinancialYearSetupService,
)
from onboarding.services.module_config_service import (
    MODULE_DISPLAY_NAMES,
)
from onboarding.services.wizard_service import (
    ALL_MODULES,
    CORE_MODULES,
    OPTIONAL_MODULES,
)


# --------------------------------------------------------------------------- #
# Base form with Bootstrap 5 widget classes
# --------------------------------------------------------------------------- #

class BootstrapForm(forms.Form):
    """Apply Bootstrap 5 widget classes to all fields.

    Mirrors the ``BootstrapForm`` class in ``housing/forms.py`` so that
    every wizard form renders consistently with the rest of the project.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            self._apply_widget_classes(field)

    @staticmethod
    def _apply_widget_classes(field):
        widget = field.widget
        css_class = "form-control"
        if isinstance(widget, forms.CheckboxInput):
            css_class = "form-check-input"
        elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
            css_class = "form-select"
        elif isinstance(widget, forms.RadioSelect):
            css_class = "form-check-input"
        elif isinstance(widget, forms.Textarea):
            css_class = "form-control"
        existing = widget.attrs.get("class", "")
        widget.attrs["class"] = f"{existing} {css_class}".strip()


# --------------------------------------------------------------------------- #
# Step 1 — Society Details
# --------------------------------------------------------------------------- #

_PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")
_GST_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z]\d[Z]\w$")
_TAN_RE = re.compile(r"^[A-Z]{4}\d{5}[A-Z]$")
_PIN_RE = re.compile(r"^\d{6}$")
_PHONE_RE = re.compile(r"^\+?\d{10,15}$")

FY_PATTERN_CHOICES = (
    (FY_PATTERN_APRIL_MARCH, _("April – March")),
    (FY_PATTERN_JAN_DEC, _("January – December")),
    (FY_PATTERN_JUL_JUN, _("July – June")),
)

TIMEZONE_CHOICES = (
    ("Asia/Kolkata", _("India (IST)")),
    ("Asia/Dubai", _("Dubai (GST)")),
    ("Asia/Singapore", _("Singapore (SGT)")),
    ("UTC", _("UTC")),
)

CURRENCY_CHOICES = (
    ("INR", _("Indian Rupee (₹)")),
    ("USD", _("US Dollar ($)")),
    ("AED", _("UAE Dirham (AED)")),
    ("SGD", _("Singapore Dollar (S$)")),
)

SOCIETY_TYPE_CHOICES = (
    ("RESIDENTIAL", _("Residential")),
    ("COMMERCIAL", _("Commercial")),
    ("MIXED", _("Mixed")),
)


class SocietyDetailsForm(BootstrapForm):
    """Step 1 — Capture society legal identity and locale configuration.

    The cleaned data is passed to
    :meth:`SocietySetupService.create_society` which expects these
    exact keys (snake_case).
    """

    name = forms.CharField(
        max_length=200,
        label=_("Society Name"),
        help_text=_("Legal name of the society as per registration."),
    )
    registration_number = forms.CharField(
        max_length=100,
        label=_("Registration Number"),
    )
    registration_date = forms.DateField(
        label=_("Registration Date"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    society_type = forms.ChoiceField(
        choices=SOCIETY_TYPE_CHOICES,
        label=_("Society Type"),
    )
    address = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        label=_("Address"),
    )
    city = forms.CharField(max_length=100, label=_("City"))
    state = forms.CharField(max_length=100, label=_("State"))
    country = forms.CharField(max_length=100, initial="India", label=_("Country"))
    pin_code = forms.CharField(max_length=10, label=_("PIN Code"))
    pan = forms.CharField(
        max_length=10,
        label=_("PAN"),
        help_text=_("10-character Permanent Account Number."),
    )
    gst_number = forms.CharField(
        max_length=20,
        required=False,
        label=_("GST Number"),
        help_text=_("15-character GSTIN. Leave blank if not registered."),
    )
    tan = forms.CharField(
        max_length=10,
        required=False,
        label=_("TAN"),
        help_text=_("10-character Tax Deduction Account Number."),
    )
    email = forms.EmailField(label=_("Society Contact Email"))
    phone = forms.CharField(max_length=15, label=_("Phone"))
    time_zone = forms.ChoiceField(
        choices=TIMEZONE_CHOICES,
        initial="Asia/Kolkata",
        label=_("Time Zone"),
    )
    currency = forms.ChoiceField(
        choices=CURRENCY_CHOICES,
        initial="INR",
        label=_("Currency"),
    )
    financial_year_pattern = forms.ChoiceField(
        choices=FY_PATTERN_CHOICES,
        initial=FY_PATTERN_APRIL_MARCH,
        label=_("Financial Year Pattern"),
        help_text=_("Determines the start/end months of the financial year."),
    )

    # -- field-level validation ------------------------------------------- #

    def clean_pin_code(self):
        value = (self.cleaned_data.get("pin_code") or "").strip()
        if not _PIN_RE.match(value):
            raise ValidationError(_("PIN code must be exactly 6 digits."))
        return value

    def clean_pan(self):
        value = (self.cleaned_data.get("pan") or "").strip().upper()
        if not _PAN_RE.match(value):
            raise ValidationError(
                _("PAN must be 5 letters, 4 digits, 1 letter (e.g. ABCDE1234F)."),
            )
        return value

    def clean_gst_number(self):
        value = (self.cleaned_data.get("gst_number") or "").strip().upper()
        if not value:
            return value
        if not _GST_RE.match(value):
            raise ValidationError(_("GST number format is invalid."))
        return value

    def clean_tan(self):
        value = (self.cleaned_data.get("tan") or "").strip().upper()
        if not value:
            return value
        if not _TAN_RE.match(value):
            raise ValidationError(
                _("TAN must be 4 letters, 5 digits, 1 letter (e.g. MUMM12345A)."),
            )
        return value

    def clean_phone(self):
        value = (self.cleaned_data.get("phone") or "").strip()
        if not _PHONE_RE.match(value):
            raise ValidationError(
                _("Phone must be 10–15 digits, optionally prefixed with '+'."),
            )
        return value

    def clean_registration_date(self):
        value = self.cleaned_data.get("registration_date")
        if value and value > date.today():
            raise ValidationError(
                _("Registration date cannot be in the future."),
            )
        return value


# --------------------------------------------------------------------------- #
# Step 2 — Society Type (NEW vs EXISTING)
# --------------------------------------------------------------------------- #

class SocietyTypeForm(BootstrapForm):
    """Step 2 — Choose whether this is a brand-new society or an existing
    one migrating from another system.

    The selected value (``NEW`` or ``EXISTING``) is passed to
    :meth:`WizardService.set_society_type`.
    """

    society_type = forms.ChoiceField(
        choices=(
            ("NEW", _("Brand New Society")),
            ("EXISTING", _("Existing Society (Migrating from another software)")),
        ),
        widget=forms.RadioSelect,
        label=_("Select Society Type"),
        help_text=_(
            "New societies skip the accounting migration steps. "
            "Existing societies import opening balances from a previous system.",
        ),
    )


# --------------------------------------------------------------------------- #
# Step 3 — Module Selection
# --------------------------------------------------------------------------- #

class ModuleSelectionForm(BootstrapForm):
    """Step 3 — Select optional modules.

    Core modules are always enabled and rendered as disabled (checked)
    checkboxes. Optional modules are selectable. The result is passed
    to :meth:`ModuleConfigurationService.configure_modules`.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Dynamically create a BooleanField for every optional module so
        # the form stays in sync with the module registry.
        for module_key in OPTIONAL_MODULES:
            label = MODULE_DISPLAY_NAMES.get(module_key, module_key.replace("_", " ").title())
            self.fields[module_key] = forms.BooleanField(
                required=False,
                label=_(label),
            )
        # Core modules rendered as disabled (always-on) checkboxes.
        for module_key in CORE_MODULES:
            label = MODULE_DISPLAY_NAMES.get(module_key, module_key.title())
            self.fields[f"_core_{module_key}"] = forms.BooleanField(
                required=False,
                initial=True,
                disabled=True,
                label=_(label),
                help_text=_("Core module — always enabled."),
            )

    def get_selected_modules(self) -> list[str]:
        """Return the list of selected optional module keys.

        Always includes the core modules. Unknown / unselected
        optional modules are omitted.
        """
        selected = list(CORE_MODULES)
        for module_key in OPTIONAL_MODULES:
            if self.cleaned_data.get(module_key):
                selected.append(module_key)
        return selected


# --------------------------------------------------------------------------- #
# Step 4 — Accounting Start Year
# --------------------------------------------------------------------------- #

class AccountingStartYearForm(BootstrapForm):
    """Step 4 — Choose the accounting start financial year.

    The FY options are populated dynamically based on the
    ``financial_year_pattern`` captured in Step 1, using
    :meth:`FinancialYearSetupService.get_fy_options`.
    """

    accounting_start_year = forms.ChoiceField(
        label=_("Financial Year"),
        help_text=_("Select the year from which accounting starts."),
    )

    def __init__(self, *args, fy_pattern=FY_PATTERN_APRIL_MARCH, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            options = FinancialYearSetupService.get_fy_options(
                fy_pattern=fy_pattern,
            )
        except Exception:  # pragma: no cover — defensive
            options = []
        self.fields["accounting_start_year"].choices = [
            (opt, opt) for opt in options
        ]


# --------------------------------------------------------------------------- #
# Step 6 — Society Structure
# --------------------------------------------------------------------------- #

TOPOLOGY_CHOICES = (
    ("SINGLE_BUILDING", _("Single Building")),
    ("MULTIPLE_BUILDINGS", _("Multiple Buildings")),
    ("COMMERCIAL_UNITS", _("Commercial Units")),
    ("MIXED_SOCIETY", _("Mixed Society")),
)


class StructureForm(BootstrapForm):
    """Step 6 — Configure society structure (buildings, wings, floors).

    The structure tree is built client-side (JS) and submitted as a JSON
    string in ``structures_json``. Each node must have at least
    ``structure_type``, ``name``, and ``display_order``.
    """

    topology_mode = forms.ChoiceField(
        choices=TOPOLOGY_CHOICES,
        label=_("Topology Mode"),
        help_text=_("Select the layout that best describes the society."),
    )
    structures_json = forms.CharField(
        widget=forms.HiddenInput,
        label=_("Structure Tree"),
        help_text=_("Generated by the structure builder."),
    )

    def clean_structures_json(self):
        raw = self.cleaned_data.get("structures_json")
        if not raw:
            raise ValidationError(_("Structure tree is required."))
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raise ValidationError(_("Structure data is not valid JSON."))

        if not isinstance(data, list) or not data:
            raise ValidationError(_("At least one structure node is required."))

        normalized: list[dict] = []
        seen_names: set[str] = set()
        for index, node in enumerate(data, start=1):
            if not isinstance(node, dict):
                raise ValidationError(
                    _("Structure node %(i)s is invalid.") % {"i": index},
                )
            name = str(node.get("name", "")).strip()
            if not name:
                raise ValidationError(
                    _("Structure node %(i)s is missing a name.") % {"i": index},
                )
            if name in seen_names:
                raise ValidationError(
                    _("Duplicate structure name '%(name)s'.") % {"name": name},
                )
            seen_names.add(name)
            structure_type = str(node.get("structure_type", "")).strip().upper()
            if not structure_type:
                raise ValidationError(
                    _("Structure '%(name)s' is missing a type.") % {"name": name},
                )
            normalized.append({
                "building_name": name,
                "wing_name": node.get("wing_name") or "",
                "floor_number": node.get("floor_number"),
                "structure_type": structure_type,
                "display_order": node.get("display_order", index),
                "parent": node.get("parent"),
            })
        return normalized


# --------------------------------------------------------------------------- #
# Step 7 — Unit Configuration
# --------------------------------------------------------------------------- #

UNIT_TYPE_CHOICES = (
    ("FLAT", _("Flat")),
    ("SHOP", _("Shop")),
    ("OFFICE", _("Office")),
    ("PLOT", _("Plot")),
    ("GARAGE", _("Garage")),
    ("OTHER", _("Other")),
)

USAGE_TYPE_CHOICES = (
    ("RESIDENTIAL", _("Residential")),
    ("COMMERCIAL", _("Commercial")),
    ("MIXED", _("Mixed")),
)


class UnitConfigurationForm(BootstrapForm):
    """Step 7 — Configure units within structures.

    Units are defined in a dynamic grid (pattern from
    ``BulkUnitCreateForm``) and submitted as JSON in ``units_json``.
    """

    units_json = forms.CharField(
        widget=forms.HiddenInput,
        label=_("Units Data"),
        help_text=_("Generated by the unit configuration grid."),
    )

    def clean_units_json(self):
        raw = self.cleaned_data.get("units_json")
        if not raw:
            raise ValidationError(_("Units data is required."))
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raise ValidationError(_("Units data is not valid JSON."))

        if not isinstance(data, list) or not data:
            raise ValidationError(_("At least one unit is required."))

        normalized: list[dict] = []
        seen_keys: set[str] = set()
        valid_unit_types = {key for key, _ in UNIT_TYPE_CHOICES}

        for index, row in enumerate(data, start=1):
            if not isinstance(row, dict):
                raise ValidationError(_("Unit row %(i)s is invalid.") % {"i": index})

            identifier = str(row.get("flat_number") or row.get("identifier") or "").strip()
            if not identifier:
                raise ValidationError(
                    _("Unit %(i)s is missing an identifier.") % {"i": index},
                )
            building = str(row.get("building") or "").strip()
            if not building:
                raise ValidationError(
                    _("Unit '%(id)s' is missing a building.") % {"id": identifier},
                )
            composite_key = f"{building}:{identifier}"
            if composite_key in seen_keys:
                raise ValidationError(
                    _("Duplicate unit identifier '%(id)s' in building '%(b)s'.")
                    % {"id": identifier, "b": building},
                )
            seen_keys.add(composite_key)

            unit_type = str(row.get("unit_type") or "FLAT").strip().upper()
            if unit_type not in valid_unit_types:
                unit_type = "FLAT"

            area = row.get("area")
            try:
                area_val = float(area) if area is not None else None
            except (TypeError, ValueError):
                area_val = None

            normalized.append({
                "flat_number": identifier,
                "area": area_val,
                "usage_type": str(row.get("usage_type") or "RESIDENTIAL").upper(),
                "unit_type": unit_type,
                "parking_allocation": row.get("parking_allocation", ""),
                "maintenance_calc_method": row.get("maintenance_calc_method", "FLAT"),
                "building": building,
                "wing": row.get("wing", ""),
                "floor": row.get("floor"),
            })
        return normalized


# --------------------------------------------------------------------------- #
# Step 8 — Member Assignment
# --------------------------------------------------------------------------- #

MEMBER_TYPE_CHOICES = (
    ("OWNER", _("Owner")),
    ("TENANT", _("Tenant")),
    ("NOMINEE", _("Nominee")),
)

OCCUPATION_STATUS_CHOICES = (
    ("OCCUPIED", _("Occupied")),
    ("VACANT", _("Vacant")),
    ("RENTED", _("Rented")),
)


class MemberAssignmentForm(BootstrapForm):
    """Step 8 — Assign members to units.

    Members are submitted as a JSON array in ``members_json``. Each
    member must have at least ``member_name``, ``unit_identifier``,
    and ``member_type``.
    """

    members_json = forms.CharField(
        widget=forms.HiddenInput,
        label=_("Members Data"),
        help_text=_("Generated by the member assignment grid."),
    )

    def clean_members_json(self):
        raw = self.cleaned_data.get("members_json")
        if not raw:
            raise ValidationError(_("Members data is required."))
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raise ValidationError(_("Members data is not valid JSON."))

        if not isinstance(data, list) or not data:
            raise ValidationError(_("At least one member is required."))

        normalized: list[dict] = []
        valid_member_types = {key for key, _ in MEMBER_TYPE_CHOICES}

        for index, row in enumerate(data, start=1):
            if not isinstance(row, dict):
                raise ValidationError(_("Member row %(i)s is invalid.") % {"i": index})

            member_name = str(row.get("member_name") or "").strip()
            if not member_name:
                raise ValidationError(
                    _("Member %(i)s is missing a name.") % {"i": index},
                )
            unit_identifier = str(row.get("unit_identifier") or "").strip()
            if not unit_identifier:
                raise ValidationError(
                    _("Member '%(name)s' is missing a unit identifier.")
                    % {"name": member_name},
                )
            member_type = str(row.get("member_type") or "OWNER").strip().upper()
            if member_type not in valid_member_types:
                member_type = "OWNER"

            email = str(row.get("email") or "").strip()
            if email and "@" not in email:
                raise ValidationError(
                    _("Member '%(name)s' has an invalid email.") % {"name": member_name},
                )

            normalized.append({
                "member_name": member_name,
                "member_type": member_type,
                "unit_identifier": unit_identifier,
                "email": email,
                "phone": str(row.get("phone") or "").strip(),
                "occupation_status": str(
                    row.get("occupation_status") or "OCCUPIED"
                ).upper(),
            })
        return normalized


# --------------------------------------------------------------------------- #
# Step 25 — Final Approval
# --------------------------------------------------------------------------- #

class FinalApprovalForm(BootstrapForm):
    """Step 25 — Explicit final approval with warning confirmation.

    The user must check the confirmation checkbox acknowledging that
    opening balances will become permanent and cannot be undone.
    """

    confirm = forms.BooleanField(
        required=True,
        label=_(
            "I understand that opening balances will become permanent "
            "and cannot be undone.",
        ),
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
        label=_("Reason / Notes"),
        help_text=_("Optional notes for the audit trail."),
    )


# --------------------------------------------------------------------------- #
# Step-to-form mapping (used by views)
# --------------------------------------------------------------------------- #

STEP_FORMS: dict[int, type[forms.Form]] = {
    1: SocietyDetailsForm,
    2: SocietyTypeForm,
    3: ModuleSelectionForm,
    4: AccountingStartYearForm,
    6: StructureForm,
    7: UnitConfigurationForm,
    8: MemberAssignmentForm,
    25: FinalApprovalForm,
}
