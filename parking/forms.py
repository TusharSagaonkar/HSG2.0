from housing.forms import BootstrapModelForm
from members.models import Member
from members.models import Unit
from parking.models import ParkingSlot
from parking.models import ParkingVehicleLimit
from parking.models import Vehicle


class ParkingSlotForm(BootstrapModelForm):
    class Meta:
        model = ParkingSlot
        fields = [
            "society",
            "slot_number",
            "slot_type",
            "is_active",
            "is_rotational",
            "is_transferable",
        ]


class VehicleForm(BootstrapModelForm):
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
        self.fields["unit"].queryset = Unit.objects.none()
        self.fields["member"].queryset = Member.objects.none()

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
        unit = None
        if self.is_bound:
            society = normalize_pk(self.data.get("society"))
            unit = normalize_pk(self.data.get("unit"))
        else:
            society = normalize_pk(self.initial.get("society"))
            unit = normalize_pk(self.initial.get("unit"))

        if society:
            self.fields["unit"].queryset = Unit.objects.filter(
                structure__society_id=society,
            ).order_by("identifier")
            if unit:
                self.fields["member"].queryset = Member.objects.filter(
                    society_id=society,
                    unit_id=unit,
                ).order_by("full_name")

        self.fields["member"].required = False
        self.fields["member"].help_text = "Optional: primary user of vehicle."

    def clean(self):
        cleaned = super().clean()
        society = cleaned.get("society")
        unit = cleaned.get("unit")
        member = cleaned.get("member")
        if society and unit and unit.structure.society_id != society.id:
            self.add_error("unit", "Selected unit must belong to selected society.")
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
        ]
