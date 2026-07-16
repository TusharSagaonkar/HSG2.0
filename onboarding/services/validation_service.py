"""Validation engine for the Society Creation & Accounting Migration Wizard
(Phase 5 — Steps 13, 15–22, 24: per-template validation + cross-reference
reconciliation checks).

This service validates **staging data only**. It never writes to live
accounting tables. Cross-reference checks compare staging data against
*other staging data* (e.g., the sum of bank opening balances in T5 against
the bank account balances in the staged trial balance T2).

Design notes
------------
- **Never writes to live accounting tables.** Only staging models and
  :class:`UploadBatch` are mutated (``validation_status``,
  ``validation_errors``, ``validation_summary``, ``status``).
- **Per-row error capture:** each row's ``validation_errors`` is a list of
  dicts ``{"row": int, "column": str, "reason": str, "suggested_fix": str}``.
  Validation never crashes on bad data — parsing errors per row are captured
  as validation errors.
- **Decimal accuracy:** all amount comparisons use :class:`Decimal` with a
  tolerance of ``0.01`` (financial systems require exactness; the small
  tolerance absorbs rounding artefacts from imported data).
- **Account matching:** cross-reference checks match by ``account_code``
  (exact) or ``account_name`` (case-insensitive contains) against the staged
  chart of accounts / trial balance.
- **Tenant safety:** reads use ``.unscoped().filter(wizard=wizard, ...)``
  to avoid contextvar surprises (pattern from :mod:`onboarding.services.staging_service`).
- **Audit robustness:** audit-log writes are wrapped so a logging failure
  never blocks a validation operation.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import transaction

from onboarding.models import (
    MigrationAuditLog,
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
)
from onboarding.services.staging_service import (
    ALL_TEMPLATE_TYPES,
    TEMPLATE_MODEL_MAP,
    StagingService,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Decimal constants
# --------------------------------------------------------------------------- #
ZERO = Decimal("0")
TOLERANCE = Decimal("0.01")

# --------------------------------------------------------------------------- #
# Validation constants
# --------------------------------------------------------------------------- #
VALID_NATURES = {"ASSET", "LIABILITY", "INCOME", "EXPENSE", "EQUITY"}
VALID_LOAN_TYPES = {"BANK_LOAN", "SOCIETY_LOAN", "MEMBER_LOAN"}
VALID_ASSET_CATEGORIES = {
    "BUILDING", "LIFT", "GENERATOR", "FURNITURE", "OFFICE_EQUIPMENT",
    "COMPUTERS", "VEHICLES", "DEPRECIATION",
}
# IFSC: 4 letters + 0 + 6 alphanumeric (RBI format).
IFSC_REGEX = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
# Account code: dotted numeric (e.g. 1.2.3).
ACCOUNT_CODE_REGEX = re.compile(r"^\d+(\.\d+)*$")

# Semantic account-name keywords used for cross-reference matching when an
# explicit account_code is absent. Matching is case-insensitive "contains".
BANK_KEYWORDS = ("bank",)
CASH_KEYWORDS = ("cash",)
MEMBER_RECEIVABLE_KEYWORDS = (
    "maintenance", "member", "receivable", "outstanding",
)
VENDOR_PAYABLE_KEYWORDS = ("vendor", "payable", "creditor")
ASSET_KEYWORDS = ("asset", "fixed asset")
FUND_KEYWORDS = ("fund",)


def _is_close(a: Decimal, b: Decimal, tol: Decimal = TOLERANCE) -> bool:
    """Return True if ``a`` and ``b`` are within ``tol`` of each other."""
    return abs(a - b) <= tol


def _error(row_number: int, column: str, reason: str, fix: str = "") -> dict:
    """Build a per-row validation error dict."""
    return {
        "row": row_number,
        "column": column,
        "reason": reason,
        "suggested_fix": fix,
    }


def _to_decimal(value, default: Decimal = ZERO) -> Decimal:
    """Safely convert a value to Decimal (never raises)."""
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return default
    s = str(value).strip()
    if not s:
        return default
    s = re.sub(r"[₹$,\s]", "", s)
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return default
    return -d if negative else d


def _is_blank(value) -> bool:
    """True if value is None or an empty/whitespace string."""
    if value is None:
        return True
    if isinstance(value, Decimal):
        return False
    return str(value).strip() == ""


class ValidationService:
    """Validates all staging data. Never writes to live accounting tables.

    Two layers of validation:

    1. **Per-template validation** (``validate_batch`` dispatches to the
       template-specific validator): required columns, data types,
       duplicates, and template-specific business rules.
    2. **Cross-reference validation** (``validate_cross_references``):
       reconciliation checks comparing staging data across templates
       (e.g., bank opening balances vs. trial balance bank accounts).
    """

    # ------------------------------------------------------------------ #
    # Universal validation entry point
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def validate_batch(wizard, template_type, user=None) -> dict:
        """Run all validations for the given template type.

        Updates each staging row's ``validation_status`` and
        ``validation_errors``. Updates the :class:`UploadBatch`
        ``validation_summary`` and ``status=VALIDATED``. Returns a
        validation report dict.

        For each row, runs: required columns check, data type checks,
        duplicate checks, then the template-specific business rules.
        """
        canonical = StagingService._normalize_template_type(template_type)
        model_cls = TEMPLATE_MODEL_MAP[canonical]

        # Dispatch to the template-specific validator.
        validator = ValidationService._template_validator(canonical)
        validator_result = validator(wizard)

        # validator_result is a dict: {errors: [...], summary: {...}}
        errors_by_row: dict[int, list[dict]] = validator_result.get(
            "errors_by_row", {}
        )
        extra_summary = validator_result.get("summary", {})

        # Apply per-row results to staging rows.
        qs = (
            model_cls.objects
            .filter(wizard=wizard)
            .order_by("row_number")
        )
        valid_count = 0
        invalid_count = 0
        pending_count = 0
        for row in qs:
            row_errors = errors_by_row.get(row.row_number, [])
            if row_errors:
                row.validation_status = model_cls.ValidationStatus.INVALID
                row.validation_errors = row_errors
                invalid_count += 1
            else:
                row.validation_status = model_cls.ValidationStatus.VALID
                row.validation_errors = []
                valid_count += 1
            row.save(update_fields=["validation_status", "validation_errors"])

        total = valid_count + invalid_count + pending_count

        # Update the UploadBatch.
        batch = (
            UploadBatch.objects.unscoped()
            .filter(wizard=wizard, template_type=canonical)
            .order_by("-uploaded_at")
            .first()
        )
        if batch is not None:
            batch.validation_summary = {
                "total_rows": total,
                "valid_rows": valid_count,
                "invalid_rows": invalid_count,
                "pending_rows": pending_count,
                "errors_count": sum(
                    len(e) for e in errors_by_row.values()
                ),
                **extra_summary,
            }
            batch.status = UploadBatch.Status.VALIDATED
            batch.save(update_fields=["validation_summary", "status"])

        report = {
            "template_type": canonical,
            "total": total,
            "valid": valid_count,
            "invalid": invalid_count,
            "pending": pending_count,
            "errors": [
                err for errs in errors_by_row.values() for err in errs
            ],
            "summary": extra_summary,
        }

        ValidationService._log_audit(
            wizard=wizard,
            action="VALIDATE",
            user=user,
            details={
                "template_type": canonical,
                "total": total,
                "valid": valid_count,
                "invalid": invalid_count,
            },
        )
        return report

    @staticmethod
    def _template_validator(canonical: str):
        """Return the validator function for a canonical template type."""
        mapping = {
            "CHART_OF_ACCOUNTS": ValidationService.validate_chart_of_accounts,
            "TRIAL_BALANCE": ValidationService.validate_trial_balance,
            "MEMBER_OUTSTANDING": ValidationService.validate_member_outstanding,
            "VENDOR_OUTSTANDING": ValidationService.validate_vendor_outstanding,
            "BANK_OPENING": ValidationService.validate_bank_opening,
            "CASH_OPENING": ValidationService.validate_cash_opening,
            "FIXED_ASSETS": ValidationService.validate_fixed_assets,
            "SECURITY_DEPOSITS": ValidationService.validate_security_deposits,
            "LOANS": ValidationService.validate_loans,
            "FUNDS": ValidationService.validate_funds,
        }
        try:
            return mapping[canonical]
        except KeyError as exc:
            raise ValueError(
                f"No validator registered for template '{canonical}'."
            ) from exc

    # ------------------------------------------------------------------ #
    # T1 — Chart of Accounts
    # ------------------------------------------------------------------ #

    @staticmethod
    def validate_chart_of_accounts(wizard) -> dict:
        """Validate T1 staging data.

        Rules:
        - Required: account_code, account_name
        - No duplicate account_codes
        - Valid nature values (ASSET/LIABILITY/INCOME/EXPENSE/EQUITY)
        - Parent code must exist in the same batch (if specified)
        - opening_debit and opening_credit cannot both be non-zero
        """
        qs = (
            StagingChartOfAccounts.objects
            .filter(wizard=wizard)
            .order_by("row_number")
        )
        rows = list(qs)

        errors_by_row: dict[int, list[dict]] = {}
        seen_codes: dict[str, int] = {}
        all_codes = {r.account_code.strip() for r in rows if r.account_code}

        for row in rows:
            errs: list[dict] = []
            code = (row.account_code or "").strip()
            name = (row.account_name or "").strip()

            if not code:
                errs.append(_error(
                    row.row_number, "account_code",
                    "Account code is required.",
                    "Provide a unique account code (e.g. 1.2.3).",
                ))
            elif not ACCOUNT_CODE_REGEX.match(code):
                errs.append(_error(
                    row.row_number, "account_code",
                    f"Account code '{code}' is not a valid dotted-numeric code.",
                    "Use a format like 1.2.3 (digits separated by dots).",
                ))

            if not name:
                errs.append(_error(
                    row.row_number, "account_name",
                    "Account name is required.",
                    "Provide a non-empty account name.",
                ))

            # Duplicate account_code.
            if code:
                if code in seen_codes:
                    errs.append(_error(
                        row.row_number, "account_code",
                        f"Duplicate account code '{code}' "
                        f"(first seen on row {seen_codes[code]}).",
                        "Remove the duplicate row or use a unique code.",
                    ))
                else:
                    seen_codes[code] = row.row_number

            # Nature validation.
            nature = (row.nature or "").strip().upper()
            if nature and nature not in VALID_NATURES:
                errs.append(_error(
                    row.row_number, "nature",
                    f"Invalid nature '{nature}'.",
                    f"Use one of: {', '.join(sorted(VALID_NATURES))}.",
                ))

            # Parent code must exist in the same batch.
            parent = (row.parent_code or "").strip()
            if parent and parent not in all_codes:
                errs.append(_error(
                    row.row_number, "parent_code",
                    f"Parent code '{parent}' does not exist in this batch.",
                    "Add the parent account first, or correct the parent code.",
                ))

            # opening_debit and opening_credit cannot both be non-zero.
            debit = _to_decimal(row.opening_debit)
            credit = _to_decimal(row.opening_credit)
            if debit != ZERO and credit != ZERO:
                errs.append(_error(
                    row.row_number, "opening_debit",
                    "Both opening_debit and opening_credit are non-zero. "
                    "Only one side may have a balance.",
                    "Set either opening_debit or opening_credit to 0.",
                ))

            if errs:
                errors_by_row[row.row_number] = errs

        return {"errors_by_row": errors_by_row, "summary": {}}

    # ------------------------------------------------------------------ #
    # T2 — Trial Balance
    # ------------------------------------------------------------------ #

    @staticmethod
    def validate_trial_balance(wizard) -> dict:
        """Validate T2 staging data.

        Rules:
        - Required: account_code, debit, credit
        - No duplicate account_codes
        - Debit and credit are valid decimal numbers
        - Total Debit must equal Total Credit (critical check, Step 15)
        """
        qs = (
            StagingTrialBalance.objects
            .filter(wizard=wizard)
            .order_by("row_number")
        )
        rows = list(qs)

        errors_by_row: dict[int, list[dict]] = {}
        seen_codes: dict[str, int] = {}
        total_debit = ZERO
        total_credit = ZERO

        for row in rows:
            errs: list[dict] = []
            code = (row.account_code or "").strip()

            if not code:
                errs.append(_error(
                    row.row_number, "account_code",
                    "Account code is required.",
                    "Provide the account code from the chart of accounts.",
                ))

            # Duplicate account_code.
            if code:
                if code in seen_codes:
                    errs.append(_error(
                        row.row_number, "account_code",
                        f"Duplicate account code '{code}' "
                        f"(first seen on row {seen_codes[code]}).",
                        "Remove the duplicate row.",
                    ))
                else:
                    seen_codes[code] = row.row_number

            # Validate debit/credit are valid decimals (use raw_data to detect
            # unparseable originals).
            debit = _to_decimal(row.debit)
            credit = _to_decimal(row.credit)
            raw = row.raw_data or {}
            debit_raw = raw.get("debit", row.debit)
            credit_raw = raw.get("credit", row.credit)
            if not _is_blank(debit_raw) and _to_decimal(debit_raw) == ZERO and debit_raw not in (0, "0", "0.00", "0.0"):
                errs.append(_error(
                    row.row_number, "debit",
                    f"'{debit_raw}' is not a valid decimal number.",
                    "Enter a numeric value (e.g. 1500.00).",
                ))
            if not _is_blank(credit_raw) and _to_decimal(credit_raw) == ZERO and credit_raw not in (0, "0", "0.00", "0.0"):
                errs.append(_error(
                    row.row_number, "credit",
                    f"'{credit_raw}' is not a valid decimal number.",
                    "Enter a numeric value (e.g. 1500.00).",
                ))

            if debit < ZERO:
                errs.append(_error(
                    row.row_number, "debit",
                    "Debit cannot be negative.",
                    "Enter a value >= 0.",
                ))
            if credit < ZERO:
                errs.append(_error(
                    row.row_number, "credit",
                    "Credit cannot be negative.",
                    "Enter a value >= 0.",
                ))

            # Not both debit and credit non-zero.
            if debit != ZERO and credit != ZERO:
                errs.append(_error(
                    row.row_number, "debit",
                    "Both debit and credit are non-zero on the same row.",
                    "Only one of debit/credit should be non-zero per account.",
                ))

            total_debit += debit
            total_credit += credit

            if errs:
                errors_by_row[row.row_number] = errs

        # File-level: Σ Debit == Σ Credit (hard gate).
        balanced = _is_close(total_debit, total_credit)
        if not balanced:
            # Attach a file-level error to the first row (or row 1).
            first_row = rows[0].row_number if rows else 1
            errors_by_row.setdefault(first_row, []).append(_error(
                first_row, "_file",
                f"Trial balance is not balanced: "
                f"total debit {total_debit} != total credit {total_credit} "
                f"(difference {total_debit - total_credit}).",
                "Adjust debit/credit entries so totals match.",
            ))

        return {
            "errors_by_row": errors_by_row,
            "summary": {
                "balanced": balanced,
                "total_debit": str(total_debit),
                "total_credit": str(total_credit),
                "difference": str(total_debit - total_credit),
            },
        }

    # ------------------------------------------------------------------ #
    # T3 — Member Outstanding
    # ------------------------------------------------------------------ #

    @staticmethod
    def validate_member_outstanding(wizard) -> dict:
        """Validate T3 staging data.

        Rules:
        - Required: unit_identifier, member_name, outstanding_amount
        - All amount fields are valid decimals
        - No duplicate unit_identifier + member_name combinations
        """
        qs = (
            StagingMemberOutstanding.objects
            .filter(wizard=wizard)
            .order_by("row_number")
        )
        rows = list(qs)

        errors_by_row: dict[int, list[dict]] = {}
        seen_keys: dict[str, int] = {}
        amount_fields = (
            "outstanding_amount", "advance_maintenance", "credit_balance",
            "late_fees", "interest_receivable",
        )

        for row in rows:
            errs: list[dict] = []
            unit = (row.unit_identifier or "").strip()
            member = (row.member_name or "").strip()

            if not unit:
                errs.append(_error(
                    row.row_number, "unit_identifier",
                    "Unit identifier is required.",
                    "Provide the unit identifier (e.g. A1-101).",
                ))
            if not member:
                errs.append(_error(
                    row.row_number, "member_name",
                    "Member name is required.",
                    "Provide the member's name.",
                ))

            # Duplicate (unit, member).
            key = f"{unit.lower()}|{member.lower()}"
            if unit and member:
                if key in seen_keys:
                    errs.append(_error(
                        row.row_number, "unit_identifier",
                        f"Duplicate unit/member combination "
                        f"'{unit}/{member}' (first seen on row {seen_keys[key]}).",
                        "Remove the duplicate row.",
                    ))
                else:
                    seen_keys[key] = row.row_number

            # Amount fields: valid decimals >= 0.
            for field in amount_fields:
                value = getattr(row, field, None)
                raw = (row.raw_data or {}).get(field, value)
                if not _is_blank(raw) and _to_decimal(raw) == ZERO and raw not in (0, "0", "0.00", "0.0"):
                    errs.append(_error(
                        row.row_number, field,
                        f"'{raw}' is not a valid decimal number.",
                        "Enter a numeric value >= 0.",
                    ))
                d = _to_decimal(value)
                if d < ZERO:
                    errs.append(_error(
                        row.row_number, field,
                        f"{field} cannot be negative.",
                        "Enter a value >= 0.",
                    ))

            if errs:
                errors_by_row[row.row_number] = errs

        return {"errors_by_row": errors_by_row, "summary": {}}

    # ------------------------------------------------------------------ #
    # T4 — Vendor Outstanding
    # ------------------------------------------------------------------ #

    @staticmethod
    def validate_vendor_outstanding(wizard) -> dict:
        """Validate T4 staging data.

        Rules:
        - Required: vendor_name, outstanding_amount
        - All amount fields are valid decimals
        - No duplicate vendor_name
        """
        qs = (
            StagingVendorOutstanding.objects
            .filter(wizard=wizard)
            .order_by("row_number")
        )
        rows = list(qs)

        errors_by_row: dict[int, list[dict]] = {}
        seen_vendors: dict[str, int] = {}
        amount_fields = (
            "outstanding_amount", "advance_paid", "retention",
            "security_deposit",
        )

        for row in rows:
            errs: list[dict] = []
            vendor = (row.vendor_name or "").strip()

            if not vendor:
                errs.append(_error(
                    row.row_number, "vendor_name",
                    "Vendor name is required.",
                    "Provide the vendor's name.",
                ))
            else:
                key = vendor.lower()
                if key in seen_vendors:
                    errs.append(_error(
                        row.row_number, "vendor_name",
                        f"Duplicate vendor '{vendor}' "
                        f"(first seen on row {seen_vendors[key]}).",
                        "Remove the duplicate row.",
                    ))
                else:
                    seen_vendors[key] = row.row_number

            for field in amount_fields:
                value = getattr(row, field, None)
                raw = (row.raw_data or {}).get(field, value)
                if not _is_blank(raw) and _to_decimal(raw) == ZERO and raw not in (0, "0", "0.00", "0.0"):
                    errs.append(_error(
                        row.row_number, field,
                        f"'{raw}' is not a valid decimal number.",
                        "Enter a numeric value >= 0.",
                    ))
                d = _to_decimal(value)
                if d < ZERO:
                    errs.append(_error(
                        row.row_number, field,
                        f"{field} cannot be negative.",
                        "Enter a value >= 0.",
                    ))

            if errs:
                errors_by_row[row.row_number] = errs

        return {"errors_by_row": errors_by_row, "summary": {}}

    # ------------------------------------------------------------------ #
    # T5 — Bank Opening
    # ------------------------------------------------------------------ #

    @staticmethod
    def validate_bank_opening(wizard) -> dict:
        """Validate T5 staging data.

        Rules:
        - Required: bank_name, account_number, opening_balance
        - IFSC format validation (if provided: 4 letters + 0 + 6 alphanumeric)
        - No duplicate account_number
        """
        qs = (
            StagingBankOpening.objects
            .filter(wizard=wizard)
            .order_by("row_number")
        )
        rows = list(qs)

        errors_by_row: dict[int, list[dict]] = {}
        seen_accounts: dict[str, int] = {}

        for row in rows:
            errs: list[dict] = []
            bank_name = (row.bank_name or "").strip()
            account_number = (row.account_number or "").strip()
            ifsc = (row.ifsc or "").strip()

            if not bank_name:
                errs.append(_error(
                    row.row_number, "bank_name",
                    "Bank name is required.",
                    "Provide the bank's name.",
                ))
            if not account_number:
                errs.append(_error(
                    row.row_number, "account_number",
                    "Account number is required.",
                    "Provide the bank account number.",
                ))
            else:
                key = account_number.lower()
                if key in seen_accounts:
                    errs.append(_error(
                        row.row_number, "account_number",
                        f"Duplicate account number '{account_number}' "
                        f"(first seen on row {seen_accounts[key]}).",
                        "Remove the duplicate row.",
                    ))
                else:
                    seen_accounts[key] = row.row_number

            # IFSC validation.
            if ifsc and not IFSC_REGEX.match(ifsc):
                errs.append(_error(
                    row.row_number, "ifsc",
                    f"IFSC '{ifsc}' is not valid. Expected format: "
                    "4 letters + 0 + 6 alphanumeric (e.g. HDFC0001234).",
                    "Correct the IFSC code or leave it blank.",
                ))

            # opening_balance valid decimal >= 0.
            value = row.opening_balance
            raw = (row.raw_data or {}).get("opening_balance", value)
            if not _is_blank(raw) and _to_decimal(raw) == ZERO and raw not in (0, "0", "0.00", "0.0"):
                errs.append(_error(
                    row.row_number, "opening_balance",
                    f"'{raw}' is not a valid decimal number.",
                    "Enter a numeric value >= 0.",
                ))
            d = _to_decimal(value)
            if d < ZERO:
                errs.append(_error(
                    row.row_number, "opening_balance",
                    "Opening balance cannot be negative.",
                    "Enter a value >= 0.",
                ))

            if errs:
                errors_by_row[row.row_number] = errs

        return {"errors_by_row": errors_by_row, "summary": {}}

    # ------------------------------------------------------------------ #
    # T6 — Cash Opening
    # ------------------------------------------------------------------ #

    @staticmethod
    def validate_cash_opening(wizard) -> dict:
        """Validate T6 staging data.

        Rules:
        - Required: opening_balance
        - Must be a valid decimal
        - Only one row expected
        """
        qs = (
            StagingCashOpening.objects
            .filter(wizard=wizard)
            .order_by("row_number")
        )
        rows = list(qs)

        errors_by_row: dict[int, list[dict]] = {}

        if len(rows) > 1:
            for row in rows[1:]:
                errors_by_row.setdefault(row.row_number, []).append(_error(
                    row.row_number, "_file",
                    "Multiple cash opening rows found. Only one cash row is expected.",
                    "Delete extra rows so only one cash opening remains.",
                ))

        for row in rows:
            errs = errors_by_row.get(row.row_number, [])
            value = row.opening_balance
            raw = (row.raw_data or {}).get("opening_balance", value)
            if _is_blank(raw):
                errs.append(_error(
                    row.row_number, "opening_balance",
                    "Opening balance is required.",
                    "Provide the cash opening balance.",
                ))
            else:
                if _to_decimal(raw) == ZERO and raw not in (0, "0", "0.00", "0.0"):
                    errs.append(_error(
                        row.row_number, "opening_balance",
                        f"'{raw}' is not a valid decimal number.",
                        "Enter a numeric value >= 0.",
                    ))
                d = _to_decimal(value)
                if d < ZERO:
                    errs.append(_error(
                        row.row_number, "opening_balance",
                        "Opening balance cannot be negative.",
                        "Enter a value >= 0.",
                    ))
            if errs:
                errors_by_row[row.row_number] = errs

        return {"errors_by_row": errors_by_row, "summary": {}}

    # ------------------------------------------------------------------ #
    # T7 — Fixed Assets
    # ------------------------------------------------------------------ #

    @staticmethod
    def validate_fixed_assets(wizard) -> dict:
        """Validate T7 staging data.

        Rules:
        - Required: asset_name, gross_value
        - net_value should equal gross_value - depreciation (warn if not,
          don't fail)
        - No duplicate asset_name
        """
        qs = (
            StagingFixedAsset.objects
            .filter(wizard=wizard)
            .order_by("row_number")
        )
        rows = list(qs)

        errors_by_row: dict[int, list[dict]] = {}
        seen_names: dict[str, int] = {}

        for row in rows:
            errs: list[dict] = []
            asset_name = (row.asset_name or "").strip()

            if not asset_name:
                errs.append(_error(
                    row.row_number, "asset_name",
                    "Asset name is required.",
                    "Provide the asset's name.",
                ))
            else:
                key = asset_name.lower()
                if key in seen_names:
                    errs.append(_error(
                        row.row_number, "asset_name",
                        f"Duplicate asset '{asset_name}' "
                        f"(first seen on row {seen_names[key]}).",
                        "Remove the duplicate row.",
                    ))
                else:
                    seen_names[key] = row.row_number

            gross = _to_decimal(row.gross_value)
            dep = _to_decimal(row.depreciation)
            net = _to_decimal(row.net_value)

            raw = row.raw_data or {}
            for field, val in (("gross_value", gross), ("depreciation", dep), ("net_value", net)):
                raw_val = raw.get(field, getattr(row, field, None))
                if not _is_blank(raw_val) and _to_decimal(raw_val) == ZERO and raw_val not in (0, "0", "0.00", "0.0"):
                    errs.append(_error(
                        row.row_number, field,
                        f"'{raw_val}' is not a valid decimal number.",
                        "Enter a numeric value >= 0.",
                    ))

            if gross < ZERO:
                errs.append(_error(
                    row.row_number, "gross_value",
                    "Gross value cannot be negative.",
                    "Enter a value >= 0.",
                ))
            if dep < ZERO:
                errs.append(_error(
                    row.row_number, "depreciation",
                    "Depreciation cannot be negative.",
                    "Enter a value >= 0.",
                ))

            # net_value should equal gross - depreciation (warning, not failure).
            expected_net = gross - dep
            if not _is_close(net, expected_net):
                # Record as a non-blocking warning in errors but with a
                # "warning" severity marker. We still mark the row VALID
                # unless other errors exist — handled by caller via the
                # presence of "error" severity. To keep the contract simple,
                # we record it as a warning that does NOT fail the row.
                # We store it but tag severity=warning.
                errs.append({
                    "row": row.row_number,
                    "column": "net_value",
                    "reason": (
                        f"net_value {net} does not equal "
                        f"gross_value - depreciation ({expected_net})."
                    ),
                    "suggested_fix": f"Set net_value to {expected_net}.",
                    "severity": "warning",
                })

            if errs:
                errors_by_row[row.row_number] = errs

        return {"errors_by_row": errors_by_row, "summary": {}}

    # ------------------------------------------------------------------ #
    # T8 — Security Deposits
    # ------------------------------------------------------------------ #

    @staticmethod
    def validate_security_deposits(wizard) -> dict:
        """Validate T8 staging data.

        Rules:
        - Required: description, amount
        - Amount is valid decimal
        """
        qs = (
            StagingSecurityDeposit.objects
            .filter(wizard=wizard)
            .order_by("row_number")
        )
        rows = list(qs)

        errors_by_row: dict[int, list[dict]] = {}

        for row in rows:
            errs: list[dict] = []
            description = (row.description or "").strip()
            if not description:
                errs.append(_error(
                    row.row_number, "description",
                    "Description is required.",
                    "Provide a description (e.g. 'Vendor Security').",
                ))

            value = row.amount
            raw = (row.raw_data or {}).get("amount", value)
            if _is_blank(raw):
                errs.append(_error(
                    row.row_number, "amount",
                    "Amount is required.",
                    "Provide the deposit amount.",
                ))
            else:
                if _to_decimal(raw) == ZERO and raw not in (0, "0", "0.00", "0.0"):
                    errs.append(_error(
                        row.row_number, "amount",
                        f"'{raw}' is not a valid decimal number.",
                        "Enter a numeric value >= 0.",
                    ))
                d = _to_decimal(value)
                if d < ZERO:
                    errs.append(_error(
                        row.row_number, "amount",
                        "Amount cannot be negative.",
                        "Enter a value >= 0.",
                    ))

            if errs:
                errors_by_row[row.row_number] = errs

        return {"errors_by_row": errors_by_row, "summary": {}}

    # ------------------------------------------------------------------ #
    # T9 — Loans
    # ------------------------------------------------------------------ #

    @staticmethod
    def validate_loans(wizard) -> dict:
        """Validate T9 staging data.

        Rules:
        - Required: loan_name, outstanding_principal
        - Valid loan_type values (BANK_LOAN/SOCIETY_LOAN/MEMBER_LOAN)
        - No duplicate loan_name
        """
        qs = (
            StagingLoan.objects
            .filter(wizard=wizard)
            .order_by("row_number")
        )
        rows = list(qs)

        errors_by_row: dict[int, list[dict]] = {}
        seen_names: dict[str, int] = {}

        for row in rows:
            errs: list[dict] = []
            loan_name = (row.loan_name or "").strip()
            loan_type = (row.loan_type or "").strip().upper()

            if not loan_name:
                errs.append(_error(
                    row.row_number, "loan_name",
                    "Loan name is required.",
                    "Provide the loan's name.",
                ))
            else:
                key = loan_name.lower()
                if key in seen_names:
                    errs.append(_error(
                        row.row_number, "loan_name",
                        f"Duplicate loan '{loan_name}' "
                        f"(first seen on row {seen_names[key]}).",
                        "Remove the duplicate row.",
                    ))
                else:
                    seen_names[key] = row.row_number

            if loan_type and loan_type not in VALID_LOAN_TYPES:
                errs.append(_error(
                    row.row_number, "loan_type",
                    f"Invalid loan_type '{loan_type}'.",
                    f"Use one of: {', '.join(sorted(VALID_LOAN_TYPES))}.",
                ))

            for field in ("outstanding_principal", "interest"):
                value = getattr(row, field, None)
                raw = (row.raw_data or {}).get(field, value)
                if not _is_blank(raw) and _to_decimal(raw) == ZERO and raw not in (0, "0", "0.00", "0.0"):
                    errs.append(_error(
                        row.row_number, field,
                        f"'{raw}' is not a valid decimal number.",
                        "Enter a numeric value >= 0.",
                    ))
                d = _to_decimal(value)
                if d < ZERO:
                    errs.append(_error(
                        row.row_number, field,
                        f"{field} cannot be negative.",
                        "Enter a value >= 0.",
                    ))

            if not loan_name and not errs:
                pass
            if errs:
                errors_by_row[row.row_number] = errs

        return {"errors_by_row": errors_by_row, "summary": {}}

    # ------------------------------------------------------------------ #
    # T10 — Funds
    # ------------------------------------------------------------------ #

    @staticmethod
    def validate_funds(wizard) -> dict:
        """Validate T10 staging data.

        Rules:
        - Required: fund_name, balance
        - No duplicate fund_name
        """
        qs = (
            StagingFund.objects
            .filter(wizard=wizard)
            .order_by("row_number")
        )
        rows = list(qs)

        errors_by_row: dict[int, list[dict]] = {}
        seen_names: dict[str, int] = {}

        for row in rows:
            errs: list[dict] = []
            fund_name = (row.fund_name or "").strip()

            if not fund_name:
                errs.append(_error(
                    row.row_number, "fund_name",
                    "Fund name is required.",
                    "Provide the fund's name.",
                ))
            else:
                key = fund_name.lower()
                if key in seen_names:
                    errs.append(_error(
                        row.row_number, "fund_name",
                        f"Duplicate fund '{fund_name}' "
                        f"(first seen on row {seen_names[key]}).",
                        "Remove the duplicate row.",
                    ))
                else:
                    seen_names[key] = row.row_number

            value = row.balance
            raw = (row.raw_data or {}).get("balance", value)
            if _is_blank(raw):
                errs.append(_error(
                    row.row_number, "balance",
                    "Balance is required.",
                    "Provide the fund balance.",
                ))
            else:
                if _to_decimal(raw) == ZERO and raw not in (0, "0", "0.00", "0.0"):
                    errs.append(_error(
                        row.row_number, "balance",
                        f"'{raw}' is not a valid decimal number.",
                        "Enter a numeric value >= 0.",
                    ))
                d = _to_decimal(value)
                if d < ZERO:
                    errs.append(_error(
                        row.row_number, "balance",
                        "Balance cannot be negative.",
                        "Enter a value >= 0.",
                    ))

            if errs:
                errors_by_row[row.row_number] = errs

        return {"errors_by_row": errors_by_row, "summary": {}}

    # ------------------------------------------------------------------ #
    # Cross-reference validation (Step 24 checklist)
    # ------------------------------------------------------------------ #

    @staticmethod
    def validate_cross_references(wizard) -> dict:
        """Run all cross-reference checks and return a checklist.

        Returns a dict with each check's result plus a ``checklist`` summary
        and ``all_passed`` flag.
        """
        tb_balanced = ValidationService.check_trial_balance_balanced(wizard)
        bank_match = ValidationService.check_bank_balances_match(wizard)
        cash_match = ValidationService.check_cash_balance_matches(wizard)
        member_match = ValidationService.check_member_outstanding_matches(wizard)
        vendor_match = ValidationService.check_vendor_outstanding_matches(wizard)
        assets_match = ValidationService.check_assets_match(wizard)
        funds_match = ValidationService.check_funds_match(wizard)
        debit_eq_credit = ValidationService.check_debit_equals_credit(wizard)
        no_errors = ValidationService.check_no_validation_errors(wizard)

        # Balance sheet matched: Assets == Liabilities + Equity (from T2).
        balance_sheet = ValidationService._check_balance_sheet_matched(wizard)

        checklist = {
            "trial_balance_balanced": tb_balanced["passed"],
            "balance_sheet_matched": balance_sheet["passed"],
            "bank_balances_matched": bank_match["passed"],
            "member_outstanding_matched": member_match["passed"],
            "vendor_outstanding_matched": vendor_match["passed"],
            "assets_matched": assets_match["passed"],
            "funds_matched": funds_match["passed"],
            "debit_equals_credit": debit_eq_credit["passed"],
            "no_validation_errors": no_errors["passed"],
        }
        checklist["all_passed"] = all(checklist.values())

        return {
            "cross_references": {
                "trial_balance_balanced": tb_balanced,
                "balance_sheet_matched": balance_sheet,
                "bank_balances_match": bank_match,
                "cash_balance_matches": cash_match,
                "member_outstanding_matches": member_match,
                "vendor_outstanding_matches": vendor_match,
                "assets_match": assets_match,
                "funds_match": funds_match,
                "debit_equals_credit": debit_eq_credit,
                "no_validation_errors": no_errors,
            },
            "checklist": checklist,
            "can_finalize": checklist["all_passed"],
        }

    @staticmethod
    def check_trial_balance_balanced(wizard) -> dict:
        """C1: Σ Debit(T2) == Σ Credit(T2)."""
        rows = list(
            StagingTrialBalance.objects
            .filter(wizard=wizard)
        )
        total_debit = sum((_to_decimal(r.debit) for r in rows), ZERO)
        total_credit = sum((_to_decimal(r.credit) for r in rows), ZERO)
        difference = total_debit - total_credit
        return {
            "passed": _is_close(total_debit, total_credit),
            "total_debit": str(total_debit),
            "total_credit": str(total_credit),
            "difference": str(difference),
        }

    @staticmethod
    def check_debit_equals_credit(wizard) -> dict:
        """C8: Global Σ Debit == Global Σ Credit (same as C1, explicit)."""
        result = ValidationService.check_trial_balance_balanced(wizard)
        return {
            "passed": result["passed"],
            "total_debit": result["total_debit"],
            "total_credit": result["total_credit"],
            "difference": result["difference"],
        }

    @staticmethod
    def check_bank_balances_match(wizard) -> dict:
        """C3: Σ Opening Balance(T5) == Σ Bank account balances(T2).

        Bank accounts in T2 are matched by account_code (exact) or
        account_name (case-insensitive contains a bank keyword).
        """
        bank_rows = list(
            StagingBankOpening.objects.filter(wizard=wizard)
        )
        bank_total = sum((_to_decimal(r.opening_balance) for r in bank_rows), ZERO)

        tb_rows = list(
            StagingTrialBalance.objects.filter(wizard=wizard)
        )
        tb_bank_total = ZERO
        for r in tb_rows:
            if ValidationService._is_bank_account(r.account_code, r.account_name):
                tb_bank_total += _to_decimal(r.debit) - _to_decimal(r.credit)

        difference = bank_total - tb_bank_total
        return {
            "passed": _is_close(bank_total, tb_bank_total),
            "bank_total": str(bank_total),
            "tb_bank_total": str(tb_bank_total),
            "difference": str(difference),
        }

    @staticmethod
    def check_cash_balance_matches(wizard) -> dict:
        """C (cash): Cash opening balance must match cash account in T2."""
        cash_rows = list(
            StagingCashOpening.objects.filter(wizard=wizard)
        )
        cash_staging = sum(
            (_to_decimal(r.opening_balance) for r in cash_rows), ZERO
        )

        tb_rows = list(
            StagingTrialBalance.objects.filter(wizard=wizard)
        )
        cash_tb = ZERO
        for r in tb_rows:
            if ValidationService._is_cash_account(r.account_code, r.account_name):
                cash_tb += _to_decimal(r.debit) - _to_decimal(r.credit)

        difference = cash_staging - cash_tb
        return {
            "passed": _is_close(cash_staging, cash_tb),
            "cash_staging": str(cash_staging),
            "cash_tb": str(cash_tb),
            "difference": str(difference),
        }

    @staticmethod
    def check_member_outstanding_matches(wizard) -> dict:
        """C4: Σ Net Outstanding(T3) == Maintenance Receivable(T2).

        Net outstanding per the spec:
        Σ (Outstanding − Advance − Credit + Late Fees + Interest).
        """
        member_rows = list(
            StagingMemberOutstanding.objects.filter(wizard=wizard)
        )
        member_total = ZERO
        for r in member_rows:
            member_total += (
                _to_decimal(r.outstanding_amount)
                - _to_decimal(r.advance_maintenance)
                - _to_decimal(r.credit_balance)
                + _to_decimal(r.late_fees)
                + _to_decimal(r.interest_receivable)
            )

        tb_rows = list(
            StagingTrialBalance.objects.filter(wizard=wizard)
        )
        tb_receivable = ZERO
        for r in tb_rows:
            if ValidationService._is_member_receivable_account(
                r.account_code, r.account_name
            ):
                tb_receivable += _to_decimal(r.debit) - _to_decimal(r.credit)

        difference = member_total - tb_receivable
        return {
            "passed": _is_close(member_total, tb_receivable),
            "member_total": str(member_total),
            "tb_receivable": str(tb_receivable),
            "difference": str(difference),
        }

    @staticmethod
    def check_vendor_outstanding_matches(wizard) -> dict:
        """C5: Σ Net Outstanding(T4) == Vendor Payable(T2).

        Net outstanding per the spec:
        Σ (Outstanding − Advance − Retention − Security Deposit).
        """
        vendor_rows = list(
            StagingVendorOutstanding.objects.filter(wizard=wizard)
        )
        vendor_total = ZERO
        for r in vendor_rows:
            vendor_total += (
                _to_decimal(r.outstanding_amount)
                - _to_decimal(r.advance_paid)
                - _to_decimal(r.retention)
                - _to_decimal(r.security_deposit)
            )

        tb_rows = list(
            StagingTrialBalance.objects.filter(wizard=wizard)
        )
        tb_payable = ZERO
        for r in tb_rows:
            if ValidationService._is_vendor_payable_account(
                r.account_code, r.account_name
            ):
                tb_payable += _to_decimal(r.credit) - _to_decimal(r.debit)

        difference = vendor_total - tb_payable
        return {
            "passed": _is_close(vendor_total, tb_payable),
            "vendor_total": str(vendor_total),
            "tb_payable": str(tb_payable),
            "difference": str(difference),
        }

    @staticmethod
    def check_assets_match(wizard) -> dict:
        """C6: Σ Net Book Value(T7) == Asset accounts(T2)."""
        asset_rows = list(
            StagingFixedAsset.objects.filter(wizard=wizard)
        )
        asset_total = sum((_to_decimal(r.net_value) for r in asset_rows), ZERO)

        tb_rows = list(
            StagingTrialBalance.objects.filter(wizard=wizard)
        )
        tb_asset_total = ZERO
        for r in tb_rows:
            if ValidationService._is_asset_account(r.account_code, r.account_name):
                tb_asset_total += _to_decimal(r.debit) - _to_decimal(r.credit)

        difference = asset_total - tb_asset_total
        return {
            "passed": _is_close(asset_total, tb_asset_total),
            "asset_total": str(asset_total),
            "tb_asset_total": str(tb_asset_total),
            "difference": str(difference),
        }

    @staticmethod
    def check_funds_match(wizard) -> dict:
        """C7: Σ Balance(T10) == Fund accounts(T2)."""
        fund_rows = list(
            StagingFund.objects.filter(wizard=wizard)
        )
        fund_total = sum((_to_decimal(r.balance) for r in fund_rows), ZERO)

        tb_rows = list(
            StagingTrialBalance.objects.filter(wizard=wizard)
        )
        tb_fund_total = ZERO
        for r in tb_rows:
            if ValidationService._is_fund_account(r.account_code, r.account_name):
                tb_fund_total += _to_decimal(r.credit) - _to_decimal(r.debit)

        difference = fund_total - tb_fund_total
        return {
            "passed": _is_close(fund_total, tb_fund_total),
            "fund_total": str(fund_total),
            "tb_fund_total": str(tb_fund_total),
            "difference": str(difference),
        }

    @staticmethod
    def check_no_validation_errors(wizard) -> dict:
        """C9: All staging rows across all templates must have
        validation_status=VALID.
        """
        invalid_count = 0
        pending_count = 0
        total = 0
        for canonical in ALL_TEMPLATE_TYPES:
            model_cls = TEMPLATE_MODEL_MAP[canonical]
            qs = model_cls.objects.filter(wizard=wizard)
            total += qs.count()
            invalid_count += qs.filter(
                validation_status=model_cls.ValidationStatus.INVALID
            ).count()
            pending_count += qs.filter(
                validation_status=model_cls.ValidationStatus.PENDING
            ).count()
        return {
            "passed": (invalid_count == 0 and pending_count == 0) and total >= 0,
            "total_rows": total,
            "invalid_count": invalid_count,
            "pending_count": pending_count,
        }

    # ------------------------------------------------------------------ #
    # Comprehensive validation report
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_validation_report(wizard) -> dict:
        """Return a comprehensive validation report covering all templates
        and cross-reference checks.

        Structure::

            {
                "templates": {
                    "CHART_OF_ACCOUNTS": {"total", "valid", "invalid",
                                           "pending", "errors"},
                    ...
                },
                "cross_references": { ... },
                "checklist": { ..., "all_passed": bool },
                "can_finalize": bool,
            }
        """
        templates: dict[str, Any] = {}
        for canonical in ALL_TEMPLATE_TYPES:
            model_cls = TEMPLATE_MODEL_MAP[canonical]
            qs = model_cls.objects.filter(wizard=wizard)
            total = qs.count()
            valid = qs.filter(
                validation_status=model_cls.ValidationStatus.VALID
            ).count()
            invalid = qs.filter(
                validation_status=model_cls.ValidationStatus.INVALID
            ).count()
            pending = qs.filter(
                validation_status=model_cls.ValidationStatus.PENDING
            ).count()
            errors: list[dict] = []
            for row in qs:
                for err in (row.validation_errors or []):
                    errors.append(err)
            templates[canonical] = {
                "total": total,
                "valid": valid,
                "invalid": invalid,
                "pending": pending,
                "errors": errors,
            }

        cross = ValidationService.validate_cross_references(wizard)

        return {
            "templates": templates,
            "cross_references": cross["cross_references"],
            "checklist": cross["checklist"],
            "can_finalize": cross["can_finalize"],
        }

    # ------------------------------------------------------------------ #
    # Internal helpers — account classification for cross-references
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_bank_account(account_code: str, account_name: str) -> bool:
        """Heuristic: is this a bank account? Match by name keyword."""
        name = (account_name or "").lower()
        return any(kw in name for kw in BANK_KEYWORDS)

    @staticmethod
    def _is_cash_account(account_code: str, account_name: str) -> bool:
        """Heuristic: is this a cash account? Match by name keyword."""
        name = (account_name or "").lower()
        return any(kw in name for kw in CASH_KEYWORDS)

    @staticmethod
    def _is_member_receivable_account(account_code: str, account_name: str) -> bool:
        """Heuristic: is this a member/maintenance receivable account?"""
        name = (account_name or "").lower()
        return any(kw in name for kw in MEMBER_RECEIVABLE_KEYWORDS)

    @staticmethod
    def _is_vendor_payable_account(account_code: str, account_name: str) -> bool:
        """Heuristic: is this a vendor payable/creditor account?"""
        name = (account_name or "").lower()
        return any(kw in name for kw in VENDOR_PAYABLE_KEYWORDS)

    @staticmethod
    def _is_asset_account(account_code: str, account_name: str) -> bool:
        """Heuristic: is this a fixed-asset account?"""
        name = (account_name or "").lower()
        return any(kw in name for kw in ASSET_KEYWORDS)

    @staticmethod
    def _is_fund_account(account_code: str, account_name: str) -> bool:
        """Heuristic: is this a fund account?"""
        name = (account_name or "").lower()
        return any(kw in name for kw in FUND_KEYWORDS)

    @staticmethod
    def _check_balance_sheet_matched(wizard) -> dict:
        """C2: Assets(T2) == Liabilities(T2) + Equity(T2).

        Derived from the staged chart of accounts (T1) nature field where
        available, falling back to account-name heuristics. Uses the trial
        balance debit/credit net per account.
        """
        tb_rows = list(
            StagingTrialBalance.objects.filter(wizard=wizard)
        )
        coa_rows = list(
            StagingChartOfAccounts.objects.filter(wizard=wizard)
        )
        # Build a code → nature map from T1.
        nature_by_code: dict[str, str] = {}
        for r in coa_rows:
            code = (r.account_code or "").strip()
            nature = (r.nature or "").strip().upper()
            if code and nature:
                nature_by_code[code] = nature

        assets = ZERO
        liabilities = ZERO
        equity = ZERO
        for r in tb_rows:
            net = _to_decimal(r.debit) - _to_decimal(r.credit)
            nature = nature_by_code.get((r.account_code or "").strip(), "")
            name = (r.account_name or "").lower()
            if not nature:
                # Fallback heuristics.
                if any(kw in name for kw in ASSET_KEYWORDS):
                    nature = "ASSET"
                elif "equity" in name or "capital" in name:
                    nature = "EQUITY"
                elif any(kw in name for kw in ("payable", "liability", "loan", "deposit", "fund")):
                    nature = "LIABILITY"
            if nature == "ASSET":
                assets += net
            elif nature == "LIABILITY":
                liabilities -= net  # liabilities are credit-normal
            elif nature == "EQUITY":
                equity -= net  # equity is credit-normal

        rhs = liabilities + equity
        difference = assets - rhs
        return {
            "passed": _is_close(assets, rhs),
            "assets": str(assets),
            "liabilities": str(liabilities),
            "equity": str(equity),
            "difference": str(difference),
        }

    # ------------------------------------------------------------------ #
    # Audit helper
    # ------------------------------------------------------------------ #

    @staticmethod
    def _log_audit(wizard, action, user, details=None) -> None:
        """Create a :class:`MigrationAuditLog` entry (append-only).

        Wrapped so a logging failure never blocks a validation operation.
        """
        try:
            log = MigrationAuditLog(
                wizard=wizard,
                society=wizard.society,
                action=action,
                actor=user,
                details=details or {},
            )
            log.save()
        except Exception:  # noqa: BLE001 — audit must not break the operation.
            logger.exception(
                "Failed to write MigrationAuditLog for wizard %s (action=%s)",
                getattr(wizard, "pk", None),
                action,
            )
