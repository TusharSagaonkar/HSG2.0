from django.contrib import admin
from django.contrib import messages
from django.utils.translation import gettext_lazy as _

from .models.model_SocietyConfig import SocietyConfig


# Inline for Society admin (will be added to housing/admin.py)
class SocietyConfigInline(admin.StackedInline):
    model = SocietyConfig
    can_delete = False
    fieldsets = (
        (None, {
            "fields": (
                "share_value",
                "default_share_count",
            )
        }),
        ("Fees", {
            "fields": (
                "entrance_fee",
                "transfer_fee",
                "premium_amount",
            ),
            "classes": ("collapse",),
        }),
        ("Settings", {
            "fields": (
                "allow_multiple_nominees",
                "require_approval",
                "auto_generate_vouchers",
            ),
            "classes": ("collapse",),
        }),
    )
    extra = 0
    max_num = 1
    verbose_name = "Share Configuration"
    verbose_name_plural = "Share Configuration"


@admin.register(SocietyConfig)
class SocietyConfigAdmin(admin.ModelAdmin):
    """
    Admin for SocietyConfig (per-society share management configuration).
    """
    list_display = (
        "society",
        "share_value",
        "default_share_count",
        "entrance_fee",
        "transfer_fee",
        "premium_amount",
        "allow_multiple_nominees",
        "require_approval",
        "auto_generate_vouchers",
        "updated_at",
    )
    list_filter = (
        "allow_multiple_nominees",
        "require_approval",
        "auto_generate_vouchers",
    )
    search_fields = (
        "society__name",
        "society__registration_number",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
    )
    ordering = ("society__name",)
    raw_id_fields = ("society",)

    fieldsets = (
        (None, {
            "fields": (
                "society",
            )
        }),
        ("Share Configuration", {
            "fields": (
                "share_value",
                "default_share_count",
            )
        }),
        ("Fee Configuration", {
            "fields": (
                "entrance_fee",
                "transfer_fee",
                "premium_amount",
            )
        }),
        ("Nominee & Approval", {
            "fields": (
                "allow_multiple_nominees",
                "require_approval",
            )
        }),
        ("Voucher Generation", {
            "fields": (
                "auto_generate_vouchers",
            )
        }),
        ("Audit", {
            "fields": (
                "created_at",
                "updated_at",
            ),
            "classes": ("collapse",),
        }),
    )

    # Custom actions
    @admin.action(description="Reset selected configurations to defaults")
    def reset_to_defaults(self, request, queryset):
        defaults = {
            "share_value": 100.00,
            "default_share_count": 1,
            "entrance_fee": 0,
            "transfer_fee": 0,
            "premium_amount": 0,
            "allow_multiple_nominees": False,
            "require_approval": True,
            "auto_generate_vouchers": True,
        }
        updated = queryset.update(**defaults)
        self.message_user(
            request,
            f"{updated} configurations reset to defaults.",
            level=messages.SUCCESS,
        )

    actions = [reset_to_defaults]