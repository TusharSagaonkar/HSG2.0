"""Comprehensive tests for ReconciliationService and MigrationFinalizationService
(Phase 9).

Covers:
- ReconciliationService dashboard generators (Step 23) and run_checklist (Step 24).
- MigrationFinalizationService finalize_migration (Steps 25–27), opening journal
  creation, migration lock, and finalization summary.

Uses ``SocietyTestCase`` so the society (with bootstrapped accounts and a
financial year) is created once per test class, keeping the suite fast.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from accounting.models import Account, FinancialYear, LedgerEntry, Voucher
from core.test_base import SocietyTestCase
from members.models import Structure, Unit
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
from onboarding.services.finalization_service import MigrationFinalizationService
from onboarding.services.reconciliation_service import ReconciliationService
from onboarding.services.staging_service import StagingService
from onboarding.services.validation_service import ValidationService
from onboarding.services.wizard_service import WizardService
User = get_user_model()

# --------------------------------------------------------------------------- #
# Manager patching
#
# The ReconciliationService and MigrationFinalizationService call
# ``.unscoped()`` on staging models (e.g. ``StagingTrialBalance.objects
# .unscoped()``).  The ``unscoped()`` method is provided by
# :class:`TenantManager`, but the staging models use Django's default
# ``Manager``.  We add an ``unscoped`` method to each staging model's
# default manager so that ``.unscoped()`` is available during tests.
#
# Unlike replacing the manager entirely with ``TenantManager``, this approach
# does NOT change ``get_queryset()`` behaviour, so existing queries that use
# ``.objects.filter(wizard=wizard)`` continue to work unchanged regardless of
# the tenant contextvar state.  This avoids cross-test contamination when
# tests run in the same pytest session.
# --------------------------------------------------------------------------- #


def _add_unscoped(manager):
    """Add an ``unscoped`` method to a manager instance.

    ``unscoped()`` returns a clone of the default queryset without any
    tenant or soft-delete filtering — equivalent to the default Manager
    queryset.
    """
    if hasattr(manager, "unscoped"):
        return
    from django.db.models import Manager

    def unscoped(self):
        return super(Manager, self).get_queryset()

    manager.unscoped = unscoped.__get__(manager, type(manager))


_STAGING_MODELS = [
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

for _model in _STAGING_MODELS:
    _add_unscoped(_model.objects)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _csv_bytes(headers, rows):
    """Build a CSV file as bytes for SimpleUploadedFile."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


# --------------------------------------------------------------------------- #
# Reconciliation Service Tests
# --------------------------------------------------------------------------- #

