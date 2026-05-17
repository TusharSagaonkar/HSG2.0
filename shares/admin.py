from django.contrib import admin
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from django.db.models import Sum
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.html import format_html

from .models import ShareLedger, ShareCertificate, EventLog


@admin.register(ShareLedger)
class ShareLedgerAdmin(admin.ModelAdmin):
    """
    Admin for ShareLedger (append-only share transactions).
    Read-only for most fields; only allow adding new transactions.
    """
    list_display = (
        "id",
        "society",
        "member",
        "transaction_type",
        "shares_in",
        "shares_out",
        "balance_after",
        "transaction_date",
        "reference_id",
        "created_at",
    )
    list_filter = (
        "society",
        "transaction_type",
        "transaction_date",
    )
    search_fields = (
        "member__full_name",
        "reference_id",
        "reason",
    )
    readonly_fields = (
        "society",
        "member",
        "shares_in",
        "shares_out",
        "balance_after",
        "transaction_type",
        "reference_id",
        "transaction_date",
        "reason",
        "created_by",
        "voucher",
        "created_at",
    )
    ordering = ("-transaction_date", "-created_at")
    date_hierarchy = "transaction_date"

    fieldsets = (
        (None, {
            "fields": (
                "society",
                "member",
                "transaction_type",
                "transaction_date",
            )
        }),
        ("Share Movement", {
            "fields": (
                "shares_in",
                "shares_out",
                "balance_after",
            ),
            "classes": ("collapse",),
        }),
        ("Metadata", {
            "fields": (
                "reference_id",
                "reason",
                "created_by",
                "voucher",
                "created_at",
            ),
            "classes": ("collapse",),
        }),
    )

    # Append-only: prevent editing of existing records
    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    # Custom action: generate voucher for selected ledger entries
    @admin.action(description="Generate accounting vouchers for selected entries")
    def generate_vouchers(self, request, queryset):
        from accounting.services.share_voucher import generate_share_voucher
        count = 0
        for entry in queryset:
            if not entry.voucher:
                try:
                    voucher = generate_share_voucher(entry)
                    entry.voucher = voucher
                    entry.save(update_fields=["voucher"])
                    count += 1
                except Exception as e:
                    self.message_user(
                        request,
                        f"Failed to generate voucher for entry {entry.id}: {e}",
                        level=messages.ERROR,
                    )
        if count:
            self.message_user(
                request,
                f"Generated vouchers for {count} entries.",
                level=messages.SUCCESS,
            )

    actions = [generate_vouchers]


@admin.register(ShareCertificate)
class ShareCertificateAdmin(admin.ModelAdmin):
    """
    Admin for ShareCertificate.
    """
    list_display = (
        "certificate_no",
        "member",
        "share_count",
        "issued_date",
        "status",
        "transferred_to",
        "transferred_date",
        "issued_by",
    )
    list_filter = (
        "status",
        "issued_date",
        "member__society",
    )
    search_fields = (
        "certificate_no",
        "member__full_name",
        "transferred_to__full_name",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
    )
    ordering = ("-issued_date", "certificate_no")
    date_hierarchy = "issued_date"
    raw_id_fields = ("member", "transferred_to", "issued_by")

    fieldsets = (
        (None, {
            "fields": (
                "member",
                "certificate_no",
                "share_count",
                "issued_date",
                "status",
            )
        }),
        ("Transfer Details", {
            "fields": (
                "transferred_to",
                "transferred_date",
            ),
            "classes": ("collapse",),
        }),
        ("Audit", {
            "fields": (
                "issued_by",
                "created_at",
                "updated_at",
            ),
            "classes": ("collapse",),
        }),
    )

    # Custom actions
    @admin.action(description="Mark selected certificates as cancelled")
    def mark_cancelled(self, request, queryset):
        updated = queryset.update(status=ShareCertificate.Status.CANCELLED)
        self.message_user(
            request,
            f"{updated} certificates marked as cancelled.",
            level=messages.SUCCESS,
        )

    @admin.action(description="Mark selected certificates as transferred")
    def mark_transferred(self, request, queryset):
        # This action would need a form to select transferred_to and date
        # For simplicity, we'll just set status to TRANSFERRED
        updated = queryset.update(status=ShareCertificate.Status.TRANSFERRED)
        self.message_user(
            request,
            f"{updated} certificates marked as transferred.",
            level=messages.SUCCESS,
        )

    actions = [mark_cancelled, mark_transferred]

    # Inline for ShareLedger? Not needed because ShareLedger is separate.
    # But we can add a link to member's share ledger.
    def view_share_ledger_link(self, obj):
        url = reverse("admin:shares_shareledger_changelist") + f"?member__id__exact={obj.member.id}"
        return format_html('<a href="{}">View Share Ledger</a>', url)
    view_share_ledger_link.short_description = "Share Ledger"

    # Add custom column
    list_display = list_display + ("view_share_ledger_link",)


