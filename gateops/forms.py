"""Practical Django forms for the server-rendered gate operations test console."""

from __future__ import annotations

import json

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit

from gateops.models import (
    ApprovalType,
    Contract,
    Contractor,
    Gate,
    GateEvent,
    GateEventApproval,
    GateOpsRole,
    GateOpsSocietyConfig,
    GateVehicle,
    GuardShift,
    HolidayCalendar,
    MasterSettings,
    MaterialCategory,
    NotificationPreference,
    PassType,
    Person,
    Rule,
    RuleAction,
    RuleCondition,
    SecurityGuard,
    ShiftHandover,
    VehicleCategory,
    VisitorCategory,
    WorkPermit,
    Worker,
)


_FORM_CONTROL_CLASS = "form-control"
_FORM_SELECT_CLASS = "form-select"
_FORM_CHECK_CLASS = "form-check-input"


def _json_loads(value: str, *, empty_default):
    """Parse a JSON textarea value with a predictable empty default."""
    if value in (None, ""):
        return empty_default
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Enter valid JSON: {exc.msg}") from exc


class SocietyScopedModelForm(forms.ModelForm):
    """Base form that limits gateops FK querysets to one society."""

    society_scoped_fields = (
        "visitor_category",
        "vehicle_category",
        "material_category",
        "gate",
    )

    def __init__(self, *args, society=None, **kwargs):
        self.society = society
        super().__init__(*args, **kwargs)
        if society is not None:
            if "visitor_category" in self.fields:
                self.fields["visitor_category"].queryset = VisitorCategory.objects.filter(society=society, is_active=True)
            if "vehicle_category" in self.fields:
                self.fields["vehicle_category"].queryset = VehicleCategory.objects.filter(society=society, is_active=True)
            if "material_category" in self.fields:
                self.fields["material_category"].queryset = MaterialCategory.objects.filter(society=society, is_active=True)
            if "gate" in self.fields:
                self.fields["gate"].queryset = Gate.objects.filter(society=society, is_active=True)
            if "default_pass_type" in self.fields:
                self.fields["default_pass_type"].queryset = PassType.objects.filter(society=society, is_active=True)
        for field_name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", _FORM_CHECK_CLASS)
            elif isinstance(widget, forms.Select):
                widget.attrs.setdefault("class", _FORM_SELECT_CLASS)
            else:
                widget.attrs.setdefault("class", _FORM_CONTROL_CLASS)

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.society is not None and hasattr(instance, "society_id"):
            instance.society = self.society
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class RuleForm(SocietyScopedModelForm):
    """Create/edit a gate operations rule for the selected society."""

    class Meta:
        model = Rule
        fields = (
            "name",
            "code",
            "description",
            "priority",
            "is_active",
            "visitor_category",
            "vehicle_category",
            "material_category",
            "gate",
            "valid_from",
            "valid_until",
            "applies_on",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "valid_from": forms.DateInput(attrs={"type": "date"}),
            "valid_until": forms.DateInput(attrs={"type": "date"}),
        }

    fieldsets = (
        {
            "title": "Rule identity",
            "description": "Use a clear name and stable code so audit logs and rule test output are easy to read.",
            "fields": ("name", "code", "description"),
        },
        {
            "title": "Priority and status",
            "description": "Active rules run in ascending priority order. Lower numbers run first, and the first matching rule decides the action.",
            "fields": ("priority", "is_active", "valid_from", "valid_until"),
        },
        {
            "title": "Where this rule applies",
            "description": "Leave a selector blank to apply the rule to all values for that dimension. Combine selectors only when the rule must be narrow.",
            "fields": ("applies_on", "visitor_category", "vehicle_category", "material_category", "gate"),
        },
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].help_text = "Short operational name, for example Delivery Auto Approve."
        self.fields["code"].help_text = "Uppercase letters, numbers, and underscores only. Must be unique among active rules for this society."
        self.fields["description"].help_text = "Optional internal note explaining why this rule exists and when it should be reviewed."
        self.fields["priority"].help_text = "Lower number means higher priority. Use gaps such as 10, 20, 30 so future rules can fit between them."
        self.fields["is_active"].help_text = "Only active rules are evaluated. Disable instead of deleting when you need to pause behavior."
        self.fields["applies_on"].help_text = "Choose entry, exit, or both depending on when the gate decision should be made."
        self.fields["visitor_category"].help_text = "Optional. Restricts the rule to one visitor category such as Delivery or Contractor."
        self.fields["vehicle_category"].help_text = "Optional. Use only when the vehicle type changes the gate decision."
        self.fields["material_category"].help_text = "Optional. Use for material movement rules such as inbound or outbound goods."
        self.fields["gate"].help_text = "Optional. Restricts the rule to one gate; blank means all gates."
        self.fields["valid_from"].help_text = "Date from which this rule can match. Defaults to today."
        self.fields["valid_until"].help_text = "Optional end date. Must be after the start date when provided."
        self.fields["visitor_category"].required = False
        self.fields["vehicle_category"].required = False
        self.fields["material_category"].required = False
        self.fields["gate"].required = False
        self.fields["valid_from"].initial = self.fields["valid_from"].initial or timezone.localdate()

    def clean_code(self):
        return str(self.cleaned_data["code"]).strip().upper()

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.society is not None:
            instance.society = self.society
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class GateOpsSocietyConfigForm(SocietyScopedModelForm):
    """Edit society-level GateOps behavior switches."""

    class Meta:
        model = GateOpsSocietyConfig
        fields = (
            "default_approval_timeout_minutes",
            "photo_required",
            "otp_length",
            "data_retention_days",
            "offline_sync_window_hours",
            "require_id_verification",
            "enable_qr_pass",
            "enable_otp_pass",
            "enable_pin_pass",
            "auto_close_enabled",
            "auto_close_after_hours",
            "max_concurrent_visitors",
            "night_mode_start",
            "night_mode_end",
        )
        widgets = {
            "night_mode_start": forms.TimeInput(attrs={"type": "time"}),
            "night_mode_end": forms.TimeInput(attrs={"type": "time"}),
        }