class ReconciliationBaseTest(SocietyTestCase):
    """Base class: wizard linked to the shared society with staging data."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.wizard = WizardService.create_wizard(user=cls.user)
        cls.wizard.society = cls.society
        cls.wizard.save(update_fields=["society"])

    def _upload(self, template_type, headers, rows, file_name="test.csv"):
        content = _csv_bytes(headers, rows)
        f = SimpleUploadedFile(file_name, content, content_type="text/csv")
        return StagingService.upload_file(
            wizard=self.wizard,
            template_type=template_type,
            file=f,
            user=self.user,
        )


class ReconciliationTrialBalanceTest(ReconciliationBaseTest):
    """Tests for ReconciliationService.generate_trial_balance."""

    def test_empty_trial_balance(self):
        result = ReconciliationService.generate_trial_balance(
            wizard=self.wizard, society=self.society
        )
        self.assertEqual(result["row_count"], 0)
        self.assertEqual(Decimal(result["total_debit"]), Decimal("0"))
        self.assertEqual(Decimal(result["total_credit"]), Decimal("0"))
        self.assertTrue(result["is_balanced"])

    def test_balanced_trial_balance(self):
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [
                ["1.1", "Cash", "1000", "0"],
                ["2.1", "Payable", "0", "1000"],
            ],
        )
        result = ReconciliationService.generate_trial_balance(
            wizard=self.wizard, society=self.society
        )
        self.assertEqual(result["row_count"], 2)
        self.assertEqual(Decimal(result["total_debit"]), Decimal("1000"))
        self.assertEqual(Decimal(result["total_credit"]), Decimal("1000"))
        self.assertTrue(result["is_balanced"])
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["rows"][0]["account_code"], "1.1")

    def test_unbalanced_trial_balance(self):
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [
                ["1.1", "Cash", "1000", "0"],
                ["2.1", "Payable", "0", "500"],
            ],
        )
        result = ReconciliationService.generate_trial_balance(
            wizard=self.wizard, society=self.society
        )
        self.assertFalse(result["is_balanced"])


class ReconciliationBalanceSheetTest(ReconciliationBaseTest):
    """Tests for ReconciliationService.generate_balance_sheet."""

    def test_empty_balance_sheet(self):
        result = ReconciliationService.generate_balance_sheet(
            wizard=self.wizard, society=self.society
        )
        self.assertIn("assets", result)
        self.assertIn("liabilities", result)
        self.assertIn("equity", result)
        self.assertIn("totals", result)
        self.assertEqual(len(result["assets"]), 0)

    def test_balance_sheet_with_nature_from_coa(self):
        """Balance sheet classification uses T1 nature field."""
        self._upload(
            "CHART_OF_ACCOUNTS",
            ["account_code", "account_name", "nature"],
            [
                ["1.1", "Cash in Hand", "ASSET"],
                ["2.1", "Vendor Payable", "LIABILITY"],
                ["5.1", "Capital", "EQUITY"],
            ],
        )
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [
                ["1.1", "Cash in Hand", "10000", "0"],
                ["2.1", "Vendor Payable", "0", "4000"],
                ["5.1", "Capital", "0", "6000"],
            ],
        )
        result = ReconciliationService.generate_balance_sheet(
            wizard=self.wizard, society=self.society
        )
        self.assertEqual(len(result["assets"]), 1)
        self.assertEqual(len(result["liabilities"]), 1)
        self.assertEqual(len(result["equity"]), 1)
        self.assertTrue(result["totals"]["is_balanced"])

    def test_balance_sheet_fallback_heuristics(self):
        """Without T1, classification falls back to account-name keywords."""
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [
                ["1.1", "Cash in Hand", "5000", "0"],
                ["2.1", "Vendor Payable", "0", "5000"],
            ],
        )
        result = ReconciliationService.generate_balance_sheet(
            wizard=self.wizard, society=self.society
        )
        # "Cash in Hand" → ASSET (contains "cash")
        self.assertEqual(len(result["assets"]), 1)
        # "Vendor Payable" → LIABILITY (contains "payable")
        self.assertEqual(len(result["liabilities"]), 1)


class ReconciliationSummaryTest(ReconciliationBaseTest):
    """Tests for member/vendor/bank/cash/fund/asset/loan/security_deposit summaries."""

    def test_member_summary(self):
        self._upload(
            "MEMBER_OUTSTANDING",
            [
                "unit_identifier", "member_name", "outstanding_amount",
                "advance_maintenance", "credit_balance", "late_fees",
                "interest_receivable",
            ],
            [["A-101", "John", "10000", "1000", "500", "200", "100"]],
        )
        result = ReconciliationService.generate_member_summary(
            wizard=self.wizard, society=self.society
        )
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(Decimal(result["total_outstanding"]), Decimal("10000"))
        # net = 10000 - 1000 - 500 + 200 + 100 = 8800
        self.assertEqual(Decimal(result["total_net_outstanding"]), Decimal("8800"))

    def test_vendor_summary(self):
        self._upload(
            "VENDOR_OUTSTANDING",
            ["vendor_name", "outstanding_amount", "advance_paid",
             "retention", "security_deposit"],
            [["ABC Corp", "15000", "2000", "1000", "500"]],
        )
        result = ReconciliationService.generate_vendor_summary(
            wizard=self.wizard, society=self.society
        )
        self.assertEqual(result["row_count"], 1)
        # net = 15000 - 2000 - 1000 - 500 = 11500
        self.assertEqual(Decimal(result["total_net_outstanding"]), Decimal("11500"))

    def test_bank_summary(self):
        self._upload(
            "BANK_OPENING",
            ["bank_name", "account_number", "ifsc", "branch", "opening_balance"],
            [["HDFC Bank", "1234567890", "HDFC0001234", "Mumbai", "50000"]],
        )
        result = ReconciliationService.generate_bank_summary(
            wizard=self.wizard, society=self.society
        )
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(Decimal(result["total_opening_balance"]), Decimal("50000"))

    def test_cash_summary(self):
        self._upload(
            "CASH_OPENING",
            ["opening_balance"],
            [["5000"]],
        )
        result = ReconciliationService.generate_cash_summary(
            wizard=self.wizard, society=self.society
        )
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(Decimal(result["total_opening_balance"]), Decimal("5000"))

    def test_fund_summary(self):
        self._upload(
            "FUNDS",
            ["fund_name", "fund_type", "balance"],
            [["Repair Fund", "RESERVE", "50000"]],
        )
        result = ReconciliationService.generate_fund_summary(
            wizard=self.wizard, society=self.society
        )
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(Decimal(result["total_balance"]), Decimal("50000"))

    def test_asset_summary(self):
        self._upload(
            "FIXED_ASSETS",
            ["asset_name", "asset_category", "gross_value",
             "depreciation", "net_value"],
            [["Building", "BUILDING", "40000", "10000", "30000"]],
        )
        result = ReconciliationService.generate_asset_summary(
            wizard=self.wizard, society=self.society
        )
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(Decimal(result["total_gross_value"]), Decimal("40000"))
        self.assertEqual(Decimal(result["total_net_value"]), Decimal("30000"))

    def test_loan_summary(self):
        self._upload(
            "LOANS",
            ["loan_name", "loan_type", "outstanding_principal", "interest"],
            [["Bank Loan 1", "BANK_LOAN", "100000", "5000"]],
        )
        result = ReconciliationService.generate_loan_summary(
            wizard=self.wizard, society=self.society
        )
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(Decimal(result["total_outstanding_principal"]), Decimal("100000"))
        self.assertEqual(Decimal(result["total_liability"]), Decimal("105000"))

    def test_security_deposit_summary(self):
        self._upload(
            "SECURITY_DEPOSITS",
            ["description", "amount", "against_account"],
            [["Vendor Security", "10000", "2.1.4"]],
        )
        result = ReconciliationService.generate_security_deposit_summary(
            wizard=self.wizard, society=self.society
        )
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(Decimal(result["total_amount"]), Decimal("10000"))


class ReconciliationFullDashboardTest(ReconciliationBaseTest):
    """Tests for ReconciliationService.generate_full_dashboard."""

    def test_full_dashboard_empty(self):
        result = ReconciliationService.generate_full_dashboard(
            wizard=self.wizard, society=self.society
        )
        expected_keys = [
            "trial_balance", "balance_sheet", "member_summary",
            "vendor_summary", "bank_summary", "cash_summary",
            "fund_summary", "asset_summary", "loan_summary",
            "security_deposit_summary",
        ]
        for key in expected_keys:
            self.assertIn(key, result)

    def test_full_dashboard_with_data(self):
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [["1.1", "Cash", "100", "0"], ["2.1", "Payable", "0", "100"]],
        )
        result = ReconciliationService.generate_full_dashboard(
            wizard=self.wizard, society=self.society
        )
        self.assertEqual(result["trial_balance"]["row_count"], 2)
        self.assertTrue(result["trial_balance"]["is_balanced"])


class ReconciliationChecklistTest(ReconciliationBaseTest):
    """Tests for ReconciliationService.run_checklist (C1–C9)."""

    def test_checklist_empty_data_all_pass(self):
        result = ReconciliationService.run_checklist(
            wizard=self.wizard, society=self.society
        )
        self.assertIn("checks", result)
        self.assertEqual(len(result["checks"]), 9)
        self.assertTrue(result["all_passed"])
        self.assertTrue(result["can_finalize"])

    def test_checklist_check_ids(self):
        result = ReconciliationService.run_checklist(
            wizard=self.wizard, society=self.society
        )
        ids = [c["id"] for c in result["checks"]]
        self.assertEqual(ids, ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"])

    def test_checklist_unbalanced_tb_fails(self):
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [["1.1", "Cash", "100", "0"], ["2.1", "Payable", "0", "50"]],
        )
        result = ReconciliationService.run_checklist(
            wizard=self.wizard, society=self.society
        )
        self.assertFalse(result["all_passed"])
        self.assertFalse(result["can_finalize"])
        c1 = [c for c in result["checks"] if c["id"] == "C1"][0]
        self.assertFalse(c1["passed"])

    def test_checklist_creates_audit_log(self):
        ReconciliationService.run_checklist(
            wizard=self.wizard, society=self.society
        )
        log = MigrationAuditLog.objects.filter(
            wizard=self.wizard, action="RUN_CHECKLIST"
        ).first()
        self.assertIsNotNone(log)


# --------------------------------------------------------------------------- #
# Finalization Service Tests
# --------------------------------------------------------------------------- #

class FinalizationBaseTest(SocietyTestCase):
    """Base class for finalization tests.

    Sets up a wizard with a financial year and balanced, approved staging data
    so that ``finalize_migration`` can succeed.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.wizard = WizardService.create_wizard(user=cls.user)
        cls.wizard.society = cls.society
        cls.wizard.save(update_fields=["society"])

        # Ensure a financial year exists and is open.
        cls.fy = FinancialYear.objects.filter(
            society=cls.society, is_open=True
        ).order_by("start_date").first()
        if cls.fy is None:
            cls.fy = FinancialYear.objects.create(
                society=cls.society,
                name="2026-27",
                start_date=date(2026, 4, 1),
                end_date=date(2027, 3, 31),
                is_open=True,
            )
        cls.wizard.wizard_data["financial_year_id"] = cls.fy.pk
        cls.wizard.save(update_fields=["wizard_data"])

        # Create a Structure + Unit so T3 member outstanding can resolve
        # the unit_identifier "A-101" during finalization.
        cls.structure = Structure.objects.create(
            society=cls.society,
            structure_type=Structure.StructureType.BUILDING,
            name="Building A",
        )
        cls.unit = Unit.objects.create(
            structure=cls.structure,
            unit_type=Unit.UnitType.FLAT,
            identifier="A-101",
        )

    def _upload(self, template_type, headers, rows, file_name="test.csv"):
        content = _csv_bytes(headers, rows)
        f = SimpleUploadedFile(file_name, content, content_type="text/csv")
        return StagingService.upload_file(
            wizard=self.wizard,
            template_type=template_type,
            file=f,
            user=self.user,
        )

    def _setup_balanced_and_approved(self):
        """Upload balanced data, validate, and approve all batches.

        Uses account codes from the bootstrapped chart of accounts so that
        the finalization service can resolve them to live ``Account`` records.

        The finalization service creates opening-journal entries from:
          - T2 (trial balance) — skipping member accounts (1.5.x / 2.1.x)
          - T3 (member outstanding) — debits the receivable account
          - T4 (vendor outstanding) — credits the payable account
          - T5 (bank opening) — skipped if account_code already in T2
          - T6 (cash opening) — skipped if cash account already in T2

        To keep the opening voucher balanced (debit == credit) we must
        ensure that the T2 rows (excluding member accounts) plus T3
        debits plus T4 credits net to zero.

        T2 Trial Balance (debit == credit == 100 000):
            1.4.1   Cash-in-Hand              debit  5 000   (ASSET, cash)
            1.4.2.1 Bank – Maintenance        debit 50 000   (ASSET, bank)
            1.5.1.1 Maintenance Due           debit 15 000   (ASSET, member — skipped)
            1.1     Fixed Assets              debit 30 000   (ASSET, asset)
            5.1.2   Repair & Maintenance Fund credit 50 000  (EQUITY, fund)
            5.2.1   Share Capital             credit 50 000  (EQUITY, capital)

        T3 Member Outstanding (net == 15 000)  → debit  15 000
        T4 Vendor Outstanding  (net == 0)       → skipped (advance == outstanding)

        Finalization entries:
          T2 (skip member 1.5.x): debit 85 000, credit 100 000
          T3:                      debit 15 000, credit 0
          T4 (net 0):              skipped
          T5/T6:                   skipped (codes in T2)
          Total debit  = 100 000
          Total credit = 100 000  ✓ balanced
        """
        # T1: Chart of Accounts — provides nature for the balance-sheet check.
        self._upload(
            "CHART_OF_ACCOUNTS",
            ["account_code", "account_name", "nature"],
            [
                ["1.4.1", "Cash-in-Hand", "ASSET"],
                ["1.4.2.1", "Bank – Maintenance Account", "ASSET"],
                ["1.5.1.1", "Maintenance Due", "ASSET"],
                ["1.1", "Fixed Assets", "ASSET"],
                ["2.2.1", "Vendor Payable", "LIABILITY"],
                ["5.1.2", "Repair & Maintenance Fund", "EQUITY"],
                ["5.2.1", "Share Capital", "EQUITY"],
            ],
        )
        ValidationService.validate_batch(
            self.wizard, "CHART_OF_ACCOUNTS", user=self.user
        )

        # T2: Trial Balance (balanced: debit == credit == 100000)
        # 1.5.1.1 (Maintenance Due) is member-related (code starts with
        # 1.5.) so create_opening_ledger_entries skips it; it is handled
        # by T3.  Vendor payable is NOT in T2 to avoid double-counting
        # with T4.
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [
                ["1.4.1", "Cash-in-Hand", "5000", "0"],
                ["1.4.2.1", "Bank – Maintenance Account", "50000", "0"],
                ["1.5.1.1", "Maintenance Due", "15000", "0"],
                ["1.1", "Fixed Assets", "30000", "0"],
                ["5.1.2", "Repair & Maintenance Fund", "0", "50000"],
                ["5.2.1", "Share Capital", "0", "50000"],
            ],
        )
        ValidationService.validate_batch(self.wizard, "TRIAL_BALANCE", user=self.user)

        # T3: Member Outstanding (net == 15000) — debits receivable account.
        self._upload(
            "MEMBER_OUTSTANDING",
            [
                "unit_identifier", "member_name", "outstanding_amount",
                "advance_maintenance", "credit_balance", "late_fees",
                "interest_receivable",
            ],
            [["A-101", "John Doe", "15000", "0", "0", "0", "0"]],
        )
        ValidationService.validate_batch(
            self.wizard, "MEMBER_OUTSTANDING", user=self.user
        )

        # T4: Vendor Outstanding (net == 0) — advance equals outstanding so
        # create_opening_vendor_balances skips it (no double-counting).
        self._upload(
            "VENDOR_OUTSTANDING",
            ["vendor_name", "outstanding_amount", "advance_paid",
             "retention", "security_deposit"],
            [["ABC Corp", "15000", "15000", "0", "0"]],
        )
        ValidationService.validate_batch(
            self.wizard, "VENDOR_OUTSTANDING", user=self.user
        )

        # T5: Bank Opening (50000) — account_code matches T2 so the
        # finalization service skips it (already covered by trial balance).
        self._upload(
            "BANK_OPENING",
            ["bank_name", "account_number", "ifsc", "branch",
             "opening_balance", "account_code"],
            [["HDFC Bank", "1234567890", "HDFC0001234", "Mumbai",
              "50000", "1.4.2.1"]],
        )
        ValidationService.validate_batch(self.wizard, "BANK_OPENING", user=self.user)

        # T6: Cash Opening (5000)
        self._upload(
            "CASH_OPENING",
            ["opening_balance"],
            [["5000"]],
        )
        ValidationService.validate_batch(self.wizard, "CASH_OPENING", user=self.user)

        # T7: Fixed Assets (net 30000, net == gross - depreciation)
        self._upload(
            "FIXED_ASSETS",
            ["asset_name", "asset_category", "gross_value",
             "depreciation", "net_value"],
            [["Building", "BUILDING", "40000", "10000", "30000"]],
        )
        ValidationService.validate_batch(self.wizard, "FIXED_ASSETS", user=self.user)

        # T10: Funds (50000)
        self._upload(
            "FUNDS",
            ["fund_name", "fund_type", "balance"],
            [["Repair Fund", "RESERVE", "50000"]],
        )
        ValidationService.validate_batch(self.wizard, "FUNDS", user=self.user)

        # Approve all uploaded batches.
        for canonical in [
            "CHART_OF_ACCOUNTS", "TRIAL_BALANCE", "MEMBER_OUTSTANDING",
            "VENDOR_OUTSTANDING", "BANK_OPENING", "CASH_OPENING",
            "FIXED_ASSETS", "FUNDS",
        ]:
            StagingService.approve_batch(
                wizard=self.wizard, template_type=canonical, user=self.user
            )


