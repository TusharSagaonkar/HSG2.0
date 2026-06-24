"""
Tests for the redesigned Manual Bank Statement Entry module.

Covers:
  - Manual entry page loads correctly
  - Row add HTMX endpoint returns row HTML
  - Row validation endpoint returns errors for invalid data
  - Batch save creates BankStatementImport and BankTransaction records
  - Shortcodes endpoint returns correct mappings
  - Service layer validation logic
"""

from datetime import date
from decimal import Decimal

import pytest
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.urls import reverse

from accounting.models import Account, Voucher
from reconciliation.models import BankStatementImport, BankTransaction, ReconciliationLink
from reconciliation.services.manual_entry_batch_service import (
    SHORTCODE_MAP,
    calculate_balances,
    get_shortcodes,
    save_batch,
    validate_row,
)
from reconciliation.tests.factories import (
    BankAccountFactory,
    BankStatementImportFactory,
    BankTransactionFactory,
    LedgerEntryFactory,
    ReconciliationLinkFactory,
    VoucherFactory,
    SocietyFactory,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _login_and_select_society(client, user, society):
    """Log the user in and set the society selection in the session."""
    from societies.models import Membership

    client.force_login(user)
    Membership.objects.get_or_create(
        user=user,
        society=society,
        defaults={"role": Membership.Role.OWNER, "is_active": True},
    )
    session = client.session
    session["selected_society_id"] = society.pk
    session.save()


def _valid_row_dict(**overrides):
    """Return a valid row dict for tests."""
    return {
        "date": "15/06/2026",
        "narration": "Maintenance Collection",
        "reference_no": "REF-001",
        "debit": "0",
        "credit": "5000.00",
        **overrides,
    }


# ---------------------------------------------------------------------------
# Page Load Tests
# ---------------------------------------------------------------------------

class TestManualEntryPage:
    def test_requires_authentication(self, client):
        response = client.get(reverse("reconciliation:manual-entry"))
        assert response.status_code == 302

    def test_no_society_redirects_to_dashboard(self, client, user):
        client.force_login(user)
        response = client.get(reverse("reconciliation:manual-entry"))
        assert response.status_code == 302
        assert response.url == reverse("reconciliation:dashboard")

    def test_renders_template_with_society(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:manual-entry"))

        assert response.status_code == 200
        template_names = [t.name for t in response.templates]
        assert "reconciliation/manual_entry.html" in template_names

    def test_context_has_bank_accounts(self, client, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:manual-entry"))

        assert response.status_code == 200
        assert "bank_accounts" in response.context
        assert bank_account in response.context["bank_accounts"]

    def test_context_has_batch_form(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:manual-entry"))

        assert response.status_code == 200
        assert "batch_form" in response.context
        assert "row_form" in response.context

    def test_context_has_shortcodes_url(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:manual-entry"))

        assert response.status_code == 200
        assert "shortcodes_url" in response.context
        assert "narrations_url" in response.context

    def test_context_has_recent_bank_transactions_for_reconciliation(self, client, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        statement = BankStatementImportFactory(
            society=society,
            bank_account=bank_account,
            uploaded_by=user,
        )
        transaction = BankTransactionFactory(
            bank_statement_import=statement,
            narration="Bank-side reconciliation reference",
            reference_no="BANK-REF-001",
            amount=Decimal("1250.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
            balance=Decimal("11250.00"),
        )
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:manual-entry"))

        assert response.status_code == 200
        assert response.context["selected_bank_account"] == bank_account
        assert "recent_bank_transactions" in response.context
        assert transaction in response.context["recent_bank_transactions"]
        content = response.content.decode()
        assert "Saved Bank-Side Entries" in content
        assert "Bank-side reconciliation reference" in content
        assert "BANK-REF-001" in content
        assert "Unmatched" in content

    def test_recent_bank_transactions_show_reconciliation_link(self, client, user):
        society = SocietyFactory()
        bank_tx = BankTransactionFactory(
            bank_statement_import__society=society,
            narration="Matched bank reference",
            amount=Decimal("1000.00"),
        )
        link = ReconciliationLinkFactory(
            society=society,
            bank_transaction=bank_tx,
            matched_amount=bank_tx.amount,
            status=ReconciliationLink.Status.MATCHED,
        )
        _login_and_select_society(client, user, society)

        response = client.get(
            reverse("reconciliation:manual-entry"),
            {"recon_filter": "reconciled"},
        )

        assert response.status_code == 200
        content = response.content.decode()
        assert "Matched bank reference" in content
        assert link.get_status_display() in content
        assert link.voucher_entry.account.name in content

    @staticmethod
    def _posted_bank_ledger_entry(
        society,
        bank_account,
        narration,
        amount="500.00",
        voucher_date=None,
        payment_mode="",
        voucher_type=None,
    ):
        voucher = VoucherFactory(
            society=society,
            narration=narration,
            voucher_date=voucher_date or date.today(),
            payment_mode=payment_mode,
            voucher_type=voucher_type or Voucher.VoucherType.RECEIPT,
        )
        ledger_entry = LedgerEntryFactory(
            voucher=voucher,
            account=bank_account,
            debit=Decimal(amount),
            credit=Decimal("0.00"),
        )
        voucher.posted_at = timezone.now()
        voucher.save(update_fields=["posted_at"])
        return ledger_entry

    def test_filters_reference_data_by_selected_bank_account(self, client, user):
        society = SocietyFactory()
        selected_bank = BankAccountFactory(society=society, name="Selected Bank")
        other_bank = BankAccountFactory(society=society, name="Other Bank")
        selected_statement = BankStatementImportFactory(
            society=society,
            bank_account=selected_bank,
            uploaded_by=user,
        )
        other_statement = BankStatementImportFactory(
            society=society,
            bank_account=other_bank,
            uploaded_by=user,
        )
        selected_tx = BankTransactionFactory(
            bank_statement_import=selected_statement,
            narration="Selected bank-side row",
        )
        other_tx = BankTransactionFactory(
            bank_statement_import=other_statement,
            narration="Other bank-side row",
        )
        self._posted_bank_ledger_entry(
            society,
            selected_bank,
            "Selected bank voucher",
        )
        _login_and_select_society(client, user, society)

        response = client.get(
            reverse("reconciliation:manual-entry"),
            {"bank_account": str(selected_bank.pk)},
        )

        assert response.status_code == 200
        assert response.context["selected_bank_account"] == selected_bank
        assert selected_tx in response.context["recent_bank_transactions"]
        assert other_tx not in response.context["recent_bank_transactions"]
        content = response.content.decode()
        assert "Voucher Entries" in content
        assert "Selected bank voucher" in content
        assert "Selected bank-side row" in content
        assert "Other bank-side row" not in content

    def test_default_recon_filter_shows_only_pending_rows(self, client, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        pending_entry = self._posted_bank_ledger_entry(
            society,
            bank_account,
            "Pending voucher row",
        )
        matched_entry = self._posted_bank_ledger_entry(
            society,
            bank_account,
            "Already reconciled voucher row",
            amount="750.00",
        )
        pending_statement = BankStatementImportFactory(society=society, bank_account=bank_account)
        matched_statement = BankStatementImportFactory(society=society, bank_account=bank_account)
        pending_tx = BankTransactionFactory(
            bank_statement_import=pending_statement,
            narration="Pending bank-side row",
        )
        matched_tx = BankTransactionFactory(
            bank_statement_import=matched_statement,
            narration="Already reconciled bank-side row",
        )
        ReconciliationLinkFactory(
            society=society,
            bank_transaction=matched_tx,
            voucher_entry=matched_entry,
            matched_amount=Decimal("750.00"),
            status=ReconciliationLink.Status.MATCHED,
        )
        _login_and_select_society(client, user, society)

        response = client.get(
            reverse("reconciliation:manual-entry"),
            {"bank_account": str(bank_account.pk)},
        )

        assert response.status_code == 200
        assert response.context["recon_filter"] == "pending"
        assert pending_entry in response.context["voucher_entries"]
        assert matched_entry not in response.context["voucher_entries"]
        assert pending_tx in response.context["recent_bank_transactions"]
        assert matched_tx not in response.context["recent_bank_transactions"]
        content = response.content.decode()
        assert "Pending voucher row" in content
        assert "Already reconciled voucher row" not in content
        assert "Pending bank-side row" in content
        assert "Already reconciled bank-side row" not in content

    def test_reconciled_filter_shows_matched_rows(self, client, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        pending_entry = self._posted_bank_ledger_entry(
            society,
            bank_account,
            "Pending voucher excluded from reconciled filter",
        )
        matched_entry = self._posted_bank_ledger_entry(
            society,
            bank_account,
            "Matched voucher visible in reconciled filter",
            amount="750.00",
        )
        statement = BankStatementImportFactory(society=society, bank_account=bank_account)
        matched_tx = BankTransactionFactory(
            bank_statement_import=statement,
            narration="Matched bank visible in reconciled filter",
        )
        ReconciliationLinkFactory(
            society=society,
            bank_transaction=matched_tx,
            voucher_entry=matched_entry,
            matched_amount=Decimal("750.00"),
            status=ReconciliationLink.Status.FORCE_MATCHED,
        )
        _login_and_select_society(client, user, society)

        response = client.get(
            reverse("reconciliation:manual-entry"),
            {"bank_account": str(bank_account.pk), "recon_filter": "reconciled"},
        )

        assert response.status_code == 200
        assert response.context["recon_filter"] == "reconciled"
        assert matched_entry in response.context["voucher_entries"]
        assert pending_entry not in response.context["voucher_entries"]
        assert matched_tx in response.context["recent_bank_transactions"]
        content = response.content.decode()
        assert "Matched voucher visible in reconciled filter" in content
        assert "Pending voucher excluded from reconciled filter" not in content
        assert "Matched bank visible in reconciled filter" in content

    def test_manual_filter_shows_only_manual_matches(self, client, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        manual_entry = self._posted_bank_ledger_entry(
            society,
            bank_account,
            "Manual matched voucher row",
            amount="900.00",
        )
        auto_entry = self._posted_bank_ledger_entry(
            society,
            bank_account,
            "Auto matched voucher row",
            amount="950.00",
        )
        manual_statement = BankStatementImportFactory(society=society, bank_account=bank_account)
        auto_statement = BankStatementImportFactory(society=society, bank_account=bank_account)
        manual_tx = BankTransactionFactory(
            bank_statement_import=manual_statement,
            narration="Manual matched bank row",
        )
        auto_tx = BankTransactionFactory(
            bank_statement_import=auto_statement,
            narration="Auto matched bank row",
        )
        ReconciliationLinkFactory(
            society=society,
            bank_transaction=manual_tx,
            voucher_entry=manual_entry,
            matched_amount=Decimal("900.00"),
            status=ReconciliationLink.Status.FORCE_MATCHED,
            is_manual=True,
        )
        ReconciliationLinkFactory(
            society=society,
            bank_transaction=auto_tx,
            voucher_entry=auto_entry,
            matched_amount=Decimal("950.00"),
            status=ReconciliationLink.Status.MATCHED,
            is_manual=False,
        )
        _login_and_select_society(client, user, society)

        response = client.get(
            reverse("reconciliation:manual-entry"),
            {"bank_account": str(bank_account.pk), "recon_filter": "manual"},
        )

        assert response.status_code == 200
        assert response.context["recon_filter"] == "manual"
        assert manual_entry in response.context["voucher_entries"]
        assert auto_entry not in response.context["voucher_entries"]
        assert manual_tx in response.context["recent_bank_transactions"]
        assert auto_tx not in response.context["recent_bank_transactions"]
        content = response.content.decode()
        assert "Manual matched voucher row" in content
        assert "Auto matched voucher row" not in content
        assert "Manual matched bank row" in content
        assert "Auto matched bank row" not in content


    def test_filter_by_date_amount_and_type(self, client, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        visible_entry = self._posted_bank_ledger_entry(
            society,
            bank_account,
            "Visible debit-side voucher",
            amount="1200.00",
            voucher_date=date(2026, 6, 15),
        )
        hidden_entry = self._posted_bank_ledger_entry(
            society,
            bank_account,
            "Hidden low amount voucher",
            amount="300.00",
            voucher_date=date(2026, 6, 10),
        )
        visible_statement = BankStatementImportFactory(society=society, bank_account=bank_account)
        hidden_statement = BankStatementImportFactory(society=society, bank_account=bank_account)
        visible_tx = BankTransactionFactory(
            bank_statement_import=visible_statement,
            transaction_date=date(2026, 6, 16),
            narration="Visible credit bank row",
            amount=Decimal("1200.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        hidden_tx = BankTransactionFactory(
            bank_statement_import=hidden_statement,
            transaction_date=date(2026, 6, 20),
            narration="Hidden debit bank row",
            amount=Decimal("300.00"),
            dr_cr=BankTransaction.DrCr.DEBIT,
        )
        _login_and_select_society(client, user, society)

        response = client.get(
            reverse("reconciliation:manual-entry"),
            {
                "bank_account": str(bank_account.pk),
                "date_from": "2026-06-14",
                "date_to": "2026-06-18",
                "amount_min": "1000",
                "amount_max": "1500",
                "dr_cr": BankTransaction.DrCr.CREDIT,
            },
        )

        assert response.status_code == 200
        assert visible_entry in response.context["voucher_entries"]
        assert hidden_entry not in response.context["voucher_entries"]
        assert visible_tx in response.context["recent_bank_transactions"]
        assert hidden_tx not in response.context["recent_bank_transactions"]
        assert response.context["filter_values"]["dr_cr"] == BankTransaction.DrCr.CREDIT
        content = response.content.decode()
        assert "Visible debit-side voucher" in content
        assert "Hidden low amount voucher" not in content
        assert "Visible credit bank row" in content
        assert "Hidden debit bank row" not in content

    def test_filter_by_search_payment_mode_and_voucher_type(self, client, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        visible_entry = self._posted_bank_ledger_entry(
            society,
            bank_account,
            "NEFT maintenance receipt",
            amount="800.00",
            payment_mode="BANK_TRANSFER",
            voucher_type="RECEIPT",
        )
        hidden_entry = self._posted_bank_ledger_entry(
            society,
            bank_account,
            "Cash payment voucher",
            amount="800.00",
            payment_mode="CASH",
            voucher_type="PAYMENT",
        )
        visible_statement = BankStatementImportFactory(society=society, bank_account=bank_account)
        hidden_statement = BankStatementImportFactory(society=society, bank_account=bank_account)
        visible_tx = BankTransactionFactory(
            bank_statement_import=visible_statement,
            narration="Bank row with NEFT maintenance receipt",
            reference_no="NEFT-7788",
        )
        hidden_tx = BankTransactionFactory(
            bank_statement_import=hidden_statement,
            narration="Unrelated bank row",
            reference_no="UPI-1000",
        )
        _login_and_select_society(client, user, society)

        response = client.get(
            reverse("reconciliation:manual-entry"),
            {
                "bank_account": str(bank_account.pk),
                "search": "NEFT",
                "payment_mode": "BANK_TRANSFER",
                "voucher_type": "RECEIPT",
            },
        )

        assert response.status_code == 200
        assert visible_entry in response.context["voucher_entries"]
        assert hidden_entry not in response.context["voucher_entries"]
        assert visible_tx in response.context["recent_bank_transactions"]
        assert hidden_tx not in response.context["recent_bank_transactions"]
        assert response.context["filter_values"]["search"] == "NEFT"
        assert response.context["filter_values"]["payment_mode"] == "BANK_TRANSFER"
        assert response.context["filter_values"]["voucher_type"] == "RECEIPT"
        content = response.content.decode()
        assert "NEFT maintenance receipt" in content
        assert "Cash payment voucher" not in content
        assert "Bank row with NEFT maintenance receipt" in content
        assert "Unrelated bank row" not in content


# ---------------------------------------------------------------------------
# Voucher-first Manual Reconciliation Endpoint
# ---------------------------------------------------------------------------

class TestManualEntryVoucherMatch:
    def test_creates_bank_entry_and_match_for_voucher(self, client, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        voucher = VoucherFactory(
            society=society,
            voucher_date=date(2026, 6, 15),
            narration="Hard copy statement receipt",
            reference_number="VCH-REF-001",
        )
        ledger_entry = LedgerEntryFactory(
            voucher=voucher,
            account=bank_account,
            debit=Decimal("1250.00"),
            credit=Decimal("0.00"),
        )
        voucher.posted_at = timezone.now()
        voucher.save(update_fields=["posted_at"])
        _login_and_select_society(client, user, society)

        response = client.post(
            reverse("reconciliation:manual-entry-voucher-match"),
            {
                "ledger_entry": str(ledger_entry.pk),
                "transaction_date": "2026-06-16",
                "reference_no": "BANK-REF-001",
                "narration": "Bank receipt from hard copy",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        assert response.status_code == 201
        data = response.json()
        bank_tx = BankTransaction.objects.get(pk=data["bank_transaction_id"])
        link = ReconciliationLink.objects.get(pk=data["link_id"])
        assert bank_tx.bank_statement_import.bank_account == bank_account
        assert bank_tx.bank_statement_import.source_type == "MANUAL_RECON"
        assert bank_tx.transaction_date == date(2026, 6, 16)
        assert bank_tx.reference_no == "BANK-REF-001"
        assert bank_tx.amount == Decimal("1250.00")
        assert bank_tx.dr_cr == BankTransaction.DrCr.CREDIT
        assert link.voucher_entry == ledger_entry
        assert link.bank_transaction == bank_tx
        assert link.status == ReconciliationLink.Status.FORCE_MATCHED
        assert link.is_manual is True

    def test_rejects_already_reconciled_voucher_entry(self, client, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        bank_tx = BankTransactionFactory(bank_statement_import__society=society)
        voucher = VoucherFactory(society=society)
        ledger_entry = LedgerEntryFactory(
            voucher=voucher,
            account=bank_account,
            debit=Decimal("1000.00"),
            credit=Decimal("0.00"),
        )
        voucher.posted_at = timezone.now()
        voucher.save(update_fields=["posted_at"])
        ReconciliationLinkFactory(
            society=society,
            bank_transaction=bank_tx,
            voucher_entry=ledger_entry,
            matched_amount=Decimal("1000.00"),
            status=ReconciliationLink.Status.FORCE_MATCHED,
            match_type=ReconciliationLink.MatchType.FORCE,
        )
        _login_and_select_society(client, user, society)

        response = client.post(
            reverse("reconciliation:manual-entry-voucher-match"),
            {
                "ledger_entry": str(ledger_entry.pk),
                "transaction_date": "2026-06-16",
                "reference_no": "BANK-REF-001",
                "narration": "Duplicate manual recon",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        assert response.status_code == 409
        assert BankTransaction.objects.filter(narration="Duplicate manual recon").count() == 0


    def test_edits_manual_match_by_reversing_and_recreating_link(self, client, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        voucher = VoucherFactory(society=society, narration="Original manual receipt")
        ledger_entry = LedgerEntryFactory(
            voucher=voucher,
            account=bank_account,
            debit=Decimal("1500.00"),
            credit=Decimal("0.00"),
        )
        voucher.posted_at = timezone.now()
        voucher.save(update_fields=["posted_at"])
        statement_import = BankStatementImportFactory(
            society=society,
            bank_account=bank_account,
            source_type="MANUAL_RECON",
        )
        old_bank_tx = BankTransactionFactory(
            bank_statement_import=statement_import,
            transaction_date=date(2026, 6, 15),
            narration="Old bank narration",
            reference_no="OLD-REF",
            amount=Decimal("1500.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        old_link = ReconciliationLinkFactory(
            society=society,
            bank_transaction=old_bank_tx,
            voucher_entry=ledger_entry,
            matched_amount=Decimal("1500.00"),
            status=ReconciliationLink.Status.FORCE_MATCHED,
            match_type=ReconciliationLink.MatchType.FORCE,
            is_manual=True,
        )
        _login_and_select_society(client, user, society)

        response = client.post(
            reverse("reconciliation:manual-entry-voucher-match-edit", kwargs={"link_id": old_link.pk}),
            {
                "transaction_date": "2026-06-18",
                "reference_no": "NEW-REF",
                "narration": "Corrected bank narration",
                "remarks": "Corrected bank ref from passbook",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        assert response.status_code == 201
        data = response.json()
        old_link.refresh_from_db()
        new_link = ReconciliationLink.objects.get(pk=data["link_id"])
        new_bank_tx = BankTransaction.objects.get(pk=data["bank_transaction_id"])
        assert old_link.status == ReconciliationLink.Status.REVERSED
        assert "Reversed before manual edit" in old_link.remarks
        assert new_link.voucher_entry == ledger_entry
        assert new_link.bank_transaction == new_bank_tx
        assert new_link.is_manual is True
        assert new_link.status == ReconciliationLink.Status.FORCE_MATCHED
        assert new_link.remarks == "Corrected bank ref from passbook"
        assert new_bank_tx.bank_statement_import.source_type == "MANUAL_RECON"
        assert new_bank_tx.bank_statement_import.bank_account == bank_account
        assert new_bank_tx.transaction_date == date(2026, 6, 18)
        assert new_bank_tx.reference_no == "NEW-REF"
        assert new_bank_tx.narration == "Corrected bank narration"
        assert new_bank_tx.amount == Decimal("1500.00")
        assert new_bank_tx.raw_row_data["previous_link_id"] == old_link.id
        assert new_bank_tx.raw_row_data["previous_bank_transaction_id"] == old_bank_tx.id

    def test_rejects_edit_for_non_manual_match(self, client, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        voucher = VoucherFactory(society=society)
        ledger_entry = LedgerEntryFactory(
            voucher=voucher,
            account=bank_account,
            debit=Decimal("1000.00"),
            credit=Decimal("0.00"),
        )
        voucher.posted_at = timezone.now()
        voucher.save(update_fields=["posted_at"])
        bank_tx = BankTransactionFactory(
            bank_statement_import__society=society,
            bank_statement_import__bank_account=bank_account,
        )
        link = ReconciliationLinkFactory(
            society=society,
            bank_transaction=bank_tx,
            voucher_entry=ledger_entry,
            matched_amount=Decimal("1000.00"),
            status=ReconciliationLink.Status.FORCE_MATCHED,
            match_type=ReconciliationLink.MatchType.FORCE,
            is_manual=False,
        )
        _login_and_select_society(client, user, society)

        response = client.post(
            reverse("reconciliation:manual-entry-voucher-match-edit", kwargs={"link_id": link.pk}),
            {
                "transaction_date": "2026-06-18",
                "reference_no": "NEW-REF",
                "narration": "Should not update",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        assert response.status_code == 403
        link.refresh_from_db()
        assert link.status == ReconciliationLink.Status.FORCE_MATCHED
        assert BankTransaction.objects.filter(narration="Should not update").count() == 0


# ---------------------------------------------------------------------------
# Row Add HTMX Endpoint
# ---------------------------------------------------------------------------

class TestManualEntryRowAdd:
    def test_requires_authentication(self, client):
        response = client.post(reverse("reconciliation:manual-entry-row-add"))
        assert response.status_code == 302

    def test_no_society_returns_bad_request(self, client, user):
        client.force_login(user)
        response = client.post(reverse("reconciliation:manual-entry-row-add"))
        assert response.status_code == 400

    def test_returns_row_html(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.post(
            reverse("reconciliation:manual-entry-row-add"),
            {"row_index": "0"},
        )

        assert response.status_code == 200
        template_names = [t.name for t in response.templates]
        assert "reconciliation/partials/manual_entry_row.html" in template_names
        assert "row_form" in response.context
        assert "row_number" in response.context

    def test_returns_row_html_with_row_index(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.post(
            reverse("reconciliation:manual-entry-row-add"),
            {"row_index": "5"},
        )

        assert response.status_code == 200
        assert response.context["row_number"] == 5


# ---------------------------------------------------------------------------
# Row Validation Endpoint
# ---------------------------------------------------------------------------

class TestManualEntryRowValidate:
    def test_requires_authentication(self, client):
        response = client.post(reverse("reconciliation:manual-entry-row-validate"))
        assert response.status_code == 302

    def test_valid_row_returns_200(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.post(
            reverse("reconciliation:manual-entry-row-validate"),
            {
                "date": "15/06/2026",
                "narration": "Test",
                "reference_no": "",
                "debit": "0",
                "credit": "100.00",
                "row_index": "0",
            },
        )

        assert response.status_code == 200

    def test_invalid_row_returns_422(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.post(
            reverse("reconciliation:manual-entry-row-validate"),
            {
                "date": "invalid-date",
                "narration": "",
                "reference_no": "",
                "debit": "",
                "credit": "",
                "row_index": "0",
            },
        )

        assert response.status_code == 422

    def test_both_debit_and_credit_returns_422(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.post(
            reverse("reconciliation:manual-entry-row-validate"),
            {
                "date": "15/06/2026",
                "narration": "Test",
                "reference_no": "",
                "debit": "100.00",
                "credit": "100.00",
                "row_index": "0",
            },
        )

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Batch Save Endpoint
# ---------------------------------------------------------------------------

class TestManualEntryBatchSave:
    def _batch_post_data(self, bank_account, **overrides):
        """Return default valid batch save POST data."""
        data = {
            "bank_account": str(bank_account.pk),
            "period_start": "2026-06-01",
            "period_end": "2026-06-30",
            "opening_balance": "10000.00",
            "rows": '[{"date":"15/06/2026","narration":"Test","reference_no":"REF1","debit":"0","credit":"500.00"}]',
        }
        data.update(overrides)
        return data

    @staticmethod
    def _make_bank_account(society):
        """Create a bank account for the given society.

        Uses BankAccountFactory's default code generation (dot-notation)
        which is guaranteed unique per-society via django_get_or_create.
        """
        return BankAccountFactory(society=society)

    def test_requires_authentication(self, client):
        response = client.post(reverse("reconciliation:manual-entry-batch-save"))
        assert response.status_code == 302

    def test_no_society_returns_error(self, client, user):
        client.force_login(user)
        response = client.post(reverse("reconciliation:manual-entry-batch-save"))
        assert response.status_code == 400

    def test_successful_save_creates_records(self, client, user):
        society = SocietyFactory()
        bank_account = self._make_bank_account(society)
        _login_and_select_society(client, user, society)

        response = client.post(
            reverse("reconciliation:manual-entry-batch-save"),
            self._batch_post_data(bank_account),
        )

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "ok"
        assert data["import_id"] is not None
        assert data["transaction_count"] == 1

        # Verify DB records
        statement = BankStatementImport.objects.get(pk=data["import_id"])
        assert statement.source_type == "MANUAL"
        assert statement.society == society
        assert statement.bank_account == bank_account
        assert statement.row_count == 1

        transactions = BankTransaction.objects.filter(
            bank_statement_import=statement,
        )
        assert transactions.count() == 1
        tx = transactions.first()
        assert tx.narration == "Test"
        assert tx.amount == Decimal("500.00")
        assert tx.dr_cr == BankTransaction.DrCr.CREDIT

    def test_empty_rows_returns_error(self, client, user):
        society = SocietyFactory()
        bank_account = self._make_bank_account(society)
        _login_and_select_society(client, user, society)

        response = client.post(
            reverse("reconciliation:manual-entry-batch-save"),
            self._batch_post_data(bank_account, rows="[]"),
        )

        assert response.status_code == 400
        data = response.json()
        error_msg = str(data.get("error", "")).lower()
        assert "no rows to save" in error_msg

    def test_invalid_rows_json_returns_error(self, client, user):
        society = SocietyFactory()
        bank_account = self._make_bank_account(society)
        _login_and_select_society(client, user, society)

        response = client.post(
            reverse("reconciliation:manual-entry-batch-save"),
            self._batch_post_data(bank_account, rows="not-json"),
        )

        assert response.status_code == 400

    def test_row_validation_errors_returned(self, client, user):
        society = SocietyFactory()
        bank_account = self._make_bank_account(society)
        _login_and_select_society(client, user, society)

        response = client.post(
            reverse("reconciliation:manual-entry-batch-save"),
            self._batch_post_data(
                bank_account,
                rows='[{"date":"bad","narration":"","reference_no":"","debit":"","credit":""}]',
            ),
        )

        assert response.status_code == 400
        data = response.json()
        assert "row_errors" in data

    def test_invalid_batch_header_returns_error(self, client, user):
        society = SocietyFactory()
        bank_account = self._make_bank_account(society)
        _login_and_select_society(client, user, society)

        response = client.post(
            reverse("reconciliation:manual-entry-batch-save"),
            self._batch_post_data(bank_account, period_start="bad-date"),
        )

        assert response.status_code == 400

    def test_multiple_rows_save_successfully(self, client, user):
        society = SocietyFactory()
        bank_account = self._make_bank_account(society)
        _login_and_select_society(client, user, society)

        rows = [
            {"date": "15/06/2026", "narration": "Row 1", "reference_no": "", "debit": "100.00", "credit": "0"},
            {"date": "16/06/2026", "narration": "Row 2", "reference_no": "", "debit": "0", "credit": "200.00"},
        ]

        response = client.post(
            reverse("reconciliation:manual-entry-batch-save"),
            self._batch_post_data(bank_account, rows=__import__("json").dumps(rows)),
        )

        assert response.status_code == 201
        data = response.json()
        assert data["transaction_count"] == 2

        statement = BankStatementImport.objects.get(pk=data["import_id"])
        assert statement.row_count == 2


# ---------------------------------------------------------------------------
# Shortcodes Endpoint
# ---------------------------------------------------------------------------

class TestManualEntryShortcodes:
    def test_requires_authentication(self, client):
        response = client.get(reverse("reconciliation:manual-entry-shortcodes"))
        assert response.status_code == 302

    def test_returns_shortcodes_json(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:manual-entry-shortcodes"))

        assert response.status_code == 200
        data = response.json()
        assert "shortcodes" in data
        assert isinstance(data["shortcodes"], dict)
        # Verify key shortcodes are present
        assert data["shortcodes"]["mc"] == "Maintenance Collection"
        assert data["shortcodes"]["bc"] == "Bank Charges"


# ---------------------------------------------------------------------------
# Narrations Endpoint
# ---------------------------------------------------------------------------

class TestManualEntryNarrations:
    def test_requires_authentication(self, client):
        response = client.get(reverse("reconciliation:manual-entry-narrations"))
        assert response.status_code == 302

    def test_returns_empty_when_no_narrations(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:manual-entry-narrations"))

        assert response.status_code == 200
        data = response.json()
        assert data["narrations"] == []

    def test_returns_existing_narrations(self, client, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        _login_and_select_society(client, user, society)

        statement = BankStatementImportFactory(
            society=society,
            bank_account=bank_account,
            source_type="MANUAL",
            uploaded_by=user,
        )
        BankTransactionFactory(
            bank_statement_import=statement,
            narration="Maintenance Collection",
            transaction_date=date.today(),
            amount=Decimal("500.00"),
        )

        response = client.get(reverse("reconciliation:manual-entry-narrations"))

        assert response.status_code == 200
        data = response.json()
        assert "Maintenance Collection" in data["narrations"]


# ---------------------------------------------------------------------------
# Service Layer: validate_row
# ---------------------------------------------------------------------------

class TestServiceValidateRow:
    def test_valid_row_passes(self):
        row = _valid_row_dict()
        is_valid, errors = validate_row(row)
        assert is_valid is True
        assert errors == {}

    def test_missing_date_fails(self):
        row = _valid_row_dict(date="")
        is_valid, errors = validate_row(row)
        assert is_valid is False
        assert "date" in errors

    def test_invalid_date_fails(self):
        row = _valid_row_dict(date="not-a-date")
        is_valid, errors = validate_row(row)
        assert is_valid is False
        assert "date" in errors

    def test_missing_narration_fails(self):
        row = _valid_row_dict(narration="")
        is_valid, errors = validate_row(row)
        assert is_valid is False
        assert "narration" in errors

    def test_whitespace_only_narration_fails(self):
        row = _valid_row_dict(narration="   ")
        is_valid, errors = validate_row(row)
        assert is_valid is False
        assert "narration" in errors

    def test_no_amount_fails(self):
        row = _valid_row_dict(debit="", credit="")
        is_valid, errors = validate_row(row)
        assert is_valid is False
        assert "amount" in errors

    def test_both_debit_and_credit_fails(self):
        row = _valid_row_dict(debit="100.00", credit="100.00")
        is_valid, errors = validate_row(row)
        assert is_valid is False
        assert "amount" in errors

    def test_negative_debit_fails(self):
        row = _valid_row_dict(debit="-10.00", credit="0")
        is_valid, errors = validate_row(row)
        assert is_valid is False
        assert "debit" in errors

    def test_negative_credit_fails(self):
        row = _valid_row_dict(debit="0", credit="-10.00")
        is_valid, errors = validate_row(row)
        assert is_valid is False
        assert "credit" in errors

    def test_zero_debit_and_credit_fails(self):
        row = _valid_row_dict(debit="0", credit="0")
        is_valid, errors = validate_row(row)
        assert is_valid is False
        assert "amount" in errors

    def test_debit_only_passes(self):
        row = _valid_row_dict(debit="100.00", credit="0")
        is_valid, errors = validate_row(row)
        assert is_valid is True
        assert errors == {}

    def test_credit_only_passes(self):
        row = _valid_row_dict(debit="0", credit="100.00")
        is_valid, errors = validate_row(row)
        assert is_valid is True
        assert errors == {}

    def test_yyyy_mm_dd_date_format(self):
        row = _valid_row_dict(date="2026-06-15")
        is_valid, errors = validate_row(row)
        assert is_valid is True
        assert "date" not in errors

    def test_dd_mm_yyyy_date_format(self):
        row = _valid_row_dict(date="15-06-2026")
        is_valid, errors = validate_row(row)
        assert is_valid is True
        assert "date" not in errors


# ---------------------------------------------------------------------------
# Service Layer: calculate_balances
# ---------------------------------------------------------------------------

class TestServiceCalculateBalances:
    def test_balance_from_opening(self):
        rows = [
            {"debit": "0", "credit": "100.00"},
            {"debit": "50.00", "credit": "0"},
        ]
        result = calculate_balances(rows, opening_balance=Decimal("500.00"))
        assert result[0]["balance"] == Decimal("600.00")
        assert result[1]["balance"] == Decimal("550.00")

    def test_balance_defaults_to_zero(self):
        rows = [
            {"debit": "0", "credit": "100.00"},
        ]
        result = calculate_balances(rows)
        assert result[0]["balance"] == Decimal("100.00")

    def test_balance_with_none_opening(self):
        rows = [
            {"debit": "200.00", "credit": "0"},
        ]
        result = calculate_balances(rows, opening_balance=None)
        assert result[0]["balance"] == Decimal("-200.00")

    def test_empty_rows(self):
        result = calculate_balances([], opening_balance=Decimal("100.00"))
        assert result == []


# ---------------------------------------------------------------------------
# Service Layer: save_batch
# ---------------------------------------------------------------------------

class TestServiceSaveBatch:
    def test_creates_statement_and_transactions(self, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)

        rows = [
            {
                "date": "15/06/2026",
                "narration": "Test Credit",
                "reference_no": "REF1",
                "debit": "0",
                "credit": "500.00",
            },
            {
                "date": "16/06/2026",
                "narration": "Test Debit",
                "reference_no": "REF2",
                "debit": "200.00",
                "credit": "0",
            },
        ]

        statement, transactions, errors = save_batch(
            user=user,
            society=society,
            bank_account=bank_account,
            period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30),
            opening_balance=Decimal("1000.00"),
            closing_balance=None,
            rows=rows,
        )

        assert statement.source_type == "MANUAL"
        assert statement.society == society
        assert statement.bank_account == bank_account
        assert statement.row_count == 2
        assert len(transactions) == 2
        assert errors == []

        # Verify first transaction (credit)
        tx1 = transactions[0]
        assert tx1.amount == Decimal("500.00")
        assert tx1.dr_cr == BankTransaction.DrCr.CREDIT
        assert tx1.narration == "Test Credit"
        assert tx1.balance == Decimal("1500.00")  # 1000 + 500

        # Verify second transaction (debit)
        tx2 = transactions[1]
        assert tx2.amount == Decimal("200.00")
        assert tx2.dr_cr == BankTransaction.DrCr.DEBIT
        assert tx2.balance == Decimal("1300.00")  # 1500 - 200

    def test_raises_on_empty_rows(self, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)

        with pytest.raises(ValueError, match="At least one row is required"):
            save_batch(
                user=user,
                society=society,
                bank_account=bank_account,
                period_start=date(2026, 6, 1),
                period_end=date(2026, 6, 30),
                opening_balance=Decimal("0.00"),
                closing_balance=None,
                rows=[],
            )

    def test_raises_validation_error_on_invalid_rows(self, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)

        rows = [
            {
                "date": "",
                "narration": "",
                "reference_no": "",
                "debit": "",
                "credit": "",
            },
        ]

        with pytest.raises(ValidationError) as exc_info:
            save_batch(
                user=user,
                society=society,
                bank_account=bank_account,
                period_start=date(2026, 6, 1),
                period_end=date(2026, 6, 30),
                opening_balance=Decimal("0.00"),
                closing_balance=None,
                rows=rows,
            )

        assert "row_errors" in exc_info.value.params
        assert len(exc_info.value.params["row_errors"]) == 1

    def test_closing_balance_mismatch_logs_warning(self, user, caplog):
        import logging

        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)

        rows = [
            {
                "date": "15/06/2026",
                "narration": "Test",
                "reference_no": "",
                "debit": "0",
                "credit": "100.00",
            },
        ]

        with caplog.at_level(logging.WARNING):
            save_batch(
                user=user,
                society=society,
                bank_account=bank_account,
                period_start=date(2026, 6, 1),
                period_end=date(2026, 6, 30),
                opening_balance=Decimal("0.00"),
                closing_balance=Decimal("999.99"),  # mismatched
                rows=rows,
            )

        assert "Closing balance mismatch" in caplog.text


# ---------------------------------------------------------------------------
# Service Layer: get_shortcodes
# ---------------------------------------------------------------------------

class TestServiceGetShortcodes:
    def test_returns_all_shortcodes(self):
        result = get_shortcodes()
        assert isinstance(result, dict)
        assert result == SHORTCODE_MAP
        assert result["mc"] == "Maintenance Collection"
        assert result["bc"] == "Bank Charges"
        assert result["ic"] == "Interest Credit"
        assert result["upi"] == "UPI Collection"
        assert result["cd"] == "Cheque Deposit"
        assert result["nc"] == "NEFT Credit"
        assert result["rc"] == "RTGS Credit"

    def test_returns_a_copy_not_reference(self):
        result = get_shortcodes()
        result["new_key"] = "New Value"
        assert "new_key" not in SHORTCODE_MAP


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------

class TestManualEntryBatchForm:
    def test_bank_account_queryset_scoped_to_society(self, user):
        from reconciliation.forms import ManualEntryBatchForm

        society1 = SocietyFactory()
        society2 = SocietyFactory()
        bank1 = BankAccountFactory(society=society1, name="Bank 1")
        bank2 = BankAccountFactory(society=society2, name="Bank 2")

        form = ManualEntryBatchForm(society=society1)
        qs = form.fields["bank_account"].queryset

        assert bank1 in qs
        assert bank2 not in qs

    def test_period_end_before_start_fails(self, user):
        from reconciliation.forms import ManualEntryBatchForm

        society = SocietyFactory()
        form = ManualEntryBatchForm(
            society=society,
            data={
                "bank_account": "",
                "period_start": "2026-06-30",
                "period_end": "2026-06-01",
                "opening_balance": "0.00",
            },
        )
        assert not form.is_valid()
        assert "period" in str(form.errors).lower() or "__all__" in form.errors


class TestManualEntryRowForm:
    def test_valid_row(self):
        from reconciliation.forms import ManualEntryRowForm

        form = ManualEntryRowForm(
            data={
                "date": "15/06/2026",
                "narration": "Test",
                "reference_no": "",
                "debit": "0",
                "credit": "100.00",
            },
        )
        assert form.is_valid()

    def test_both_debit_and_credit_fails(self):
        from reconciliation.forms import ManualEntryRowForm

        form = ManualEntryRowForm(
            data={
                "date": "15/06/2026",
                "narration": "Test",
                "reference_no": "",
                "debit": "100.00",
                "credit": "100.00",
            },
        )
        assert not form.is_valid()
        assert "not both" in str(form.errors).lower()

    def test_no_amount_fails(self):
        from reconciliation.forms import ManualEntryRowForm

        form = ManualEntryRowForm(
            data={
                "date": "15/06/2026",
                "narration": "Test",
                "reference_no": "",
                "debit": "",
                "credit": "",
            },
        )
        assert not form.is_valid()