class GateForm(SocietyScopedModelForm):
    class Meta:
        model = Gate
        fields = ("name", "code", "gate_type", "gps_lat", "gps_lng", "is_active")

    def clean_code(self):
        return str(self.cleaned_data["code"]).strip().upper()

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.society is not None:
            instance.society = self.society
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class VisitorCategoryForm(SocietyScopedModelForm):
    class Meta:
        model = VisitorCategory
        fields = (
            "name",
            "code",
            "icon",
            "is_delivery",
            "is_domestic_help",
            "is_contractor",
            "is_emergency",
            "is_resident",
            "requires_approval_default",
            "default_pass_type",
            "sort_order",
            "is_active",
        )

    def clean_code(self):
        return str(self.cleaned_data["code"]).strip().upper()

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.society is not None:
            instance.society = self.society
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class VehicleCategoryForm(SocietyScopedModelForm):
    class Meta:
        model = VehicleCategory
        fields = (
            "name",
            "code",
            "is_commercial",
            "is_delivery",
            "is_emergency",
            "is_electric",
            "is_oversized",
            "requires_approval_default",
            "sort_order",
            "is_active",
        )

    def clean_code(self):
        return str(self.cleaned_data["code"]).strip().upper()

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.society is not None:
            instance.society = self.society
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class MaterialCategoryForm(SocietyScopedModelForm):
    class Meta:
        model = MaterialCategory
        fields = ("name", "code", "is_inbound_default", "requires_approval_default", "sort_order", "is_active")

    def clean_code(self):
        return str(self.cleaned_data["code"]).strip().upper()

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.society is not None:
            instance.society = self.society
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class PassTypeForm(SocietyScopedModelForm):
    class Meta:
        model = PassType
        fields = ("name", "code", "validation_method", "duration_type", "default_validity_hours", "is_active")

    def clean_code(self):
        return str(self.cleaned_data["code"]).strip().upper()

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.society is not None:
            instance.society = self.society
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class ApprovalTypeForm(SocietyScopedModelForm):
    class Meta:
        model = ApprovalType
        fields = ("name", "code", "approver", "escalation_timeout_minutes", "is_active")

    def clean_code(self):
        return str(self.cleaned_data["code"]).strip().upper()

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.society is not None:
            instance.society = self.society
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class NotificationPreferenceForm(SocietyScopedModelForm):
    class Meta:
        model = NotificationPreference
        fields = ("visitor_category", "channel", "trigger", "is_silent", "bundle_window_minutes", "is_active")

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.society is not None:
            instance.society = self.society
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class GateOpsRoleForm(SocietyScopedModelForm):
    permissions_text = forms.CharField(
        label="Permissions JSON",
        required=False,
        widget=forms.Textarea(attrs={"rows": 8, "class": _FORM_CONTROL_CLASS}),
        help_text="JSON object using GateOps permission keys with boolean values.",
    )

    class Meta:
        model = GateOpsRole
        fields = ("name", "code", "permissions_text", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["permissions_text"].initial = json.dumps(self.instance.permissions, indent=2, sort_keys=True)
        else:
            self.fields["permissions_text"].initial = "{}"

    def clean_permissions_text(self):
        value = _json_loads(self.cleaned_data.get("permissions_text", ""), empty_default={})
        if not isinstance(value, dict):
            raise ValidationError("Permissions JSON must be an object.")
        return value

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.society is not None:
            instance.society = self.society
        instance.permissions = self.cleaned_data["permissions_text"]
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class HolidayCalendarForm(SocietyScopedModelForm):
    class Meta:
        model = HolidayCalendar
        fields = ("name", "date", "is_recurring_annually", "affects", "notes")
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.society is not None:
            instance.society = self.society
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class MasterSettingsForm(SocietyScopedModelForm):
    settings_text = forms.CharField(
        label="Settings JSON",
        required=False,
        widget=forms.Textarea(attrs={"rows": 10, "class": _FORM_CONTROL_CLASS}),
        help_text='JSON object for flexible society-specific settings, for example {"default_language": "en"}.',
    )

    class Meta:
        model = MasterSettings
        fields = ("settings_text",)

    def __init__(self, *args, **kwargs):
        self.updated_by = kwargs.pop("updated_by", None)
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["settings_text"].initial = json.dumps(self.instance.settings, indent=2, sort_keys=True)
        else:
            self.fields["settings_text"].initial = "{}"

    def clean_settings_text(self):
        value = _json_loads(self.cleaned_data.get("settings_text", ""), empty_default={})
        if not isinstance(value, dict):
            raise ValidationError("Settings JSON must be an object.")
        return value

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.society is not None:
            instance.society = self.society
        instance.settings = self.cleaned_data["settings_text"]
        if self.updated_by is not None and getattr(self.updated_by, "is_authenticated", False):
            instance.updated_by = self.updated_by
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class RuleConditionForm(forms.ModelForm):
    """Small condition editor with JSON value support."""

    value_text = forms.CharField(
        label="Value JSON",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "class": _FORM_CONTROL_CLASS}),
        help_text='Examples: "DELIVERY", "A", ["A", "B"], {"start": 5, "end": 15}. Boolean operators may use {}.',
    )

    class Meta:
        model = RuleCondition
        fields = ("field", "operator", "logical_connector", "sort_order")
        widgets = {
            "field": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "operator": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "logical_connector": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "sort_order": forms.NumberInput(attrs={"class": _FORM_CONTROL_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        self.rule = kwargs.pop("rule", None)
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["value_text"].initial = json.dumps(self.instance.value, indent=2, default=str)
        else:
            self.fields["field"].initial = RuleCondition.ConditionField.VISITOR_TYPE
            self.fields["operator"].initial = RuleCondition.Operator.EQ
            self.fields["value_text"].initial = '"DELIVERY"'

    def clean_value_text(self):
        operator = self.cleaned_data.get("operator")
        value = _json_loads(self.cleaned_data.get("value_text", ""), empty_default={})
        if operator in {RuleCondition.Operator.IS_TRUE, RuleCondition.Operator.IS_FALSE}:
            return value or {}
        if value in (None, "", {}, []):
            raise ValidationError("Value JSON is required for this operator.")
        return value

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.rule is not None:
            instance.rule = self.rule
        instance.value = self.cleaned_data["value_text"]
        if commit:
            instance.save()
        return instance


class RuleActionForm(forms.ModelForm):
    """Small action editor with JSON parameters support."""

    parameters_text = forms.CharField(
        label="Parameters JSON",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "class": _FORM_CONTROL_CLASS}),
        help_text='Optional action parameters, for example {"notify_channels": ["push"]}.',
    )

    class Meta:
        model = RuleAction
        fields = ("action", "execution_order")
        widgets = {
            "action": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "execution_order": forms.NumberInput(attrs={"class": _FORM_CONTROL_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        self.rule = kwargs.pop("rule", None)
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["parameters_text"].initial = json.dumps(self.instance.parameters, indent=2, default=str)
        else:
            self.fields["action"].initial = RuleAction.ActionType.AUTO_APPROVE
            self.fields["parameters_text"].initial = "{}"

    def clean_parameters_text(self):
        value = _json_loads(self.cleaned_data.get("parameters_text", ""), empty_default={})
        if not isinstance(value, dict):
            raise ValidationError("Parameters JSON must be an object.")
        return value

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.rule is not None:
            instance.rule = self.rule
        instance.parameters = self.cleaned_data["parameters_text"]
        if commit:
            instance.save()
        return instance


class RuleContextTestForm(forms.Form):
    """Browser-friendly context builder for the real rule engine."""

    applies_on = forms.ChoiceField(choices=Rule.AppliesOn.choices, initial=Rule.AppliesOn.ENTRY)
    visitor_category = forms.ChoiceField(choices=(), initial="DELIVERY")
    vehicle_category = forms.ChoiceField(choices=(), required=False)
    gate = forms.ChoiceField(choices=(), required=False)
    date = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}))
    time = forms.TimeField(required=False, widget=forms.TimeInput(attrs={"type": "time"}))
    tower = forms.CharField(initial="A", max_length=20)
    wing = forms.CharField(initial="1", max_length=20)
    flat = forms.CharField(initial="101", max_length=20)
    max_visitors = forms.IntegerField(required=False, min_value=0)
    is_emergency = forms.BooleanField(required=False, initial=False)
    is_vip = forms.BooleanField(required=False, initial=False)
    is_blacklisted = forms.BooleanField(required=False, initial=False)
    pass_is_valid = forms.BooleanField(required=False, initial=True)
    extra_context = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="Optional JSON object merged into the context after the fields above.",
    )
    rule = forms.ModelChoiceField(
        queryset=Rule.objects.none(),
        required=False,
        help_text="Optional: also dry-run this single rule with RuleTestService.",
    )

    def __init__(self, *args, society=None, **kwargs):
        self.society = society
        super().__init__(*args, **kwargs)
        visitor_choices = [(item.code, f"{item.name} ({item.code})") for item in VisitorCategory.objects.filter(society=society, is_active=True)] if society else []
        vehicle_choices = [("", "No vehicle category")] + ([(item.code, f"{item.name} ({item.code})") for item in VehicleCategory.objects.filter(society=society, is_active=True)] if society else [])
        gate_choices = [("", "Any gate")] + ([(str(item.pk), f"{item.name} ({item.code})") for item in Gate.objects.filter(society=society, is_active=True)] if society else [])
        self.fields["visitor_category"].choices = visitor_choices or [("DELIVERY", "Delivery (DELIVERY)")]
        self.fields["vehicle_category"].choices = vehicle_choices
        self.fields["gate"].choices = gate_choices
        if society is not None:
            self.fields["rule"].queryset = Rule.objects.filter(society=society).order_by("priority", "name")
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", _FORM_CHECK_CLASS)
            elif isinstance(widget, forms.Select):
                widget.attrs.setdefault("class", _FORM_SELECT_CLASS)
            else:
                widget.attrs.setdefault("class", _FORM_CONTROL_CLASS)

    def clean_extra_context(self):
        value = self.cleaned_data.get("extra_context", "")
        parsed = _json_loads(value, empty_default={})
        if not isinstance(parsed, dict):
            raise ValidationError("Extra context must be a JSON object.")
        return parsed

    def build_context(self):
        data = self.cleaned_data
        visitor_category = data["visitor_category"]
        vehicle_category = data.get("vehicle_category") or None
        context = {
            "society": self.society,
            "society_id": self.society.pk if self.society else None,
            "applies_on": data["applies_on"],
            "date": data["date"],
            "time": data.get("time"),
            "visitor_category": visitor_category,
            "visitor_category_code": visitor_category,
            "vehicle_category": vehicle_category,
            "vehicle_category_code": vehicle_category,
            "tower": data["tower"],
            "wing": data["wing"],
            "flat": data["flat"],
            "max_visitors": data.get("max_visitors"),
            "is_emergency": data.get("is_emergency", False),
            "is_vip": data.get("is_vip", False),
            "person": {"is_blacklisted": data.get("is_blacklisted", False)},
            "pass": {"is_valid": data.get("pass_is_valid", False)},
        }
        if data.get("gate"):
            context["gate_id"] = int(data["gate"])
        visitor = VisitorCategory.objects.filter(society=self.society, code=visitor_category).first()
        if visitor:
            context["visitor_category_id"] = visitor.pk
        vehicle = VehicleCategory.objects.filter(society=self.society, code=vehicle_category).first() if vehicle_category else None
        if vehicle:
            context["vehicle_category_id"] = vehicle.pk
        context.update(data.get("extra_context") or {})
        return {key: value for key, value in context.items() if value is not None}


