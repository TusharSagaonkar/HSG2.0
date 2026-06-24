"""
Forms for the Bank Reconciliation Engine.
"""

import io
import csv
from datetime import date

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.forms import Textarea

from accounting.models.model_Account import Account
from accounting.models.model_LedgerEntry import LedgerEntry
from accounting.models.model_Voucher import Voucher
from reconciliation.models import BankStatementImport, BankTransaction, ReconciliationLink


class StatementImportForm(forms.Form):
    """Form for uploading a bank statement file."""

    file = forms.FileField(
        label="Bank Statement File",
        help_text="Upload a CSV or XLSX bank statement.",
        widget=forms.FileInput(attrs={"accept": ".csv,.xlsx,.xls"}),
    )
    bank = forms.CharField(
        max_length=60,
        required=False,
        label="Bank Name",
        help_text="Optional: identify the bank for better parsing.",
        widget=forms.TextInput(attrs={"placeholder": "e.g., HDFC, ICICI, SBI"}),
    )

    def __init__(self, *args, **kwargs):
        self.society = kwargs.pop("society", None)
        super().__init__(*args, **kwargs)

    def clean_file(self):
        uploaded_file = self.file
        ext = uploaded_file.name.rsplit(".", 1)[-1].lower() if "." in uploaded_file.name else ""
        if ext not in ("csv", "xlsx", "xls"):
            raise ValidationError(
                "Unsupported file format. Please upload a CSV or Excel (.xlsx) file."
            )

        # Check for duplicate import by computing hash
        from reconciliation.models.model_BankStatementImport import BankStatementImport as BSI

        file_hash = BSI.compute_file_hash(uploaded_file)
        uploaded_file.seek(0)

        if self.society and BSI.objects.filter(
            society=self.society, file_hash=file_hash,
        ).exists():
            raise ValidationError(
                "This file has already been imported. Duplicate imports are not allowed."
            )

        return uploaded_file


