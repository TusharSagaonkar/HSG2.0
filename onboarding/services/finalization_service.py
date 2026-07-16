"""Migration finalization service for the Society Creation & Accounting
Migration Wizard (Phase 7 — Steps 25–27: final approval, opening journal
creation, and migration lock).

This is the **most critical service** in the wizard: it is the **only**
service that writes to live accounting tables (``Voucher``,
``LedgerEntry``). It creates the immutable Opening Journal from the staged
data and locks the migration so staging rows become read-only.

Design notes
------------
- **Only service that writes to live accounting.** All other wizard services
  (staging, validation, reconciliation) only touch staging tables.
- **All mutations are ``@transaction.atomic``** per the service contract.
- **Voucher immutability:** after ``voucher.post()`` the voucher (and its
  ledger entries) are immutable — ``Voucher.save()`` rejects field changes
  and ``LedgerEntry.save()``/``delete()`` reject modifications to posted
  vouchers.
- **Tenant context:** uses :func:`tenant_context` to set the contextvar so
  that ``TenantManager``-scoped models (``Account``, ``Voucher``, etc.) are
  filtered to the correct society during finalization.
- **Decimal accuracy:** all amounts use :class:`Decimal`.
- **Unit FK requirement:** :class:`LedgerEntry` for member-related accounts
  (code ``1.5.x`` or ``2.1.x``) requires the ``unit`` FK. Income/expense/
  equity accounts forbid the ``unit`` FK.
- **Audit robustness:** audit-log writes are wrapped so a logging failure
  never blocks a legitimate finalization operation.
- **Pre-flight checks:** ``finalize_migration`` verifies all checklist checks
  pass and all staging batches are APPROVED before proceeding.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounting.models import (
    Account,
    FinancialYear,
    LedgerEntry,
    Voucher,
)
from accounting.services.gst_vouchers import AccountCodes
from accounting.services.standard_accounts import (
    create_default_accounts_for_society,
)
from auditlog.models import AuditLog
from members.models import Unit
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
)
from onboarding.services.reconciliation_service import ReconciliationService
from societies.utils import tenant_context

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


class MigrationFinalizationService:
    """Finalizes the accounting migration by creating the opening journal
    in live accounting tables and locking the migration (Steps 25–27).

    This is the **only service** that writes to live accounting tables.
    """

    # ------------------------------------------------------------------ #
    # Step 25–27 — Orchestration
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def finalize_migration(*, wizard, society, user) -> Voucher:
        """Orchestrate the complete migration finalization.

        Pre-flight:
        1. Verify all checklist checks (C1–C9) pass.
        2. Verify all staging batches are APPROVED.

        Execution:
        3. Ensure standard accounts exist for the society.
        4. Create the opening journal (``create_opening_journal``).
        5. Lock the migration (``lock_migration``).
        6. Set ``wizard.status = COMPLETED``, ``wizard.completed_at = now``.

        Parameters
        ----------
        wizard : OnboardingWizard
            The wizard being finalized.
        society : Society
            The society being migrated.
        user : User
            The user performing the finalization (for audit logging).

        Returns
        -------
        Voucher
            The posted opening voucher.

        Raises
        ------
        ValidationError
            If pre-flight checks fail (checklist not passed, batches not
            approved, or wizard already finalized).
        """
        if wizard.is_finalized:
            raise ValidationError(
                "Migration has already been finalized. "
                "The wizard is locked and cannot be re-finalized."
            )

        # Pre-flight 1: verify all checklist checks pass.
        checklist = ReconciliationService.run_checklist(
            wizard=wizard, society=society
        )
        if not checklist["all_passed"]:
            failed = [c["name"] for c in checklist["checks"] if not c["passed"]]
            raise ValidationError(
                "Cannot finalize migration: validation checklist failed. "
                f"Failed checks: {', '.join(failed)}."
            )

        # Pre-flight 2: verify all staging batches are APPROVED.
        MigrationFinalizationService._verify_batches_approved(wizard)

        # Ensure standard accounts exist (idempotent).
        create_default_accounts_for_society(society)

        # Create the opening journal (writes to live accounting).
        voucher = MigrationFinalizationService.create_opening_journal(
            wizard=wizard, society=society, user=user
        )

        # Lock the migration.
        MigrationFinalizationService.lock_migration(
            wizard=wizard, society=society, user=user
        )

        # Mark wizard as completed.
        wizard.status = OnboardingWizard.Status.COMPLETED
        wizard.is_finalized = True
        wizard.completed_at = timezone.now()
        wizard.save(update_fields=["status", "is_finalized", "completed_at"])

        # Platform-wide audit log.
        MigrationFinalizationService._log_platform_audit(
            society=society,
            action=AuditLog.Action.POST,
            entity_type="OnboardingWizard",
            entity_id=wizard.pk,
            actor=user,
            after_value={
                "voucher_id": voucher.pk,
                "voucher_number": voucher.voucher_number,
                "status": wizard.status,
            },
            reason="Migration finalized — opening journal created.",
        )

        return voucher

    # ------------------------------------------------------------------ #
    # Step 26 — Create Opening Journal
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def create_opening_journal(*, wizard, society, user) -> Voucher:
        """Create the opening journal voucher from staged data.

        Creates a :class:`Voucher` with ``voucher_type=OPENING``, then
        creates :class:`LedgerEntry` rows from:
        - T2 (trial balance) — general account balances
        - T3 (member outstanding) — member receivable balances with unit FK
        - T4 (vendor outstanding) — vendor payable balances
        - T5 (bank opening) — bank-specific balances (if not already in T2)
        - T6 (cash opening) — cash balance (if not already in T2)

        After all entries are created, calls ``voucher.post()`` which:
        - Validates the voucher is balanced (debit == credit).
        - Assigns a voucher number.
        - Sets ``posted_at`` (making the voucher immutable).

        Parameters
        ----------
        wizard : OnboardingWizard
        society : Society
        user : User

        Returns
        -------
        Voucher
            The posted opening voucher.

        Raises
        ------
        ValidationError
            If the financial year is not open, the voucher is unbalanced,
            or account lookups fail.
        """
        # Get the financial year for the wizard.
        fy_id = None
        if wizard.wizard_data:
            fy_id = wizard.wizard_data.get("financial_year_id")
        fy = None
        if fy_id:
            fy = FinancialYear.objects.filter(pk=fy_id, society=society).first()
        if fy is None:
            fy = FinancialYear.objects.filter(society=society, is_open=True).order_by("start_date").first()
        if fy is None:
            raise ValidationError(
                "No open financial year found for the society. "
                "Ensure Step 5 (Financial Year Creation) is completed."
            )

        # Ensure the FY start date period is open for posting.
        if not fy.is_open:
            raise ValidationError(
                f"Financial year '{fy.name}' is not open. "
                "Cannot post the opening journal to a closed financial year."
            )

        # Create the opening voucher (draft — posted_at is null).
        with tenant_context(society):
            voucher = Voucher.objects.create(
                society=society,
                voucher_type=Voucher.VoucherType.OPENING,
                voucher_date=fy.start_date,
                narration="Opening balances — Migration from previous system",
            )

            # Create ledger entries from each staging template.
            MigrationFinalizationService.create_opening_ledger_entries(
                voucher=voucher, wizard=wizard, society=society
            )
            MigrationFinalizationService.create_opening_member_balances(
                voucher=voucher, wizard=wizard, society=society
            )
            MigrationFinalizationService.create_opening_vendor_balances(
                voucher=voucher, wizard=wizard, society=society
            )
            MigrationFinalizationService.create_opening_bank_balances(
                voucher=voucher, wizard=wizard, society=society
            )

            # Validate and post the voucher (assigns number, sets posted_at).
            voucher.post()

        # Audit log the opening journal creation.
        MigrationFinalizationService._log_platform_audit(
            society=society,
            action=AuditLog.Action.POST,
            entity_type="Voucher",
            entity_id=voucher.pk,
            actor=user,
            after_value={
                "voucher_number": voucher.voucher_number,
                "voucher_date": voucher.voucher_date.isoformat(),
                "voucher_type": voucher.voucher_type,
            },
            reason="Opening journal created during migration finalization.",
        )

        MigrationFinalizationService._log_migration_audit(
            wizard=wizard,
            action="CREATE_OPENING_JOURNAL",
            user=user,
            after_state={
                "voucher_id": voucher.pk,
                "voucher_number": voucher.voucher_number,
                "voucher_date": voucher.voucher_date.isoformat(),
            },
        )

        return voucher

    @staticmethod
    def create_opening_ledger_entries(*, voucher, wizard, society) -> list[LedgerEntry]:
        """Create ledger entries from :class:`StagingTrialBalance` rows.

        For each T2 row with a non-zero balance:
        - Look up the live :class:`Account` by code.
        - Create a :class:`LedgerEntry` with the debit/credit from staging.

        Member-related accounts (code ``1.5.x`` or ``2.1.x``) are skipped
        here — they are created by ``create_opening_member_balances`` with
        the required ``unit`` FK.

        Parameters
        ----------
        voucher : Voucher
            The draft opening voucher to attach entries to.
        wizard : OnboardingWizard
        society : Society

        Returns
        -------
        list[LedgerEntry]
            The created ledger entries.
        """
        entries: list[LedgerEntry] = []
        tb_rows = list(
            StagingTrialBalance.objects.unscoped()
            .filter(wizard=wizard, society=society)
            .order_by("row_number")
        )

        for r in tb_rows:
            debit = _to_decimal(r.debit)
            credit = _to_decimal(r.credit)
            if _is_zero(debit) and _is_zero(credit):
                continue

            code = (r.account_code or "").strip()
            if not code:
                logger.warning(
                    "Skipping T2 row %s: no account_code.", r.row_number
                )
                continue

            # Skip member-related accounts — handled by member balances.
            if _is_member_account_code(code):
                continue

            account = MigrationFinalizationService._resolve_account(
                society=society, code=code, name=r.account_name
            )

            entry = LedgerEntry.objects.create(
                voucher=voucher,
                account=account,
                debit=debit,
                credit=credit,
            )
            entries.append(entry)

        return entries

    @staticmethod
    def create_opening_member_balances(*, voucher, wizard, society) -> list[LedgerEntry]:
        """Create ledger entries from :class:`StagingMemberOutstanding` rows.

        For each T3 row:
        - Look up the :class:`Unit` by ``unit_identifier``.
        - Look up the member receivable account (``AccountCodes.MAINTENANCE_DUE``).
        - Create a :class:`LedgerEntry` with the ``unit`` FK (required for
          member-related accounts).

        The net outstanding is: ``outstanding - advance - credit + late_fees
        + interest_receivable``. If net > 0, debit the receivable account.

        Parameters
        ----------
        voucher : Voucher
        wizard : OnboardingWizard
        society : Society

        Returns
        -------
        list[LedgerEntry]
        """
        entries: list[LedgerEntry] = []
        member_rows = list(
            StagingMemberOutstanding.objects.unscoped()
            .filter(wizard=wizard, society=society)
            .order_by("row_number")
        )

        receivable_account = MigrationFinalizationService._resolve_account(
            society=society, code=AccountCodes.MAINTENANCE_DUE
        )

        for r in member_rows:
            outstanding = _to_decimal(r.outstanding_amount)
            advance = _to_decimal(r.advance_maintenance)
            credit = _to_decimal(r.credit_balance)
            late_fees = _to_decimal(r.late_fees)
            interest = _to_decimal(r.interest_receivable)
            net = outstanding - advance - credit + late_fees + interest

            if _is_zero(net):
                continue

            unit = MigrationFinalizationService._resolve_unit(
                society=society, identifier=r.unit_identifier
            )

            if net > 0:
                debit = net
                credit_amount = ZERO
            else:
                debit = ZERO
                credit_amount = abs(net)

            entry = LedgerEntry.objects.create(
                voucher=voucher,
                account=receivable_account,
                unit=unit,
                debit=debit,
                credit=credit_amount,
                reference_type=LedgerEntry.ReferenceType.MEMBER,
                reference_id=str(unit.pk) if unit else "",
            )
            entries.append(entry)

        return entries

    @staticmethod
    def create_opening_vendor_balances(*, voucher, wizard, society) -> list[LedgerEntry]:
        """Create ledger entries from :class:`StagingVendorOutstanding` rows.

        For each T4 row:
        - Look up the vendor payable account (``AccountCodes.VENDOR_PAYABLE``).
        - Create a :class:`LedgerEntry` crediting the payable account.

        The net outstanding is: ``outstanding - advance - retention -
        security_deposit``. If net > 0, credit the payable account.

        Parameters
        ----------
        voucher : Voucher
        wizard : OnboardingWizard
        society : Society

        Returns
        -------
        list[LedgerEntry]
        """
        entries: list[LedgerEntry] = []
        vendor_rows = list(
            StagingVendorOutstanding.objects.unscoped()
            .filter(wizard=wizard, society=society)
            .order_by("row_number")
        )

        payable_account = MigrationFinalizationService._resolve_account(
            society=society, code=AccountCodes.VENDOR_PAYABLE
        )

        for r in vendor_rows:
            outstanding = _to_decimal(r.outstanding_amount)
            advance = _to_decimal(r.advance_paid)
            retention = _to_decimal(r.retention)
            deposit = _to_decimal(r.security_deposit)
            net = outstanding - advance - retention - deposit

            if _is_zero(net):
                continue

            if net > 0:
                debit = ZERO
                credit_amount = net
            else:
                debit = abs(net)
                credit_amount = ZERO

            entry = LedgerEntry.objects.create(
                voucher=voucher,
                account=payable_account,
                debit=debit,
                credit=credit_amount,
                reference_type=LedgerEntry.ReferenceType.VENDOR,
                reference_id=r.vendor_name,
            )
            entries.append(entry)

        return entries

    @staticmethod
    def create_opening_bank_balances(*, voucher, wizard, society) -> list[LedgerEntry]:
        """Create ledger entries from :class:`StagingBankOpening` and
        :class:`StagingCashOpening` rows.

        For each T5 (bank) row:
        - Look up the bank account by ``account_code`` (if provided) or
          fall back to ``AccountCodes.BANK_MAINTENANCE``.
        - Create a :class:`LedgerEntry` debiting the bank account.

        For T6 (cash) row:
        - Look up ``AccountCodes.CASH_IN_HAND``.
        - Create a :class:`LedgerEntry` debiting the cash account.

        Note: If bank/cash balances are already present in T2 (trial balance),
        they will be skipped here to avoid double-counting. This method only
        creates entries for bank/cash rows that are NOT already covered by T2.

        Parameters
        ----------
        voucher : Voucher
        wizard : OnboardingWizard
        society : Society

        Returns
        -------
        list[LedgerEntry]
        """
        entries: list[LedgerEntry] = []

        # Collect account codes already in T2 to avoid double-counting.
        tb_codes = set(
            StagingTrialBalance.objects.unscoped()
            .filter(wizard=wizard, society=society)
            .exclude(account_code="")
            .values_list("account_code", flat=True)
        )

        # Bank opening balances (T5).
        bank_rows = list(
            StagingBankOpening.objects.unscoped()
            .filter(wizard=wizard, society=society)
            .order_by("row_number")
        )

        for r in bank_rows:
            balance = _to_decimal(r.opening_balance)
            if _is_zero(balance):
                continue

            code = (r.account_code or "").strip()
            if code and code in tb_codes:
                # Already covered by T2 — skip to avoid double-counting.
                continue

            account = MigrationFinalizationService._resolve_account(
                society=society,
                code=code or AccountCodes.BANK_MAINTENANCE,
                name=r.bank_name,
            )

            entry = LedgerEntry.objects.create(
                voucher=voucher,
                account=account,
                debit=balance,
                credit=ZERO,
            )
            entries.append(entry)

        # Cash opening balance (T6).
        cash_rows = list(
            StagingCashOpening.objects.unscoped()
            .filter(wizard=wizard, society=society)
            .order_by("row_number")
        )

        cash_code = AccountCodes.CASH_IN_HAND
        for r in cash_rows:
            balance = _to_decimal(r.opening_balance)
            if _is_zero(balance):
                continue

            if cash_code in tb_codes:
                # Already covered by T2 — skip.
                continue

            account = MigrationFinalizationService._resolve_account(
                society=society, code=cash_code
            )

            entry = LedgerEntry.objects.create(
                voucher=voucher,
                account=account,
                debit=balance,
                credit=ZERO,
            )
            entries.append(entry)

        return entries

    # ------------------------------------------------------------------ #
    # Step 27 — Lock Migration
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def lock_migration(*, wizard, society, user) -> None:
        """Lock the migration so staging rows become read-only.

        - Mark all staging rows' ``is_approved = True`` (read-only flag).
        - Mark all :class:`UploadBatch` rows as ``COMMITTED``.
        - The opening voucher is already immutable via ``Voucher.post()``.

        Parameters
        ----------
        wizard : OnboardingWizard
        society : Society
        user : User
        """
        # Mark all staging rows as approved (read-only).
        staging_models = [
            StagingChartOfAccounts,
            StagingTrialBalance,
            StagingMemberOutstanding,
            StagingVendorOutstanding,
            StagingBankOpening,
            StagingCashOpening,
            StagingFixedAsset,
            StagingSecurityDeposit,
            StagingLoan,
            StagingFund,
        ]
        for model_cls in staging_models:
            model_cls.objects.unscoped().filter(
                wizard=wizard, society=society
            ).update(is_approved=True)

        # Mark all upload batches as COMMITTED.
        UploadBatch.objects.unscoped().filter(
            wizard=wizard, society=society
        ).update(status=UploadBatch.Status.COMMITTED)

        # Migration-specific audit log.
        MigrationFinalizationService._log_migration_audit(
            wizard=wizard,
            action="LOCK_MIGRATION",
            user=user,
            after_state={"status": "LOCKED"},
        )

        # Platform-wide audit log.
        MigrationFinalizationService._log_platform_audit(
            society=society,
            action=AuditLog.Action.LOCK,
            entity_type="OnboardingWizard",
            entity_id=wizard.pk,
            actor=user,
            reason="Migration locked — staging data committed.",
        )

    # ------------------------------------------------------------------ #
    # Summary
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_finalization_summary(*, wizard, society) -> dict:
        """Return a summary of the finalization state for display.

        Returns::

            {
                "wizard_status": "COMPLETED",
                "is_finalized": True,
                "completed_at": "2026-07-14T13:00:00Z",
                "opening_voucher": {
                    "id": 1, "number": "OPE-00001", "date": "2026-04-01",
                    "posted_at": "...",
                } | None,
                "staging_committed": True,
                "batch_count": 10,
                "committed_batch_count": 10,
            }
        """
        opening_voucher = None
        with tenant_context(society):
            voucher = (
                Voucher.objects.filter(
                    society=society,
                    voucher_type=Voucher.VoucherType.OPENING,
                )
                .order_by("-posted_at")
                .first()
            )
            if voucher:
                opening_voucher = {
                    "id": voucher.pk,
                    "number": voucher.display_number,
                    "date": voucher.voucher_date.isoformat(),
                    "posted_at": voucher.posted_at.isoformat() if voucher.posted_at else None,
                }

        batches = UploadBatch.objects.unscoped().filter(wizard=wizard, society=society)
        batch_count = batches.count()
        committed_count = batches.filter(status=UploadBatch.Status.COMMITTED).count()

        return {
            "wizard_status": wizard.status,
            "is_finalized": wizard.is_finalized,
            "completed_at": wizard.completed_at.isoformat() if wizard.completed_at else None,
            "opening_voucher": opening_voucher,
            "staging_committed": committed_count == batch_count and batch_count > 0,
            "batch_count": batch_count,
            "committed_batch_count": committed_count,
        }

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_account(*, society, code, name="") -> Account:
        """Look up a live :class:`Account` by code, falling back to name.

        Parameters
        ----------
        society : Society
        code : str
            The account code (e.g. ``"1.5.1.1"``).
        name : str
            Fallback account name for lookup if code fails.

        Returns
        -------
        Account

        Raises
        ------
        ValidationError
            If the account cannot be found by code or name.
        """
        with tenant_context(society):
            account = Account.objects.filter(society=society, code=code).first()
            if account:
                return account
            if name:
                account = Account.objects.filter(society=society, name=name).first()
                if account:
                    return account
            raise ValidationError(
                f"Account not found for code '{code}'"
                + (f" or name '{name}'." if name else ".")
            )

    @staticmethod
    def _resolve_unit(*, society, identifier) -> Unit:
        """Look up a :class:`Unit` by identifier within the society's
        structures.

        Parameters
        ----------
        society : Society
        identifier : str
            The unit identifier (e.g. ``"A1-101"``).

        Returns
        -------
        Unit

        Raises
        ------
        ValidationError
            If the unit cannot be found.
        """
        from members.models import Structure

        structure_ids = Structure.objects.filter(society=society).values_list("pk", flat=True)
        unit = Unit.objects.filter(
            structure__in=structure_ids,
            identifier=identifier,
        ).first()
        if unit:
            return unit

        # Try case-insensitive match as fallback.
        unit = Unit.objects.filter(
            structure__in=structure_ids,
            identifier__iexact=identifier,
        ).first()
        if unit:
            return unit

        raise ValidationError(
            f"Unit not found for identifier '{identifier}'. "
            "Ensure Step 7 (Unit Configuration) and Step 8 (Member Assignment) "
            "are completed before finalization."
        )

    @staticmethod
    def _verify_batches_approved(wizard) -> None:
        """Verify all staging upload batches are APPROVED.

        Raises
        ------
        ValidationError
            If any batch is not in APPROVED status.
        """
        batches = UploadBatch.objects.unscoped().filter(wizard=wizard)
        if not batches.exists():
            raise ValidationError(
                "No staging data found. Ensure all templates are uploaded "
                "and approved before finalization."
            )
        not_approved = batches.exclude(status=UploadBatch.Status.APPROVED)
        if not_approved.exists():
            pending = list(not_approved.values_list("template_type", flat=True))
            raise ValidationError(
                "Cannot finalize migration: not all staging batches are approved. "
                f"Pending batches: {', '.join(pending)}."
            )

    @staticmethod
    def _log_migration_audit(
        *,
        wizard,
        action,
        user=None,
        details=None,
        before_state=None,
        after_state=None,
    ) -> None:
        """Create a :class:`MigrationAuditLog` entry (append-only).

        Wrapped so a logging failure never blocks a legitimate operation.
        """
        try:
            log = MigrationAuditLog(
                wizard=wizard,
                society=wizard.society,
                action=action,
                actor=user,
                details=details or {},
                before_state=before_state or {},
                after_state=after_state or {},
            )
            log.save()
        except Exception:  # noqa: BLE001 — audit must not break the operation.
            logger.exception(
                "Failed to write MigrationAuditLog for wizard %s (action=%s)",
                getattr(wizard, "pk", None),
                action,
            )

    @staticmethod
    def _log_platform_audit(
        *,
        society,
        action,
        entity_type,
        entity_id,
        actor=None,
        before_value=None,
        after_value=None,
        reason=None,
    ) -> None:
        """Create a platform-wide :class:`AuditLog` entry (append-only).

        Wrapped so a logging failure never blocks a legitimate operation.
        """
        try:
            AuditLog.log(
                society=society,
                action=action,
                entity_type=entity_type,
                entity_id=str(entity_id),
                actor=actor,
                before_value=before_value,
                after_value=after_value,
                module="onboarding",
                reason=reason,
            )
        except Exception:  # noqa: BLE001 — audit must not break the operation.
            logger.exception(
                "Failed to write AuditLog for entity %s:%s (action=%s)",
                entity_type,
                entity_id,
                action,
            )


# --------------------------------------------------------------------------- #
# Module-level helpers
# --------------------------------------------------------------------------- #

def _is_zero(value: Decimal) -> bool:
    """Return True if the decimal value is effectively zero."""
    return abs(_to_decimal(value)) < TOLERANCE


def _is_member_account_code(code: str) -> bool:
    """Return True if the account code is a member-related account.

    Member-related accounts have codes starting with ``1.5.`` (receivables)
    or ``2.1.`` (member liabilities). These require the ``unit`` FK on
    :class:`LedgerEntry`.
    """
    if not code:
        return False
    return code.startswith("1.5.") or code.startswith("2.1.")
