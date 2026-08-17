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
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.forms import formset_factory
from django.utils.translation import gettext_lazy as _

from members.models import Unit
from members.models import Structure

from onboarding.services.financial_year_service import (
    FY_PATTERN_APRIL_MARCH,
    FY_PATTERN_JAN_DEC,
    FY_PATTERN_JUL_JUN,
    FinancialYearSetupService,
)
from onboarding.services.module_config_service import (
    MODULE_DISPLAY_NAMES,
)
from onboarding.services.staging_service import (
    TEMPLATE_DECIMAL_FIELDS,
    StagingService,
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
        widget=forms.Select,
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
            ("", _("Select a financial year")),
            *((option, option) for option in options),
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
    Supports both manual individual unit entry and bulk generation.
    """

    units_json = forms.CharField(
        widget=forms.HiddenInput,
        label=_("Units Data"),
        help_text=_("Generated by the unit configuration grid."),
    )

    structure_id = forms.IntegerField(
        required=False,
        widget=forms.HiddenInput(),
        help_text=_("Structure ID for bulk generation."),
    )
    floors = forms.IntegerField(
        min_value=1,
        max_value=100,
        initial=10,
        required=False,
        label=_("Floors"),
        help_text=_("Number of floors for bulk generation."),
    )
    units_per_floor = forms.IntegerField(
        min_value=1,
        max_value=100,
        initial=12,
        required=False,
        label=_("Units per floor"),
    )
    starting_floor = forms.IntegerField(
        min_value=0,
        max_value=999,
        initial=1,
        required=False,
        label=_("Starting floor"),
        help_text=_("Grid starts from this floor number."),
    )
    starting_number = forms.IntegerField(
        min_value=1,
        max_value=999999,
        initial=1,
        required=False,
        label=_("Starting number"),
        help_text=_("First generated unit number."),
    )
    numbering_style = forms.ChoiceField(
        choices=(
            ("continuous", _("Continuous")),
            ("floor_based", _("Floor based")),
        ),
        initial="continuous",
        required=False,
        label=_("Numbering style"),
    )
    default_unit_type = forms.ChoiceField(
        choices=Unit.UnitType.choices,
        initial=Unit.UnitType.FLAT,
        required=False,
        label=_("Default unit type"),
    )
    default_area_sqft = forms.DecimalField(
        required=False,
        min_value=Decimal("0"),
        decimal_places=2,
        max_digits=8,
        label=_("Default area (sq ft)"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from members.models import Structure

        society = kwargs.get("initial", {}).get("society")
        if society:
            queryset = Structure.objects.filter(
                society=society,
                parent__isnull=True,
            ).order_by("name")
            self.fields["structure_id"].queryset = queryset
        # Remember the society so clean_units_json can detect duplicates
        # against units that already exist in the database (e.g. when the
        # user goes back to Step 7 after units were already created).
        self._society_id = society

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

        # Pre-fetch the society's structures and existing unit identifiers
        # per structure so we can validate structure_id references and detect
        # duplicates against already-created units. Units are unique per
        # structure (a unit "101" can exist under building A AND wing B).
        structures_by_id: dict[int, "Structure"] = {}
        existing_by_structure: dict[int, set[str]] = {}
        if self._society_id:
            from members.models import Structure, Unit

            structures_by_id = {
                s.pk: s
                for s in Structure.objects.filter(society_id=self._society_id)
            }
            existing = Unit.objects.filter(
                structure__society_id=self._society_id
            ).values_list("structure_id", "identifier")
            for sid, ident in existing:
                existing_by_structure.setdefault(sid, set()).add(ident)

        for index, row in enumerate(data, start=1):
            if not isinstance(row, dict):
                raise ValidationError(_("Unit row %(i)s is invalid.") % {"i": index})

            identifier = str(row.get("flat_number") or row.get("identifier") or "").strip()
            if not identifier:
                raise ValidationError(
                    _("Unit %(i)s is missing an identifier.") % {"i": index},
                )

            # A unit is attached to a structure. Prefer an explicit
            # structure_id (allows any structure: building, wing, block,
            # tower, floor). Fall back to building-name resolution for
            # backward compatibility with older unit data.
            structure_id = row.get("structure_id")
            building = str(row.get("building") or "").strip()

            if structure_id:
                try:
                    sid = int(structure_id)
                except (TypeError, ValueError):
                    raise ValidationError(
                        _("Unit '%(id)s' has an invalid structure reference.")
                        % {"id": identifier},
                    )
                if sid not in structures_by_id:
                    raise ValidationError(
                        _(
                            "Unit '%(id)s' refers to a structure that does not "
                            "exist in this society."
                        )
                        % {"id": identifier},
                    )
                # Derive a friendly building name for metadata/duplicate keys.
                if not building:
                    building = structures_by_id[sid].name
            elif not building:
                raise ValidationError(
                    _("Unit '%(id)s' is missing a structure or building.")
                    % {"id": identifier},
                )

            # Duplicate key is per-structure when structure_id is used, else
            # per-building-name (legacy).
            if structure_id:
                dup_key = f"s{sid}:{identifier}"
            else:
                dup_key = f"b:{building}:{identifier}"
            if dup_key in seen_keys:
                raise ValidationError(
                    _("Duplicate unit identifier '%(id)s'.")
                    % {"id": identifier},
                )
            seen_keys.add(dup_key)

            # Flag duplicates against units already saved in the database.
            if structure_id:
                db_set = existing_by_structure.get(sid)
            else:
                db_set = existing_by_structure.get(None)
                # Legacy: look up by building subtree (best effort).
                if db_set is None and self._society_id:
                    from members.models import Structure, Unit
                    bids = list(
                        Structure.objects.filter(
                            society_id=self._society_id,
                            parent__isnull=True,
                            name=building,
                        ).values_list("id", flat=True)
                    )
                    subtree = set(bids)
                    subtree.update(
                        Structure.objects.filter(
                            parent_id__in=bids
                        ).values_list("id", flat=True)
                    )
                    db_set = set(
                        Unit.objects.filter(
                            structure_id__in=subtree
                        ).values_list("identifier", flat=True)
                    )
            if db_set is not None and identifier in db_set:
                raise ValidationError(
                    _(
                        "Unit '%(id)s' already exists in this structure. "
                        "Remove it from the grid or use a different identifier."
                    )
                    % {"id": identifier},
                )

            unit_type = str(row.get("unit_type") or "FLAT").strip().upper()
            if unit_type not in valid_unit_types:
                unit_type = "FLAT"

            area = row.get("area")
            try:
                area_val = float(area) if area is not None else None
            except (TypeError, ValueError):
                area_val = None

            entry = {
                "flat_number": identifier,
                "area": area_val,
                "usage_type": str(row.get("usage_type") or "RESIDENTIAL").upper(),
                "unit_type": unit_type,
                "parking_allocation": row.get("parking_allocation", ""),
                "maintenance_calc_method": row.get("maintenance_calc_method", "FLAT"),
                "building": building,
                "wing": row.get("wing", ""),
                "floor": row.get("floor"),
            }
            if structure_id:
                entry["structure_id"] = sid
            normalized.append(entry)
        return normalized

    def generate_bulk_units(self) -> list[dict]:
        """Generate unit data for bulk creation based on form fields.

        Returns a list of unit dicts compatible with UnitConfigurationForm's
        expected format (flat_number, building, wing, floor, unit_type, etc.).
        """
        from decimal import Decimal

        structure_id = self.cleaned_data.get("structure_id")
        floors = self.cleaned_data.get("floors", 10)
        units_per_floor = self.cleaned_data.get("units_per_floor", 12)
        starting_floor = self.cleaned_data.get("starting_floor", 1)
        starting_number = self.cleaned_data.get("starting_number", 1)
        numbering_style = self.cleaned_data.get("numbering_style", "continuous")
        default_unit_type = self.cleaned_data.get("default_unit_type", "FLAT")
        default_area = self.cleaned_data.get("default_area_sqft")
        if default_area is not None:
            default_area = Decimal(str(default_area))

        if not structure_id:
            return []

        try:
            structure = Structure.objects.get(pk=structure_id)
        except Structure.DoesNotExist:
            return []

        units = []
        unit_num = starting_number

        for floor in range(starting_floor, starting_floor + floors):
            for unit_idx in range(1, units_per_floor + 1):
                if numbering_style == "floor_based":
                    identifier = f"{floor}{str(unit_idx).zfill(2)}"
                else:
                    identifier = str(unit_num)

                unit_num += 1

                unit_data = {
                    "flat_number": identifier,
                    "building": structure.name,
                    "wing": "",
                    "floor": floor,
                    "unit_type": default_unit_type,
                    "usage_type": "RESIDENTIAL",
                    "area": float(default_area) if default_area is not None else None,
                    "parking_allocation": "",
                    "maintenance_calc_method": "FLAT",
                }
                units.append(unit_data)

        return units


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
# Step 11 — Manual template entry
# --------------------------------------------------------------------------- #

def _humanize_template_column(column_name: str) -> str:
    """Convert a staging column name into a human-friendly label."""
    return column_name.replace("_", " ").strip().title()


def build_template_entry_form_class(template_type: str) -> type[forms.Form]:
    """Build a per-template row form for direct manual entry.

    The generated form mirrors the staging template columns so users can
    type data directly instead of uploading CSV/XLSX files.
    """
    canonical = StagingService._normalize_template_type(template_type)
    columns = StagingService.get_template_columns(canonical)
    decimal_fields = set(TEMPLATE_DECIMAL_FIELDS[canonical])

    attrs: dict[str, object] = {"__module__": __name__}
    for column in columns:
        label = _humanize_template_column(column)
        if column in decimal_fields:
            attrs[column] = forms.DecimalField(
                required=False,
                max_digits=18,
                decimal_places=2,
                label=label,
            )
        else:
            attrs[column] = forms.CharField(
                required=False,
                max_length=255,
                label=label,
            )

    form_name = f"{canonical.title().replace('_', '')}ManualEntryForm"
    return type(form_name, (BootstrapForm,), attrs)


def build_template_entry_formset(
    template_type: str,
    *,
    data=None,
    initial=None,
    extra: int = 1,
    prefix: str = "rows",
):
    """Return a formset instance for manual staging entry."""
    form_class = build_template_entry_form_class(template_type)
    formset_class = formset_factory(
        form_class,
        extra=extra,
        can_delete=False,
    )
    return formset_class(data=data, initial=initial, prefix=prefix)


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
