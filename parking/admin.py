from django import forms
from django.contrib import admin
from django.utils.html import format_html

from .models import ParkingSlot
from .models import ParkingSlotOwnershipHistory
from .models import ParkingPermit
from .models import ParkingRotationAllocation
from .models import ParkingRotationApplication
from .models import ParkingRotationCycle
from .models import ParkingRotationPolicy
from .models import ParkingRotationPolicyAudit
from .models import ParkingRotationQueue
from .models import ParkingVehicleLimit
from .models import Vehicle


@admin.register(ParkingSlot)
class ParkingSlotAdmin(admin.ModelAdmin):
    list_display = ("slot_number", "society", "parking_model", "owned_unit", "slot_type", "is_active")
    list_filter = ("society", "parking_model", "slot_type", "is_active")
    search_fields = ("slot_number",)


class VehicleAdminForm(forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance.pk:
            society = self.instance.society
            self.fields["unit"].queryset = self.fields["unit"].queryset.filter(
                structure__society=society
            )
            self.fields["member"].queryset = self.fields["member"].queryset.filter(
                society=society
            )
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
    list_display = (
        "vehicle_number",
        "vehicle_type",
        "society",
        "unit",
        "is_active",
        "rule_status",
    )
    list_filter = ("vehicle_type", "society", "is_active", "rule_status")
    search_fields = ("vehicle_number",)


@admin.register(ParkingVehicleLimit)
class ParkingVehicleLimitAdmin(admin.ModelAdmin):
    list_display = (
        "society",
        "member_role",
        "vehicle_type",
        "max_allowed",
        "start_date",
        "end_date",
        "status_badge",
        "created_by",
    )
    list_filter = ("society", "member_role", "vehicle_type", "status")
    search_fields = ("society__name",)
    readonly_fields = ("created_at",)

    def status_badge(self, obj):
        css = "success" if obj.status == ParkingVehicleLimit.Status.ACTIVE else "secondary"
        return format_html('<span class="badge bg-{}">{}</span>', css, obj.get_status_display())

    status_badge.short_description = "Status"

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        if obj is not None:
            return False
        return super().has_change_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if change:
            return
        ParkingVehicleLimit.create_new_version(
            society=obj.society,
            member_role=obj.member_role,
            vehicle_type=obj.vehicle_type,
            max_allowed=obj.max_allowed,
            changed_reason=obj.changed_reason,
            created_by=request.user if request.user.is_authenticated else None,
        )


@admin.register(ParkingPermit)
class ParkingPermitAdmin(admin.ModelAdmin):
    list_display = (
        "society",
        "slot",
        "unit",
        "vehicle",
        "permit_type",
        "status",
        "issued_at",
        "expires_at",
    )
    list_filter = ("society", "permit_type", "status")
    search_fields = ("vehicle__vehicle_number", "slot__slot_number", "unit__identifier")
    readonly_fields = ("issued_at", "qr_token")


@admin.register(ParkingSlotOwnershipHistory)
class ParkingSlotOwnershipHistoryAdmin(admin.ModelAdmin):
    list_display = ("slot", "unit", "start_date", "end_date", "reason")
    list_filter = ("slot__society",)
    search_fields = ("slot__slot_number", "unit__identifier", "reason")


@admin.register(ParkingRotationPolicy)
class ParkingRotationPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "society",
        "policy_name",
        "rotation_method",
        "rotation_period_months",
        "effective_from",
        "effective_to",
        "is_active",
        "created_by",
    )
    list_filter = ("society", "rotation_method", "priority_rule", "is_active")
    search_fields = ("society__name", "policy_name")
    readonly_fields = ("created_at", "updated_at")

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        if obj is not None:
            return False
        return super().has_change_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if change:
            return
        ParkingRotationPolicy.create_new_version(
            society=obj.society,
            policy_name=obj.policy_name,
            rotation_period_months=obj.rotation_period_months,
            rotation_method=obj.rotation_method,
            vehicle_required_before_apply=obj.vehicle_required_before_apply,
            allow_sold_parking_owner=obj.allow_sold_parking_owner,
            allow_tenant_application=obj.allow_tenant_application,
            max_rotational_slots_per_unit=obj.max_rotational_slots_per_unit,
            max_total_parking_per_unit=obj.max_total_parking_per_unit,
            skip_units_with_outstanding_dues=obj.skip_units_with_outstanding_dues,
            skip_units_with_parking_violation=obj.skip_units_with_parking_violation,
            unused_parking_reassignment_days=obj.unused_parking_reassignment_days,
            application_window_days=obj.application_window_days,
            priority_rule=obj.priority_rule,
            effective_from=obj.effective_from,
            changed_by=request.user if request.user.is_authenticated else None,
            change_reason=getattr(obj, "change_reason", ""),
        )


@admin.register(ParkingRotationPolicyAudit)
class ParkingRotationPolicyAuditAdmin(admin.ModelAdmin):
    list_display = ("policy", "changed_by", "changed_at")
    list_filter = ("policy__society", "changed_by")
    search_fields = ("policy__policy_name", "change_reason")
    readonly_fields = ("policy", "old_values", "new_values", "changed_by", "change_reason", "changed_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ParkingRotationCycle)
class ParkingRotationCycleAdmin(admin.ModelAdmin):
    list_display = (
        "society",
        "policy",
        "cycle_number",
        "cycle_start_date",
        "cycle_end_date",
        "allocation_status",
        "total_rotational_spots",
    )
    list_filter = ("society", "allocation_status")
    search_fields = ("society__name", "policy__policy_name")
    readonly_fields = ("created_at",)


@admin.register(ParkingRotationQueue)
class ParkingRotationQueueAdmin(admin.ModelAdmin):
    list_display = ("society", "unit", "queue_position", "last_allocated_cycle", "created_at")
    list_filter = ("society",)
    search_fields = ("society__name", "unit__identifier")
    readonly_fields = ("created_at",)


@admin.register(ParkingRotationApplication)
class ParkingRotationApplicationAdmin(admin.ModelAdmin):
    list_display = ("cycle", "unit", "vehicle", "application_status", "applied_at")
    list_filter = ("cycle__society", "application_status")
    search_fields = ("unit__identifier", "vehicle__vehicle_number")
    readonly_fields = ("applied_at",)


@admin.register(ParkingRotationAllocation)
class ParkingRotationAllocationAdmin(admin.ModelAdmin):
    list_display = ("cycle", "unit", "parking_spot", "vehicle", "allocation_method", "assigned_at", "expires_at")
    list_filter = ("cycle__society", "allocation_method")
    search_fields = ("unit__identifier", "parking_spot__slot_number", "vehicle__vehicle_number")
    readonly_fields = ("assigned_at",)