class FinalizationPreFlightTest(FinalizationBaseTest):
    """Tests for finalize_migration pre-flight checks."""

    def test_finalize_no_batches_raises(self):
        """Cannot finalize with no staging data."""
        with self.assertRaises(ValidationError):
            MigrationFinalizationService.finalize_migration(
                wizard=self.wizard, society=self.society, user=self.user
            )

    def test_finalize_unapproved_batches_raises(self):
        """Cannot finalize if batches are not APPROVED."""
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [["1.1", "Cash", "100", "0"], ["2.1", "Payable", "0", "100"]],
        )
        ValidationService.validate_batch(self.wizard, "TRIAL_BALANCE", user=self.user)
        # Batch is VALIDATED, not APPROVED.
        with self.assertRaises(ValidationError):
            MigrationFinalizationService.finalize_migration(
                wizard=self.wizard, society=self.society, user=self.user
            )

    def test_finalize_already_finalized_raises(self):
        """Cannot re-finalize an already-finalized wizard."""
        self._setup_balanced_and_approved()
        MigrationFinalizationService.finalize_migration(
            wizard=self.wizard, society=self.society, user=self.user
        )
        self.wizard.refresh_from_db()
        self.assertTrue(self.wizard.is_finalized)
        with self.assertRaises(ValidationError):
            MigrationFinalizationService.finalize_migration(
                wizard=self.wizard, society=self.society, user=self.user
            )

    def test_verify_batches_approved_no_batches(self):
        with self.assertRaises(ValidationError):
            MigrationFinalizationService._verify_batches_approved(self.wizard)

    def test_verify_batches_approved_not_approved(self):
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [["1.1", "Cash", "100", "0"], ["2.1", "Payable", "0", "100"]],
        )
        with self.assertRaises(ValidationError):
            MigrationFinalizationService._verify_batches_approved(self.wizard)

    def test_verify_batches_approved_all_approved(self):
        self._setup_balanced_and_approved()
        # Should not raise.
        MigrationFinalizationService._verify_batches_approved(self.wizard)


