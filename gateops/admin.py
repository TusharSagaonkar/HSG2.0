"""Admin registrations for the ``gateops`` app.

Registers all 15 Phase-1 foundation models with list displays, filters, and
search fields. Follows the ``@admin.register(Model)`` decorator style used in
``accounting/admin.py``. For models with a ``society`` FK, ``society`` is added
to ``list_filter``; for models with ``is_active``, ``is_active`` is added too.
"""

from django.contrib import admin

from gateops.models import (
    AnalyticsSnapshot,
    ApprovalType,
    Contract,
    Contractor,
    Gate,
    GateOpsAuditLog,
    GateOpsRole,
    GateOpsSocietyConfig,
    GateVehicle,
    GuardShift,
    GuardShiftAssignment,
    HolidayCalendar,
    MasterSettings,
    MaterialCategory,
    MaterialMovement,
    NotificationBundle,
    NotificationPreference,
    Parcel,
    Pass,
    PassType,
    Rule,
    RuleAction,
    RuleCondition,
    RuleEvaluation,
    SecurityGuard,
    VehicleCategory,
    VisitorCategory,
    Worker,
    WorkPermit,
)


@admin.register(GateOpsSocietyConfig)
class GateOpsSocietyConfigAdmin(admin.ModelAdmin):
    list_display = (
        "society",
        "default_approval_timeout_minutes",
        "photo_required",
        "otp_length",
        "offline_sync_window_hours",
        "auto_close_enabled",
    )
    list_filter = ("auto_close_enabled", "photo_required", "require_id_verification")
    search_fields = ("society__name",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Gate)