class ManualStatementImportForm(forms.Form):
    """Form for manually entering hard-copy statement rows."""

    bank_account = forms.ModelChoiceField(
        queryset=Account.objects.none(),
        label="Bank Account",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    rows_text = forms.CharField(
        label="Statement Rows",
        widget=forms.Textarea(
            attrs={
                "rows": 12,
                "class": "form-control font-monospace",
                "placeholder": "Date,Narration,Debit,Credit,Balance\n01/06/2025,Maintenance receipt,0,5000,50000",
            }
        ),
        help_text="Paste one row per line. Use CSV format with headers: Date,Narration,Debit,Credit,Balance.",
    )
    statement_name = forms.CharField(
        max_length=255,
        required=False,
        initial="manual_statement.csv",
        label="Statement Name",
    )

    def __init__(self, *args, **kwargs):
        self.society = kwargs.pop("society", None)
        super().__init__(*args, **kwargs)
        if self.society:
            self.fields["bank_account"].queryset = Account.objects.filter(
                Q(is_bank=True) | Q(sub_type=Account.SubType.BANK),
                society=self.society,
            ).order_by("name")

    def clean_rows_text(self):
        rows_text = self.cleaned_data["rows_text"].strip()
        if not rows_text:
            raise ValidationError("Please paste at least one statement row.")
        return rows_text

    def parse_rows(self):
        rows_text = self.cleaned_data["rows_text"]
        reader = csv.DictReader(io.StringIO(rows_text))
        rows = []
        for idx, row in enumerate(reader, start=1):
            rows.append(
                {
                    "transaction_date": row.get("Date") or row.get("date") or "",
                    "narration": row.get("Narration") or row.get("narration") or "",
                    "amount": self._pick_amount(row),
                    "dr_cr": self._pick_drcr(row),
                    "reference_no": row.get("Ref") or row.get("Reference") or "",
                    "cheque_no": row.get("Cheque") or row.get("Chq/Ref Number") or "",
                    "value_date": row.get("Value Dt") or row.get("Value Date") or "",
                    "balance": row.get("Balance") or row.get("Closing Balance") or "",
                    "raw_row": dict(row),
                    "source_row_index": idx,
                }
            )
        return rows

    @staticmethod
    def _pick_amount(row):
        debit = (row.get("Debit") or row.get("Withdrawal") or row.get("debit") or "").strip()
        credit = (row.get("Credit") or row.get("Deposit") or row.get("credit") or "").strip()
        if credit and credit not in {"0", "0.00", "-"}:
            return credit
        return debit

    @staticmethod
    def _pick_drcr(row):
        credit = (row.get("Credit") or row.get("Deposit") or row.get("credit") or "").strip()
        if credit and credit not in {"0", "0.00", "-"}:
            return "CREDIT"
        return "DEBIT"


class ManualWorkspaceFiltersForm(forms.Form):
    bank_account = forms.ModelChoiceField(
        queryset=Account.objects.none(),
        required=False,
        label="Bank Account",
        widget=forms.Select(attrs={"class": "form-select form-select-sm", "data-workspace-filter": "bank_account"}),
    )
    statement_date_from = forms.DateField(
        required=False,
        label="From",
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control form-control-sm"}),
    )
    statement_date_to = forms.DateField(
        required=False,
        label="To",
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control form-control-sm"}),
    )
    search_voucher = forms.CharField(
        required=False,
        label="Search Voucher",
        widget=forms.TextInput(attrs={"class": "form-control form-control-sm", "placeholder": "Voucher no / member / narration"}),
    )
    show_unmatched_only = forms.BooleanField(
        required=False,
        label="Show unmatched only",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    show_reconciled = forms.BooleanField(
        required=False,
        label="Show reconciled",
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, **kwargs):
        self.society = kwargs.pop("society", None)
        super().__init__(*args, **kwargs)
        if self.society:
            self.fields["bank_account"].queryset = Account.objects.filter(
                society=self.society,
                is_bank=True,
                is_active=True,
            ).order_by("name")


class ManualWorkspaceRowForm(forms.Form):
    transaction_date = forms.DateField(
        label="Date",
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control form-control-sm", "data-grid-input": "date"}),
    )
    narration = forms.CharField(
        label="Narration",
        widget=forms.TextInput(attrs={"class": "form-control form-control-sm", "data-grid-input": "narration", "autocomplete": "off"}),
    )
    reference_no = forms.CharField(
        required=False,
        label="Ref No",
        widget=forms.TextInput(attrs={"class": "form-control form-control-sm", "data-grid-input": "reference_no", "autocomplete": "off"}),
    )
    debit = forms.DecimalField(
        required=False,
        max_digits=12,
        decimal_places=2,
        label="Debit",
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm text-end", "step": "0.01", "min": "0", "data-grid-input": "debit"}),
    )
    credit = forms.DecimalField(
        required=False,
        max_digits=12,
        decimal_places=2,
        label="Credit",
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm text-end", "step": "0.01", "min": "0", "data-grid-input": "credit"}),
    )
    balance = forms.DecimalField(
        required=False,
        max_digits=12,
        decimal_places=2,
        label="Balance",
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm text-end", "step": "0.01", "data-grid-input": "balance"}),
    )
    selected_tx_id = forms.IntegerField(required=False, widget=forms.HiddenInput())

    def clean(self):
        cleaned = super().clean()
        debit = cleaned.get("debit")
        credit = cleaned.get("credit")
        if debit and credit:
            raise ValidationError("Enter either debit or credit, not both.")
        if not debit and not credit:
            raise ValidationError("Enter a debit or credit amount.")
        return cleaned


class ManualWorkspacePasteForm(forms.Form):
    pasted_rows = forms.CharField(
        label="Paste rows",
        widget=Textarea(attrs={
            "class": "form-control font-monospace",
            "rows": 8,
            "placeholder": "Date\tNarration\tRef No\tDebit\tCredit\tBalance",
            "data-paste-area": "true",
        }),
        required=False,
    )


class ManualEntryBatchForm(forms.Form):
    """Form for the redesigned manual bank statement entry batch header."""

    bank_account = forms.ModelChoiceField(
        queryset=Account.objects.none(),
        label="Bank Account",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    period_start = forms.DateField(
        label="Statement Period Start",
        widget=forms.DateInput(
            attrs={"type": "date", "class": "form-control"},
        ),
    )
    period_end = forms.DateField(
        label="Statement Period End",
        widget=forms.DateInput(
            attrs={"type": "date", "class": "form-control"},
        ),
    )
    opening_balance = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        label="Opening Balance",
        widget=forms.NumberInput(
            attrs={"class": "form-control text-end", "step": "0.01"},
        ),
    )
    closing_balance = forms.DecimalField(
        required=False,
        max_digits=12,
        decimal_places=2,
        label="Closing Balance (optional)",
        widget=forms.NumberInput(
            attrs={"class": "form-control text-end", "step": "0.01"},
        ),
    )

    def __init__(self, *args, **kwargs):
        self.society = kwargs.pop("society", None)
        super().__init__(*args, **kwargs)
        if self.society:
            self.fields["bank_account"].queryset = Account.objects.filter(
                society=self.society,
            ).order_by("name")

    def clean_bank_account(self):
        bank_account = self.cleaned_data.get("bank_account")
        if not bank_account:
            return bank_account

        is_bank_like = bank_account.is_bank or bank_account.sub_type == Account.SubType.BANK
        if not is_bank_like and "bank" not in bank_account.name.lower():
            raise ValidationError("Select a valid bank account.")
        return bank_account

    def clean(self):
        cleaned = super().clean()
        period_start = cleaned.get("period_start")
        period_end = cleaned.get("period_end")
        if period_start and period_end and period_start > period_end:
            raise ValidationError("Period start date must be before end date.")
        return cleaned


class ManualEntryRowForm(forms.Form):
    """Form for validating a single row in the manual entry grid."""

    date = forms.CharField(
        label="Date",
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm grid-cell-input",
                "placeholder": "DD/MM/YYYY",
                "data-grid-field": "date",
            },
        ),
    )
    narration = forms.CharField(
        label="Narration",
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm grid-cell-input",
                "data-grid-field": "narration",
                "autocomplete": "off",
            },
        ),
    )
    reference_no = forms.CharField(
        required=False,
        label="Reference No",
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm grid-cell-input",
                "data-grid-field": "reference_no",
                "autocomplete": "off",
            },
        ),
    )
    debit = forms.DecimalField(
        required=False,
        max_digits=12,
        decimal_places=2,
        label="Debit",
        widget=forms.NumberInput(
            attrs={
                "class": "form-control form-control-sm text-end grid-cell-input",
                "step": "0.01",
                "min": "0",
                "data-grid-field": "debit",
            },
        ),
    )
    credit = forms.DecimalField(
        required=False,
        max_digits=12,
        decimal_places=2,
        label="Credit",
        widget=forms.NumberInput(
            attrs={
                "class": "form-control form-control-sm text-end grid-cell-input",
                "step": "0.01",
                "min": "0",
                "data-grid-field": "credit",
            },
        ),
    )
    balance = forms.DecimalField(
        required=False,
        max_digits=12,
        decimal_places=2,
        label="Balance",
        widget=forms.NumberInput(
            attrs={
                "class": "form-control form-control-sm text-end",
                "step": "0.01",
                "readonly": "readonly",
                "data-grid-field": "balance",
            },
        ),
    )
    row_index = forms.IntegerField(
        required=False,
        widget=forms.HiddenInput(),
    )

    def clean_date(self):
        value = self.cleaned_data.get("date", "")
        from reconciliation.services.manual_entry_batch_service import _parse_date
        if _parse_date(value) is None:
            raise ValidationError("Enter a valid date.")
        return value

    def clean(self):
        cleaned = super().clean()
        debit = cleaned.get("debit")
        credit = cleaned.get("credit")
        if debit and credit:
            if debit > 0 and credit > 0:
                raise ValidationError("Enter either debit or credit, not both.")
        if not debit and not credit:
            raise ValidationError("Enter a debit or credit amount.")
        return cleaned