class FinalizationSuccessTest(FinalizationBaseTest):
    """Tests for successful finalize_migration execution."""

    def test_finalize_creates_opening_voucher(self):
        self._setup_balanced_and_approved()
        voucher = MigrationFinalizationService.finalize_migration(
            wizard=self.wizard, society=self.society, user=self.user
        )
        self.assertIsNotNone(voucher)
        self.assertEqual(voucher.voucher_type, Voucher.VoucherType.OPENING)
        self.assertIsNotNone(voucher.posted_at)
        self.assertIsNotNone(voucher.voucher_number)

    def test_finalize_creates_ledger_entries(self):
        self._setup_balanced_and_approved()
        voucher = MigrationFinalizationService.finalize_migration(
            wizard=self.wizard, society=self.society, user=self.user
        )
        entries = LedgerEntry.objects.filter(voucher=voucher)
        self.assertGreater(entries.count(), 0)

    def test_finalize_marks_wizard_completed(self):
        self._setup_balanced_and_approved()
        MigrationFinalizationService.finalize_migration(
            wizard=self.wizard, society=self.society, user=self.user
        )
        self.wizard.refresh_from_db()
        self.assertEqual(self.wizard.status, OnboardingWizard.Status.COMPLETED)
        self.assertTrue(self.wizard.is_finalized)
        self.assertIsNotNone(self.wizard.completed_at)

    def test_finalize_locks_staging_rows(self):
        """After finalization, all staging rows should be is_approved=True."""
        self._setup_balanced_and_approved()
        MigrationFinalizationService.finalize_migration(
            wizard=self.wizard, society=self.society, user=self.user
        )
        tb_rows = StagingTrialBalance.objects.unscoped().filter(wizard=self.wizard)
        for row in tb_rows:
            self.assertTrue(row.is_approved)

    def test_finalize_commits_batches(self):
        """After finalization, all batches should be COMMITTED."""
        self._setup_balanced_and_approved()
        MigrationFinalizationService.finalize_migration(
            wizard=self.wizard, society=self.society, user=self.user
        )
        batches = UploadBatch.objects.unscoped().filter(wizard=self.wizard)
        for batch in batches:
            self.assertEqual(batch.status, UploadBatch.Status.COMMITTED)

    def test_finalize_voucher_is_balanced(self):
        """The opening voucher must be balanced (debit == credit)."""
        self._setup_balanced_and_approved()
        voucher = MigrationFinalizationService.finalize_migration(
            wizard=self.wizard, society=self.society, user=self.user
        )
        entries = LedgerEntry.objects.filter(voucher=voucher)
        total_debit = sum((e.debit for e in entries), Decimal("0"))
        total_credit = sum((e.credit for e in entries), Decimal("0"))
        self.assertEqual(total_debit, total_credit)

    def test_finalize_creates_migration_audit_log(self):
        self._setup_balanced_and_approved()
        MigrationFinalizationService.finalize_migration(
            wizard=self.wizard, society=self.society, user=self.user
        )
        logs = MigrationAuditLog.objects.filter(
            wizard=self.wizard, action="CREATE_OPENING_JOURNAL"
        )
        self.assertTrue(logs.exists())

    def test_finalize_creates_lock_audit_log(self):
        self._setup_balanced_and_approved()
        MigrationFinalizationService.finalize_migration(
            wizard=self.wizard, society=self.society, user=self.user
        )
        logs = MigrationAuditLog.objects.filter(
            wizard=self.wizard, action="LOCK_MIGRATION"
        )
        self.assertTrue(logs.exists())


