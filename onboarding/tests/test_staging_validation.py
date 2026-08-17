"""Comprehensive tests for StagingService and ValidationService (Phase 9).

Covers file upload (CSV), parsing, storage, retrieval, delete, approve,
per-template validation, and cross-reference validation checks (C1–C9).

Uses ``SocietyTestCase`` so the society (with bootstrapped accounts) is
created once per test class, keeping the suite fast.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from core.test_base import SocietyTestCase
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
from onboarding.services.staging_service import StagingService
from onboarding.services.validation_service import ValidationService
from onboarding.services.wizard_service import WizardService

User = get_user_model()


def _csv_bytes(headers, rows):
    """Build a CSV file as bytes for SimpleUploadedFile."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


class StagingServiceBaseTest(SocietyTestCase):
    """Base class that creates a wizard linked to the shared society."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.wizard = WizardService.create_wizard(user=cls.user)
        cls.wizard.society = cls.society
        cls.wizard.save(update_fields=["society"])

    def _upload(self, template_type, headers, rows, file_name="test.csv"):
        """Helper: upload a CSV file for a template type."""
        content = _csv_bytes(headers, rows)
        f = SimpleUploadedFile(file_name, content, content_type="text/csv")
        return StagingService.upload_file(
            wizard=self.wizard,
            template_type=template_type,
            file=f,
            user=self.user,
        )


class StagingUploadTest(StagingServiceBaseTest):
    """Tests for StagingService.upload_file and store_staging_data."""

    def test_upload_csv_creates_batch_and_rows(self):
        batch = self._upload(
            "CHART_OF_ACCOUNTS",
            ["account_code", "account_name", "nature"],
            [
                ["1.1", "Cash", "ASSET"],
                ["1.2", "Bank", "ASSET"],
            ],
        )
        self.assertEqual(batch.status, UploadBatch.Status.UPLOADED)
        self.assertEqual(batch.row_count, 2)
        self.assertEqual(
            StagingChartOfAccounts.objects.filter(wizard=self.wizard).count(), 2
        )

    def test_upload_trial_balance(self):
        batch = self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [
                ["1.1", "Cash", "1000", "0"],
                ["2.1", "Payable", "0", "1000"],
            ],
        )
        self.assertEqual(batch.row_count, 2)
        rows = StagingTrialBalance.objects.filter(wizard=self.wizard).order_by("row_number")
        self.assertEqual(rows[0].debit, Decimal("1000"))
        self.assertEqual(rows[1].credit, Decimal("1000"))

    def test_upload_replaces_previous_data(self):
        """Re-uploading for the same template deletes old data first."""
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [["1.1", "Cash", "100", "0"]],
        )
        self.assertEqual(
            StagingTrialBalance.objects.filter(wizard=self.wizard).count(), 1
        )
        # Re-upload with different data.
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [
                ["1.1", "Cash", "200", "0"],
                ["2.1", "Payable", "0", "200"],
            ],
        )
        self.assertEqual(
            StagingTrialBalance.objects.filter(wizard=self.wizard).count(), 2
        )
        # Only one batch should remain (old one deleted).
        batches = UploadBatch.objects.unscoped().filter(
            wizard=self.wizard, template_type="TRIAL_BALANCE"
        )
        self.assertEqual(batches.count(), 1)

    def test_upload_invalid_extension_raises(self):
        f = SimpleUploadedFile("test.txt", b"data", content_type="text/plain")
        with self.assertRaises(ValueError):
            StagingService.upload_file(
                wizard=self.wizard, template_type="TRIAL_BALANCE", file=f
            )

    def test_upload_unknown_template_type_raises(self):
        f = SimpleUploadedFile("test.csv", b"a,b\n1,2", content_type="text/csv")
        with self.assertRaises(ValueError):
            StagingService.upload_file(
                wizard=self.wizard, template_type="UNKNOWN_TYPE", file=f
            )

    def test_upload_none_wizard_raises(self):
        f = SimpleUploadedFile("test.csv", b"a,b\n1,2", content_type="text/csv")
        with self.assertRaises(ValueError):
            StagingService.upload_file(
                wizard=None, template_type="TRIAL_BALANCE", file=f
            )

    def test_upload_none_file_raises(self):
        with self.assertRaises(ValueError):
            StagingService.upload_file(
                wizard=self.wizard, template_type="TRIAL_BALANCE", file=None
            )

    def test_upload_creates_audit_log(self):
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [["1.1", "Cash", "100", "0"]],
        )
        self.assertTrue(
            MigrationAuditLog.objects.filter(
                wizard=self.wizard, action="UPLOAD"
            ).exists()
        )

    def test_upload_stores_raw_data(self):
        """Each staging row should store the original row in raw_data."""
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [["1.1", "Cash", "100", "0"]],
        )
        row = StagingTrialBalance.objects.filter(wizard=self.wizard).first()
        self.assertIsNotNone(row.raw_data)
        self.assertIn("account_code", row.raw_data)

    def test_upload_decimal_parsing(self):
        """Decimal fields should be parsed from strings."""
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [["1.1", "Cash", "1,234.56", "0"]],
        )
        row = StagingTrialBalance.objects.filter(wizard=self.wizard).first()
        self.assertEqual(row.debit, Decimal("1234.56"))


class StagingRetrievalTest(StagingServiceBaseTest):
    """Tests for get_staging_data and get_upload_summary."""

    def test_get_staging_data_returns_rows(self):
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [
                ["1.1", "Cash", "100", "0"],
                ["2.1", "Payable", "0", "100"],
            ],
        )
        data = StagingService.get_staging_data(self.wizard, "TRIAL_BALANCE")
        self.assertEqual(data["total_count"], 2)
        self.assertEqual(len(data["rows"]), 2)

    def test_get_staging_data_empty(self):
        data = StagingService.get_staging_data(self.wizard, "TRIAL_BALANCE")
        self.assertEqual(data["total_count"], 0)
        self.assertEqual(data["rows"], [])

    def test_get_upload_summary_all_templates(self):
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [["1.1", "Cash", "100", "0"]],
        )
        summary = StagingService.get_upload_summary(self.wizard)
        self.assertIn("TRIAL_BALANCE", summary)
        self.assertTrue(summary["TRIAL_BALANCE"]["has_data"])
        self.assertEqual(summary["TRIAL_BALANCE"]["row_count"], 1)
        # Templates without data should show has_data=False.
        self.assertFalse(summary["CHART_OF_ACCOUNTS"]["has_data"])

    def test_get_template_columns(self):
        cols = StagingService.get_template_columns("TRIAL_BALANCE")
        self.assertIn("account_code", cols)
        self.assertIn("debit", cols)
        self.assertIn("credit", cols)


class StagingDeleteApproveTest(StagingServiceBaseTest):
    """Tests for delete_batch and approve_batch."""

    def _upload_and_validate(self, template_type, headers, rows):
        self._upload(template_type, headers, rows)
        ValidationService.validate_batch(
            wizard=self.wizard, template_type=template_type, user=self.user
        )

    def test_delete_batch_removes_data(self):
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [["1.1", "Cash", "100", "0"]],
        )
        StagingService.delete_batch(self.wizard, "TRIAL_BALANCE", user=self.user)
        self.assertEqual(
            StagingTrialBalance.objects.filter(wizard=self.wizard).count(), 0
        )

    def test_delete_batch_marks_batch_deleted(self):
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [["1.1", "Cash", "100", "0"]],
        )
        StagingService.delete_batch(self.wizard, "TRIAL_BALANCE", user=self.user)
        # The batch is deleted after being marked DELETED.
        self.assertEqual(
            UploadBatch.objects.unscoped().filter(
                wizard=self.wizard, template_type="TRIAL_BALANCE"
            ).count(),
            0,
        )

    def test_delete_batch_creates_audit_log(self):
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [["1.1", "Cash", "100", "0"]],
        )
        StagingService.delete_batch(self.wizard, "TRIAL_BALANCE", user=self.user)
        self.assertTrue(
            MigrationAuditLog.objects.filter(
                wizard=self.wizard, action="DELETE"
            ).exists()
        )

    def test_approve_batch_valid_rows(self):
        self._upload_and_validate(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [
                ["1.1", "Cash", "100", "0"],
                ["2.1", "Payable", "0", "100"],
            ],
        )
        batch = StagingService.approve_batch(self.wizard, "TRIAL_BALANCE", user=self.user)
        self.assertEqual(batch.status, UploadBatch.Status.APPROVED)
        rows = StagingTrialBalance.objects.filter(wizard=self.wizard)
        self.assertTrue(all(r.is_approved for r in rows))

    def test_approve_batch_no_data_raises(self):
        with self.assertRaises(ValueError):
            StagingService.approve_batch(self.wizard, "TRIAL_BALANCE", user=self.user)

    def test_approve_batch_pending_rows_raises(self):
        """Uploading without validating leaves rows PENDING — approve fails."""
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [["1.1", "Cash", "100", "0"]],
        )
        with self.assertRaises(ValueError):
            StagingService.approve_batch(self.wizard, "TRIAL_BALANCE", user=self.user)

    def test_approve_batch_creates_audit_log(self):
        self._upload_and_validate(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [
                ["1.1", "Cash", "100", "0"],
                ["2.1", "Payable", "0", "100"],
            ],
        )
        StagingService.approve_batch(self.wizard, "TRIAL_BALANCE", user=self.user)
        self.assertTrue(
            MigrationAuditLog.objects.filter(
                wizard=self.wizard, action="APPROVE"
            ).exists()
        )


class ValidationChartOfAccountsTest(StagingServiceBaseTest):
    """Tests for ValidationService.validate_chart_of_accounts (T1)."""

    def test_validate_valid_coa(self):
        self._upload(
            "CHART_OF_ACCOUNTS",
            ["account_code", "account_name", "nature"],
            [
                ["1.1", "Cash", "ASSET"],
                ["2.1", "Payable", "LIABILITY"],
            ],
        )
        report = ValidationService.validate_batch(
            self.wizard, "CHART_OF_ACCOUNTS", user=self.user
        )
        self.assertEqual(report["valid"], 2)
        self.assertEqual(report["invalid"], 0)

    def test_validate_coa_missing_code(self):
        self._upload(
            "CHART_OF_ACCOUNTS",
            ["account_code", "account_name", "nature"],
            [["", "No Code Account", "ASSET"]],
        )
        report = ValidationService.validate_batch(
            self.wizard, "CHART_OF_ACCOUNTS", user=self.user
        )
        self.assertEqual(report["invalid"], 1)
        self.assertGreater(len(report["errors"]), 0)

    def test_validate_coa_missing_name(self):
        self._upload(
            "CHART_OF_ACCOUNTS",
            ["account_code", "account_name", "nature"],
            [["1.1", "", "ASSET"]],
        )
        report = ValidationService.validate_batch(
            self.wizard, "CHART_OF_ACCOUNTS", user=self.user
        )
        self.assertEqual(report["invalid"], 1)

    def test_validate_coa_duplicate_code(self):
        self._upload(
            "CHART_OF_ACCOUNTS",
            ["account_code", "account_name", "nature"],
            [
                ["1.1", "Cash", "ASSET"],
                ["1.1", "Duplicate", "ASSET"],
            ],
        )
        report = ValidationService.validate_batch(
            self.wizard, "CHART_OF_ACCOUNTS", user=self.user
        )
        self.assertEqual(report["invalid"], 1)

    def test_validate_coa_invalid_nature(self):
        self._upload(
            "CHART_OF_ACCOUNTS",
            ["account_code", "account_name", "nature"],
            [["1.1", "Cash", "BOGUS"]],
        )
        report = ValidationService.validate_batch(
            self.wizard, "CHART_OF_ACCOUNTS", user=self.user
        )
        self.assertEqual(report["invalid"], 1)

    def test_validate_coa_general_nature_is_ignored(self):
        self._upload(
            "CHART_OF_ACCOUNTS",
            ["account_code", "account_name", "nature"],
            [["1.1", "Cash", "GENERAL"]],
        )
        report = ValidationService.validate_batch(
            self.wizard, "CHART_OF_ACCOUNTS", user=self.user
        )
        self.assertEqual(report["valid"], 1)
        self.assertEqual(report["invalid"], 0)

    def test_validate_coa_invalid_code_format(self):
        self._upload(
            "CHART_OF_ACCOUNTS",
            ["account_code", "account_name", "nature"],
            [["abc", "Bad Code", "ASSET"]],
        )
        report = ValidationService.validate_batch(
            self.wizard, "CHART_OF_ACCOUNTS", user=self.user
        )
        self.assertEqual(report["invalid"], 1)

    def test_validate_updates_batch_status(self):
        self._upload(
            "CHART_OF_ACCOUNTS",
            ["account_code", "account_name", "nature"],
            [["1.1", "Cash", "ASSET"]],
        )
        ValidationService.validate_batch(
            self.wizard, "CHART_OF_ACCOUNTS", user=self.user
        )
        batch = UploadBatch.objects.unscoped().filter(
            wizard=self.wizard, template_type="CHART_OF_ACCOUNTS"
        ).first()
        self.assertEqual(batch.status, UploadBatch.Status.VALIDATED)


class ValidationTrialBalanceTest(StagingServiceBaseTest):
    """Tests for ValidationService.validate_trial_balance (T2)."""

    def test_validate_balanced_tb(self):
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [
                ["1.1", "Cash", "1000", "0"],
                ["2.1", "Payable", "0", "1000"],
            ],
        )
        report = ValidationService.validate_batch(
            self.wizard, "TRIAL_BALANCE", user=self.user
        )
        self.assertEqual(report["valid"], 2)
        self.assertEqual(report["invalid"], 0)

    def test_validate_tb_missing_code(self):
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [["", "No Code", "100", "0"]],
        )
        report = ValidationService.validate_batch(
            self.wizard, "TRIAL_BALANCE", user=self.user
        )
        self.assertEqual(report["invalid"], 1)

    def test_validate_tb_both_debit_and_credit(self):
        """A row with both debit and credit should be flagged."""
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [["1.1", "Cash", "100", "100"]],
        )
        report = ValidationService.validate_batch(
            self.wizard, "TRIAL_BALANCE", user=self.user
        )
        self.assertEqual(report["invalid"], 1)


class ValidationBankOpeningTest(StagingServiceBaseTest):
    """Tests for ValidationService.validate_bank_opening (T5)."""

    def test_validate_valid_bank(self):
        self._upload(
            "BANK_OPENING",
            ["bank_name", "account_number", "ifsc", "branch", "opening_balance"],
            [["HDFC Bank", "1234567890", "HDFC0001234", "Mumbai", "50000"]],
        )
        report = ValidationService.validate_batch(
            self.wizard, "BANK_OPENING", user=self.user
        )
        self.assertEqual(report["valid"], 1)
        self.assertEqual(report["invalid"], 0)

    def test_validate_bank_invalid_ifsc(self):
        self._upload(
            "BANK_OPENING",
            ["bank_name", "account_number", "ifsc", "branch", "opening_balance"],
            [["HDFC Bank", "1234567890", "INVALID", "Mumbai", "50000"]],
        )
        report = ValidationService.validate_batch(
            self.wizard, "BANK_OPENING", user=self.user
        )
        self.assertEqual(report["invalid"], 1)

    def test_validate_bank_missing_name(self):
        self._upload(
            "BANK_OPENING",
            ["bank_name", "account_number", "ifsc", "branch", "opening_balance"],
            [["", "1234567890", "HDFC0001234", "Mumbai", "50000"]],
        )
        report = ValidationService.validate_batch(
            self.wizard, "BANK_OPENING", user=self.user
        )
        self.assertEqual(report["invalid"], 1)


class ValidationCrossReferenceTest(StagingServiceBaseTest):
    """Tests for ValidationService.validate_cross_references (C1–C9).

    The cross-reference checks use name-based heuristics to classify T2
    accounts (e.g. "bank" → bank account, "fund" → fund account).  The
    balance-sheet check (C2) prefers the T1 ``nature`` field and falls
    back to the same heuristics.

    Balanced dataset design (all amounts in plain integers for clarity):

    T2 Trial Balance (debit == credit == 95 000):
        1.1   Cash in Hand            debit  5 000   (ASSET, cash)
        1.2   Bank Account            debit 50 000   (ASSET, bank)
        1.3   Maintenance Receivable  debit 10 000   (ASSET, member)
        1.4   Fixed Asset             debit 30 000   (ASSET, asset)
        2.1   Vendor Payable          credit 15 000  (LIABILITY, vendor)
        3.1   Repair Fund             credit 50 000  (LIABILITY, fund)
        3.2   Capital                 credit 30 000  (EQUITY)

    Cross-reference expectations:
        C1  TB balanced:        95 000 == 95 000 ✓
        C2  Balance sheet:      Assets 95 000 == Liab 65 000 + Equity 30 000 ✓
        C3  Bank match:         T5 50 000 == T2 bank 50 000 ✓
        C4  Member match:        T3 net 10 000 == T2 member recv 10 000 ✓
        C5  Vendor match:       T4 net 15 000 == T2 vendor payable 15 000 ✓
        C6  Assets match:       T7 net 30 000 == T2 asset 30 000 ✓
        C7  Funds match:        T10 50 000 == T2 fund 50 000 ✓
        C8  Debit == Credit:    same as C1 ✓
        C9  No validation errs: all rows VALID ✓
    """

    def _setup_balanced_data(self):
        """Upload a fully balanced set of staging data that passes all 9 checks."""

        # T1: Chart of Accounts — provides nature for the balance-sheet check.
        self._upload(
            "CHART_OF_ACCOUNTS",
            ["account_code", "account_name", "nature"],
            [
                ["1.1", "Cash in Hand", "ASSET"],
                ["1.2", "Bank Account", "ASSET"],
                ["1.3", "Maintenance Receivable", "ASSET"],
                ["1.4", "Fixed Asset", "ASSET"],
                ["2.1", "Vendor Payable", "LIABILITY"],
                ["3.1", "Repair Fund", "LIABILITY"],
                ["3.2", "Capital", "EQUITY"],
            ],
        )
        ValidationService.validate_batch(
            self.wizard, "CHART_OF_ACCOUNTS", user=self.user
        )

        # T2: Trial Balance (balanced).
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [
                ["1.1", "Cash in Hand", "5000", "0"],
                ["1.2", "Bank Account", "50000", "0"],
                ["1.3", "Maintenance Receivable", "10000", "0"],
                ["1.4", "Fixed Asset", "30000", "0"],
                ["2.1", "Vendor Payable", "0", "15000"],
                ["3.1", "Repair Fund", "0", "50000"],
                ["3.2", "Capital", "0", "30000"],
            ],
        )
        ValidationService.validate_batch(self.wizard, "TRIAL_BALANCE", user=self.user)

        # T3: Member Outstanding — net == 10 000 (matches T2 member receivable).
        self._upload(
            "MEMBER_OUTSTANDING",
            [
                "unit_identifier", "member_name", "outstanding_amount",
                "advance_maintenance", "credit_balance", "late_fees",
                "interest_receivable",
            ],
            [["A-101", "John Doe", "10000", "0", "0", "0", "0"]],
        )
        ValidationService.validate_batch(
            self.wizard, "MEMBER_OUTSTANDING", user=self.user
        )

        # T4: Vendor Outstanding — net == 15 000 (matches T2 vendor payable).
        self._upload(
            "VENDOR_OUTSTANDING",
            ["vendor_name", "outstanding_amount", "advance_paid",
             "retention", "security_deposit"],
            [["ABC Corp", "15000", "0", "0", "0"]],
        )
        ValidationService.validate_batch(
            self.wizard, "VENDOR_OUTSTANDING", user=self.user
        )

        # T5: Bank Opening — 50 000 (matches T2 bank).
        self._upload(
            "BANK_OPENING",
            ["bank_name", "account_number", "ifsc", "branch", "opening_balance"],
            [["HDFC Bank", "1234567890", "HDFC0001234", "Mumbai", "50000"]],
        )
        ValidationService.validate_batch(self.wizard, "BANK_OPENING", user=self.user)

        # T6: Cash Opening — 5 000 (matches T2 cash).
        self._upload(
            "CASH_OPENING",
            ["opening_balance"],
            [["5000"]],
        )
        ValidationService.validate_batch(self.wizard, "CASH_OPENING", user=self.user)

        # T7: Fixed Assets — net 30 000 (matches T2 asset).
        # net_value must equal gross - depreciation to avoid a warning.
        self._upload(
            "FIXED_ASSETS",
            ["asset_name", "asset_category", "gross_value",
             "depreciation", "net_value"],
            [["Building", "BUILDING", "40000", "10000", "30000"]],
        )
        ValidationService.validate_batch(self.wizard, "FIXED_ASSETS", user=self.user)

        # T10: Funds — 50 000 (matches T2 fund).
        self._upload(
            "FUNDS",
            ["fund_name", "fund_type", "balance"],
            [["Repair Fund", "RESERVE", "50000"]],
        )
        ValidationService.validate_batch(self.wizard, "FUNDS", user=self.user)

    def test_cross_references_empty_data_all_pass(self):
        """With no staging data, checks should pass (0 == 0)."""
        result = ValidationService.validate_cross_references(self.wizard)
        self.assertIn("checklist", result)
        self.assertTrue(result["checklist"]["all_passed"])

    def test_cross_references_balanced_data_passes(self):
        self._setup_balanced_data()
        result = ValidationService.validate_cross_references(self.wizard)
        self.assertTrue(
            result["checklist"]["all_passed"],
            f"Failed checks: {[k for k, v in result['checklist'].items() if not v]}",
        )

    def test_cross_references_trial_balance_unbalanced_fails(self):
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [
                ["1.1", "Cash", "1000", "0"],
                ["2.1", "Payable", "0", "500"],
            ],
        )
        ValidationService.validate_batch(self.wizard, "TRIAL_BALANCE", user=self.user)
        result = ValidationService.validate_cross_references(self.wizard)
        self.assertFalse(result["checklist"]["trial_balance_balanced"])
        self.assertFalse(result["checklist"]["all_passed"])

    def test_cross_references_bank_mismatch_fails(self):
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [
                ["1.2", "Bank Account", "50000", "0"],
                ["2.1", "Vendor Payable", "0", "50000"],
            ],
        )
        ValidationService.validate_batch(self.wizard, "TRIAL_BALANCE", user=self.user)
        # Bank opening does NOT match T2 bank total.
        self._upload(
            "BANK_OPENING",
            ["bank_name", "account_number", "ifsc", "branch", "opening_balance"],
            [["HDFC Bank", "1234567890", "HDFC0001234", "Mumbai", "40000"]],
        )
        ValidationService.validate_batch(self.wizard, "BANK_OPENING", user=self.user)
        result = ValidationService.validate_cross_references(self.wizard)
        self.assertFalse(result["checklist"]["bank_balances_matched"])

    def test_cross_references_cash_mismatch_fails(self):
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [
                ["1.1", "Cash in Hand", "5000", "0"],
                ["2.1", "Vendor Payable", "0", "5000"],
            ],
        )
        ValidationService.validate_batch(self.wizard, "TRIAL_BALANCE", user=self.user)
        self._upload(
            "CASH_OPENING",
            ["opening_balance"],
            [["3000"]],
        )
        ValidationService.validate_batch(self.wizard, "CASH_OPENING", user=self.user)
        result = ValidationService.validate_cross_references(self.wizard)
        # The cash check lives in cross_references (not the flat checklist).
        self.assertFalse(
            result["cross_references"]["cash_balance_matches"]["passed"]
        )

    def test_cross_references_member_mismatch_fails(self):
        """C4: T3 net outstanding must match T2 member receivable."""
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [
                ["1.3", "Maintenance Receivable", "10000", "0"],
                ["2.1", "Vendor Payable", "0", "10000"],
            ],
        )
        ValidationService.validate_batch(self.wizard, "TRIAL_BALANCE", user=self.user)
        # T3 net = 5000 but T2 member receivable = 10000.
        self._upload(
            "MEMBER_OUTSTANDING",
            [
                "unit_identifier", "member_name", "outstanding_amount",
                "advance_maintenance", "credit_balance", "late_fees",
                "interest_receivable",
            ],
            [["A-101", "John", "5000", "0", "0", "0", "0"]],
        )
        ValidationService.validate_batch(
            self.wizard, "MEMBER_OUTSTANDING", user=self.user
        )
        result = ValidationService.validate_cross_references(self.wizard)
        self.assertFalse(result["checklist"]["member_outstanding_matched"])

    def test_cross_references_funds_mismatch_fails(self):
        """C7: T10 fund balance must match T2 fund account."""
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [
                ["1.1", "Cash in Hand", "50000", "0"],
                ["3.1", "Repair Fund", "0", "50000"],
            ],
        )
        ValidationService.validate_batch(self.wizard, "TRIAL_BALANCE", user=self.user)
        # T10 = 40000 but T2 fund = 50000.
        self._upload(
            "FUNDS",
            ["fund_name", "fund_type", "balance"],
            [["Repair Fund", "RESERVE", "40000"]],
        )
        ValidationService.validate_batch(self.wizard, "FUNDS", user=self.user)
        result = ValidationService.validate_cross_references(self.wizard)
        self.assertFalse(result["checklist"]["funds_matched"])

    def test_cross_references_returns_can_finalize(self):
        result = ValidationService.validate_cross_references(self.wizard)
        self.assertIn("can_finalize", result)

    def test_get_validation_report_structure(self):
        self._setup_balanced_data()
        report = ValidationService.get_validation_report(self.wizard)
        self.assertIn("templates", report)
        self.assertIn("cross_references", report)
        self.assertIn("checklist", report)
        self.assertIn("can_finalize", report)
        self.assertIn("TRIAL_BALANCE", report["templates"])

    def test_check_no_validation_errors_with_invalid_rows(self):
        """C9 should fail if any staging rows are INVALID."""
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [["", "No Code", "100", "0"]],  # invalid: missing code
        )
        ValidationService.validate_batch(self.wizard, "TRIAL_BALANCE", user=self.user)
        result = ValidationService.check_no_validation_errors(self.wizard)
        self.assertFalse(result["passed"])
        self.assertGreater(result["invalid_count"], 0)

    def test_check_no_validation_errors_all_valid(self):
        self._upload(
            "TRIAL_BALANCE",
            ["account_code", "account_name", "debit", "credit"],
            [
                ["1.1", "Cash", "100", "0"],
                ["2.1", "Payable", "0", "100"],
            ],
        )
        ValidationService.validate_batch(self.wizard, "TRIAL_BALANCE", user=self.user)
        result = ValidationService.check_no_validation_errors(self.wizard)
        self.assertTrue(result["passed"])
        self.assertEqual(result["invalid_count"], 0)
