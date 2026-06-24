from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from django.utils.translation import gettext_lazy as _

from housing.forms import BootstrapForm
from housing.forms import BootstrapModelForm
from members.models import Member
from members.models import Structure
from members.models import Unit
from parking.models import ParkingSlot
from parking.models import ParkingRotationPolicy
from societies.models import Society
from parking.models import ParkingVehicleLimit
from parking.models import Vehicle


class StructuredUnitChoiceField(forms.ModelChoiceField):
    """
    Custom field that displays units grouped by structure.
    Uses optgroups to organize units hierarchically.
    
    OPTIMIZATION: No society name in display (society already in form column)
    Display format: "Structure Name - Unit Identifier"
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.widget = StructuredUnitSelect()
    
    @property 
    def queryset(self):
        """Get queryset from field"""
        return self._queryset
    
    @queryset.setter
    def queryset(self, value):
        """When queryset changes, update both field and widget"""
        self._queryset = value
        self.widget._queryset = value
    
    def label_from_instance(self, obj):
        """Display unit without society name (since society is in separate form field)"""
        return f"{obj.structure.name} - {obj.identifier}"


class StructuredUnitSelect(forms.Select):
    """
    Custom widget that renders units grouped by structure using optgroups.
    Gets queryset from the field that uses this widget.
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._queryset = None
    
    def optgroups(self, name, value, attrs=None):
        """
        Override to group options by structure.
        The queryset is set by the field when it's updated.
        """
        # If we don't have a queryset, use parent implementation
        if not self._queryset:
            return super().optgroups(name, value, attrs)
        
        queryset = self._queryset
        
        # Group units by structure
        structure_units = {}
        for unit in queryset:
            struct_name = unit.structure.name
            if struct_name not in structure_units:
                structure_units[struct_name] = []
            structure_units[struct_name].append(unit)
        
        # Build optgroups
        optgroups = []
        option_index = 0
        for struct_name in sorted(structure_units.keys()):
            units_in_struct = structure_units[struct_name]
            options = []
            for unit in sorted(units_in_struct, key=lambda u: u.identifier):
                selected = str(unit.pk) == str(value) if value else False
                option = {
                    'name': name,
                    'value': unit.pk,
                    'label': f"{unit.identifier} ({unit.get_unit_type_display()})",
                    'selected': selected,
                    'index': str(option_index),
                    'attrs': {'class': 'unit-option'} if not selected else {'class': 'unit-option', 'selected': True},
                    'type': 'select',
                    'template_name': 'django/forms/widgets/select_option.html',
                    'wrap_label': True,
                }
                options.append(option)
                option_index += 1
            
            optgroups.append((struct_name, options, 0))
        
        return optgroups


