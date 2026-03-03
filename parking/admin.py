from django.contrib import admin
from .models import ParkingSlot


@admin.register(ParkingSlot)
class ParkingSlotAdmin(admin.ModelAdmin):
    list_display = ("slot_number", "society", "slot_type", "is_active")
    list_filter = ("society", "slot_type", "is_active")
    search_fields = ("slot_number",)

from django import forms
from django.contrib import admin
from .models import Vehicle


class VehicleAdminForm(forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # If instance exists (edit mode)
        if self.instance.pk:
            society = self.instance.society
            self.fields["unit"].queryset = self.fields["unit"].queryset.filter(
                structure__society=society
            )
            self.fields["member"].queryset = self.fields["member"].queryset.filter(
                society=society
            )

        # If creating new object (based on POST data)
        elif "society" in self.data:
            try:
                society_id = int(self.data.get("society"))
                self.fields["unit"].queryset = self.fields["unit"].queryset.filter(
                    structure__society_id=society_id
                )
                self.fields["member"].queryset = self.fields["member"].queryset.filter(
                    society_id=society_id
                )
            except (ValueError, TypeError):
                pass


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    form = VehicleAdminForm
    list_display = ("vehicle_number", "vehicle_type", "society", "unit", "is_active")
    list_filter = ("vehicle_type", "society", "is_active")
    search_fields = ("vehicle_number",)


from .models import ParkingVehicleLimit


@admin.register(ParkingVehicleLimit)
class ParkingVehicleLimitAdmin(admin.ModelAdmin):
    list_display = ("society", "member_role", "vehicle_type", "max_allowed")
    list_filter = ("society", "member_role", "vehicle_type")