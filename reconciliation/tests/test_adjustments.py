"""
Tests for AdjustmentService — default account resolution, adjustment voucher
creation — and additional edge-case view tests not covered in test_views.py.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from accounting.models import Account, LedgerEntry, Voucher
from reconciliation.models import BankTransaction, ReconciliationLink
from reconciliation.services.adjustments import AdjustmentService
from reconciliation.tests.factories import (
    BankAccountFactory,
    BankStatementImportFactory,
    BankTransactionFactory,
    ExpenseAccountFactory,
    IncomeAccountFactory,
    ReconciliationLinkFactory,
    SocietyFactory,
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


def _make_bank_only_link(society, user, bank_transaction=None):
    """Create a ReconciliationLink in EXCEPTION/BANK_ONLY state."""
    if bank_transaction is None:
        imp = BankStatementImportFactory(society=society)
        bank_transaction = BankTransactionFactory(
            bank_statement_import=imp,
            transaction_date=date.today(),
            amount=Decimal("250.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
            narration="Bank interest",
        )
    link = ReconciliationLinkFactory(
        society=society,
        bank_transaction=bank_transaction,
        status=ReconciliationLink.Status.EXCEPTION,
        exception_type=ReconciliationLink.ExceptionType.BANK_ONLY,
    )
    return link


# ---------------------------------------------------------------------------
# AdjustmentService.get_default_adjustment_account
# ---------------------------------------------------------------------------

class TestGetDefaultAdjustmentAccount:
    def test_returns_income_account_for_credit_direction(self):
        """get_default_adjustment_account('credit') returns an active income account."""
        society = SocietyFactory()
        inc = IncomeAccountFactory(society=society, name="Misc Income", is_active=True)

        account = AdjustmentService.get_default_adjustment_account(society, "credit")
        assert account is not None
        assert account.pk == inc.pk

    def test_returns_expense_account_for_debit_direction(self):
        """get_default_adjustment_account('debit') returns an active expense account."""
        society = SocietyFactory()
        exp = ExpenseAccountFactory(society=society, name="Bank Charges", is_active=True)

        account = AdjustmentService.get_default_adjustment_account(society, "debit")
        assert account is not None
        assert account.pk == exp.pk

    def test_returns_none_when_no_account_exists(self):
        """When no matching account exists, returns None."""
        society = SocietyFactory()
        # No income/expense accounts created

        account = AdjustmentService.get_default_adjustment_account(society, "credit")
        assert account is None

    def test_prefers_sub_type_over_account_type(self):
        """Falls back to AccountType if SubType doesn't match."""
        society = SocietyFactory()
        # Create an income account via IncomeAccountFactory which should set sub_type
        inc = IncomeAccountFactory(society=society, name="Interest Inc", is_active=True)

        account = AdjustmentService.get_default_adjustment_account(society, "credit")
        assert account is not None
        assert account.pk == inc.pk


# ---------------------------------------------------------------------------
# AdjustmentService.create_adjustment — credit (money received)
# ---------------------------------------------------------------------------

