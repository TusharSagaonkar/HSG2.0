from pyexpat.errors import messages
from django.contrib import admin
from django.core.exceptions import ValidationError
from .models.model_Account import Account
from .models.model_AccountCategory import AccountCategory
from .models.model_FinancialYear import FinancialYear
from .models.model_Voucher import Voucher
from .models.model_LedgerEntry import LedgerEntry
from .models.model_AccountingPeriod import AccountingPeriod
# accounting/admin.py

@admin.register(AccountingPeriod)
class AccountingPeriodAdmin(admin.ModelAdmin):
    list_display = (
        "society",
        "financial_year",
        "start_date",
        "end_date",
        "is_open",
    )

    list_filter = (
        "society",
        "financial_year",
        "is_open",
    )

    ordering = ("start_date",)
    search_fields = ("financial_year__name",)

    # 🔒 Infrastructure model — no free edits
    readonly_fields = (
        "society",
        "financial_year",
        "start_date",
        "end_date",
    )

    # ❌ No add / delete
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    # ✅ Allow edit only to toggle is_open (via actions)
    def has_change_permission(self, request, obj=None):
        return True

    actions = ["close_period"]

    @admin.action(description="Close selected period (chronologically safe)")
    def close_period(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request,
                "Select exactly ONE period to close.",
                level=messages.ERROR,
            )
            return

        period = queryset.first()

        if not period.is_open:
            self.message_user(
                request,
                "Selected period is already closed.",
                level=messages.WARNING,
            )
            return

        # 🔒 Ensure no earlier open periods exist
        earlier_open = AccountingPeriod.objects.filter(
            society=period.society,
            financial_year=period.financial_year,
            start_date__lt=period.start_date,
            is_open=True,
        ).exists()

        if earlier_open:
            self.message_user(
                request,
                "Cannot close this period while earlier periods are still open.",
                level=messages.ERROR,
            )
            return

        # 🔒 Find next period
        next_period = AccountingPeriod.objects.filter(
            society=period.society,
            financial_year=period.financial_year,
            start_date__gt=period.end_date,
        ).order_by("start_date").first()

        # Close current
        period.is_open = False
        period.save(update_fields=["is_open"])

        # Open next (if exists)
        if next_period:
            next_period.is_open = True
            next_period.save(update_fields=["is_open"])

            self.message_user(
                request,
                f"Period closed. Next period ({next_period.start_date} → {next_period.end_date}) opened.",
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                "Final period closed. Financial year is now fully closed.",
                level=messages.SUCCESS,
            )


@admin.register(FinancialYear)
class FinancialYearAdmin(admin.ModelAdmin):
    list_display = ("name", "start_date", "end_date", "is_open")

def financial_year_status(self, obj):
    from accounting.models.model_FinancialYear import FinancialYear
    return "OPEN" if FinancialYear.get_open_year_for_date(obj.voucher_date) else "CLOSED"

financial_year_status.short_description = "FY Status"



@admin.register(AccountCategory)
class AccountCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "account_type")


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("name", "account_type", "is_active")
    list_filter = ("category__account_type",)


class LedgerEntryInline(admin.TabularInline):
    model = LedgerEntry
    extra = 0

    def has_add_permission(self, request, obj):
        return not (obj and obj.posted_at)

    def has_change_permission(self, request, obj=None):
        return not (obj and obj.posted_at)

    def has_delete_permission(self, request, obj=None):
        return not (obj and obj.posted_at)


@admin.action(description="Post selected vouchers")
def post_vouchers(modeladmin, request, queryset):
    for voucher in queryset:
        voucher.post()


@admin.register(Voucher)
class VoucherAdmin(admin.ModelAdmin):
    list_display = (
        "voucher_type",
        "voucher_number",
        "voucher_date",
        "posted_at",
        "society",
    )

    readonly_fields = ("voucher_number", "posted_at")
    inlines = [LedgerEntryInline]
    actions = [post_vouchers]

    def has_change_permission(self, request, obj=None):
        return not (obj and obj.posted_at)
