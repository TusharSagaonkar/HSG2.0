from django.contrib import admin
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from django.db.models import Sum

from .models.model_Nominee import Nominee


@admin.register(Nominee)
class NomineeAdmin(admin.ModelAdmin):
    """
    Admin for Nominee records.
    """
    list_display = (
        "id",
        "member",
        "name",
        "relationship",
        "percentage",
        "priority_order",
        "is_active",
        "created_at",
        "deactivated_at",
    )
    list_filter = (
        "is_active",
        "relationship",
        "member__society",
    )
    search_fields = (
        "name",
        "member__full_name",
        "relationship",
    )
    readonly_fields = (
        "created_at",
        "deactivated_at",
        "deactivated_by",
    )
    ordering = ("member", "priority_order")
    raw_id_fields = ("member", "deactivated_by")

    fieldsets = (
        (None, {
            "fields": (
                "member",
                "name",
                "relationship",
                "percentage",
                "priority_order",
                "is_active",
            )
        }),
        ("Audit", {
            "fields": (
                "created_at",
                "deactivated_at",
                "deactivated_by",
            ),
            "classes": ("collapse",),
        }),
    )

    # Custom actions
    @admin.action(description="Activate selected nominees")
    def activate_nominees(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(
            request,
            f"{updated} nominees activated.",
            level=messages.SUCCESS,
        )

    @admin.action(description="Deactivate selected nominees")
    def deactivate_nominees(self, request, queryset):
        from django.utils import timezone
        user = request.user
        for nominee in queryset:
            nominee.deactivate(user)
        self.message_user(
            request,
            f"{queryset.count()} nominees deactivated.",
            level=messages.SUCCESS,
        )

    @admin.action(description="Validate nominee percentages")
    def validate_percentages(self, request, queryset):
        errors = []
        for nominee in queryset:
            try:
                nominee.clean()
            except Exception as e:
                errors.append(f"Nominee {nominee.id}: {e}")
        if errors:
            self.message_user(
                request,
                "Validation errors:\n" + "\n".join(errors),
                level=messages.ERROR,
            )
        else:
            self.message_user(
                request,
                "All selected nominees have valid percentages.",
                level=messages.SUCCESS,
            )

    actions = [activate_nominees, deactivate_nominees, validate_percentages]

    # Inline for Member admin (will be added to housing/admin.py)
    # This class is defined here for reuse.
    class NomineeInline(admin.TabularInline):
        model = Nominee
        extra = 0
        fields = ("name", "relationship", "percentage", "priority_order", "is_active")
        readonly_fields = ("created_at", "deactivated_at")
        can_delete = True
        ordering = ("priority_order",)

        def has_change_permission(self, request, obj=None):
            return True

        def has_add_permission(self, request, obj):
            return True

        def has_delete_permission(self, request, obj=None):
            return True