class PersonForm(SocietyScopedModelForm):
    """Form for creating/editing a Person (master visitor record)."""

    id_number = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": _FORM_CONTROL_CLASS, "placeholder": _("Enter ID number")}),
        help_text=_("Stored encrypted at rest."),
    )

    class Meta:
        model = Person
        fields = ("name", "phone", "email", "id_type", "is_blacklisted", "blacklist_reason", "blacklist_until", "is_vip")
        widgets = {
            "name": forms.TextInput(attrs={"class": _FORM_CONTROL_CLASS}),
            "phone": forms.TextInput(attrs={"class": _FORM_CONTROL_CLASS}),
            "email": forms.EmailInput(attrs={"class": _FORM_CONTROL_CLASS}),
            "id_type": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "blacklist_reason": forms.Textarea(attrs={"class": _FORM_CONTROL_CLASS, "rows": 2}),
            "blacklist_until": forms.DateInput(attrs={"class": _FORM_CONTROL_CLASS, "type": "date"}),
            "is_blacklisted": forms.CheckboxInput(attrs={"class": _FORM_CHECK_CLASS}),
            "is_vip": forms.CheckboxInput(attrs={"class": _FORM_CHECK_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["id_number"].initial = self.instance.id_number

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.id_number = self.cleaned_data.get("id_number", "")
        if commit:
            instance.save()
        return instance


class GateEventForm(SocietyScopedModelForm):
    """Form for creating a new gate event (arrival flow)."""

    person_phone = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": _FORM_CONTROL_CLASS, "placeholder": _("Phone number to lookup or create person")}),
        help_text=_("If a person with this phone exists, they will be linked; otherwise a new person is created."),
    )
    person_name = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": _FORM_CONTROL_CLASS, "placeholder": _("Visitor name (required for new person)")}),
    )

    class Meta:
        model = GateEvent
        fields = ("visitor_category", "gate", "direction", "purpose", "photo_url", "id_verified", "notes")
        widgets = {
            "visitor_category": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "gate": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "direction": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "purpose": forms.Textarea(attrs={"class": _FORM_CONTROL_CLASS, "rows": 2}),
            "photo_url": forms.URLInput(attrs={"class": _FORM_CONTROL_CLASS, "placeholder": "https://..."}),
            "id_verified": forms.CheckboxInput(attrs={"class": _FORM_CHECK_CLASS}),
            "notes": forms.Textarea(attrs={"class": _FORM_CONTROL_CLASS, "rows": 2}),
        }

    def save(self, commit=True):
        """Don't save the GateEvent here — the view handles lifecycle via the service."""
        # This form is used for data collection; the view calls GateEventLifecycleService
        return self.cleaned_data


class GateEventApprovalForm(SocietyScopedModelForm):
    """Form for recording an approval/rejection decision."""

    class Meta:
        model = GateEventApproval
        fields = ("decision", "decision_method", "notes")
        widgets = {
            "decision": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "decision_method": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "notes": forms.Textarea(attrs={"class": _FORM_CONTROL_CLASS, "rows": 2}),
        }


class VehicleRegisterForm(forms.ModelForm):
    """Register a visitor/non-resident vehicle for the active society.

    Society-scoped querysets for ``vehicle_category`` and ``person`` are
    narrowed in ``__init__`` from an optional ``society`` kwarg supplied by
    the view. Watchlist, repeat, and timestamp fields are intentionally
    excluded — they are owned by the vehicle service layer, not this form.
    """

    class Meta:
        model = GateVehicle
        fields = ("vehicle_number", "vehicle_category", "person", "notes")
        widgets = {
            "vehicle_number": forms.TextInput(
                attrs={"class": _FORM_CONTROL_CLASS, "placeholder": "e.g. MH12 AB 1234"}
            ),
            "vehicle_category": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "person": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "notes": forms.Textarea(attrs={"class": _FORM_CONTROL_CLASS, "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        society = kwargs.pop("society", None)
        super().__init__(*args, **kwargs)
        if society is not None:
            self.fields["vehicle_category"].queryset = VehicleCategory.objects.filter(
                society=society, is_active=True
            )
            self.fields["person"].queryset = Person.objects.filter(
                society=society, is_active=True
            )
        self.fields["vehicle_number"].help_text = _("Vehicle registration number")
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.add_input(Submit("submit", _("Register Vehicle")))

    def clean_vehicle_number(self):
        """Normalize to uppercase and strip whitespace (matches model.clean)."""
        return self.cleaned_data["vehicle_number"].upper().strip()


# ---------------------------------------------------------------------------
# Phase 9: Contractor Management
# ---------------------------------------------------------------------------


class ContractorForm(SocietyScopedModelForm):
    """Create/edit a Contractor (contracting company master record).

    Society scoping is handled by :class:`SocietyScopedModelForm` — the
    ``society`` kwarg is supplied by the view and stamped onto the instance
    in :meth:`save`.
    """

    class Meta:
        model = Contractor
        fields = (
            "company_name",
            "supervisor_name",
            "supervisor_phone",
            "contact_person",
            "contact_phone",
            "gst_number",
            "pan_number",
            "address",
        )
        widgets = {
            "company_name": forms.TextInput(attrs={"class": _FORM_CONTROL_CLASS}),
            "supervisor_name": forms.TextInput(attrs={"class": _FORM_CONTROL_CLASS}),
            "supervisor_phone": forms.TextInput(attrs={"class": _FORM_CONTROL_CLASS}),
            "contact_person": forms.TextInput(attrs={"class": _FORM_CONTROL_CLASS}),
            "contact_phone": forms.TextInput(attrs={"class": _FORM_CONTROL_CLASS}),
            "gst_number": forms.TextInput(attrs={"class": _FORM_CONTROL_CLASS}),
            "pan_number": forms.TextInput(attrs={"class": _FORM_CONTROL_CLASS}),
            "address": forms.Textarea(attrs={"class": _FORM_CONTROL_CLASS, "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.add_input(Submit("submit", _("Save Contractor")))

    def clean_company_name(self):
        return str(self.cleaned_data["company_name"]).strip()

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.society is not None:
            instance.society = self.society
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class ContractForm(SocietyScopedModelForm):
    """Create/edit a Contract (work engagement under a contractor).

    The ``contractor`` queryset is narrowed to the current society's active
    contractors in ``__init__`` so a cross-tenant contractor can never be
    selected from the dropdown.
    """

    class Meta:
        model = Contract
        fields = (
            "contractor",
            "title",
            "description",
            "start_date",
            "end_date",
            "max_workers",
            "status",
        )
        widgets = {
            "contractor": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "title": forms.TextInput(attrs={"class": _FORM_CONTROL_CLASS}),
            "description": forms.Textarea(attrs={"class": _FORM_CONTROL_CLASS, "rows": 3}),
            "start_date": forms.DateInput(attrs={"class": _FORM_CONTROL_CLASS, "type": "date"}),
            "end_date": forms.DateInput(attrs={"class": _FORM_CONTROL_CLASS, "type": "date"}),
            "max_workers": forms.NumberInput(attrs={"class": _FORM_CONTROL_CLASS}),
            "status": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.society is not None:
            self.fields["contractor"].queryset = Contractor.objects.filter(
                society=self.society, is_active=True
            )
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.add_input(Submit("submit", _("Save Contract")))

    def clean_title(self):
        return str(self.cleaned_data["title"]).strip()

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.society is not None:
            instance.society = self.society
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class WorkerForm(SocietyScopedModelForm):
    """Register/edit a Worker (Person ↔ Contract link).

    The ``contract`` and ``person`` querysets are narrowed to the current
    society's active rows in ``__init__`` so cross-tenant data cannot leak
    through the dropdowns.
    """

    class Meta:
        model = Worker
        fields = ("contract", "person", "designation", "id_type", "id_number")
        widgets = {
            "contract": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "person": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "designation": forms.TextInput(attrs={"class": _FORM_CONTROL_CLASS}),
            "id_type": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "id_number": forms.TextInput(attrs={"class": _FORM_CONTROL_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.society is not None:
            self.fields["contract"].queryset = Contract.objects.filter(
                society=self.society, is_active=True
            )
            self.fields["person"].queryset = Person.objects.filter(
                society=self.society, is_active=True
            )
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.add_input(Submit("submit", _("Save Worker")))

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.society is not None:
            instance.society = self.society
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class WorkPermitForm(SocietyScopedModelForm):
    """Issue/edit a WorkPermit (time-bound work authorization).

    The ``contract`` queryset is narrowed to the current society's active
    contracts in ``__init__`` so a cross-tenant contract can never be
    selected from the dropdown.
    """

    class Meta:
        model = WorkPermit
        fields = (
            "contract",
            "permit_number",
            "issued_at",
            "expires_at",
            "safety_docs_verified",
            "safety_briefing_given",
            "work_area",
            "hazard_level",
            "notes",
        )
        widgets = {
            "contract": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "permit_number": forms.TextInput(attrs={"class": _FORM_CONTROL_CLASS}),
            "issued_at": forms.DateTimeInput(attrs={"class": _FORM_CONTROL_CLASS, "type": "datetime-local"}),
            "expires_at": forms.DateTimeInput(attrs={"class": _FORM_CONTROL_CLASS, "type": "datetime-local"}),
            "safety_docs_verified": forms.CheckboxInput(attrs={"class": _FORM_CHECK_CLASS}),
            "safety_briefing_given": forms.CheckboxInput(attrs={"class": _FORM_CHECK_CLASS}),
            "work_area": forms.TextInput(attrs={"class": _FORM_CONTROL_CLASS}),
            "hazard_level": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "notes": forms.Textarea(attrs={"class": _FORM_CONTROL_CLASS, "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.society is not None:
            self.fields["contract"].queryset = Contract.objects.filter(
                society=self.society, is_active=True
            )
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.add_input(Submit("submit", _("Save Work Permit")))

    def clean_permit_number(self):
        return str(self.cleaned_data["permit_number"]).strip()

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.society is not None:
            instance.society = self.society
        if commit:
            instance.save()
            self.save_m2m()
        return instance


# ---------------------------------------------------------------------------
# Phase 12 — Exit Management forms
# ---------------------------------------------------------------------------


class QuickExitForm(forms.Form):
    """Single-field form for one-tap exit by GateEvent UUID or PK."""

    gate_event_id = forms.CharField(
        label=_("Gate Event ID or UUID"),
        max_length=64,
        widget=forms.TextInput(attrs={"class": _FORM_CONTROL_CLASS, "placeholder": _("UUID or numeric ID")}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.add_input(Submit("submit", _("Process Quick Exit")))

    def clean_gate_event_id(self):
        return str(self.cleaned_data["gate_event_id"]).strip()


class QrExitForm(forms.Form):
    """Single-field form for QR-code-based exit (Pass code or GateEvent UUID)."""

    qr_code = forms.CharField(
        label=_("QR Code"),
        max_length=128,
        widget=forms.TextInput(
            attrs={"class": _FORM_CONTROL_CLASS, "placeholder": _("Scan or enter QR code"), "autocomplete": "off"}
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.add_input(Submit("submit", _("Process QR Exit")))

    def clean_qr_code(self):
        return str(self.cleaned_data["qr_code"]).strip()


class ShiftHandoverForm(SocietyScopedModelForm):
    """Create a ShiftHandover (outgoing guard hands over to incoming guard).

    Society-scoped querysets for ``outgoing_guard``, ``incoming_guard``,
    ``gate`` and ``shift`` are narrowed in ``__init__`` so cross-tenant
    selections are impossible from the dropdown.
    """

    class Meta:
        model = ShiftHandover
        fields = (
            "outgoing_guard",
            "incoming_guard",
            "gate",
            "shift",
            "outgoing_notes",
        )
        widgets = {
            "outgoing_guard": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "incoming_guard": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "gate": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "shift": forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
            "outgoing_notes": forms.Textarea(attrs={"class": _FORM_CONTROL_CLASS, "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.society is not None:
            guard_qs = SecurityGuard.objects.filter(society=self.society, is_active=True)
            self.fields["outgoing_guard"].queryset = guard_qs
            self.fields["incoming_guard"].queryset = guard_qs
            self.fields["shift"].queryset = GuardShift.objects.filter(society=self.society, is_active=True)
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.add_input(Submit("submit", _("Create Handover")))

    def clean_outgoing_notes(self):
        return str(self.cleaned_data.get("outgoing_notes", "")).strip()

    def clean(self):
        # Set society on the instance BEFORE _post_clean() invokes
        # instance.full_clean(), so the model's cross-society checks
        # (e.g. outgoing_guard.society_id != self.society_id) see a non-null
        # society. Without this, validation always fails because society is
        # only stamped in save() — which runs after full_clean().
        if self.society is not None and hasattr(self.instance, "society_id"):
            self.instance.society = self.society
        cleaned = super().clean()
        outgoing = cleaned.get("outgoing_guard")
        incoming = cleaned.get("incoming_guard")
        if outgoing is not None and incoming is not None and outgoing.pk == incoming.pk:
            raise ValidationError({"incoming_guard": _("Incoming guard must differ from outgoing guard.")})
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.society is not None:
            instance.society = self.society
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class HandoverAcknowledgeForm(forms.Form):
    """Acknowledge a pending/disputed handover with optional notes."""

    notes = forms.CharField(
        label=_("Acknowledgement Notes"),
        required=False,
        widget=forms.Textarea(attrs={"class": _FORM_CONTROL_CLASS, "rows": 3}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.add_input(Submit("submit", _("Acknowledge Handover")))

    def clean_notes(self):
        return str(self.cleaned_data.get("notes", "")).strip()


class HandoverDisputeForm(forms.Form):
    """Dispute a pending handover with a mandatory reason."""

    reason = forms.CharField(
        label=_("Dispute Reason"),
        required=True,
        widget=forms.Textarea(attrs={"class": _FORM_CONTROL_CLASS, "rows": 3}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.add_input(Submit("submit", _("Submit Dispute")))

    def clean_reason(self):
        reason = str(self.cleaned_data.get("reason", "")).strip()
        if not reason:
            raise ValidationError(_("A dispute reason is required."))
        return reason


class CurrentlyInsideFilterForm(forms.Form):
    """GET-bound filter form for the 'Currently Inside' screen.

    Not a crispy save form — used purely to validate and apply filters to the
    inside-events queryset. All fields are optional.
    """

    gate = forms.IntegerField(required=False, widget=forms.HiddenInput())
    visitor_category = forms.IntegerField(required=False, widget=forms.HiddenInput())
    min_duration = forms.IntegerField(
        required=False, min_value=0, widget=forms.NumberInput(attrs={"class": _FORM_CONTROL_CLASS, "placeholder": _("min minutes")})
    )
    max_duration = forms.IntegerField(
        required=False, min_value=0, widget=forms.NumberInput(attrs={"class": _FORM_CONTROL_CLASS, "placeholder": _("max minutes")})
    )
    is_overstay = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={"class": _FORM_CHECK_CLASS}))
    search = forms.CharField(
        required=False, widget=forms.TextInput(attrs={"class": _FORM_CONTROL_CLASS, "placeholder": _("search name / phone / vehicle")})
    )

    def clean_search(self):
        return str(self.cleaned_data.get("search", "")).strip()


# --- Phase 13: Analytics -------------------------------------------------


class AnalyticsDateRangeForm(forms.Form):
    """GET-bound date-range selector for analytics views.

    Used by peak-hours, guard-performance, and rule-violations views.
    All fields are optional — views default to the last 7 days when
    nothing is supplied.
    """

    GRANULARITY_CHOICES = [
        ("daily", _("Daily")),
        ("weekly", _("Weekly")),
        ("monthly", _("Monthly")),
    ]

    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": _FORM_CONTROL_CLASS, "type": "date"}),
        label=_("From"),
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": _FORM_CONTROL_CLASS, "type": "date"}),
        label=_("To"),
    )
    granularity = forms.ChoiceField(
        choices=GRANULARITY_CHOICES,
        initial="daily",
        required=False,
        widget=forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
        label=_("Granularity"),
    )

    def __init__(self, *args, society=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = "get"
        self.helper.add_input(Submit("submit", _("Apply")))

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("date_from")
        end = cleaned.get("date_to")
        if start and end and start > end:
            raise ValidationError(_("'From' date must be before or equal to 'To' date."))
        return cleaned


class AnalyticsCustomReportForm(forms.Form):
    """Filter form for the custom analytics report.

    Provides dimension filters (gate, visitor category, event type,
    status) plus metric selection and grouping.  ``society`` is accepted
    to scope the gate / visitor-category querysets.
    """

    METRIC_CHOICES = [
        ("total_events", _("Total Events")),
        ("by_status", _("By Status")),
        ("by_visitor_category", _("By Visitor Category")),
        ("by_gate", _("By Gate")),
        ("by_event_type", _("By Event Type")),
        ("by_guard", _("By Guard")),
        ("by_hour", _("By Hour")),
        ("by_day", _("By Day")),
    ]

    GROUP_BY_CHOICES = [
        ("", _("None")),
        ("gate", _("Gate")),
        ("category", _("Visitor Category")),
        ("guard", _("Guard")),
        ("hour", _("Hour")),
        ("day", _("Day")),
        ("status", _("Status")),
    ]

    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": _FORM_CONTROL_CLASS, "type": "date"}),
        label=_("From"),
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": _FORM_CONTROL_CLASS, "type": "date"}),
        label=_("To"),
    )
    metrics = forms.MultipleChoiceField(
        choices=METRIC_CHOICES,
        required=False,
        widget=forms.SelectMultiple(attrs={"class": _FORM_SELECT_CLASS, "size": "8"}),
        label=_("Metrics"),
    )
    group_by = forms.ChoiceField(
        choices=GROUP_BY_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
        label=_("Group By"),
    )
    gate = forms.ModelChoiceField(
        queryset=None,
        required=False,
        widget=forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
        label=_("Gate"),
    )
    visitor_category = forms.ModelChoiceField(
        queryset=None,
        required=False,
        widget=forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
        label=_("Visitor Category"),
    )
    event_type = forms.ChoiceField(
        choices=[("", _("--- All ---"))] + GateEvent.EventType.choices,
        required=False,
        widget=forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
        label=_("Event Type"),
    )
    status = forms.ChoiceField(
        choices=[("", _("--- All ---"))] + GateEvent.Status.choices,
        required=False,
        widget=forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
        label=_("Status"),
    )

    def __init__(self, *args, society=None, **kwargs):
        super().__init__(*args, **kwargs)
        if society:
            self.fields["gate"].queryset = Gate.objects.filter(
                society=society, is_active=True
            )
            self.fields["visitor_category"].queryset = VisitorCategory.objects.filter(
                society=society, is_active=True
            )
        self.helper = FormHelper()
        self.helper.form_method = "get"
        self.helper.add_input(Submit("submit", _("Generate Report")))

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("date_from")
        end = cleaned.get("date_to")
        if start and end and start > end:
            raise ValidationError(_("'From' date must be before or equal to 'To' date."))
        return cleaned


class AnalyticsExportForm(forms.Form):
    """POST form for CSV export of analytics data."""

    EXPORT_CHOICES = [
        ("events", _("Gate Events")),
        ("guard_performance", _("Guard Performance")),
        ("rule_violations", _("Rule Violations")),
        ("anomalies", _("Anomalies")),
    ]

    FORMAT_CHOICES = [
        ("csv", _("CSV")),
    ]

    date_from = forms.DateField(
        widget=forms.DateInput(attrs={"class": _FORM_CONTROL_CLASS, "type": "date"}),
        label=_("From"),
    )
    date_to = forms.DateField(
        widget=forms.DateInput(attrs={"class": _FORM_CONTROL_CLASS, "type": "date"}),
        label=_("To"),
    )
    export_type = forms.ChoiceField(
        choices=EXPORT_CHOICES,
        initial="events",
        widget=forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
        label=_("Export Type"),
    )
    format = forms.ChoiceField(
        choices=FORMAT_CHOICES,
        initial="csv",
        widget=forms.Select(attrs={"class": _FORM_SELECT_CLASS}),
        label=_("Format"),
    )

    def __init__(self, *args, society=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = "post"

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("date_from")
        end = cleaned.get("date_to")
        if start and end and start > end:
            raise ValidationError(_("'From' date must be before or equal to 'To' date."))
        return cleaned