class GateAdmin(admin.ModelAdmin):
    list_display = ("society", "name", "code", "gate_type", "is_active", "created_at")
    list_filter = ("society", "gate_type", "is_active")
    search_fields = ("name", "code", "society__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(SecurityGuard)
class SecurityGuardAdmin(admin.ModelAdmin):
    list_display = (
        "society",
        "name",
        "badge_number",
        "agency_name",
        "is_active",
        "created_at",
    )
    list_filter = ("society", "is_active", "agency_name")
    search_fields = ("name", "badge_number", "phone", "agency_name", "society__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(GuardShift)
class GuardShiftAdmin(admin.ModelAdmin):
    list_display = ("society", "name", "start_time", "end_time", "is_active")
    list_filter = ("society", "is_active")
    search_fields = ("name", "society__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(GuardShiftAssignment)
class GuardShiftAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        "society",
        "guard",
        "shift",
        "gate",
        "date",
        "check_in_at",
        "check_out_at",
    )
    list_filter = ("society", "date", "shift", "gate")
    search_fields = ("guard__name", "society__name", "handover_notes")
    readonly_fields = ("created_at", "updated_at")


@admin.register(VisitorCategory)
class VisitorCategoryAdmin(admin.ModelAdmin):
    list_display = (
        "society",
        "name",
        "code",
        "is_delivery",
        "is_domestic_help",
        "is_contractor",
        "is_emergency",
        "is_resident",
        "requires_approval_default",
        "sort_order",
        "is_active",
    )
    list_filter = (
        "society",
        "is_active",
        "is_delivery",
        "is_domestic_help",
        "is_contractor",
        "is_emergency",
        "is_resident",
        "requires_approval_default",
    )
    search_fields = ("name", "code", "society__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(VehicleCategory)
class VehicleCategoryAdmin(admin.ModelAdmin):
    list_display = (
        "society",
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
    list_filter = (
        "society",
        "is_active",
        "is_commercial",
        "is_delivery",
        "is_emergency",
        "is_electric",
        "is_oversized",
    )
    search_fields = ("name", "code", "society__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(MaterialCategory)
class MaterialCategoryAdmin(admin.ModelAdmin):
    list_display = (
        "society",
        "name",
        "code",
        "is_inbound_default",
        "requires_approval_default",
        "sort_order",
        "is_active",
    )
    list_filter = ("society", "is_active", "is_inbound_default", "requires_approval_default")
    search_fields = ("name", "code", "society__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(PassType)
class PassTypeAdmin(admin.ModelAdmin):
    list_display = (
        "society",
        "name",
        "code",
        "validation_method",
        "duration_type",
        "default_validity_hours",
        "is_active",
    )
    list_filter = ("society", "is_active", "validation_method", "duration_type")
    search_fields = ("name", "code", "society__name")
    readonly_fields = ("created_at", "updated_at")


# --- Phase 5: Pass Management ----------------------------------------------
@admin.register(Pass)
class PassAdmin(admin.ModelAdmin):
    list_display = (
        "society",
        "code",
        "person",
        "pass_type",
        "status",
        "valid_from",
        "valid_until",
        "usage_count",
        "max_usage",
        "is_active",
    )
    list_filter = ("society", "status", "is_active", "pass_type__validation_method")
    search_fields = ("code", "person__name", "person__phone", "pass_type__name")
    readonly_fields = ("code", "usage_count", "created_at", "updated_at")
    date_hierarchy = "valid_from"
    ordering = ("-created_at",)


# --- Phase 6: Vehicle Module -----------------------------------------------
@admin.register(GateVehicle)
class GateVehicleAdmin(admin.ModelAdmin):
    list_display = ("society", "vehicle_number", "person", "vehicle_category",
                    "is_watchlisted", "is_repeat", "last_seen_at", "is_active")
    list_filter = ("society", "is_watchlisted", "is_repeat", "is_active",
                    "vehicle_category__code")
    search_fields = ("vehicle_number", "person__name", "person__phone",
                     "vehicle_category__name", "vehicle_category__code")
    readonly_fields = ("first_seen_at", "created_at", "updated_at")
    date_hierarchy = "last_seen_at"
    ordering = ("-last_seen_at",)
    list_editable = ("is_watchlisted",)  # allow quick toggling from list view


# --- Phase 7: Material Movement --------------------------------------------
@admin.register(MaterialMovement)
class MaterialMovementAdmin(admin.ModelAdmin):
    list_display = ("society", "gate_event", "material_category", "quantity", "unit",
                    "owner", "status", "expected_return_at", "returned_at", "is_active")
    list_filter = ("society", "status", "is_active", "material_category__code")
    search_fields = ("owner", "purpose", "material_category__name",
                     "material_category__code", "gate_event__event_uuid")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)


# --- Phase 8: Parcel Management ---------------------------------------------
@admin.register(Parcel)
class ParcelAdmin(admin.ModelAdmin):
    list_display = ("society", "gate_event", "tracking_number", "courier",
                    "status", "is_cold_storage", "is_fragile", "is_cod",
                    "cod_amount", "stored_at", "collected_at", "is_active")
    list_filter = ("society", "status", "is_active", "is_cold_storage",
                   "is_fragile", "is_cod")
    search_fields = ("tracking_number", "courier", "gate_event__event_uuid")
    readonly_fields = ("created_at", "updated_at", "otp_code")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)


# --- Phase 9: Contractor Management ----------------------------------------
@admin.register(Contractor)
class ContractorAdmin(admin.ModelAdmin):
    list_display = ("society", "company_name", "supervisor_name",
                    "supervisor_phone", "contact_person", "contact_phone",
                    "gst_number", "is_active", "created_at")
    list_filter = ("society", "is_active")
    search_fields = ("company_name", "supervisor_name", "contact_person",
                     "supervisor_phone", "contact_phone", "gst_number",
                     "pan_number", "society__name")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = ("society", "contractor", "title", "start_date",
                    "end_date", "max_workers", "status", "is_active",
                    "created_at")
    list_filter = ("society", "is_active", "status")
    search_fields = ("title", "description", "contractor__company_name",
                     "society__name")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)


@admin.register(Worker)
class WorkerAdmin(admin.ModelAdmin):
    list_display = ("society", "contract", "person", "designation",
                    "id_type", "id_number", "is_active", "created_at")
    list_filter = ("society", "is_active", "id_type")
    search_fields = ("designation", "id_number", "person__name",
                     "person__phone", "contract__title",
                     "contract__contractor__company_name", "society__name")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)


@admin.register(WorkPermit)
class WorkPermitAdmin(admin.ModelAdmin):
    list_display = ("society", "contract", "permit_number", "issued_at",
                    "expires_at", "hazard_level", "status",
                    "safety_docs_verified", "safety_briefing_given",
                    "work_area", "is_active", "created_at")
    list_filter = ("society", "is_active", "status", "hazard_level",
                   "safety_docs_verified", "safety_briefing_given")
    search_fields = ("permit_number", "work_area", "notes",
                     "contract__title", "contract__contractor__company_name",
                     "society__name")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "issued_at"
    ordering = ("-created_at",)


@admin.register(ApprovalType)
class ApprovalTypeAdmin(admin.ModelAdmin):
    list_display = (
        "society",
        "name",
        "code",
        "approver",
        "escalation_timeout_minutes",
        "is_active",
    )
    list_filter = ("society", "is_active", "approver")
    search_fields = ("name", "code", "society__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = (
        "society",
        "visitor_category",
        "channel",
        "trigger",
        "is_silent",
        "bundle_window_minutes",
        "is_active",
    )
    list_filter = ("society", "is_active", "channel", "trigger", "is_silent")
    search_fields = ("society__name", "visitor_category__name")
    readonly_fields = ("created_at", "updated_at")


# --- Phase 10: Smart Notification Engine ------------------------------------
@admin.register(NotificationBundle)
class NotificationBundleAdmin(admin.ModelAdmin):
    list_display = (
        "society",
        "visitor_category",
        "host_unit",
        "trigger",
        "channel",
        "status",
        "recipient_email",
        "dispatched_at",
        "is_active",
    )
    list_filter = ("society", "is_active", "status", "channel", "trigger")
    search_fields = ("society__name", "recipient_email", "visitor_category__name")
    readonly_fields = ("created_at", "updated_at", "dispatched_at")
    ordering = ("-created_at",)


@admin.register(GateOpsRole)
class GateOpsRoleAdmin(admin.ModelAdmin):
    list_display = ("society", "name", "code", "is_active", "created_at")
    list_filter = ("society", "is_active", "code")
    search_fields = ("name", "code", "society__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(GateOpsAuditLog)
class GateOpsAuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "society",
        "action",
        "entity_type",
        "entity_id",
        "actor",
        "created_at",
    )
    list_filter = ("society", "action", "entity_type")
    search_fields = ("entity_type", "entity_id", "society__name")
    readonly_fields = (
        "society",
        "actor",
        "action",
        "entity_type",
        "entity_id",
        "before_value",
        "after_value",
        "ip_address",
        "device_info",
        "gps_lat",
        "gps_lng",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(HolidayCalendar)
class HolidayCalendarAdmin(admin.ModelAdmin):
    list_display = (
        "society",
        "name",
        "date",
        "is_recurring_annually",
        "affects",
    )
    list_filter = ("society", "affects", "is_recurring_annually")
    search_fields = ("name", "society__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(MasterSettings)
class MasterSettingsAdmin(admin.ModelAdmin):
    list_display = ("society", "updated_by", "updated_at")
    list_filter = ("society",)
    search_fields = ("society__name",)
    readonly_fields = ("created_at", "updated_at")


# ---------------------------------------------------------------------------
# Phase 2: Rule Engine
# ---------------------------------------------------------------------------


class RuleConditionInline(admin.TabularInline):
    model = RuleCondition
    extra = 1
    ordering = ("sort_order",)
    readonly_fields = ("created_at",)


class RuleActionInline(admin.TabularInline):
    model = RuleAction
    extra = 1
    ordering = ("execution_order",)
    readonly_fields = ("created_at",)


@admin.register(Rule)
class RuleAdmin(admin.ModelAdmin):
    list_display = (
        "society",
        "code",
        "name",
        "priority",
        "applies_on",
        "is_active",
        "valid_from",
        "valid_until",
        "created_at",
    )
    list_filter = (
        "society",
        "is_active",
        "applies_on",
        "visitor_category",
        "vehicle_category",
        "material_category",
        "gate",
    )
    search_fields = ("name", "code", "description", "society__name")
    readonly_fields = ("created_at", "updated_at")
    inlines = [RuleConditionInline, RuleActionInline]


@admin.register(RuleCondition)
class RuleConditionAdmin(admin.ModelAdmin):
    list_display = (
        "rule",
        "field",
        "operator",
        "logical_connector",
        "sort_order",
        "created_at",
    )
    list_filter = ("field", "operator", "logical_connector")
    search_fields = ("rule__code", "rule__name")
    readonly_fields = ("created_at",)


@admin.register(RuleAction)
class RuleActionAdmin(admin.ModelAdmin):
    list_display = (
        "rule",
        "action",
        "execution_order",
        "created_at",
    )
    list_filter = ("action",)
    search_fields = ("rule__code", "rule__name")
    readonly_fields = ("created_at",)


@admin.register(RuleEvaluation)
class RuleEvaluationAdmin(admin.ModelAdmin):
    list_display = (
        "society",
        "rule",
        "action_taken",
        "execution_time_ms",
        "evaluated_at",
    )
    list_filter = ("society", "action_taken")
    search_fields = ("society__name", "rule__code", "error_message")
    readonly_fields = (
        "society",
        "rule",
        "evaluated_at",
        "input_context",
        "matched_conditions",
        "action_taken",
        "execution_time_ms",
        "created_by",
        "error_message",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# Phase 13: Analytics
# ---------------------------------------------------------------------------


@admin.register(AnalyticsSnapshot)
class AnalyticsSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "society",
        "date",
        "snapshot_type",
        "generated_at",
        "is_active",
    )
    list_filter = ("snapshot_type", "is_active", "date", "generated_at")
    search_fields = ("society__name",)
    readonly_fields = ("metrics", "generated_at")
    date_hierarchy = "date"
    ordering = ("-date", "-generated_at")
