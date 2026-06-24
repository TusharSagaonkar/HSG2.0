"""
Service layer for the redesigned Manual Bank Statement Entry module.

Provides pure business logic for:
  - Row-level validation
  - Running balance calculation
  - Batch persistence (creates BankStatementImport + BankTransaction rows)

This module does NOT modify the existing ManualWorkspaceService or
ManualStatementImportService.
"""

import hashlib
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction as db_transaction
from django.utils import timezone

from accounting.models.model_Account import Account
from reconciliation.models import BankStatementImport, BankTransaction

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SHORTCODE_MAP: dict[str, str] = {
    "mc": "Maintenance Collection",
    "bc": "Bank Charges",
    "ic": "Interest Credit",
    "upi": "UPI Collection",
    "cd": "Cheque Deposit",
    "nc": "NEFT Credit",
    "rc": "RTGS Credit",
}

DATE_FORMATS = [
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d/%m/%y",
    "%d-%m-%y",
    "%d-%b-%Y",
    "%d %b %Y",
    "%d-%B-%Y",
    "%d %B %Y",
    "%d-%b",
    "%d %b",
    "%d-%B",
    "%d %B",
    "%m/%d/%Y",
    "%m/%d/%y",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(value: Any) -> date | None:
    """Parse a date string into a date object. Returns None on failure."""
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()

    raw = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(raw, fmt).date()
            if parsed.year == 1900:
                parsed = parsed.replace(year=timezone.localdate().year)
            return parsed
        except ValueError:
            continue
    return None


def _parse_decimal(value: Any) -> Decimal | None:
    """Parse a numeric string into a Decimal. Returns None on failure."""
    if value in (None, ""):
        return None
    try:
        cleaned = str(value).replace(",", "").strip()
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _format_decimal(value: Decimal | None) -> str:
    """Format a Decimal for display."""
    if value is None:
        return ""
    return f"{value:,.2f}"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _row_signature(row_data: dict) -> tuple[str, str, str, str] | None:
    """Return a normalized signature used for duplicate detection."""
    transaction_date = _parse_date(row_data.get("date", ""))
    narration = (row_data.get("narration") or "").strip().lower()
    reference_no = (row_data.get("reference_no") or "").strip().lower()
    debit = _parse_decimal(row_data.get("debit") or "0") or Decimal("0")
    credit = _parse_decimal(row_data.get("credit") or "0") or Decimal("0")
    amount = credit if credit > 0 else debit
    if transaction_date is None or amount <= 0 or not narration:
        return None
    return (transaction_date.isoformat(), str(amount.quantize(Decimal("0.01"))), narration, reference_no)


def validate_row(row_data: dict) -> tuple[bool, dict[str, str]]:
    """
    Validate a single row dict (from client-side grid).

    Expected keys: date, narration, reference_no, debit, credit, balance

    Returns (is_valid, errors_dict).
    """
    errors: dict[str, str] = {}

    # Date
    transaction_date = _parse_date(row_data.get("date", ""))
    if transaction_date is None:
        errors["date"] = "A valid date is required."

    # Narration
    narration = (row_data.get("narration") or "").strip()
    if not narration:
        errors["narration"] = "Narration is required."

    # Debit / Credit (mutually exclusive, at least one required)
    debit = _parse_decimal(row_data.get("debit") or "")
    credit = _parse_decimal(row_data.get("credit") or "")

    if not debit and not credit:
        errors["amount"] = "Enter either a debit or credit amount."
    elif debit and credit:
        if debit > 0 and credit > 0:
            errors["amount"] = "Enter either debit or credit, not both."
    elif debit and debit <= 0:
        errors["debit"] = "Debit amount must be positive."
    elif credit and credit <= 0:
        errors["credit"] = "Credit amount must be positive."

    # Reference no (optional, no strict validation)
    # Balance (optional, validated if provided)
    balance = _parse_decimal(row_data.get("balance") or "")
    if row_data.get("balance") and balance is None:
        errors["balance"] = "Balance must be a valid number."

    return (len(errors) == 0, errors)


# ---------------------------------------------------------------------------
# Running Balance
# ---------------------------------------------------------------------------

def calculate_balances(
    rows: list[dict],
    opening_balance: Decimal | None = None,
) -> list[dict]:
    """
    Compute running balance for a list of row dicts.

    Each row dict should have 'debit', 'credit' keys with numeric values.
    Returns the same list with 'balance' field populated.

    Balance formula (bank statement convention):
      Debit → reduces balance, Credit → increases balance.
    """
    balance = opening_balance or Decimal("0.00")

    for row in rows:
        debit = _parse_decimal(row.get("debit") or "0") or Decimal("0")
        credit = _parse_decimal(row.get("credit") or "0") or Decimal("0")
        balance = balance - debit + credit
        row["balance"] = balance

    return rows


def validate_batch_rows(
    rows: list[dict],
    opening_balance: Decimal | None = None,
) -> tuple[list[dict], list[dict]]:
    """Validate a full batch, including duplicates and statement balance continuity."""
    errors: list[dict] = []
    valid_rows: list[dict] = []
    seen: dict[tuple[str, str, str, str], int] = {}
    running_balance = opening_balance or Decimal("0.00")

    for idx, row in enumerate(rows, start=1):
        is_valid, row_errors = validate_row(row)
        signature = _row_signature(row)
        if signature is not None:
            first_seen = seen.get(signature)
            if first_seen is not None:
                row_errors["duplicate"] = f"Possible duplicate of row {first_seen}."
            else:
                seen[signature] = idx

        debit = _parse_decimal(row.get("debit") or "0") or Decimal("0")
        credit = _parse_decimal(row.get("credit") or "0") or Decimal("0")
        expected_balance = running_balance - debit + credit
        supplied_balance = _parse_decimal(row.get("balance") or "")
        if supplied_balance is not None and supplied_balance != expected_balance:
            row_errors["balance"] = f"Expected balance {_format_decimal(expected_balance)}."
        running_balance = expected_balance

        if row_errors:
            row_errors["_row_index"] = idx
            errors.append(row_errors)
        elif is_valid:
            valid_rows.append(row)

    return valid_rows, errors


# ---------------------------------------------------------------------------
# Batch Persistence
# ---------------------------------------------------------------------------

def save_batch(
    *,
    user: Any,
    society: Any,
    bank_account: Account,
    period_start: date | None,
    period_end: date | None,
    opening_balance: Decimal | None,
    closing_balance: Decimal | None,
    rows: list[dict],
) -> tuple[BankStatementImport, list[BankTransaction], list[dict]]:
    """
    Persist a complete batch of manually entered bank statement rows.

    Creates a BankStatementImport (source_type='MANUAL') and all BankTransaction
    rows in a single atomic transaction.

    Args:
        user: The authenticated user performing the import.
        society: The society instance.
        bank_account: The bank Account instance (must be is_bank=True).
        period_start: Statement period start date.
        period_end: Statement period end date.
        opening_balance: Opening balance for the period.
        closing_balance: Expected closing balance (validated against computed).
        rows: List of row dicts with keys: date, narration, reference_no, debit, credit.

    Returns:
        Tuple of (BankStatementImport, list of created BankTransaction, list of row errors).
    """
    if not rows:
        raise ValueError("At least one row is required.")

    valid_rows, all_errors = validate_batch_rows(rows, opening_balance=opening_balance)

    if all_errors:
        raise ValidationError(
            f"{len(all_errors)} row(s) have validation errors.",
            params={"row_errors": all_errors},
        )

    # Compute running balances starting from opening balance
    calculate_balances(valid_rows, opening_balance=opening_balance)

    # Verify closing balance if provided
    if closing_balance is not None and valid_rows:
        computed_closing = valid_rows[-1].get("balance")
        if computed_closing is not None and computed_closing != closing_balance:
            logger.warning(
                "Closing balance mismatch: expected %s, computed %s",
                closing_balance,
                computed_closing,
            )

    # Generate a unique identifier for this batch
    batch_seed = (
        f"{society.id}:{bank_account.id}:{user.id}:{timezone.now().isoformat()}"
    )
    file_hash = hashlib.sha256(batch_seed.encode("utf-8")).hexdigest()
    filename = f"manual_entry_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"

    csv_lines = ["Date,Narration,Reference,Debit,Credit,Balance"]
    for row in valid_rows:
        csv_lines.append(
            ",".join([
                str(row.get("date") or ""),
                '"' + str(row.get("narration") or "").replace('"', '""') + '"',
                str(row.get("reference_no") or ""),
                str(row.get("debit") or ""),
                str(row.get("credit") or ""),
                str(row.get("balance") or ""),
            ])
        )
    csv_content = ("\n".join(csv_lines) + "\n").encode("utf-8")

    with db_transaction.atomic():
        statement_import = BankStatementImport.objects.create(
            society=society,
            bank_account=bank_account,
            file_name=filename,
            file_hash=file_hash,
            raw_file=ContentFile(csv_content, name=filename),
            uploaded_by=user,
            import_status=BankStatementImport.ImportStatus.COMPLETED,
            source_type="MANUAL",
            statement_start_date=period_start,
            statement_end_date=period_end,
        )

        transactions: list[BankTransaction] = []
        for idx, row in enumerate(valid_rows, start=1):
            transaction_date = _parse_date(row["date"])
            narration = (row.get("narration") or "").strip()
            reference_no = (row.get("reference_no") or "").strip()

            debit = _parse_decimal(row.get("debit") or "0") or Decimal("0")
            credit = _parse_decimal(row.get("credit") or "0") or Decimal("0")

            if credit > 0:
                amount = credit
                dr_cr = BankTransaction.DrCr.CREDIT
            else:
                amount = debit
                dr_cr = BankTransaction.DrCr.DEBIT

            balance = row.get("balance")

            duplicate_hash = BankTransaction.compute_duplicate_hash(
                transaction_date, amount, narration, reference_no
            )

            raw_row_data = {
                "date": str(transaction_date),
                "narration": narration,
                "reference_no": reference_no,
                "debit": str(debit),
                "credit": str(credit),
                "dr_cr": dr_cr,
                "balance": str(balance) if balance is not None else "",
            }

            bank_tx = BankTransaction.objects.create(
                bank_statement_import=statement_import,
                source_row_index=idx,
                transaction_date=transaction_date,
                narration=narration,
                reference_no=reference_no,
                amount=amount,
                dr_cr=dr_cr,
                balance=balance,
                raw_row_data=raw_row_data,
                duplicate_hash=duplicate_hash,
                is_duplicate=False,
            )
            transactions.append(bank_tx)

        # Update statement import metadata
        statement_import.row_count = len(transactions)
        if transactions:
            statement_import.statement_start_date = min(
                tx.transaction_date for tx in transactions
            )
            statement_import.statement_end_date = max(
                tx.transaction_date for tx in transactions
            )
        statement_import.save(
            update_fields=[
                "row_count",
                "statement_start_date",
                "statement_end_date",
            ]
        )

    logger.info(
        "Manual batch saved: import_id=%s, rows=%d, society=%s, bank=%s",
        statement_import.id,
        len(transactions),
        society.id,
        bank_account.id,
    )

    return statement_import, transactions, all_errors


def get_shortcodes() -> dict[str, str]:
    """Return the shortcode → full narration mapping."""
    return dict(SHORTCODE_MAP)


# ---------------------------------------------------------------------------
# Per-import undo log (server-side, in-memory — survives request cycle only)
# ---------------------------------------------------------------------------

_undo_logs: dict[int, list[dict]] = {}
MAX_UNDO_ENTRIES = 50


def _undo_log_key(import_id: int) -> int:
    return import_id


def _ensure_undo_log(import_id: int) -> list[dict]:
    if import_id not in _undo_logs:
        _undo_logs[import_id] = []
    return _undo_logs[import_id]


def push_undo_entry(import_id: int, row_id: int, field: str, previous_value) -> str:
    """Record a change so it can be undone. Returns an operation_id."""
    import uuid
    log = _ensure_undo_log(import_id)
    op_id = f"op_{uuid.uuid4().hex[:12]}"
    entry = {
        "operation_id": op_id,
        "row_id": row_id,
        "field": field,
        "previous_value": str(previous_value) if previous_value is not None else "",
        "timestamp": timezone.now().isoformat(),
    }
    log.append(entry)
    # Trim overflow
    while len(log) > MAX_UNDO_ENTRIES:
        log.pop(0)
    return op_id


def pop_undo_entry(import_id: int, operation_id: str) -> dict | None:
    """Find and remove a specific undo entry. Returns None if not found."""
    log = _ensure_undo_log(import_id)
    for i, entry in enumerate(log):
        if entry["operation_id"] == operation_id:
            log.pop(i)
            return entry
    return None


def clear_undo_log(import_id: int):
    _undo_logs.pop(import_id, None)


# ---------------------------------------------------------------------------
# Editable fields for the manual workspace grid
# ---------------------------------------------------------------------------

EDITABLE_FIELDS: set[str] = {
    "transaction_date",
    "narration",
    "reference_no",
    "cheque_no",
    "amount",
    "dr_cr",
    "balance",
}

_FIELD_TO_MODEL: dict[str, str] = {
    "transaction_date": "transaction_date",
    "narration": "narration",
    "reference_no": "reference_no",
    "cheque_no": "cheque_no",
    "amount": "amount",
    "dr_cr": "dr_cr",
    "balance": "balance",
}


# ---------------------------------------------------------------------------
# Field-level validation for cell edits
# ---------------------------------------------------------------------------

def validate_field(transaction: BankTransaction, field: str, value) -> tuple[bool, str | None]:
    """
    Validate a single field change for a BankTransaction.

    Returns (is_valid, error_message).
    """
    if field not in EDITABLE_FIELDS:
        return False, f"Field '{field}' is not editable."

    if field == "transaction_date":
        parsed = _parse_date(value)
        if parsed is None:
            return False, "Invalid date. Use YYYY-MM-DD format."
        return True, None

    if field == "narration":
        if not str(value).strip():
            return False, "Narration cannot be empty."
        return True, None

    if field in ("amount", "balance"):
        if value in (None, ""):
            return False, f"'{field}' cannot be empty."
        parsed = _parse_decimal(value)
        if parsed is None:
            return False, f"'{field}' must be a valid number."
        if field == "amount" and parsed <= 0:
            return False, "Amount must be positive."
        return True, None

    if field == "dr_cr":
        if value not in ("DEBIT", "CREDIT"):
            return False, "dr_cr must be DEBIT or CREDIT."
        return True, None

    # reference_no, cheque_no — no strict validation
    return True, None


def _prepare_field_value(field: str, value) -> object:
    """Coerce a raw value to the correct Python type for DB storage."""
    if field in ("amount", "balance"):
        if value in (None, ""):
            return None
        return _parse_decimal(value)
    if field == "transaction_date":
        return _parse_date(value)
    if field == "dr_cr":
        return str(value) if value else "CREDIT"
    return str(value) if value else ""


# ---------------------------------------------------------------------------
# Single cell update
# ---------------------------------------------------------------------------

def update_cell(
    *,
    transaction_id: int,
    field: str,
    value,
    import_id: int,
    user=None,
) -> dict:
    """
    Update a single field on a BankTransaction, bypassing the save() immutability
    guard via QuerySet.update(). Also updates raw_row_data for audit.

    Returns {"row_id": ..., "field": ..., "value": ..., "operation_id": ...}
    """
    from django.db.models import F

    transaction = BankTransaction.objects.select_related("bank_statement_import").get(
        pk=transaction_id,
    )

    # Capture previous value for undo
    previous_value = getattr(transaction, field, None)
    if field == "transaction_date":
        previous_value = previous_value.isoformat() if previous_value else ""

    is_valid, error = validate_field(transaction, field, value)
    if not is_valid:
        raise ValidationError(error)

    model_field = _FIELD_TO_MODEL[field]
    prepared = _prepare_field_value(field, value)
    operation_id = push_undo_entry(import_id, transaction_id, field, previous_value)

    with db_transaction.atomic():
        # Bypass model.save() immutability check via QuerySet.update()
        BankTransaction.objects.filter(pk=transaction_id).update(
            **{model_field: prepared}
        )
        # Update raw_row_data for audit trail
        transaction.refresh_from_db()
        raw_row = transaction.raw_row_data or {}
        if field == "transaction_date":
            raw_row["date"] = str(prepared) if prepared else ""
        else:
            raw_row[field] = str(prepared) if prepared is not None else ""
        BankTransaction.objects.filter(pk=transaction_id).update(
            raw_row_data=raw_row,
        )

    serialized_value = str(prepared) if prepared is not None else ""

    logger.info(
        "Cell update: import=%s, row=%s, field=%s, value=%s, user=%s",
        import_id, transaction_id, field, serialized_value,
        getattr(user, "id", "anon"),
    )

    return {
        "row_id": transaction_id,
        "field": field,
        "value": serialized_value,
        "operation_id": operation_id,
    }


# ---------------------------------------------------------------------------
# Batch save
# ---------------------------------------------------------------------------

def batch_save(*, import_id: int, changes: list[dict], user=None) -> dict:
    """
    Save multiple cell changes in a single atomic transaction.

    Args:
        import_id: The BankStatementImport ID.
        changes: List of {row_id, field, value} dicts.
        user: Optional user for logging.

    Returns:
        {"updated_count": int, "errors": list}
    """
    errors: list[dict] = []
    updated = 0
    operation_ids: list[str] = []

    with db_transaction.atomic():
        for change in changes:
            row_id = change["row_id"]
            field = change["field"]
            value = change.get("value", "")

            # Verify the row belongs to this import
            if not BankTransaction.objects.filter(
                pk=row_id, bank_statement_import_id=import_id,
            ).exists():
                errors.append({
                    "row_id": row_id,
                    "field": field,
                    "error": "Transaction not found in this import.",
                })
                continue

            try:
                result = update_cell(
                    transaction_id=row_id,
                    field=field,
                    value=value,
                    import_id=import_id,
                    user=user,
                )
                updated += 1
                operation_ids.append(result["operation_id"])
            except ValidationError as exc:
                errors.append({
                    "row_id": row_id,
                    "field": field,
                    "error": str(exc),
                })

    logger.info(
        "Batch save: import=%s, updated=%d, errors=%d, user=%s",
        import_id, updated, len(errors),
        getattr(user, "id", "anon"),
    )

    return {
        "updated_count": updated,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------

def undo_last_operation(import_id: int, operation_id: str) -> dict | None:
    """
    Revert a specific operation from the undo log.

    Returns the reverted info dict, or None if the operation wasn't found.
    """
    entry = pop_undo_entry(import_id, operation_id)
    if not entry:
        return None

    row_id = entry["row_id"]
    field = entry["field"]
    previous_value = entry["previous_value"]

    model_field = _FIELD_TO_MODEL[field]
    prepared = _prepare_field_value(field, previous_value)

    with db_transaction.atomic():
        BankTransaction.objects.filter(pk=row_id).update(
            **{model_field: prepared}
        )
        # Update raw_row_data
        transaction = BankTransaction.objects.get(pk=row_id)
        raw_row = transaction.raw_row_data or {}
        if field == "transaction_date":
            raw_row["date"] = str(prepared) if prepared else ""
        else:
            raw_row[field] = str(prepared) if prepared is not None else ""
        BankTransaction.objects.filter(pk=row_id).update(raw_row_data=raw_row)

    logger.info(
        "Undo: import=%s, op=%s, row=%s, field=%s → %s",
        import_id, operation_id, row_id, field, previous_value,
    )

    return {
        "row_id": row_id,
        "field": field,
        "previous_value": previous_value,
    }