class ManualCellUpdateForm(forms.Form):
    """Validates a single cell update from the Excel-like manual workspace grid."""

    row_id = forms.IntegerField(min_value=1)
    field = forms.ChoiceField(
        choices=[
            ("transaction_date", "Transaction Date"),
            ("narration", "Narration"),
            ("reference_no", "Reference No"),
            ("cheque_no", "Cheque No"),
            ("amount", "Amount"),
            ("dr_cr", "Dr/Cr"),
            ("balance", "Balance"),
        ],
    )
    value = forms.CharField(required=False)

    def clean_value(self):
        field = self.cleaned_data.get("field")
        value = self.cleaned_data.get("value")
        if field == "transaction_date" and value:
            from reconciliation.services.manual_entry_batch_service import _parse_date
            if _parse_date(value) is None:
                raise ValidationError("Invalid date format. Use YYYY-MM-DD.")
        if field in ("amount", "balance") and value not in (None, ""):
            from reconciliation.services.manual_entry_batch_service import _parse_decimal
            if _parse_decimal(value) is None:
                raise ValidationError(f"Invalid numeric value for {field}.")
        if field == "dr_cr" and value not in ("DEBIT", "CREDIT", ""):
            raise ValidationError("dr_cr must be DEBIT or CREDIT.")
        if field == "narration" and not value.strip():
            raise ValidationError("Narration cannot be empty.")
        return value


