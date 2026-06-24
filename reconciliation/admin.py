from django.contrib import admin
from reconciliation.models import (
    BankStatementImport,
    BankTransaction,
    BankTransactionNormalized,
    ReconciliationLink,
    ReconciliationHistory,
)


@admin.register(BankStatementImport)
class BankStatementImportAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "society",
        "bank_account",
        "file_name",
        "import_status",
        "row_count",
        "uploaded_at",
    )
    list_filter = ("society", "import_status", "bank_account")
    search_fields = ("file_name", "file_hash")
    readonly_fields = ("file_hash", "uploaded_at", "row_count")
    ordering = ("-uploaded_at",)


@admin.register(BankTransaction)
class BankTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "get_society",
        "transaction_date",
        "amount",
        "dr_cr",
        "reference_no",
        "cheque_no",
        "is_duplicate",
    )
    list_filter = ("dr_cr", "is_duplicate", "transaction_date")
    search_fields = ("reference_no", "cheque_no", "narration")
    readonly_fields = (
        "bank_statement_import",
        "transaction_date",
        "value_date",
        "narration",
        "reference_no",
        "cheque_no",
        "amount",
        "dr_cr",
        "balance",
        "raw_row_data",
        "duplicate_hash",
    )
    ordering = ("-transaction_date",)

    @admin.display(description="Society")
    def get_society(self, obj):
        return obj.bank_statement_import.society_id


@admin.register(BankTransactionNormalized)
class BankTransactionNormalizedAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "bank_transaction",
        "extracted_utr",
        "extracted_flat_no",
        "extracted_reference",
    )
    search_fields = ("extracted_utr", "extracted_flat_no", "extracted_reference")
    readonly_fields = ("bank_transaction", "normalized_at")


@admin.register(ReconciliationLink)
class ReconciliationLinkAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "society",
        "voucher_entry",
        "bank_transaction",
        "matched_amount",
        "match_type",
        "status",
        "confidence_score",
        "is_manual",
        "matched_at",
    )
    list_filter = ("society", "status", "match_type", "is_manual")
    search_fields = ("remarks",)
    readonly_fields = ("matched_at",)
    ordering = ("-matched_at", "-id")


@admin.register(ReconciliationHistory)
class ReconciliationHistoryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "reconciliation_link",
        "action",
        "previous_status",
        "new_status",
        "performed_by",
        "performed_at",
    )
    list_filter = ("action", "performed_at")
    search_fields = ("previous_status", "new_status")
    readonly_fields = (
        "reconciliation_link",
        "action",
        "previous_status",
        "new_status",
        "previous_match_type",
        "new_match_type",
        "previous_confidence",
        "new_confidence",
        "performed_by",
        "performed_at",
        "details",
    )
    ordering = ("-performed_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False