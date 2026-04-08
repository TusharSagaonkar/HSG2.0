from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Prefetch

from housing.forms import BootstrapModelForm
from members.models import Member
from members.models import Structure
from members.models import Unit
from parking.models import ParkingSlot
from parking.models import ParkingRotationPolicy
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
        empty_label="-- Select Structure --",
        help_text="Optional: Select a structure to filter units"
    )
    
    # OPTIMIZATION: Use custom field that groups units by structure
    unit = StructuredUnitChoiceField(
        queryset=Unit.objects.none(),
        required=False,
        help_text="Select a unit from the structure"
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
        self.fields["member"].help_text = "Optional: primary user of vehicle."

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
