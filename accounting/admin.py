from django.contrib import admin
from django.core.exceptions import ValidationError

from .models.model_Account import Account
from .models.model_AccountCategory import AccountCategory
from .models.model_FinancialYear import FinancialYear
from .models.model_Voucher import Voucher
from .models.model_LedgerEntry import LedgerEntry

from .models.model_AccountingPeriod import AccountingPeriod


@admin.register(AccountingPeriod)
class AccountingPeriodAdmin(admin.ModelAdmin):
    list_display = ("year", "month", "is_open")
    list_filter = ("year", "is_open")


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
