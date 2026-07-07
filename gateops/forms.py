"""Practical Django forms for the server-rendered gate operations test console."""

from __future__ import annotations

import json

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from gateops.models import (
    ApprovalType,
    Gate,
    GateEvent,
    GateEventApproval,
    GateOpsRole,
    GateOpsSocietyConfig,
    HolidayCalendar,
    MasterSettings,
    MaterialCategory,
    NotificationPreference,
    PassType,
    Person,
    Rule,
    RuleAction,
    RuleCondition,
    VehicleCategory,
    VisitorCategory,
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
