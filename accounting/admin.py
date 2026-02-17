from django.contrib import messages
from django.contrib import admin
from django.core.exceptions import ValidationError
from .models.model_Account import Account
from .models.model_AccountCategory import AccountCategory
from .models.model_FinancialYear import FinancialYear
from .models.model_Voucher import Voucher
from .models.model_LedgerEntry import LedgerEntry
from .models.model_AccountingPeriod import AccountingPeriod
from .models.model_PeriodStatusLog import PeriodStatusLog
from .models.model_YearEndCloseLog import YearEndCloseLog
from .services.period_workflow import close_period
from .services.period_workflow import reopen_period
from .services.year_end import close_financial_year_with_carry_forward
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

    actions = ["close_selected_period", "reopen_selected_period"]

    @admin.action(description="Close selected period (chronologically safe)")
    def close_selected_period(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request,
                "Select exactly ONE period to close.",
                level=messages.ERROR,
            )
            return

        period = queryset.first()

        try:
            next_period = close_period(period, performed_by=request.user, reason="Closed from admin action")
        except ValidationError as exc:
            self.message_user(request, "; ".join(exc.messages), level=messages.ERROR)
            return

        if next_period:
            self.message_user(
                request,
                f"Period closed. Next period ({next_period.start_date} → {next_period.end_date}) opened.",
                level=messages.SUCCESS,
            )
            return

        self.message_user(
            request,
            "Final period closed. Financial year is now fully closed.",
            level=messages.SUCCESS,
        )

    @admin.action(description="Reopen selected period (controlled)")
    def reopen_selected_period(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request,
                "Select exactly ONE period to reopen.",
                level=messages.ERROR,
            )
            return
        period = queryset.first()
        try:
            reopen_period(period, performed_by=request.user, reason="Reopened from admin action")
        except ValidationError as exc:
            self.message_user(request, "; ".join(exc.messages), level=messages.ERROR)
            return
        self.message_user(
            request,
            f"Period {period.start_date} → {period.end_date} reopened.",
            level=messages.SUCCESS,
        )


@admin.register(FinancialYear)
class FinancialYearAdmin(admin.ModelAdmin):
    list_display = ("society", "name", "start_date", "end_date", "is_open")
    list_filter = ("society", "is_open")
    actions = ["close_and_carry_forward"]

    @admin.action(description="Close FY and carry-forward opening balances")
    def close_and_carry_forward(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request,
                "Select exactly ONE financial year to close.",
                level=messages.ERROR,
            )
            return
        fy = queryset.first()
        try:
            next_fy, opening_voucher = close_financial_year_with_carry_forward(
                fy,
                performed_by=request.user,
                notes="Closed from admin action",
            )
        except ValidationError as exc:
            self.message_user(request, "; ".join(exc.messages), level=messages.ERROR)
            return
        self.message_user(
            request,
            f"{fy.name} closed. Opening voucher {opening_voucher.display_number} created in {next_fy.name}.",
            level=messages.SUCCESS,
        )



@admin.register(AccountCategory)
class AccountCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "account_type", "society")
    list_filter = ("society", "account_type")


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


@admin.register(PeriodStatusLog)
class PeriodStatusLogAdmin(admin.ModelAdmin):
    list_display = ("period", "action", "performed_by", "performed_at", "reason")
    list_filter = ("action", "period__society", "period__financial_year")
    readonly_fields = ("period", "action", "performed_by", "performed_at", "reason")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(YearEndCloseLog)
class YearEndCloseLogAdmin(admin.ModelAdmin):
    list_display = (
        "source_financial_year",
        "target_financial_year",
        "opening_voucher",
        "performed_by",
        "performed_at",
    )
    list_filter = ("source_financial_year__society",)
    readonly_fields = (
        "source_financial_year",
        "target_financial_year",
        "opening_voucher",
        "performed_by",
        "performed_at",
        "notes",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
