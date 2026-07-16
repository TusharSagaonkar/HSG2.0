"""Admin registrations for the ``onboarding`` app.

Registers all wizard/staging models with read-only display for debugging.
Append-only models (``WizardStepLog``, ``MigrationAuditLog``) disable
add/change/delete permissions. Follows the ``@admin.register(Model)``
decorator style used in ``gateops/admin.py``.
"""

from django.contrib import admin

from onboarding.models import (
    MigrationAuditLog,
    OnboardingWizard,
    StagingBankOpening,
    StagingCashOpening,
    StagingChartOfAccounts,
    StagingFixedAsset,
    StagingFund,
    StagingLoan,
    StagingMemberOutstanding,
    StagingSecurityDeposit,
    StagingTrialBalance,
    StagingVendorOutstanding,
    UploadBatch,
    WizardStepLog,
)


@admin.register(OnboardingWizard)
class OnboardingWizardAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "society",
        "society_type",
        "status",
        "current_step",
        "is_finalized",
        "created_by",
        "started_at",
        "completed_at",
    )
    list_filter = ("society_type", "status", "is_finalized")
    search_fields = ("society__name", "created_by__email")
    readonly_fields = ("started_at", "completed_at")


@admin.register(WizardStepLog)
class WizardStepLogAdmin(admin.ModelAdmin):
    list_display = (
        "wizard",
        "step_number",
        "step_name",
        "status",
        "completed_at",
        "completed_by",
    )
    list_filter = ("status",)
    search_fields = ("step_name", "wizard__id")
    readonly_fields = (
        "wizard",
        "step_number",
        "step_name",
        "status",
        "data_snapshot",
        "completed_at",
        "completed_by",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UploadBatch)
class UploadBatchAdmin(admin.ModelAdmin):
    list_display = (
        "wizard",
        "society",
        "template_type",
        "file_name",
        "row_count",
        "status",
        "uploaded_at",
        "uploaded_by",
    )
    list_filter = ("template_type", "status")
    search_fields = ("file_name", "society__name", "wizard__id")
    readonly_fields = ("uploaded_at",)


@admin.register(StagingChartOfAccounts)
class StagingChartOfAccountsAdmin(admin.ModelAdmin):
    list_display = (
        "wizard",
        "row_number",
        "account_code",
        "account_name",
        "nature",
        "validation_status",
        "is_approved",
    )
    list_filter = ("validation_status", "nature", "is_approved")
    search_fields = ("account_code", "account_name", "wizard__id")
    readonly_fields = ("upload_batch", "row_number")


@admin.register(StagingTrialBalance)
class StagingTrialBalanceAdmin(admin.ModelAdmin):
    list_display = (
        "wizard",
        "row_number",
        "account_code",
        "account_name",
        "debit",
        "credit",
        "validation_status",
        "is_approved",
    )
    list_filter = ("validation_status", "is_approved")
    search_fields = ("account_code", "account_name", "wizard__id")
    readonly_fields = ("upload_batch", "row_number")


@admin.register(StagingMemberOutstanding)
class StagingMemberOutstandingAdmin(admin.ModelAdmin):
    list_display = (
        "wizard",
        "row_number",
        "unit_identifier",
        "member_name",
        "outstanding_amount",
        "validation_status",
        "is_approved",
    )
    list_filter = ("validation_status", "is_approved")
    search_fields = ("unit_identifier", "member_name", "wizard__id")
    readonly_fields = ("upload_batch", "row_number")


@admin.register(StagingVendorOutstanding)
class StagingVendorOutstandingAdmin(admin.ModelAdmin):
    list_display = (
        "wizard",
        "row_number",
        "vendor_name",
        "outstanding_amount",
        "validation_status",
        "is_approved",
    )
    list_filter = ("validation_status", "is_approved")
    search_fields = ("vendor_name", "wizard__id")
    readonly_fields = ("upload_batch", "row_number")


@admin.register(StagingBankOpening)
class StagingBankOpeningAdmin(admin.ModelAdmin):
    list_display = (
        "wizard",
        "row_number",
        "bank_name",
        "account_number",
        "opening_balance",
        "validation_status",
        "is_approved",
    )
    list_filter = ("validation_status", "is_approved")
    search_fields = ("bank_name", "account_number", "wizard__id")
    readonly_fields = ("upload_batch", "row_number")


@admin.register(StagingCashOpening)
class StagingCashOpeningAdmin(admin.ModelAdmin):
    list_display = (
        "wizard",
        "row_number",
        "opening_balance",
        "validation_status",
        "is_approved",
    )
    list_filter = ("validation_status", "is_approved")
    search_fields = ("wizard__id",)
    readonly_fields = ("upload_batch", "row_number")


@admin.register(StagingFixedAsset)
class StagingFixedAssetAdmin(admin.ModelAdmin):
    list_display = (
        "wizard",
        "row_number",
        "asset_name",
        "asset_category",
        "gross_value",
        "depreciation",
        "net_value",
        "validation_status",
        "is_approved",
    )
    list_filter = ("validation_status", "is_approved", "asset_category")
    search_fields = ("asset_name", "asset_category", "wizard__id")
    readonly_fields = ("upload_batch", "row_number")


@admin.register(StagingSecurityDeposit)
class StagingSecurityDepositAdmin(admin.ModelAdmin):
    list_display = (
        "wizard",
        "row_number",
        "description",
        "amount",
        "against_account",
        "validation_status",
        "is_approved",
    )
    list_filter = ("validation_status", "is_approved")
    search_fields = ("description", "against_account", "wizard__id")
    readonly_fields = ("upload_batch", "row_number")


@admin.register(StagingLoan)
class StagingLoanAdmin(admin.ModelAdmin):
    list_display = (
        "wizard",
        "row_number",
        "loan_name",
        "loan_type",
        "outstanding_principal",
        "interest",
        "validation_status",
        "is_approved",
    )
    list_filter = ("validation_status", "is_approved", "loan_type")
    search_fields = ("loan_name", "loan_type", "wizard__id")
    readonly_fields = ("upload_batch", "row_number")


@admin.register(StagingFund)
class StagingFundAdmin(admin.ModelAdmin):
    list_display = (
        "wizard",
        "row_number",
        "fund_name",
        "fund_type",
        "balance",
        "validation_status",
        "is_approved",
    )
    list_filter = ("validation_status", "is_approved", "fund_type")
    search_fields = ("fund_name", "fund_type", "wizard__id")
    readonly_fields = ("upload_batch", "row_number")


@admin.register(MigrationAuditLog)
class MigrationAuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "wizard",
        "society",
        "action",
        "actor",
        "timestamp",
    )
    list_filter = ("action",)
    search_fields = ("action", "society__name", "wizard__id")
    readonly_fields = (
        "wizard",
        "society",
        "action",
        "actor",
        "timestamp",
        "details",
        "before_state",
        "after_state",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