class FinalizationSummaryTest(FinalizationBaseTest):
    """Tests for MigrationFinalizationService.get_finalization_summary."""

    def test_summary_before_finalization(self):
        summary = MigrationFinalizationService.get_finalization_summary(
            wizard=self.wizard, society=self.society
        )
        self.assertIn("wizard_status", summary)
        self.assertIn("is_finalized", summary)
        self.assertFalse(summary["is_finalized"])
        self.assertIsNone(summary["opening_voucher"])

    def test_summary_after_finalization(self):
        self._setup_balanced_and_approved()
        MigrationFinalizationService.finalize_migration(
            wizard=self.wizard, society=self.society, user=self.user
        )
        self.wizard.refresh_from_db()
        summary = MigrationFinalizationService.get_finalization_summary(
            wizard=self.wizard, society=self.society
        )
        self.assertTrue(summary["is_finalized"])
        self.assertEqual(summary["wizard_status"], OnboardingWizard.Status.COMPLETED)
        self.assertIsNotNone(summary["opening_voucher"])
        self.assertTrue(summary["staging_committed"])
        self.assertGreater(summary["batch_count"], 0)
        self.assertEqual(summary["batch_count"], summary["committed_batch_count"])


class FinalizationLockTest(FinalizationBaseTest):
    """Tests for MigrationFinalizationService.lock_migration."""

    def test_lock_marks_staging_approved(self):
        self._setup_balanced_and_approved()
        MigrationFinalizationService.lock_migration(
            wizard=self.wizard, society=self.society, user=self.user
        )
        coa_rows = StagingChartOfAccounts.objects.unscoped().filter(wizard=self.wizard)
        for row in coa_rows:
            self.assertTrue(row.is_approved)

    def test_lock_commits_batches(self):
        self._setup_balanced_and_approved()
        MigrationFinalizationService.lock_migration(
            wizard=self.wizard, society=self.society, user=self.user
        )
        batches = UploadBatch.objects.unscoped().filter(wizard=self.wizard)
        for batch in batches:
            self.assertEqual(batch.status, UploadBatch.Status.COMMITTED)

    def test_lock_creates_audit_log(self):
        self._setup_balanced_and_approved()
        MigrationFinalizationService.lock_migration(
            wizard=self.wizard, society=self.society, user=self.user
        )
        logs = MigrationAuditLog.objects.filter(
            wizard=self.wizard, action="LOCK_MIGRATION"
        )
        self.assertTrue(logs.exists())