@admin.register(EventLog)
class EventLogAdmin(admin.ModelAdmin):
    """
    Admin for EventLog - comprehensive audit log for share transactions and member events.
    Read-only interface since event logs are append-only audit records.
    """
    list_display = (
        "timestamp",
        "event_type",
        "member_link",
        "from_member_link",
        "to_member_link",
        "share_count",
        "certificate_number",
        "performed_by",
        "society",
        "ip_address",
        "view_details_link",
    )
    
    list_filter = (
        "event_type",
        "society",
        ("timestamp", admin.DateFieldListFilter),
        "performed_by",
        "member__society",  # Filter by member's society
    )
    
    search_fields = (
        "member__full_name",
        "from_member__full_name",
        "to_member__full_name",
        "certificate_number",
        "description",
        "ip_address",
        "performed_by__username",
        "performed_by__email",
    )
    
    readonly_fields = (
        "timestamp",
        "event_type",
        "member",
        "from_member",
        "to_member",
        "share_count",
        "share_value",
        "certificate_number",
        "nominee",
        "performed_by",
        "society",
        "description",
        "metadata",
        "ip_address",
        "user_agent",
        "created_at",
    )
    
    date_hierarchy = "timestamp"
    ordering = ("-timestamp", "-created_at")
    list_per_page = 50
    
    fieldsets = (
        ("Event Details", {
            "fields": (
                "timestamp",
                "event_type",
                "description",
            )
        }),
        ("Members Involved", {
            "fields": (
                "member",
                "from_member",
                "to_member",
                "nominee",
            ),
            "classes": ("collapse",),
        }),
        ("Share Details", {
            "fields": (
                "share_count",
                "share_value",
                "certificate_number",
            ),
            "classes": ("collapse",),
        }),
        ("Context", {
            "fields": (
                "society",
                "performed_by",
                "metadata",
            ),
            "classes": ("collapse",),
        }),
        ("Request Info", {
            "fields": (
                "ip_address",
                "user_agent",
            ),
            "classes": ("collapse",),
        }),
        ("Audit", {
            "fields": (
                "created_at",
            ),
            "classes": ("collapse",),
        }),
    )
    
    # Custom methods for clickable links
    def member_link(self, obj):
        if obj.member:
            url = reverse("admin:members_member_change", args=[obj.member.id])
            return format_html('<a href="{}">{}</a>', url, obj.member.full_name)
        return "-"
    member_link.short_description = "Member"
    
    def from_member_link(self, obj):
        if obj.from_member:
            url = reverse("admin:members_member_change", args=[obj.from_member.id])
            return format_html('<a href="{}">{}</a>', url, obj.from_member.full_name)
        return "-"
    from_member_link.short_description = "From Member"
    
    def to_member_link(self, obj):
        if obj.to_member:
            url = reverse("admin:members_member_change", args=[obj.to_member.id])
            return format_html('<a href="{}">{}</a>', url, obj.to_member.full_name)
        return "-"
    to_member_link.short_description = "To Member"
    
    def view_details_link(self, obj):
        url = reverse("admin:shares_eventlog_change", args=[obj.id])
        return format_html('<a href="{}">View Details</a>', url)
    view_details_link.short_description = "Details"
    
    # Prevent modification of event logs (append-only)
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False
    
    def has_add_permission(self, request):
        return False  # Event logs should only be created via signals/service
    
    # Custom admin actions
    @admin.action(description="Export selected logs as CSV")
    def export_as_csv(self, request, queryset):
        import csv
        from django.http import HttpResponse
        
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="event_logs.csv"'
        
        writer = csv.writer(response)
        writer.writerow([
            "Timestamp", "Event Type", "Member", "From Member", "To Member",
            "Share Count", "Certificate Number", "Performed By", "Society",
            "IP Address", "Description"
        ])
        
        for log in queryset:
            writer.writerow([
                log.timestamp,
                log.get_event_type_display(),
                log.member.full_name if log.member else "",
                log.from_member.full_name if log.from_member else "",
                log.to_member.full_name if log.to_member else "",
                log.share_count,
                log.certificate_number,
                log.performed_by.username,
                log.society.name,
                log.ip_address,
                log.description[:100] + "..." if len(log.description) > 100 else log.description
            ])
        
        return response
    
    @admin.action(description="Filter to share-related events")
    def filter_share_events(self, request, queryset):
        from .models import EventLog
        share_events = [
            EventLog.EventType.SHARE_ALLOTMENT,
            EventLog.EventType.SHARE_TRANSFER,
            EventLog.EventType.SHARE_TRANSMISSION,
            EventLog.EventType.SHARE_CORRECTION,
            EventLog.EventType.SHARE_FORFEITURE,
            EventLog.EventType.SHARE_BUYBACK,
            EventLog.EventType.SHARE_ADJUSTMENT,
        ]
        return queryset.filter(event_type__in=share_events)
    
    actions = [export_as_csv, filter_share_events]
