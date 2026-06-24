"""
Tests for ReportService — BRS, Unmatched, Duplicates, Exception Summary —
and the report views (BRSReportView, UnmatchedReportView, DuplicateReportView,
ExceptionListView).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from accounting.models import Account, LedgerEntry, Voucher
from reconciliation.models import BankTransaction, ReconciliationLink
from reconciliation.services.reports import ReportService
from reconciliation.tests.factories import (
    BankAccountFactory,
    BankStatementImportFactory,
    BankTransactionFactory,
    BankTransactionNormalizedFactory,
    ExpenseAccountFactory,
    IncomeAccountFactory,
    LedgerEntryFactory,
    ReconciliationLinkFactory,
    SocietyFactory,
    VoucherFactory,
)
from societies.models import Membership

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _login_and_select_society(client, user, society):
    """Log the user in and set the society scope in the session."""
    client.force_login(user)
    Membership.objects.get_or_create(
        user=user,
        society=society,
        defaults={"role": Membership.Role.OWNER, "is_active": True},
    )
    session = client.session
    session["selected_society_id"] = society.pk
    session.save()


def _make_bank_account(society):
    """Create a bank account scoped to the given society."""
    return BankAccountFactory(society=society, code="1.0", name="Bank Account")


def _make_posted_voucher(society, voucher_type, payment_mode, voucher_date, narration="", reference_number=""):
    """Create and 'post' a voucher so it has a voucher_number and posted_at."""
    v = VoucherFactory(
        society=society,
        voucher_type=voucher_type,
        payment_mode=payment_mode,
        voucher_date=voucher_date,
        narration=narration,
        reference_number=reference_number,
    )
    Voucher.objects.filter(pk=v.pk).update(
        voucher_number=v.pk,
        posted_at=voucher_date,
    )
    v.refresh_from_db()
    return v


def _make_bank_debit_entry(society, voucher, bank_account, amount):
    """Create a debit ledger entry on a bank account (money received)."""
    return LedgerEntryFactory(
        voucher=voucher,
        account=bank_account,
        debit=amount,
        credit=Decimal("0.00"),
    )


def _make_bank_credit_entry(society, voucher, bank_account, amount):
    """Create a credit ledger entry on a bank account (money paid)."""
    return LedgerEntryFactory(
        voucher=voucher,
        account=bank_account,
        debit=Decimal("0.00"),
        credit=amount,
    )


# ---------------------------------------------------------------------------
# ReportService.get_brs_data
# ---------------------------------------------------------------------------

class TestGetBRSData:
    def test_returns_expected_keys(self):
        society = SocietyFactory()
        data = ReportService.get_brs_data(society)
        for key in (
            "book_balance",
            "bank_balance",
            "unpresented_credits",
            "unpresented_debits",
            "uncredited_items",
            "outstanding_cheques",
            "adjusted_book_balance",
            "adjusted_bank_balance",
            "is_balanced",
            "difference",
            "matched_count",
            "unmatched_count",
            "exception_count",
            "bank_accounts",
        ):
            assert key in data, f"Missing key: {key}"

    def test_no_transactions_zero_balances(self):
        society = SocietyFactory()
        _make_bank_account(society)
        data = ReportService.get_brs_data(society)
        assert data["book_balance"] == Decimal("0.00")
        assert data["bank_balance"] == Decimal("0.00")
        assert data["is_balanced"] is True
        assert data["difference"] == Decimal("0.00")

    def test_book_balance_computed_from_ledger_entries(self):
        society = SocietyFactory()
        bank_account = _make_bank_account(society)

        v = _make_posted_voucher(
            society, Voucher.VoucherType.RECEIPT, Voucher.PaymentMode.BANK_TRANSFER, date.today()
        )
        _make_bank_debit_entry(society, v, bank_account, Decimal("1000.00"))

        data = ReportService.get_brs_data(society)
        assert data["book_balance"] == Decimal("1000.00")

    def test_book_balance_credit_reduces_balance(self):
        society = SocietyFactory()
        bank_account = _make_bank_account(society)

        v1 = _make_posted_voucher(
            society, Voucher.VoucherType.RECEIPT, Voucher.PaymentMode.BANK_TRANSFER, date.today()
        )
        _make_bank_debit_entry(society, v1, bank_account, Decimal("5000.00"))

        v2 = _make_posted_voucher(
            society, Voucher.VoucherType.PAYMENT, Voucher.PaymentMode.BANK_TRANSFER, date.today()
        )
        _make_bank_credit_entry(society, v2, bank_account, Decimal("2000.00"))

        data = ReportService.get_brs_data(society)
        assert data["book_balance"] == Decimal("3000.00")

    def test_bank_balance_computed_from_transactions(self):
        society = SocietyFactory()
        _make_bank_account(society)
        imp = BankStatementImportFactory(society=society)

        BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("5000.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("1500.00"),
            dr_cr=BankTransaction.DrCr.DEBIT,
        )

        data = ReportService.get_brs_data(society)
        assert data["bank_balance"] == Decimal("3500.00")

    def test_matched_transactions_excluded_from_reconciling_items(self):
        society = SocietyFactory()
        bank_account = _make_bank_account(society)
        imp = BankStatementImportFactory(society=society)

        # Create bank transactions
        bt = BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("1000.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        # Create matching book entry
        v = _make_posted_voucher(
            society, Voucher.VoucherType.RECEIPT, Voucher.PaymentMode.BANK_TRANSFER, date.today()
        )
        entry = _make_bank_debit_entry(society, v, bank_account, Decimal("1000.00"))

        # Create matched link
        ReconciliationLinkFactory(
            society=society,
            bank_transaction=bt,
            voucher_entry=entry,
            matched_amount=Decimal("1000.00"),
            status=ReconciliationLink.Status.MATCHED,
        )

        data = ReportService.get_brs_data(society)
        # No reconciling items because the single transaction is matched
        assert len(data["uncredited_items"]) == 0
        assert len(data["unpresented_credits"]) == 0
        assert len(data["unpresented_debits"]) == 0
        assert len(data["outstanding_cheques"]) == 0

    def test_as_of_date_filters_correctly(self):
        society = SocietyFactory()
        bank_account = _make_bank_account(society)

        v1 = _make_posted_voucher(
            society, Voucher.VoucherType.RECEIPT, Voucher.PaymentMode.BANK_TRANSFER,
            date(2025, 1, 15),
        )
        _make_bank_debit_entry(society, v1, bank_account, Decimal("500.00"))

        v2 = _make_posted_voucher(
            society, Voucher.VoucherType.RECEIPT, Voucher.PaymentMode.BANK_TRANSFER,
            date(2025, 6, 15),
        )
        _make_bank_debit_entry(society, v2, bank_account, Decimal("700.00"))

        data = ReportService.get_brs_data(society, as_of_date=date(2025, 3, 31))
        assert data["book_balance"] == Decimal("500.00")

    def test_bank_duplicate_transactions_excluded(self):
        society = SocietyFactory()
        _make_bank_account(society)
        imp = BankStatementImportFactory(society=society)

        BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("1000.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("99999.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
            is_duplicate=True,
        )

        data = ReportService.get_brs_data(society)
        assert data["bank_balance"] == Decimal("1000.00")


# ---------------------------------------------------------------------------
# ReportService.get_unmatched_report
# ---------------------------------------------------------------------------

class TestGetUnmatchedReport:
    def test_returns_expected_keys(self):
        society = SocietyFactory()
        data = ReportService.get_unmatched_report(society)
        for key in ("book_only", "bank_only", "book_only_total", "bank_only_total"):
            assert key in data, f"Missing key: {key}"

    def test_book_only_entries_when_no_bank_match(self):
        society = SocietyFactory()
        bank_account = _make_bank_account(society)

        v = _make_posted_voucher(
            society, Voucher.VoucherType.RECEIPT, Voucher.PaymentMode.BANK_TRANSFER, date.today()
        )
        _make_bank_debit_entry(society, v, bank_account, Decimal("500.00"))

        data = ReportService.get_unmatched_report(society)
        assert len(data["book_only"]) >= 1
        assert data["book_only_total"] == Decimal("500.00")

    def test_bank_only_entries_when_no_book_match(self):
        society = SocietyFactory()
        _make_bank_account(society)
        imp = BankStatementImportFactory(society=society)

        BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("750.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
        )

        data = ReportService.get_unmatched_report(society)
        assert len(data["bank_only"]) >= 1
        assert data["bank_only_total"] == Decimal("750.00")

    def test_bank_only_entries_with_normalized_record(self):
        society = SocietyFactory()
        _make_bank_account(society)
        imp = BankStatementImportFactory(society=society)

        tx = BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("875.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        BankTransactionNormalizedFactory(
            bank_transaction=tx,
            cleaned_narration="maintenance receipt",
            extracted_utr="UTR875",
            extracted_flat_no="A101",
            extracted_reference="REF875",
        )

        data = ReportService.get_unmatched_report(society)
        assert len(data["bank_only"]) >= 1
        assert data["bank_only"][0]["extracted_info"]["extracted_utr"] == "UTR875"

    def test_both_book_only_and_bank_only(self):
        society = SocietyFactory()
        bank_account = _make_bank_account(society)

        v = _make_posted_voucher(
            society, Voucher.VoucherType.RECEIPT, Voucher.PaymentMode.BANK_TRANSFER, date.today()
        )
        _make_bank_debit_entry(society, v, bank_account, Decimal("300.00"))

        imp = BankStatementImportFactory(society=society)
        BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("400.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
        )

        data = ReportService.get_unmatched_report(society)
        assert len(data["book_only"]) >= 1
        assert len(data["bank_only"]) >= 1

    def test_no_unmatched_items_when_all_matched(self):
        society = SocietyFactory()
        bank_account = _make_bank_account(society)
        imp = BankStatementImportFactory(society=society)

        bt = BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("1000.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        v = _make_posted_voucher(
            society, Voucher.VoucherType.RECEIPT, Voucher.PaymentMode.BANK_TRANSFER, date.today()
        )
        entry = _make_bank_debit_entry(society, v, bank_account, Decimal("1000.00"))

        ReconciliationLinkFactory(
            society=society,
            bank_transaction=bt,
            voucher_entry=entry,
            matched_amount=Decimal("1000.00"),
            status=ReconciliationLink.Status.MATCHED,
        )

        data = ReportService.get_unmatched_report(society)
        assert len(data["book_only"]) == 0
        assert len(data["bank_only"]) == 0

    def test_book_only_excludes_linked_entries(self):
        society = SocietyFactory()
        bank_account = _make_bank_account(society)
        imp = BankStatementImportFactory(society=society)

        bt = BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("1000.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        v = _make_posted_voucher(
            society, Voucher.VoucherType.RECEIPT, Voucher.PaymentMode.BANK_TRANSFER, date.today()
        )
        entry = _make_bank_debit_entry(society, v, bank_account, Decimal("1000.00"))

        ReconciliationLinkFactory(
            society=society,
            bank_transaction=bt,
            voucher_entry=entry,
            matched_amount=Decimal("1000.00"),
            status=ReconciliationLink.Status.MATCHED,
        )

        # Add a second unmatched ledger entry
        v2 = _make_posted_voucher(
            society, Voucher.VoucherType.RECEIPT, Voucher.PaymentMode.BANK_TRANSFER, date.today()
        )
        _make_bank_debit_entry(society, v2, bank_account, Decimal("200.00"))

        data = ReportService.get_unmatched_report(society)
        assert len(data["book_only"]) == 1
        assert data["book_only"][0]["debit"] == Decimal("200.00")


# ---------------------------------------------------------------------------
# ReportService.get_duplicates_report
# ---------------------------------------------------------------------------

class TestGetDuplicatesReport:
    def test_returns_expected_keys(self):
        society = SocietyFactory()
        data = ReportService.get_duplicates_report(society)
        for key in ("duplicate_links", "duplicate_bank_transactions", "suspected_book_duplicates"):
            assert key in data, f"Missing key: {key}"

    def test_duplicate_links_reported(self):
        society = SocietyFactory()
        imp = BankStatementImportFactory(society=society)
        bank_account = _make_bank_account(society)

        bt = BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("500.00"),
        )
        v = _make_posted_voucher(
            society, Voucher.VoucherType.RECEIPT, Voucher.PaymentMode.BANK_TRANSFER, date.today()
        )
        entry = _make_bank_debit_entry(society, v, bank_account, Decimal("500.00"))

        ReconciliationLinkFactory(
            society=society,
            bank_transaction=bt,
            voucher_entry=entry,
            matched_amount=Decimal("500.00"),
            status=ReconciliationLink.Status.DUPLICATE,
        )

        data = ReportService.get_duplicates_report(society)
        assert data["duplicate_links"].count() >= 1

    def test_duplicate_bank_transactions_reported(self):
        society = SocietyFactory()
        imp = BankStatementImportFactory(society=society)

        BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("100.00"),
            is_duplicate=True,
        )

        data = ReportService.get_duplicates_report(society)
        assert len(data["duplicate_bank_transactions"]) >= 1

    def test_no_duplicates_when_none_exist(self):
        society = SocietyFactory()
        _make_bank_account(society)

        data = ReportService.get_duplicates_report(society)
        assert data["duplicate_links"].count() == 0
        assert len(data["duplicate_bank_transactions"]) == 0

    def test_suspected_book_duplicates_detected(self):
        society = SocietyFactory()
        bank_account = _make_bank_account(society)

        # Two identical ledger entries on same date → suspected duplicate
        v1 = _make_posted_voucher(
            society, Voucher.VoucherType.RECEIPT, Voucher.PaymentMode.BANK_TRANSFER, date.today()
        )
        _make_bank_debit_entry(society, v1, bank_account, Decimal("300.00"))

        v2 = _make_posted_voucher(
            society, Voucher.VoucherType.RECEIPT, Voucher.PaymentMode.BANK_TRANSFER, date.today()
        )
        _make_bank_debit_entry(society, v2, bank_account, Decimal("300.00"))

        data = ReportService.get_duplicates_report(society)
        assert len(data["suspected_book_duplicates"]) >= 1
        assert data["suspected_book_duplicates"][0]["count"] == 2


# ---------------------------------------------------------------------------
# ReportService.get_exception_summary
# ---------------------------------------------------------------------------

class TestGetExceptionSummary:
    def test_returns_expected_keys(self):
        society = SocietyFactory()
        data = ReportService.get_exception_summary(society)
        for key in ("by_type", "total_exceptions", "total_amount", "exceptions"):
            assert key in data, f"Missing key: {key}"

    def test_breakdown_by_exception_type(self):
        society = SocietyFactory()
        bank_account = _make_bank_account(society)
        imp = BankStatementImportFactory(society=society)

        bt1 = BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("100.00"),
        )
        v = _make_posted_voucher(
            society, Voucher.VoucherType.RECEIPT, Voucher.PaymentMode.BANK_TRANSFER, date.today()
        )
        entry = _make_bank_debit_entry(society, v, bank_account, Decimal("100.00"))

        ReconciliationLinkFactory(
            society=society,
            bank_transaction=bt1,
            voucher_entry=entry,
            matched_amount=Decimal("100.00"),
            status=ReconciliationLink.Status.EXCEPTION,
            exception_type=ReconciliationLink.ExceptionType.BANK_ONLY,
        )

        bt2 = BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("200.00"),
        )
        entry2 = _make_bank_debit_entry(society, v, bank_account, Decimal("200.00"))
        ReconciliationLinkFactory(
            society=society,
            bank_transaction=bt2,
            voucher_entry=entry2,
            matched_amount=Decimal("200.00"),
            status=ReconciliationLink.Status.EXCEPTION,
            exception_type=ReconciliationLink.ExceptionType.BOOK_ONLY,
        )

        data = ReportService.get_exception_summary(society)
        assert data["total_exceptions"] == 2
        assert data["by_type"]["BANK_ONLY"] == 1
        assert data["by_type"]["BOOK_ONLY"] == 1
        assert data["total_amount"] == Decimal("300.00")

    def test_no_exceptions_returns_empty(self):
        society = SocietyFactory()
        data = ReportService.get_exception_summary(society)
        assert data["total_exceptions"] == 0
        assert data["by_type"] == {}
        assert data["total_amount"] == Decimal("0.00")


# ---------------------------------------------------------------------------
# BRSReportView
# ---------------------------------------------------------------------------

class TestBRSReportView:
    def test_requires_authentication(self, client):
        response = client.get(reverse("reconciliation:report-brs"))
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_no_society_redirects(self, client, user):
        client.force_login(user)
        response = client.get(reverse("reconciliation:report-brs"))
        assert response.status_code == 302
        assert response.url == reverse("reconciliation:dashboard")

    def test_renders_correct_template(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        _make_bank_account(society)

        response = client.get(reverse("reconciliation:report-brs"))
        assert response.status_code == 200
        assert "reconciliation/report_brs.html" in [t.name for t in response.templates]

    def test_context_has_brs_data(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        _make_bank_account(society)

        response = client.get(reverse("reconciliation:report-brs"))
        ctx = response.context
        assert "book_balance" in ctx
        assert "bank_balance" in ctx
        assert "is_balanced" in ctx
        assert "adjusted_book_balance" in ctx
        assert "adjusted_bank_balance" in ctx
        assert "unpresented_credits" in ctx
        assert "unpresented_debits" in ctx
        assert "uncredited_items" in ctx
        assert "outstanding_cheques" in ctx
        assert "society_name" in ctx
        assert "as_of_date" in ctx


# ---------------------------------------------------------------------------
# UnmatchedReportView
# ---------------------------------------------------------------------------

class TestUnmatchedReportView:
    def test_requires_authentication(self, client):
        response = client.get(reverse("reconciliation:report-unmatched"))
        assert response.status_code == 302

    def test_no_society_redirects(self, client, user):
        client.force_login(user)
        response = client.get(reverse("reconciliation:report-unmatched"))
        assert response.status_code == 302

    def test_renders_correct_template(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:report-unmatched"))
        assert response.status_code == 200
        assert "reconciliation/report_unmatched.html" in [t.name for t in response.templates]

    def test_context_has_unmatched_data(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:report-unmatched"))
        ctx = response.context
        assert "book_only" in ctx
        assert "bank_only" in ctx
        assert "book_only_total" in ctx
        assert "bank_only_total" in ctx


# ---------------------------------------------------------------------------
# DuplicateReportView
# ---------------------------------------------------------------------------

class TestDuplicateReportView:
    def test_requires_authentication(self, client):
        response = client.get(reverse("reconciliation:report-duplicates"))
        assert response.status_code == 302

    def test_no_society_redirects(self, client, user):
        client.force_login(user)
        response = client.get(reverse("reconciliation:report-duplicates"))
        assert response.status_code == 302

    def test_renders_correct_template(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:report-duplicates"))
        assert response.status_code == 200
        assert "reconciliation/report_duplicates.html" in [t.name for t in response.templates]

    def test_context_has_duplicate_data(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:report-duplicates"))
        ctx = response.context
        assert "duplicate_links" in ctx
        assert "duplicate_bank_transactions" in ctx
        assert "suspected_book_duplicates" in ctx


# ---------------------------------------------------------------------------
# ExceptionListView
# ---------------------------------------------------------------------------

class TestExceptionListView:
    def test_requires_authentication(self, client):
        response = client.get(reverse("reconciliation:exceptions"))
        assert response.status_code == 302

    def test_renders_correct_template(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:exceptions"))
        assert response.status_code == 200
        assert "reconciliation/exceptions.html" in [t.name for t in response.templates]

    def test_context_includes_exceptions_queryset(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        _make_bank_account(society)
        imp = BankStatementImportFactory(society=society)

        bt = BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("500.00"),
        )
        v = _make_posted_voucher(
            society, Voucher.VoucherType.RECEIPT, Voucher.PaymentMode.BANK_TRANSFER, date.today()
        )
        entry = _make_bank_debit_entry(society, v, _make_bank_account(society), Decimal("500.00"))

        ReconciliationLinkFactory(
            society=society,
            bank_transaction=bt,
            voucher_entry=entry,
            matched_amount=Decimal("500.00"),
            status=ReconciliationLink.Status.EXCEPTION,
            exception_type=ReconciliationLink.ExceptionType.BANK_ONLY,
        )

        response = client.get(reverse("reconciliation:exceptions"))
        assert response.context["exceptions"].count() >= 1

    def test_exception_summary_in_context(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:exceptions"))
        assert "exception_summary" in response.context
        assert "by_type" in response.context["exception_summary"]
        assert "total_exceptions" in response.context["exception_summary"]

    def test_orphan_bank_txs_in_context(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        imp = BankStatementImportFactory(society=society)
        BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("99.00"),
            is_duplicate=False,
        )

        response = client.get(reverse("reconciliation:exceptions"))
        assert "orphan_bank_txs" in response.context
        assert response.context["orphan_bank_txs"].count() >= 1

    def test_pagination_default_50_per_page(self, client, user, settings):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        _make_bank_account(society)
        imp = BankStatementImportFactory(society=society)

        for i in range(60):
            bt = BankTransactionFactory(
                bank_statement_import=imp,
                amount=Decimal(f"{100 + i}.00"),
            )
            v = _make_posted_voucher(
                society, Voucher.VoucherType.RECEIPT, Voucher.PaymentMode.BANK_TRANSFER, date.today()
            )
            entry = _make_bank_debit_entry(society, v, _make_bank_account(society), Decimal(f"{100 + i}.00"))
            ReconciliationLinkFactory(
                society=society,
                bank_transaction=bt,
                voucher_entry=entry,
                matched_amount=Decimal(f"{100 + i}.00"),
                status=ReconciliationLink.Status.EXCEPTION,
                exception_type=ReconciliationLink.ExceptionType.BANK_ONLY,
            )

        response = client.get(reverse("reconciliation:exceptions"))
        assert response.status_code == 200
        assert response.context["is_paginated"] is True
        assert len(response.context["exceptions"]) <= 50
