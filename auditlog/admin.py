from django.contrib import admin

from auditlog.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "society",
        "actor",
        "action",
        "entity_type",
        "entity_id",
        "module",
    )
    list_filter = ("action", "module", "society", "created_at")
    search_fields = ("entity_type", "entity_id", "actor__email", "request_id")
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
        "request_id",
        "session_id",
        "user_agent",
        "module",
        "duration_ms",
        "reason",
        "created_at",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
