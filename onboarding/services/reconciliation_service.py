"""Reconciliation dashboard service for the Society Creation & Accounting
Migration Wizard (Phase 6 — Steps 23 & 24: reconciliation dashboard data +
validation checklist).

This service generates **read-only** reconciliation dashboard data from the
staging tables. It **never writes to live accounting tables** — it only
aggregates staging rows into summary dicts for display.

Design notes
------------
- **Never writes to live accounting tables.** Only reads from staging models.
- **All methods are ``@staticmethod``** per the service contract established
  in ``gateops/services/contractor_service.py``.
- **Tenant safety:** reads use ``.unscoped().filter(wizard=wizard, ...)``
  to avoid contextvar surprises (pattern from
  :mod:`onboarding.services.validation_service`).
- **Decimal accuracy:** all amounts use :class:`Decimal` with a tolerance of
  ``0.01`` for comparisons.
- **Checklist delegation:** ``run_checklist`` delegates to
  :meth:`ValidationService.validate_cross_references` which already implements
  the 9 cross-template checks (C1–C9).
- **Audit robustness:** audit-log writes are wrapped so a logging failure
  never blocks a read operation.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

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
)
from onboarding.services.validation_service import ValidationService

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Decimal constants
# --------------------------------------------------------------------------- #
ZERO = Decimal("0")
TOLERANCE = Decimal("0.01")


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
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return default


def _is_close(a: Decimal, b: Decimal, tol: Decimal = TOLERANCE) -> bool:
    """Return True if ``a`` and ``b`` are within ``tol`` of each other."""
    return abs(a - b) <= tol


class ReconciliationService:
    """Generates reconciliation dashboard data (Step 23) and runs the
    validation checklist (Step 24).

    All methods read **only** from staging tables. They never touch live
    accounting models (``Voucher``, ``LedgerEntry``, ``Account``).
    """

    # ------------------------------------------------------------------ #
    # Step 23 — Reconciliation Dashboard sections
    # ------------------------------------------------------------------ #

    @staticmethod
    def generate_trial_balance(*, wizard, society) -> dict:
        """Aggregate :class:`StagingTrialBalance` rows into a trial balance
        summary.

        Returns::

            {
                "rows": [
                    {"account_code", "account_name", "debit", "credit"},
                    ...
                ],
                "total_debit": "12345.00",
                "total_credit": "12345.00",
                "is_balanced": True,
                "row_count": 42,
            }
        """
        rows_qs = (
            StagingTrialBalance.objects.unscoped()
            .filter(wizard=wizard, society=society)
            .order_by("row_number")
        )

        rows: list[dict[str, Any]] = []
        total_debit = ZERO
        total_credit = ZERO

        for r in rows_qs:
            debit = _to_decimal(r.debit)
            credit = _to_decimal(r.credit)
            total_debit += debit
            total_credit += credit
            rows.append({
                "account_code": r.account_code,
                "account_name": r.account_name,
                "debit": str(debit),
                "credit": str(credit),
            })

        return {
            "rows": rows,
            "total_debit": str(total_debit),
            "total_credit": str(total_credit),
            "is_balanced": _is_close(total_debit, total_credit),
            "row_count": len(rows),
        }

    @staticmethod
    def generate_balance_sheet(*, wizard, society) -> dict:
        """Derive a balance sheet from staged T2 (trial balance) + T7 (fixed
        assets) + T8 (security deposits) + T9 (loans) + T10 (funds).

        Uses the staged chart of accounts (T1) ``nature`` field to classify
        each trial-balance row as ASSET, LIABILITY, or EQUITY. Falls back to
        account-name heuristics when nature is absent.

        Returns::

            {
                "assets": [{"account_code", "account_name", "amount"}, ...],
                "liabilities": [...],
                "equity": [...],
                "totals": {
                    "total_assets": "...",
                    "total_liabilities": "...",
                    "total_equity": "...",
                    "is_balanced": True,
                },
            }
        """
        tb_rows = list(
            StagingTrialBalance.objects.unscoped()
            .filter(wizard=wizard, society=society)
            .order_by("row_number")
        )
        coa_rows = list(
            StagingChartOfAccounts.objects.unscoped()
            .filter(wizard=wizard, society=society)
        )

        # Build code → nature map from T1.
        nature_by_code: dict[str, str] = {}
        for r in coa_rows:
            code = (r.account_code or "").strip()
            nature = (r.nature or "").strip().upper()
            if nature == "GENERAL":
                nature = ""
            if code and nature:
                nature_by_code[code] = nature

        assets: list[dict[str, Any]] = []
        liabilities: list[dict[str, Any]] = []
        equity: list[dict[str, Any]] = []

        total_assets = ZERO
        total_liabilities = ZERO
        total_equity = ZERO

        for r in tb_rows:
            debit = _to_decimal(r.debit)
            credit = _to_decimal(r.credit)
            net = debit - credit
            if _is_close(net, ZERO):
                continue

            code = (r.account_code or "").strip()
            name = (r.account_name or "")
            nature = nature_by_code.get(code, "")

            if not nature:
                name_l = name.lower()
                if any(kw in name_l for kw in ("asset", "fixed asset", "bank", "cash", "receivable", "deposit", "advance")):
                    nature = "ASSET"
                elif any(kw in name_l for kw in ("equity", "capital", "fund", "reserve", "surplus")):
                    nature = "EQUITY"
                elif any(kw in name_l for kw in ("payable", "liability", "loan", "tax", "provision")):
                    nature = "LIABILITY"

            entry = {
                "account_code": code,
                "account_name": name,
                "amount": str(abs(net)),
            }

            if nature == "ASSET":
                assets.append(entry)
                total_assets += net
            elif nature == "LIABILITY":
                liabilities.append(entry)
                total_liabilities -= net  # liabilities are credit-normal
            elif nature == "EQUITY":
                equity.append(entry)
                total_equity -= net  # equity is credit-normal

        rhs = total_liabilities + total_equity

        return {
            "assets": assets,
            "liabilities": liabilities,
            "equity": equity,
            "totals": {
                "total_assets": str(total_assets),
                "total_liabilities": str(total_liabilities),
                "total_equity": str(total_equity),
                "is_balanced": _is_close(total_assets, rhs),
                "difference": str(total_assets - rhs),
            },
        }

    @staticmethod
    def generate_member_summary(*, wizard, society) -> dict:
        """Aggregate :class:`StagingMemberOutstanding` rows into a member
        outstanding summary.

        Returns::

            {
                "rows": [
                    {"unit_identifier", "member_name", "outstanding_amount",
                     "advance_maintenance", "credit_balance", "late_fees",
                     "interest_receivable", "net_outstanding"},
                    ...
                ],
                "total_outstanding": "...",
                "total_advance": "...",
                "total_credit": "...",
                "total_late_fees": "...",
                "total_interest_receivable": "...",
                "total_net_outstanding": "...",
                "row_count": 42,
            }
        """
        rows_qs = (
            StagingMemberOutstanding.objects.unscoped()
            .filter(wizard=wizard, society=society)
            .order_by("row_number")
        )

        rows: list[dict[str, Any]] = []
        total_outstanding = ZERO
        total_advance = ZERO
        total_credit = ZERO
        total_late_fees = ZERO
        total_interest = ZERO
        total_net = ZERO

        for r in rows_qs:
            outstanding = _to_decimal(r.outstanding_amount)
            advance = _to_decimal(r.advance_maintenance)
            credit = _to_decimal(r.credit_balance)
            late_fees = _to_decimal(r.late_fees)
            interest = _to_decimal(r.interest_receivable)
            net = outstanding - advance - credit + late_fees + interest

            total_outstanding += outstanding
            total_advance += advance
            total_credit += credit
            total_late_fees += late_fees
            total_interest += interest
            total_net += net

            rows.append({
                "unit_identifier": r.unit_identifier,
                "member_name": r.member_name,
                "outstanding_amount": str(outstanding),
                "advance_maintenance": str(advance),
                "credit_balance": str(credit),
                "late_fees": str(late_fees),
                "interest_receivable": str(interest),
                "net_outstanding": str(net),
            })

        return {
            "rows": rows,
            "total_outstanding": str(total_outstanding),
            "total_advance": str(total_advance),
            "total_credit": str(total_credit),
            "total_late_fees": str(total_late_fees),
            "total_interest_receivable": str(total_interest),
            "total_net_outstanding": str(total_net),
            "row_count": len(rows),
        }

    @staticmethod
    def generate_vendor_summary(*, wizard, society) -> dict:
        """Aggregate :class:`StagingVendorOutstanding` rows into a vendor
        outstanding summary.

        Returns::

            {
                "rows": [
                    {"vendor_name", "outstanding_amount", "advance_paid",
                     "retention", "security_deposit", "net_outstanding"},
                    ...
                ],
                "total_outstanding": "...",
                "total_advance_paid": "...",
                "total_retention": "...",
                "total_security_deposit": "...",
                "total_net_outstanding": "...",
                "row_count": 10,
            }
        """
        rows_qs = (
            StagingVendorOutstanding.objects.unscoped()
            .filter(wizard=wizard, society=society)
            .order_by("row_number")
        )

        rows: list[dict[str, Any]] = []
        total_outstanding = ZERO
        total_advance = ZERO
        total_retention = ZERO
        total_deposit = ZERO
        total_net = ZERO

        for r in rows_qs:
            outstanding = _to_decimal(r.outstanding_amount)
            advance = _to_decimal(r.advance_paid)
            retention = _to_decimal(r.retention)
            deposit = _to_decimal(r.security_deposit)
            net = outstanding - advance - retention - deposit

            total_outstanding += outstanding
            total_advance += advance
            total_retention += retention
            total_deposit += deposit
            total_net += net

            rows.append({
                "vendor_name": r.vendor_name,
                "outstanding_amount": str(outstanding),
                "advance_paid": str(advance),
                "retention": str(retention),
                "security_deposit": str(deposit),
                "net_outstanding": str(net),
            })

        return {
            "rows": rows,
            "total_outstanding": str(total_outstanding),
            "total_advance_paid": str(total_advance),
            "total_retention": str(total_retention),
            "total_security_deposit": str(total_deposit),
            "total_net_outstanding": str(total_net),
            "row_count": len(rows),
        }

    @staticmethod
    def generate_bank_summary(*, wizard, society) -> dict:
        """Aggregate :class:`StagingBankOpening` rows into a bank balances
        summary.

        Returns::

            {
                "rows": [
                    {"bank_name", "account_number", "ifsc", "branch",
                     "opening_balance", "account_code"},
                    ...
                ],
                "total_opening_balance": "...",
                "row_count": 5,
            }
        """
        rows_qs = (
            StagingBankOpening.objects.unscoped()
            .filter(wizard=wizard, society=society)
            .order_by("row_number")
        )

        rows: list[dict[str, Any]] = []
        total_balance = ZERO

        for r in rows_qs:
            balance = _to_decimal(r.opening_balance)
            total_balance += balance
            rows.append({
                "bank_name": r.bank_name,
                "account_number": r.account_number,
                "ifsc": r.ifsc,
                "branch": r.branch,
                "opening_balance": str(balance),
                "account_code": r.account_code,
            })

        return {
            "rows": rows,
            "total_opening_balance": str(total_balance),
            "row_count": len(rows),
        }

    @staticmethod
    def generate_fund_summary(*, wizard, society) -> dict:
        """Aggregate :class:`StagingFund` rows into a fund balances summary.

        Returns::

            {
                "rows": [
                    {"fund_name", "fund_type", "balance", "account_code"},
                    ...
                ],
                "total_balance": "...",
                "row_count": 3,
            }
        """
        rows_qs = (
            StagingFund.objects.unscoped()
            .filter(wizard=wizard, society=society)
            .order_by("row_number")
        )

        rows: list[dict[str, Any]] = []
        total_balance = ZERO

        for r in rows_qs:
            balance = _to_decimal(r.balance)
            total_balance += balance
            rows.append({
                "fund_name": r.fund_name,
                "fund_type": r.fund_type,
                "balance": str(balance),
                "account_code": r.account_code,
            })

        return {
            "rows": rows,
            "total_balance": str(total_balance),
            "row_count": len(rows),
        }

    @staticmethod
    def generate_asset_summary(*, wizard, society) -> dict:
        """Aggregate :class:`StagingFixedAsset` rows into a fixed assets
        summary.

        Returns::

            {
                "rows": [
                    {"asset_name", "asset_category", "gross_value",
                     "depreciation", "net_value", "account_code"},
                    ...
                ],
                "total_gross_value": "...",
                "total_depreciation": "...",
                "total_net_value": "...",
                "row_count": 8,
            }
        """
        rows_qs = (
            StagingFixedAsset.objects.unscoped()
            .filter(wizard=wizard, society=society)
            .order_by("row_number")
        )

        rows: list[dict[str, Any]] = []
        total_gross = ZERO
        total_depreciation = ZERO
        total_net = ZERO

        for r in rows_qs:
            gross = _to_decimal(r.gross_value)
            depreciation = _to_decimal(r.depreciation)
            net = _to_decimal(r.net_value)
            total_gross += gross
            total_depreciation += depreciation
            total_net += net
            rows.append({
                "asset_name": r.asset_name,
                "asset_category": r.asset_category,
                "gross_value": str(gross),
                "depreciation": str(depreciation),
                "net_value": str(net),
                "account_code": r.account_code,
            })

        return {
            "rows": rows,
            "total_gross_value": str(total_gross),
            "total_depreciation": str(total_depreciation),
            "total_net_value": str(total_net),
            "row_count": len(rows),
        }

    @staticmethod
    def generate_loan_summary(*, wizard, society) -> dict:
        """Aggregate :class:`StagingLoan` rows into a loans summary.

        Returns::

            {
                "rows": [
                    {"loan_name", "loan_type", "outstanding_principal",
                     "interest", "total_liability", "account_code"},
                    ...
                ],
                "total_outstanding_principal": "...",
                "total_interest": "...",
                "total_liability": "...",
                "row_count": 2,
            }
        """
        rows_qs = (
            StagingLoan.objects.unscoped()
            .filter(wizard=wizard, society=society)
            .order_by("row_number")
        )

        rows: list[dict[str, Any]] = []
        total_principal = ZERO
        total_interest = ZERO
        total_liability = ZERO

        for r in rows_qs:
            principal = _to_decimal(r.outstanding_principal)
            interest = _to_decimal(r.interest)
            liability = principal + interest
            total_principal += principal
            total_interest += interest
            total_liability += liability
            rows.append({
                "loan_name": r.loan_name,
                "loan_type": r.loan_type,
                "outstanding_principal": str(principal),
                "interest": str(interest),
                "total_liability": str(liability),
                "account_code": r.account_code,
            })

        return {
            "rows": rows,
            "total_outstanding_principal": str(total_principal),
            "total_interest": str(total_interest),
            "total_liability": str(total_liability),
            "row_count": len(rows),
        }

    @staticmethod
    def generate_cash_summary(*, wizard, society) -> dict:
        """Aggregate :class:`StagingCashOpening` rows into a cash balance
        summary.

        Returns::

            {
                "rows": [{"opening_balance": "..."}],
                "total_opening_balance": "...",
                "row_count": 1,
            }
        """
        rows_qs = (
            StagingCashOpening.objects.unscoped()
            .filter(wizard=wizard, society=society)
            .order_by("row_number")
        )

        rows: list[dict[str, Any]] = []
        total_balance = ZERO

        for r in rows_qs:
            balance = _to_decimal(r.opening_balance)
            total_balance += balance
            rows.append({
                "opening_balance": str(balance),
            })

        return {
            "rows": rows,
            "total_opening_balance": str(total_balance),
            "row_count": len(rows),
        }

    @staticmethod
    def generate_security_deposit_summary(*, wizard, society) -> dict:
        """Aggregate :class:`StagingSecurityDeposit` rows into a security
        deposits summary.

        Returns::

            {
                "rows": [{"description", "amount", "against_account"}, ...],
                "total_amount": "...",
                "row_count": 5,
            }
        """
        rows_qs = (
            StagingSecurityDeposit.objects.unscoped()
            .filter(wizard=wizard, society=society)
            .order_by("row_number")
        )

        rows: list[dict[str, Any]] = []
        total_amount = ZERO

        for r in rows_qs:
            amount = _to_decimal(r.amount)
            total_amount += amount
            rows.append({
                "description": r.description,
                "amount": str(amount),
                "against_account": r.against_account,
            })

        return {
            "rows": rows,
            "total_amount": str(total_amount),
            "row_count": len(rows),
        }

    @staticmethod
    def generate_full_dashboard(*, wizard, society) -> dict:
        """Generate the complete reconciliation dashboard (all sections).

        Combines all summary generators into a single dict for the
        reconciliation dashboard view (Step 23).

        Returns::

            {
                "trial_balance": {...},
                "balance_sheet": {...},
                "member_summary": {...},
                "vendor_summary": {...},
                "bank_summary": {...},
                "cash_summary": {...},
                "fund_summary": {...},
                "asset_summary": {...},
                "loan_summary": {...},
                "security_deposit_summary": {...},
            }
        """
        return {
            "trial_balance": ReconciliationService.generate_trial_balance(
                wizard=wizard, society=society
            ),
            "balance_sheet": ReconciliationService.generate_balance_sheet(
                wizard=wizard, society=society
            ),
            "member_summary": ReconciliationService.generate_member_summary(
                wizard=wizard, society=society
            ),
            "vendor_summary": ReconciliationService.generate_vendor_summary(
                wizard=wizard, society=society
            ),
            "bank_summary": ReconciliationService.generate_bank_summary(
                wizard=wizard, society=society
            ),
            "cash_summary": ReconciliationService.generate_cash_summary(
                wizard=wizard, society=society
            ),
            "fund_summary": ReconciliationService.generate_fund_summary(
                wizard=wizard, society=society
            ),
            "asset_summary": ReconciliationService.generate_asset_summary(
                wizard=wizard, society=society
            ),
            "loan_summary": ReconciliationService.generate_loan_summary(
                wizard=wizard, society=society
            ),
            "security_deposit_summary": ReconciliationService.generate_security_deposit_summary(
                wizard=wizard, society=society
            ),
        }

    # ------------------------------------------------------------------ #
    # Step 24 — Validation Checklist (C1–C9)
    # ------------------------------------------------------------------ #

    @staticmethod
    def run_checklist(*, wizard, society) -> dict:
        """Run all 9 cross-template reconciliation checks (C1–C9).

        Delegates to :meth:`ValidationService.validate_cross_references`
        which already implements the checks. The results are transformed
        into a flat ``checks`` list with ``{id, name, passed, detail}``.

        Returns::

            {
                "checks": [
                    {"id": "C1", "name": "Trial Balance Balanced",
                     "passed": True, "detail": {...}},
                    ...
                ],
                "all_passed": True,
                "can_finalize": True,
            }

        ``all_passed`` gates Step 25 (Final Approval).
        """
        cross = ValidationService.validate_cross_references(wizard)

        # Map the cross-reference results to the C1–C9 checklist format.
        checks: list[dict[str, Any]] = [
            {
                "id": "C1",
                "name": "Trial Balance Balanced",
                "passed": cross["cross_references"]["trial_balance_balanced"]["passed"],
                "detail": cross["cross_references"]["trial_balance_balanced"],
            },
            {
                "id": "C2",
                "name": "Balance Sheet Matched (Assets = Liabilities + Equity)",
                "passed": cross["cross_references"]["balance_sheet_matched"]["passed"],
                "detail": cross["cross_references"]["balance_sheet_matched"],
            },
            {
                "id": "C3",
                "name": "Bank Balances Matched",
                "passed": cross["cross_references"]["bank_balances_match"]["passed"],
                "detail": cross["cross_references"]["bank_balances_match"],
            },
            {
                "id": "C4",
                "name": "Member Outstanding Matched",
                "passed": cross["cross_references"]["member_outstanding_matches"]["passed"],
                "detail": cross["cross_references"]["member_outstanding_matches"],
            },
            {
                "id": "C5",
                "name": "Vendor Outstanding Matched",
                "passed": cross["cross_references"]["vendor_outstanding_matches"]["passed"],
                "detail": cross["cross_references"]["vendor_outstanding_matches"],
            },
            {
                "id": "C6",
                "name": "Fixed Assets Matched",
                "passed": cross["cross_references"]["assets_match"]["passed"],
                "detail": cross["cross_references"]["assets_match"],
            },
            {
                "id": "C7",
                "name": "Funds Matched",
                "passed": cross["cross_references"]["funds_match"]["passed"],
                "detail": cross["cross_references"]["funds_match"],
            },
            {
                "id": "C8",
                "name": "Total Debit Equals Total Credit",
                "passed": cross["cross_references"]["debit_equals_credit"]["passed"],
                "detail": cross["cross_references"]["debit_equals_credit"],
            },
            {
                "id": "C9",
                "name": "No Validation Errors",
                "passed": cross["cross_references"]["no_validation_errors"]["passed"],
                "detail": cross["cross_references"]["no_validation_errors"],
            },
        ]

        all_passed = all(c["passed"] for c in checks)

        ReconciliationService._log_audit(
            wizard=wizard,
            action="RUN_CHECKLIST",
            details={
                "all_passed": all_passed,
                "checks_passed": sum(1 for c in checks if c["passed"]),
                "checks_failed": sum(1 for c in checks if not c["passed"]),
            },
        )

        return {
            "checks": checks,
            "all_passed": all_passed,
            "can_finalize": all_passed,
        }

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _log_audit(wizard, action, details=None) -> None:
        """Create a :class:`MigrationAuditLog` entry (append-only).

        Wrapped so a logging failure never blocks a read operation.
        """
        try:
            log = MigrationAuditLog(
                wizard=wizard,
                society=wizard.society,
                action=action,
                details=details or {},
            )
            log.save()
        except Exception:  # noqa: BLE001 — audit must not break the operation.
            logger.exception(
                "Failed to write MigrationAuditLog for wizard %s (action=%s)",
                getattr(wizard, "pk", None),
                action,
            )