class BulkParkingSlotCreateForm(BootstrapForm):
    society = forms.ModelChoiceField(
        queryset=Society.objects.none(),
        label=_("Society"),
    )
    count = forms.IntegerField(
        min_value=1,
        max_value=1000,
        initial=100,
        label=_("Number of slots"),
        help_text=_("Used when custom slot names are blank."),
    )
    prefix = forms.CharField(
        required=False,
        max_length=30,
        initial="P-",
        label=_("Prefix"),
        help_text=_("Example: P-, B1-, VIS-."),
    )
    starting_number = forms.IntegerField(
        min_value=0,
        max_value=999999,
        initial=1,
        label=_("Starting number"),
    )
    padding = forms.IntegerField(
        min_value=0,
        max_value=10,
        initial=3,
        label=_("Number padding"),
        help_text=_("3 creates P-001, P-002. Use 0 for no padding."),
    )
    custom_slot_names = forms.CharField(
        required=False,
        label=_("Custom slot names"),
        widget=forms.Textarea(attrs={"rows": 8}),
        help_text=_("Optional. Enter one slot name per line or comma-separated. These override generated names."),
    )
    parking_model = forms.ChoiceField(
        choices=ParkingSlot.ParkingModel.choices,
        initial=ParkingSlot.ParkingModel.COMMON,
        label=_("Parking model"),
    )
    slot_type = forms.ChoiceField(
        choices=ParkingSlot.SlotType.choices,
        initial=ParkingSlot.SlotType.OPEN,
        label=_("Slot type"),
    )
    is_active = forms.BooleanField(required=False, initial=True, label=_("Active"))
    is_rotational = forms.BooleanField(required=False, initial=False, label=_("Rotational"))
    is_transferable = forms.BooleanField(required=False, initial=True, label=_("Transferable"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["society"].queryset = Society.objects.order_by("name")

    def clean_custom_slot_names(self):
        value = self.cleaned_data.get("custom_slot_names") or ""
        names = []
        for raw_chunk in value.replace(",", "\n").splitlines():
            name = raw_chunk.strip()
            if name:
                names.append(name)
        return names

    def clean(self):
        cleaned = super().clean()
        society = cleaned.get("society")
        custom_names = cleaned.get("custom_slot_names") or []
        count = cleaned.get("count") or 0
        prefix = (cleaned.get("prefix") or "").strip()
        starting_number = cleaned.get("starting_number")
        padding = cleaned.get("padding") or 0
        parking_model = cleaned.get("parking_model")

        if parking_model == ParkingSlot.ParkingModel.SOLD:
            self.add_error(
                "parking_model",
                _("Sold parking slots require owned unit mapping. Use single-slot creation for sold slots."),
            )

        if custom_names:
            slot_numbers = custom_names
        else:
            if starting_number is None:
                return cleaned
            slot_numbers = [
                f"{prefix}{str(starting_number + offset).zfill(padding)}"
                for offset in range(count)
            ]

        normalized = []
        seen = set()
        for slot_number in slot_numbers:
            if len(slot_number) > 50:
                self.add_error(
                    "custom_slot_names",
                    _("Slot name '%(slot)s' exceeds 50 characters.") % {"slot": slot_number},
                )
                continue
            if slot_number in seen:
                self.add_error(
                    "custom_slot_names",
                    _("Duplicate slot name in request: %(slot)s") % {"slot": slot_number},
                )
                continue
            normalized.append(slot_number)
            seen.add(slot_number)

        if society and seen:
            existing = set(
                ParkingSlot.objects.filter(
                    society=society,
                    slot_number__in=seen,
                ).values_list("slot_number", flat=True)
            )
            if existing:
                self.add_error(
                    "custom_slot_names",
                    _("These parking slots already exist: %(slots)s")
                    % {"slots": ", ".join(sorted(existing))},
                )

        cleaned["slot_numbers"] = normalized
        return cleaned


class ParkingSlotForm(BootstrapModelForm):
    class Meta:
        model = ParkingSlot
        fields = [
            "society",
            "slot_number",
            "parking_model",
            "slot_type",
            "owned_unit",
            "is_active",
            "is_rotational",
            "is_transferable",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owned_unit"].queryset = Unit.objects.none()

        society = None
        if self.is_bound:
            society = self.data.get("society")
        else:
            initial_society = self.initial.get("society")
            society = getattr(initial_society, "id", initial_society)

        if society:
            self.fields["owned_unit"].queryset = Unit.objects.filter(
                structure__society_id=society,
            ).order_by("identifier")


class VehicleForm(BootstrapModelForm):
    # Structure selector for hierarchical unit selection
    structure = forms.ModelChoiceField(
        queryset=Structure.objects.none(),
        required=False,
        empty_label=_("Select building / wing"),
        label=_("Building / Wing"),
        help_text=_("Filter flats by building for faster selection."),
    )
    
    # OPTIMIZATION: Use custom field that groups units by structure
    unit = StructuredUnitChoiceField(
        queryset=Unit.objects.none(),
        required=False,
        label=_("Flat / Unit"),
        help_text=_("Choose the flat where this vehicle is registered."),
    )
    
    class Meta:
        model = Vehicle
        fields = [
            "society",
            "unit",
            "member",
            "vehicle_number",
            "vehicle_type",
            "color",
            "is_active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["member"].queryset = Member.objects.none()
        self.fields["society"].empty_label = _("Select society")
        self.fields["unit"].empty_label = _("Select flat / unit")
        self.fields["member"].empty_label = _("Select member")
        self.fields["vehicle_type"].empty_label = _("Select vehicle type")
        self.fields["vehicle_number"].widget.attrs.update({
            "placeholder": _("e.g. MH 01 AB 1234"),
            "autocomplete": "off",
            "inputmode": "text",
        })
        self.fields["color"].widget.attrs.update({"placeholder": _("e.g. White")})
        
        # Reorder fields: society -> structure -> unit -> member -> other fields
        field_order = ["society", "structure", "unit", "member", "vehicle_number", "vehicle_type", "color", "is_active"]
        self.fields = {key: self.fields[key] for key in field_order if key in self.fields}

        def normalize_pk(value):
            if value in (None, ""):
                return None
            if isinstance(value, int):
                return value
            if isinstance(value, str):
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None
            return getattr(value, "id", None)

        society = None
        structure = None
        unit = None
        if self.is_bound:
            society = normalize_pk(self.data.get("society"))
            structure = normalize_pk(self.data.get("structure"))
            unit = normalize_pk(self.data.get("unit"))
        else:
            society = normalize_pk(self.initial.get("society"))
            structure = normalize_pk(self.initial.get("structure"))
            unit = normalize_pk(self.initial.get("unit"))

        if society:
            # Load structures for the selected society
            self.fields["structure"].queryset = Structure.objects.filter(
                society_id=society,
            ).only("id", "name", "display_order").order_by("display_order", "id")
            
            if structure:
                # Load units for the selected structure
                self.fields["unit"].queryset = Unit.objects.filter(
                    structure_id=structure,
                ).select_related("structure").only(
                    "id", "identifier", "structure_id", "structure__name", "unit_type"
                ).order_by("identifier")
            else:
                # If no structure selected, show all units for society grouped by structure
                self.fields["unit"].queryset = Unit.objects.filter(
                    structure__society_id=society,
                ).select_related("structure").only(
                    "id", "identifier", "structure_id", "structure__name", "unit_type"
                ).order_by("structure__display_order", "structure__id", "identifier")
            
            if unit:
                # OPTIMIZATION: Use only() to reduce data transfer
                self.fields["member"].queryset = Member.objects.filter(
                    society_id=society,
                    unit_id=unit,
                ).only("id", "full_name", "society_id", "unit_id").order_by("full_name")

        self.fields["member"].required = False
        self.fields["member"].help_text = _("Primary resident using this vehicle. Optional if not assigned yet.")

    def clean(self):
        cleaned = super().clean()
        society = cleaned.get("society")
        structure = cleaned.get("structure")
        unit = cleaned.get("unit")
        member = cleaned.get("member")
        
        if society and structure and structure.society_id != society.id:
            self.add_error("structure", "Selected structure must belong to selected society.")
        
        if society and unit and unit.structure.society_id != society.id:
            self.add_error("unit", "Selected unit must belong to selected society.")
        
        if structure and unit and unit.structure_id != structure.id:
            self.add_error("unit", "Selected unit must belong to selected structure.")
        
        if society and member and member.society_id != society.id:
            self.add_error("member", "Selected member must belong to selected society.")
        
        if unit and member and member.unit_id != unit.id:
            self.add_error("member", "Selected member must belong to selected unit.")
        
        return cleaned


class ParkingVehicleLimitForm(BootstrapModelForm):
    class Meta:
        model = ParkingVehicleLimit
        fields = [
            "society",
            "member_role",
            "vehicle_type",
            "max_allowed",
            "changed_reason",
        ]


class ParkingRotationPolicyForm(BootstrapModelForm):
    class Meta:
        model = ParkingRotationPolicy
        fields = [
            "society",
            "policy_name",
            "rotation_period_months",
            "rotation_method",
            "vehicle_required_before_apply",
            "allow_sold_parking_owner",
            "allow_tenant_application",
            "max_rotational_slots_per_unit",
            "max_total_parking_per_unit",
            "skip_units_with_outstanding_dues",
            "skip_units_with_parking_violation",
            "unused_parking_reassignment_days",
            "application_window_days",
            "priority_rule",
            "effective_from",
        ]
