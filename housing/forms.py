import json
from decimal import Decimal
from decimal import InvalidOperation

from django import forms
from django.utils.translation import gettext_lazy as _

from societies.models import Society
from members.models import Member
from members.models import Structure
from members.models import Unit
from members.models import UnitOccupancy
from members.models import UnitOwnership
from billing.models import Bill
from billing.models import ChargeTemplate
from accounting.models import Account
from notifications.models import SocietyEmailSettings


class BootstrapModelForm(forms.ModelForm):
    """Apply bootstrap-friendly widgets for all model forms."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            css_class = "form-control"
            if isinstance(widget, forms.CheckboxInput):
                css_class = "form-check-input"
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                css_class = "form-select"
            existing = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{existing} {css_class}".strip()


class BootstrapForm(forms.Form):
    """Apply bootstrap-friendly widgets for standard forms."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            css_class = "form-control"
            if isinstance(widget, forms.CheckboxInput):
                css_class = "form-check-input"
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                css_class = "form-select"
            existing = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{existing} {css_class}".strip()


class SocietyForm(BootstrapModelForm):
    class Meta:
        model = Society
        fields = ["name", "registration_number", "address"]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
        }


class SocietyEmailSettingsForm(BootstrapModelForm):
    smtp_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password"},
            render_value=False,
        ),
        help_text=_("Leave blank to keep the currently stored password."),
        label=_("SMTP password"),
    )

    class Meta:
        model = SocietyEmailSettings
        fields = [
            "is_active",
            "provider_type",
            "smtp_host",
            "smtp_port",
            "smtp_username",
            "smtp_password",
            "use_tls",
            "use_ssl",
            "default_from_email",
            "default_reply_to",
            "daily_limit",
        ]

    override_fields = (
        "smtp_host",
        "smtp_port",
        "smtp_username",
        "smtp_password",
        "default_from_email",
        "default_reply_to",
        "daily_limit",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["provider_type"].initial = "SMTP"
        for field_name in ("smtp_host", "default_from_email"):
            self.fields[field_name].required = False
        self.fields["provider_type"].help_text = _(
            "SMTP is supported now. Other providers are reserved for later expansion."
        )

    def has_override_data(self):
        cleaned_data = getattr(self, "cleaned_data", {})
        for field_name in self.override_fields:
            value = cleaned_data.get(field_name)
            if value not in (None, "", []):
                return True
        return False

    def clean(self):
        cleaned_data = super().clean()
        override_enabled = cleaned_data.get("is_active")
        has_override_data = self.has_override_data()

        if override_enabled or has_override_data:
            if not cleaned_data.get("smtp_host"):
                self.add_error("smtp_host", _("SMTP host is required when override is enabled."))
            if not cleaned_data.get("default_from_email"):
                self.add_error(
                    "default_from_email",
                    _("From email is required when override is enabled."),
                )
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        smtp_password = self.cleaned_data.get("smtp_password")
        if smtp_password:
            instance.set_smtp_password(smtp_password)
        if commit:
            instance.save()
        return instance


class StructureForm(BootstrapModelForm):
    class Meta:
        model = Structure
        fields = ["society", "parent", "structure_type", "name", "display_order"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        society_id = self.initial.get("society")
        if society_id:
            self.fields["parent"].queryset = Structure.objects.filter(
                society_id=society_id
            )


class UnitForm(BootstrapModelForm):
    class Meta:
        model = Unit
        fields = [
            "structure",
            "unit_type",
            "identifier",
            "area_sqft",
            "chargeable_area_sqft",
            "is_active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        society_id = self.initial.get("society")
        if society_id:
            self.fields["structure"].queryset = Structure.objects.filter(
                society_id=society_id
            )
        self.fields["area_sqft"].help_text = _("Can be greater than 300 sq ft.")
        self.fields["chargeable_area_sqft"].help_text = _(
            "Authoritative area used for per-sqft charges."
        )


class BulkUnitCreateForm(BootstrapForm):
    class NumberingStyle:
        CONTINUOUS = "continuous"
        FLOOR_BASED = "floor_based"
        choices = (
            (CONTINUOUS, _("Continuous")),
            (FLOOR_BASED, _("Floor based")),
        )

    structure = forms.ModelChoiceField(
        queryset=Structure.objects.none(),
        label=_("Structure"),
    )
    floors = forms.IntegerField(min_value=1, max_value=100, initial=10)
    units_per_floor = forms.IntegerField(min_value=1, max_value=100, initial=12)
    starting_floor = forms.IntegerField(
        min_value=0,
        max_value=999,
        initial=1,
        help_text=_("Grid starts from this floor number."),
    )
    starting_number = forms.IntegerField(
        min_value=1,
        max_value=999999,
        initial=1,
        help_text=_("First generated unit number."),
    )
    numbering_style = forms.ChoiceField(
        choices=NumberingStyle.choices,
        initial=NumberingStyle.CONTINUOUS,
    )
    default_unit_type = forms.ChoiceField(
        choices=Unit.UnitType.choices,
        initial=Unit.UnitType.FLAT,
        label=_("Default unit type"),
    )
    default_area_sqft = forms.DecimalField(
        required=False,
        min_value=Decimal("0"),
        decimal_places=2,
        max_digits=8,
        label=_("Default area (sq ft)"),
    )
    default_chargeable_area_sqft = forms.DecimalField(
        required=False,
        min_value=Decimal("0"),
        decimal_places=2,
        max_digits=8,
        label=_("Default chargeable area (sq ft)"),
    )
    units_json = forms.CharField(widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        society_id = self.initial.get("society")
        queryset = Structure.objects.select_related("society").order_by("name")
        if society_id:
            queryset = queryset.filter(society_id=society_id)
        self.fields["structure"].queryset = queryset

    def _clean_decimal_value(self, value, field_name):
        if value in (None, ""):
            return None
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise forms.ValidationError(
                _("Enter a valid number for %(field)s."),
                params={"field": self.fields[field_name].label},
            ) from None
        if decimal_value < 0:
            raise forms.ValidationError(
                _("%(field)s cannot be negative."),
                params={"field": self.fields[field_name].label},
            )
        return decimal_value.quantize(Decimal("0.01"))

    def clean(self):
        cleaned_data = super().clean()
        payload = cleaned_data.get("units_json")
        structure = cleaned_data.get("structure")

        if not payload:
            self.add_error("units_json", _("Generate a grid before saving."))
            return cleaned_data

        try:
            rows = json.loads(payload)
        except json.JSONDecodeError:
            self.add_error("units_json", _("Grid data is invalid. Please regenerate it."))
            return cleaned_data

        if not isinstance(rows, list) or not rows:
            self.add_error("units_json", _("Add at least one unit to save."))
            return cleaned_data

        normalized_units = []
        seen_identifiers = set()

        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                self.add_error("units_json", _("Grid row %(row)s is invalid.") % {"row": index})
                continue

            identifier = str(row.get("identifier", "")).strip()
            if not identifier:
                self.add_error(
                    "units_json",
                    _("Unit %(row)s is missing an identifier.") % {"row": index},
                )
                continue

            if identifier in seen_identifiers:
                self.add_error(
                    "units_json",
                    _("Duplicate identifier %(identifier)s in the grid.")
                    % {"identifier": identifier},
                )
                continue

            unit_type = row.get("unit_type") or cleaned_data.get("default_unit_type")
            valid_unit_types = {choice[0] for choice in Unit.UnitType.choices}
            if unit_type not in valid_unit_types:
                self.add_error(
                    "units_json",
                    _("Unit %(identifier)s has an invalid type.") % {"identifier": identifier},
                )
                continue

            try:
                floor_number = int(row.get("floor"))
            except (TypeError, ValueError):
                self.add_error(
                    "units_json",
                    _("Unit %(identifier)s is missing a valid floor.")
                    % {"identifier": identifier},
                )
                continue

            try:
                column_number = int(row.get("column"))
            except (TypeError, ValueError):
                column_number = index

            try:
                area_sqft = self._clean_decimal_value(row.get("area_sqft"), "default_area_sqft")
                chargeable_area_sqft = self._clean_decimal_value(
                    row.get("chargeable_area_sqft"),
                    "default_chargeable_area_sqft",
                )
            except forms.ValidationError as error:
                self.add_error(
                    "units_json",
                    _("Unit %(identifier)s: %(message)s")
                    % {"identifier": identifier, "message": error.messages[0]},
                )
                continue

            normalized_units.append(
                {
                    "identifier": identifier,
                    "unit_type": unit_type,
                    "floor": floor_number,
                    "column": column_number,
                    "area_sqft": area_sqft,
                    "chargeable_area_sqft": chargeable_area_sqft,
                    "is_active": bool(row.get("is_active", True)),
                }
            )
            seen_identifiers.add(identifier)

        if self.errors:
            return cleaned_data

        if structure:
            existing_identifiers = set(
                Unit.objects.filter(
                    structure=structure,
                    identifier__in=seen_identifiers,
                ).values_list("identifier", flat=True)
            )
            if existing_identifiers:
                duplicates = ", ".join(sorted(existing_identifiers))
                self.add_error(
                    "units_json",
                    _("These unit identifiers already exist in this structure: %(identifiers)s")
                    % {"identifiers": duplicates},
                )

        cleaned_data["grid_units"] = normalized_units
        return cleaned_data


class UnitOwnershipForm(BootstrapModelForm):
    class Meta:
        model = UnitOwnership
        fields = ["unit", "owner", "role", "start_date", "end_date"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        society_id = self.initial.get("society")
        if society_id:
            self.fields["unit"].queryset = Unit.objects.filter(
                structure__society_id=society_id
            )


class UnitOccupancyForm(BootstrapModelForm):
    class Meta:
        model = UnitOccupancy
        fields = ["unit", "occupant", "occupancy_type", "start_date", "end_date"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        society_id = self.initial.get("society")
        if society_id:
            self.fields["unit"].queryset = Unit.objects.filter(
                structure__society_id=society_id
            )


class MemberForm(BootstrapModelForm):
    class Meta:
        model = Member
        fields = [
            "society",
            "unit",
            "user",
            "full_name",
            "email",
            "phone",
            "role",
            "status",
            "receivable_account",
            "start_date",
            "end_date",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        society_id = self.initial.get("society")
        if society_id:
            self.fields["unit"].queryset = Unit.objects.filter(
                structure__society_id=society_id
            )
            self.fields["receivable_account"].queryset = Account.objects.filter(
                society_id=society_id,
            ).order_by("name")


class ChargeTemplateForm(BootstrapModelForm):
    class Meta:
        model = ChargeTemplate
        fields = [
            "society",
            "name",
            "description",
            "charge_type",
            "rate",
            "frequency",
            "due_days",
            "late_fee_percent",
            "effective_from",
            "effective_to",
            "income_account",
            "receivable_account",
            "is_active",
        ]
        widgets = {
            "effective_from": forms.DateInput(attrs={"type": "date"}),
            "effective_to": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        society_id = self.initial.get("society")
        if society_id:
            self.fields["income_account"].queryset = Account.objects.filter(
                society_id=society_id,
                category__account_type="INCOME",
            ).order_by("name")
            self.fields["receivable_account"].queryset = Account.objects.filter(
                society_id=society_id,
                category__account_type="ASSET",
            ).order_by("name")
        self.fields["rate"].help_text = _("Use flat rate or per-sqft rate based on charge type.")


class BillingGenerationForm(BootstrapForm):
    society = forms.ModelChoiceField(queryset=Society.objects.all())
    period_start = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    period_end = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    bill_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        cleaned = super().clean()
        period_start = cleaned.get("period_start")
        period_end = cleaned.get("period_end")
        bill_date = cleaned.get("bill_date")
        if period_start and period_end and period_end < period_start:
            raise forms.ValidationError("Period end date cannot be before period start date.")
        if period_start and bill_date and bill_date < period_start:
            raise forms.ValidationError("Bill date cannot be before period start date.")
        return cleaned


class ReceiptPostingForm(BootstrapForm):
    society = forms.ModelChoiceField(queryset=Society.objects.all())
    member = forms.ModelChoiceField(queryset=Member.objects.none())
    bill = forms.ModelChoiceField(queryset=Bill.objects.none())
    receipt_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    payment_mode = forms.ChoiceField(
        choices=(
            ("CASH", "Cash"),
            ("BANK_TRANSFER", "Bank Transfer"),
            ("CHEQUE", "Cheque"),
            ("UPI", "UPI"),
            ("CARD", "Card"),
            ("OTHER", "Other"),
        )
    )
    reference_number = forms.CharField(required=False)
    deposited_account = forms.ModelChoiceField(queryset=Account.objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        society = None
        if self.is_bound:
            society_id = self.data.get("society")
            if society_id:
                society = Society.objects.filter(pk=society_id).first()
        else:
            society = self.initial.get("society")
        if society:
            self.fields["member"].queryset = Member.objects.filter(
                society=society,
                status=Member.MemberStatus.ACTIVE,
            ).order_by("full_name")
            self.fields["bill"].queryset = Bill.objects.filter(
                society=society,
                status__in=(Bill.BillStatus.OPEN, Bill.BillStatus.PARTIAL, Bill.BillStatus.OVERDUE),
            ).order_by("-due_date", "-id")
            self.fields["deposited_account"].queryset = Account.objects.filter(
                society=society,
                category__account_type="ASSET",
            ).order_by("name")

    def clean(self):
        cleaned = super().clean()
        payment_mode = cleaned.get("payment_mode")
        reference = (cleaned.get("reference_number") or "").strip()
        amount = cleaned.get("amount") or Decimal("0.00")
        bill = cleaned.get("bill")
        member = cleaned.get("member")
        if payment_mode != "CASH" and not reference:
            raise forms.ValidationError("Reference number is required for non-cash receipts.")
        if bill and member and bill.member_id != member.id:
            raise forms.ValidationError("Bill must belong to selected member.")
        if bill and amount > bill.outstanding_amount:
            raise forms.ValidationError("Receipt amount cannot exceed bill outstanding amount.")
        return cleaned