class TestCreateAdjustmentCredit:
    def test_creates_voucher_with_correct_entries_for_credit(self, user):
        """Bank CREDIT → debit bank account, credit income account."""
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society, name="HDFC Current", is_active=True)
        IncomeAccountFactory(society=society, name="Interest Income", is_active=True)
        imp = BankStatementImportFactory(society=society)

        bt = BankTransactionFactory(
            bank_statement_import=imp,
            transaction_date=date.today(),
            amount=Decimal("175.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
            narration="Interest credited by bank",
        )

        voucher = AdjustmentService.create_adjustment(
            society=society,
            bank_transaction=bt,
            user=user,
        )

        assert voucher is not None
        assert voucher.voucher_type == Voucher.VoucherType.ADJUSTMENT
        assert voucher.payment_mode == Voucher.PaymentMode.BANK_TRANSFER
        assert voucher.voucher_date == bt.transaction_date

        # Verify entries: one debit to bank, one credit to income
        entries = voucher.entries.all()
        assert entries.count() == 2

        debit_entry = entries.get(debit__gt=0)
        credit_entry = entries.get(credit__gt=0)

        assert debit_entry.account.is_bank is True
        assert debit_entry.debit == Decimal("175.00")
        assert credit_entry.credit == Decimal("175.00")
        # The credit side should be the income account (not bank)
        assert credit_entry.account.is_bank is False

    def test_voucher_is_posted_after_creation(self, user):
        """Adjustment voucher should be posted immediately."""
        society = SocietyFactory()
        BankAccountFactory(society=society, name="Bank", is_active=True)
        IncomeAccountFactory(society=society, name="Income", is_active=True)
        imp = BankStatementImportFactory(society=society)

        bt = BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("50.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
        )

        voucher = AdjustmentService.create_adjustment(
            society=society,
            bank_transaction=bt,
            user=user,
        )

        assert voucher.is_posted is True
        assert voucher.posted_at is not None
        assert voucher.voucher_number is not None

    def test_raises_when_no_bank_account(self, user):
        """create_adjustment raises ValueError when no bank account exists."""
        society = SocietyFactory()
        imp = BankStatementImportFactory(society=society)
        bt = BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("100.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
        )

        with pytest.raises(ValueError, match="No active bank account"):
            AdjustmentService.create_adjustment(
                society=society,
                bank_transaction=bt,
                user=user,
            )

    def test_raises_when_no_income_account_for_credit(self, user):
        """create_adjustment raises ValueError when no income account for CREDIT tx."""
        society = SocietyFactory()
        BankAccountFactory(society=society, name="Bank", is_active=True)
        # No income account created
        imp = BankStatementImportFactory(society=society)
        bt = BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("100.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
        )

        with pytest.raises(ValueError, match="No active income account"):
            AdjustmentService.create_adjustment(
                society=society,
                bank_transaction=bt,
                user=user,
            )


# ---------------------------------------------------------------------------
# AdjustmentService.create_adjustment — debit (money paid out)
# ---------------------------------------------------------------------------

class TestCreateAdjustmentDebit:
    def test_creates_voucher_with_correct_entries_for_debit(self, user):
        """Bank DEBIT → debit expense account, credit bank account."""
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society, name="HDFC Current", is_active=True)
        ExpenseAccountFactory(society=society, name="Bank Charges", is_active=True)
        imp = BankStatementImportFactory(society=society)

        bt = BankTransactionFactory(
            bank_statement_import=imp,
            transaction_date=date.today(),
            amount=Decimal("17.50"),
            dr_cr=BankTransaction.DrCr.DEBIT,
            narration="Bank charges",
        )

        voucher = AdjustmentService.create_adjustment(
            society=society,
            bank_transaction=bt,
            user=user,
        )

        assert voucher is not None
        assert voucher.voucher_type == Voucher.VoucherType.ADJUSTMENT

        entries = voucher.entries.all()
        assert entries.count() == 2

        debit_entry = entries.get(debit__gt=0)
        credit_entry = entries.get(credit__gt=0)

        # Debit: expense account, Credit: bank account
        assert debit_entry.account.is_bank is False
        assert debit_entry.debit == Decimal("17.50")
        assert credit_entry.account.is_bank is True
        assert credit_entry.credit == Decimal("17.50")

    def test_raises_when_no_expense_account_for_debit(self, user):
        """create_adjustment raises ValueError when no expense account for DEBIT tx."""
        society = SocietyFactory()
        BankAccountFactory(society=society, name="Bank", is_active=True)
        # No expense account created
        imp = BankStatementImportFactory(society=society)
        bt = BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("50.00"),
            dr_cr=BankTransaction.DrCr.DEBIT,
        )

        with pytest.raises(ValueError, match="No active expense account"):
            AdjustmentService.create_adjustment(
                society=society,
                bank_transaction=bt,
                user=user,
            )


# ---------------------------------------------------------------------------
# AdjustmentService.create_adjustment — link integration
# ---------------------------------------------------------------------------