class ManualBatchSaveForm(forms.Form):
    """Validates a list of cell changes for batch save."""

    changes = forms.JSONField(
        help_text="List of {row_id, field, value} objects.",
    )

    ALLOWED_FIELDS = {
        "transaction_date", "narration", "reference_no", "cheque_no",
        "amount", "dr_cr", "balance",
    }

    def clean_changes(self):
        changes = self.cleaned_data["changes"]
        if not changes or not isinstance(changes, list):
            raise ValidationError("changes must be a non-empty list.")
        for idx, change in enumerate(changes):
            if not isinstance(change, dict):
                raise ValidationError(f"Change at index {idx} must be an object.")
            if "row_id" not in change:
                raise ValidationError(f"Change at index {idx}: missing 'row_id'.")
            if "field" not in change:
                raise ValidationError(f"Change at index {idx}: missing 'field'.")
            if change["field"] not in self.ALLOWED_FIELDS:
                raise ValidationError(
                    f"Change at index {idx}: invalid field '{change['field']}'."
                )
            value = change.get("value", "")
            if change["field"] == "dr_cr" and value not in ("DEBIT", "CREDIT", ""):
                raise ValidationError(
                    f"Change at index {idx}: dr_cr must be DEBIT or CREDIT."
                )
            if change["field"] == "narration" and not str(value).strip():
                raise ValidationError(
                    f"Change at index {idx}: narration cannot be empty."
                )
        return changes


class ForceMatchForm(forms.Form):
    """Form for manually forcing a match between a bank transaction and a ledger entry."""

    bank_transaction = forms.ModelChoiceField(
        queryset=BankTransaction.objects.none(),
        label="Bank Transaction",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    ledger_entry = forms.ModelChoiceField(
        queryset=LedgerEntry.objects.none(),
        label="Ledger Entry",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    remarks = forms.CharField(
        max_length=500,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        label="Remarks",
    )

    def __init__(self, *args, **kwargs):
        self.society = kwargs.pop("society", None)
        super().__init__(*args, **kwargs)
        if self.society:
            self.fields["bank_transaction"].queryset = BankTransaction.objects.filter(
                bank_statement_import__society=self.society,
                is_duplicate=False,
            ).select_related("bank_statement_import").order_by("-transaction_date", "-id")

            self.fields["ledger_entry"].queryset = LedgerEntry.objects.filter(
                voucher__society=self.society,
                voucher__payment_mode__in=[
                    Voucher.PaymentMode.BANK_TRANSFER,
                    Voucher.PaymentMode.CHEQUE,
                    Voucher.PaymentMode.UPI,
                ],
            ).select_related("voucher", "account", "unit").order_by("-voucher__voucher_date", "-id")

    def clean(self):
        cleaned = super().clean()
        bank_tx = cleaned.get("bank_transaction")
        ledger_entry = cleaned.get("ledger_entry")

        if bank_tx and ledger_entry:
            # Check no existing non-PENDING link exists for this bank tx
            existing = ReconciliationLink.objects.filter(
                society=self.society,
                bank_transaction=bank_tx,
            ).exclude(
                status=ReconciliationLink.Status.PENDING,
            ).exists()
            if existing:
                raise ValidationError(
                    "This bank transaction already has a completed reconciliation link."
                )

        return cleaned