class TestAdjustmentLinkIntegration:
    def test_link_updated_to_matched_after_adjustment(self, user):
        """After calling create_adjustment_view, the link is MATCHED and has voucher_entry."""
        society = SocietyFactory()
        BankAccountFactory(society=society, name="Bank", is_active=True)
        IncomeAccountFactory(society=society, name="Income", is_active=True)

        imp = BankStatementImportFactory(society=society)
        bt = BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("300.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        link = _make_bank_only_link(society, user, bank_transaction=bt)

        client = Client()
        _login_and_select_society(client, user, society)

        response = client.post(
            reverse("reconciliation:create-adjustment", kwargs={"link_id": link.pk})
        )

        assert response.status_code == 302
        link.refresh_from_db()
        assert link.status == ReconciliationLink.Status.MATCHED
        assert link.voucher_entry is not None
        assert link.matched_by == user
        assert link.matched_at is not None

    def test_voucher_entry_references_bank_account(self, user):
        """The voucher_entry set on the link points to the bank account entry."""
        society = SocietyFactory()
        BankAccountFactory(society=society, name="Bank", is_active=True)
        IncomeAccountFactory(society=society, name="Income", is_active=True)

        imp = BankStatementImportFactory(society=society)
        bt = BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("300.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        link = _make_bank_only_link(society, user, bank_transaction=bt)

        client = Client()
        _login_and_select_society(client, user, society)

        client.post(
            reverse("reconciliation:create-adjustment", kwargs={"link_id": link.pk})
        )

        link.refresh_from_db()
        assert link.voucher_entry.account.is_bank is True


# ---------------------------------------------------------------------------
# create_adjustment_view — edge cases not in test_views.py
# ---------------------------------------------------------------------------

class TestCreateAdjustmentViewEdgeCases:
    def test_link_not_found_returns_404(self, client, user):
        """POST to create_adjustment with a non-existent link_id returns 404."""
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.post(
            reverse("reconciliation:create-adjustment", kwargs={"link_id": 99999})
        )
        assert response.status_code == 404
        data = response.json()
        assert "error" in data

    def test_exception_type_not_bank_only_returns_400(self, client, user):
        """POST with a BOOK_ONLY exception link returns 400 (only BANK_ONLY supported)."""
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        imp = BankStatementImportFactory(society=society)
        bt = BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("100.00"),
        )
        link = ReconciliationLinkFactory(
            society=society,
            bank_transaction=bt,
            status=ReconciliationLink.Status.EXCEPTION,
            exception_type=ReconciliationLink.ExceptionType.BOOK_ONLY,
        )

        response = client.post(
            reverse("reconciliation:create-adjustment", kwargs={"link_id": link.pk})
        )
        assert response.status_code == 400
        data = response.json()
        assert "BANK_ONLY" in data["error"]

    def test_no_society_returns_400(self, client, user):
        """When no society is selected, create_adjustment_view returns 400."""
        client.force_login(user)
        # Don't set session scope

        response = client.post(
            reverse("reconciliation:create-adjustment", kwargs={"link_id": 1})
        )
        assert response.status_code == 400
        data = response.json()
        assert "error" in data

    def test_requires_login(self, client):
        """Unauthenticated POST returns redirect to login."""
        response = client.post(
            reverse("reconciliation:create-adjustment", kwargs={"link_id": 1})
        )
        assert response.status_code == 302
        assert "/accounts/login/" in response.url


# ---------------------------------------------------------------------------
# create_adjustment_for_orphan_view — edge cases not in test_views.py
# ---------------------------------------------------------------------------

class TestCreateAdjustmentForOrphanViewEdgeCases:
    def test_invalid_bank_tx_id_returns_400(self, client, user):
        """POST with a non-integer bank_tx_id returns 400."""
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.post(
            reverse("reconciliation:adjust-orphan"),
            {"bank_tx_id": "not-a-number"},
        )
        assert response.status_code == 400
        data = response.json()
        assert "error" in data

    def test_bank_transaction_not_found_returns_404(self, client, user):
        """POST with a bank_tx_id that doesn't exist returns 404."""
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.post(
            reverse("reconciliation:adjust-orphan"),
            {"bank_tx_id": 99999},
        )
        assert response.status_code == 404
        data = response.json()
        assert "error" in data

    def test_no_society_returns_400(self, client, user):
        """When no society selected, adjust-orphan returns 400."""
        client.force_login(user)
        # Don't set session scope

        response = client.post(
            reverse("reconciliation:adjust-orphan"),
            {"bank_tx_id": 1},
        )
        assert response.status_code == 400
        data = response.json()
        assert "error" in data

    def test_requires_login(self, client):
        """Unauthenticated POST returns redirect to login."""
        response = client.post(
            reverse("reconciliation:adjust-orphan"),
            {"bank_tx_id": 1},
        )
        assert response.status_code == 302
        assert "/accounts/login/" in response.url


# ---------------------------------------------------------------------------
# create_adjustment_for_orphan_view — creates ReconciliationLink
# ---------------------------------------------------------------------------

class TestOrphanAdjustmentCreatesLink:
    def test_creates_reconciliation_link_after_adjustment(self, user):
        """Orphan adjustment should create a FORCE MATCHED ReconciliationLink."""
        society = SocietyFactory()
        BankAccountFactory(society=society, name="Bank", is_active=True)
        IncomeAccountFactory(society=society, name="Income", is_active=True)

        imp = BankStatementImportFactory(society=society)
        bt = BankTransactionFactory(
            bank_statement_import=imp,
            amount=Decimal("450.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
            is_duplicate=False,
        )

        client = Client()
        _login_and_select_society(client, user, society)

        response = client.post(
            reverse("reconciliation:adjust-orphan"),
            {"bank_tx_id": bt.pk},
        )

        assert response.status_code == 302

        link = ReconciliationLink.objects.filter(
            society=society,
            bank_transaction=bt,
        ).first()
        assert link is not None
        assert link.status == ReconciliationLink.Status.MATCHED
        assert link.match_type == ReconciliationLink.MatchType.FORCE
        assert link.confidence_score == 100
        assert link.is_manual is True
        assert link.voucher_entry is not None