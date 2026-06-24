"""
Tests for reconciliation views — authentication, society scoping,
template rendering, POST actions, and URL resolution.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponseRedirect
from django.shortcuts import resolve_url
from django.urls import reverse
from django.utils import timezone

from accounting.models import Account, AccountCategory, FinancialYear, LedgerEntry, Voucher
from reconciliation.models import (
    BankStatementImport,
    BankTransaction,
    BankTransactionNormalized,
    ReconciliationHistory,
    ReconciliationLink,
)
from reconciliation.tests.factories import (
    BankAccountFactory,
    BankStatementImportFactory,
    BankTransactionFactory,
    BankTransactionNormalizedFactory,
    ExpenseAccountFactory,
    FinancialYearFactory,
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
    """Log the user in and set the society selection in the session."""
    client.force_login(user)
    # Create membership for the user
    Membership.objects.get_or_create(
        user=user,
        society=society,
        defaults={"role": Membership.Role.OWNER, "is_active": True},
    )
    # Set session scope so get_selected_scope() returns the society
    session = client.session
    session["selected_society_id"] = society.pk
    session.save()


def _make_bank_statement_import(society, user):
    """Create a completed BankStatementImport with a few transactions."""
    fy = FinancialYearFactory(society=society)
    bank_account = BankAccountFactory(society=society)
    statement = BankStatementImportFactory(
        society=society,
        bank_account=bank_account,
        uploaded_by=user,
    )
    for i in range(3):
        BankTransactionFactory(
            bank_statement_import=statement,
            transaction_date=date.today(),
            amount=Decimal("500.00"),
            narration=f"Test transaction {i}",
        )
    return statement


def _make_reconciliation_link(society, user, bank_transaction=None, status=None, match_type=None):
    """Create a ReconciliationLink with supporting records."""
    if bank_transaction is None:
        statement = BankStatementImportFactory(society=society)
        bank_transaction = BankTransactionFactory(
            bank_statement_import=statement,
            transaction_date=date.today(),
            amount=Decimal("500.00"),
            narration="Test",
        )
    fy = FinancialYearFactory(society=society)
    bank_account = ExpenseAccountFactory(society=society)
    voucher = VoucherFactory(
        society=society,
        voucher_type=Voucher.VoucherType.RECEIPT,
        payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
    )
    entry = LedgerEntryFactory(
        voucher=voucher,
        account=bank_account,
        debit=Decimal("500.00"),
        credit=Decimal("0.00"),
    )
    kwargs = {
        "society": society,
        "bank_transaction": bank_transaction,
        "voucher_entry": entry,
        "matched_amount": Decimal("500.00"),
        "confidence_score": 95,
    }
    if status:
        kwargs["status"] = status
        if status not in {
            ReconciliationLink.Status.MATCHED,
            ReconciliationLink.Status.FORCE_MATCHED,
        }:
            kwargs["match_type"] = ReconciliationLink.MatchType.PARTIAL
    if match_type:
        kwargs["match_type"] = match_type
    return ReconciliationLinkFactory(**kwargs)


def _make_posted_bank_ledger_entry(society, bank_account, amount=Decimal("500.00")):
    voucher = VoucherFactory(
        society=society,
        voucher_type=Voucher.VoucherType.RECEIPT,
        payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
        reference_number=f"REF-{timezone.now().timestamp()}",
    )
    bank_entry = LedgerEntryFactory(
        voucher=voucher,
        account=bank_account,
        debit=amount,
        credit=Decimal("0.00"),
    )
    LedgerEntryFactory(
        voucher=voucher,
        account=IncomeAccountFactory(society=society),
        debit=Decimal("0.00"),
        credit=amount,
    )
    Voucher.objects.filter(pk=voucher.pk).update(posted_at=timezone.now())
    return bank_entry


# ---------------------------------------------------------------------------
# DashboardView
# ---------------------------------------------------------------------------

class TestDashboardView:
    def test_requires_authentication(self, client):
        response = client.get(reverse("reconciliation:dashboard"))
        assert response.status_code == 302
        assert response.url.startswith("/accounts/login/")

    def test_no_society_shows_context_flag(self, client, user):
        client.force_login(user)
        response = client.get(reverse("reconciliation:dashboard"))
        assert response.status_code == 200
        assert response.context["no_society"] is True

    def test_authenticated_renders_template(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:dashboard"))

        assert response.status_code == 200
        assert "reconciliation/dashboard.html" in [t.name for t in response.templates]

    def test_context_has_summary_counts(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        statement = _make_bank_statement_import(society, user)
        _make_reconciliation_link(society, user, status=ReconciliationLink.Status.MATCHED)

        response = client.get(reverse("reconciliation:dashboard"))

        ctx = response.context
        assert ctx["total_bank_txs"] >= 2
        assert ctx["matched_count"] >= 1
        assert "recent_imports" in ctx
        assert "recent_activity" in ctx

    def test_dashboard_transaction_count_includes_duplicate_bank_rows(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        statement = BankStatementImportFactory(society=society, uploaded_by=user)
        BankTransactionFactory(bank_statement_import=statement, is_duplicate=False)
        BankTransactionFactory(bank_statement_import=statement, is_duplicate=True)

        response = client.get(reverse("reconciliation:dashboard"))

        assert response.context["total_bank_txs"] == 2
        recent_import = response.context["recent_imports"][0]
        assert recent_import.transaction_count == 2

    def test_dashboard_counts_manual_matched_and_unmatched_bank_entries_without_upload(self, client, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        _login_and_select_society(client, user, society)

        for _ in range(4):
            bank_entry = _make_posted_bank_ledger_entry(society, bank_account)
            statement = BankStatementImportFactory(
                society=society,
                bank_account=bank_account,
                uploaded_by=user,
                source_type="MANUAL_RECON",
                row_count=1,
            )
            bank_transaction = BankTransactionFactory(
                bank_statement_import=statement,
                amount=bank_entry.debit,
                dr_cr=BankTransaction.DrCr.CREDIT,
            )
            ReconciliationLinkFactory(
                society=society,
                voucher_entry=bank_entry,
                bank_transaction=bank_transaction,
                matched_amount=bank_entry.debit,
                match_type=ReconciliationLink.MatchType.FORCE,
                status=ReconciliationLink.Status.FORCE_MATCHED,
                is_manual=True,
                matched_by=user,
            )
        _make_posted_bank_ledger_entry(society, bank_account)
        cash_account = BankAccountFactory(society=society, name="Cash-in-Hand")
        _make_posted_bank_ledger_entry(society, cash_account)

        response = client.get(reverse("reconciliation:dashboard"))

        assert response.context["total_bank_txs"] == 5
        assert response.context["matched_count"] == 4
        assert response.context["unmatched_bank"] == 1

    def test_shows_recent_imports(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        _make_bank_statement_import(society, user)

        response = client.get(reverse("reconciliation:dashboard"))

        assert response.context["recent_imports"].count() >= 1


# ---------------------------------------------------------------------------
# StatementImportView
# ---------------------------------------------------------------------------

class TestStatementImportView:
    def test_requires_authentication(self, client):
        response = client.get(reverse("reconciliation:statement-import"))
        assert response.status_code == 302

    def test_no_society_redirects_to_dashboard(self, client, user):
        client.force_login(user)
        response = client.get(reverse("reconciliation:statement-import"))
        assert response.status_code == 302
        assert response.url == reverse("reconciliation:dashboard")

    def test_renders_template_with_society(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:statement-import"))

        assert response.status_code == 200
        assert "reconciliation/import.html" in [t.name for t in response.templates]

    def test_context_has_recent_imports(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        _make_bank_statement_import(society, user)

        response = client.get(reverse("reconciliation:statement-import"))

        assert response.context["recent_imports"].count() >= 1


# ---------------------------------------------------------------------------
# StatementImportDetailView
# ---------------------------------------------------------------------------

class TestStatementImportDetailView:
    def test_requires_authentication(self, client):
        society = SocietyFactory()
        statement = BankStatementImportFactory(society=society)
        response = client.get(
            reverse("reconciliation:statement-import-detail", kwargs={"pk": statement.pk})
        )
        assert response.status_code == 302

    def test_renders_with_correct_society(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        statement = _make_bank_statement_import(society, user)

        response = client.get(
            reverse("reconciliation:statement-import-detail", kwargs={"pk": statement.pk})
        )

        assert response.status_code == 200
        assert "reconciliation/import_detail.html" in [t.name for t in response.templates]
        assert response.context["statement_import"] == statement

    def test_returns_404_for_wrong_society(self, client, user):
        society_a = SocietyFactory(name="Society A")
        society_b = SocietyFactory(name="Society B")
        _login_and_select_society(client, user, society_a)
        statement = BankStatementImportFactory(society=society_b)

        response = client.get(
            reverse("reconciliation:statement-import-detail", kwargs={"pk": statement.pk})
        )

        assert response.status_code == 404

    def test_context_has_transactions(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        statement = _make_bank_statement_import(society, user)

        response = client.get(
            reverse("reconciliation:statement-import-detail", kwargs={"pk": statement.pk})
        )

        assert response.context["transactions"].count() >= 2


# ---------------------------------------------------------------------------
# ImportHistoryView
# ---------------------------------------------------------------------------

class TestImportHistoryView:
    def test_requires_authentication(self, client):
        response = client.get(reverse("reconciliation:import-history"))
        assert response.status_code == 302

    def test_renders_with_society(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        _make_bank_statement_import(society, user)

        response = client.get(reverse("reconciliation:import-history"))

        assert response.status_code == 200
        assert "reconciliation/import_history.html" in [t.name for t in response.templates]
        assert response.context["imports"].count() >= 1

    def test_filters_by_society(self, client, user):
        society_a = SocietyFactory(name="Society A")
        society_b = SocietyFactory(name="Society B")
        _login_and_select_society(client, user, society_a)
        BankStatementImportFactory(society=society_a)
        BankStatementImportFactory(society=society_b)

        response = client.get(reverse("reconciliation:import-history"))

        # Only society_a's imports should show
        assert response.context["imports"].count() == 1


# ---------------------------------------------------------------------------
# WorkspaceView
# ---------------------------------------------------------------------------

class TestWorkspaceView:
    def test_requires_authentication(self, client):
        response = client.get(reverse("reconciliation:workspace"))
        assert response.status_code == 302

    def test_no_society_redirects(self, client, user):
        client.force_login(user)
        response = client.get(reverse("reconciliation:workspace"))
        assert response.status_code == 302
        assert response.url == reverse("reconciliation:dashboard")

    def test_renders_template(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:workspace"))

        assert response.status_code == 200
        assert "reconciliation/workspace.html" in [t.name for t in response.templates]

    def test_context_has_links_and_unmatched(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        _make_reconciliation_link(society, user)

        response = client.get(reverse("reconciliation:workspace"))

        ctx = response.context
        assert "links" in ctx
        assert "unmatched_bank_txs" in ctx
        assert "status_summary" in ctx
        assert "status_choices" in ctx

    def test_filters_by_status(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        _make_reconciliation_link(society, user, status=ReconciliationLink.Status.MATCHED)
        _make_reconciliation_link(society, user, status=ReconciliationLink.Status.SUGGESTED)

        response = client.get(
            reverse("reconciliation:workspace") + "?status=MATCHED"
        )

        assert response.status_code == 200
        assert all(link.status == ReconciliationLink.Status.MATCHED for link in response.context["links"])

    def test_filters_by_date_range(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        from_date = date(2025, 1, 1)
        to_date = date(2025, 12, 31)

        response = client.get(
            reverse("reconciliation:workspace")
            + f"?date_from={from_date.isoformat()}&date_to={to_date.isoformat()}"
        )

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# ManualWorkspaceView
# ---------------------------------------------------------------------------

class TestManualWorkspaceView:
    def test_renders_template(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:manual-workspace"))

        assert response.status_code == 200
        assert "reconciliation/manual_workspace.html" in [t.name for t in response.templates]

    def test_save_row_creates_transaction_and_suggestions(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        bank_account = BankAccountFactory(society=society)
        session = client.session
        session["manual_workspace_bank_account_id"] = bank_account.pk
        session.save()

        voucher = VoucherFactory(
            society=society,
            voucher_type=Voucher.VoucherType.RECEIPT,
            voucher_date=date.today(),
            payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
            narration="Maintenance receipt",
            reference_number="REF-123",
        )
        LedgerEntryFactory(
            voucher=voucher,
            account=bank_account,
            debit=Decimal("500.00"),
            credit=Decimal("0.00"),
        )
        LedgerEntryFactory(
            voucher=voucher,
            account=IncomeAccountFactory(society=society),
            debit=Decimal("0.00"),
            credit=Decimal("500.00"),
        )
        voucher.posted_at = timezone.now()
        voucher.save(update_fields=["posted_at"])

        response = client.post(
            reverse("reconciliation:manual-workspace-save-row"),
            data={
                "transaction_date": date.today().isoformat(),
                "narration": "Maintenance receipt",
                "reference_no": "REF-123",
                "debit": "",
                "credit": "500.00",
                "balance": "2500.00",
            },
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert BankTransaction.objects.filter(bank_statement_import__society=society).count() == 1
        assert ReconciliationLink.objects.filter(
            society=society,
            status=ReconciliationLink.Status.SUGGESTED,
        ).exists()

    def test_bulk_paste_creates_multiple_rows(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        bank_account = BankAccountFactory(society=society)
        session = client.session
        session["manual_workspace_bank_account_id"] = bank_account.pk
        session.save()

        response = client.post(
            reverse("reconciliation:manual-workspace-paste"),
            data={
                "pasted_rows": (
                    "Date\tNarration\tRef No\tDebit\tCredit\tBalance\n"
                    f"{date.today().isoformat()}\tMaintenance\tREF1\t0\t1000\t5000\n"
                    f"{date.today().isoformat()}\tBank charges\tREF2\t25\t0\t4975"
                ),
            },
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert BankTransaction.objects.filter(bank_statement_import__society=society).count() == 2


# ---------------------------------------------------------------------------
# confirm_match_view
# ---------------------------------------------------------------------------

class TestConfirmMatchView:
    def test_requires_post(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        link = _make_reconciliation_link(society, user)

        response = client.get(
            reverse("reconciliation:confirm-match", kwargs={"link_id": link.pk})
        )

        assert response.status_code == 405

    def test_requires_society(self, client, user):
        client.force_login(user)
        response = client.post(reverse("reconciliation:confirm-match", kwargs={"link_id": 1}))
        assert response.status_code == 400

    def test_returns_404_for_nonexistent_link(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.post(reverse("reconciliation:confirm-match", kwargs={"link_id": 99999}))

        assert response.status_code == 404

    def test_confirms_match_and_redirects(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        link = _make_reconciliation_link(society, user, status=ReconciliationLink.Status.SUGGESTED)

        response = client.post(
            reverse("reconciliation:confirm-match", kwargs={"link_id": link.pk})
        )

        assert response.status_code == 302
        link.refresh_from_db()
        assert link.status == ReconciliationLink.Status.MATCHED
        assert link.matched_by == user

    def test_ajax_returns_json(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        link = _make_reconciliation_link(society, user, status=ReconciliationLink.Status.SUGGESTED)

        response = client.post(
            reverse("reconciliation:confirm-match", kwargs={"link_id": link.pk}),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["new_status"] == ReconciliationLink.Status.MATCHED


# ---------------------------------------------------------------------------
# unmatched_link_view
# ---------------------------------------------------------------------------

class TestUnmatchLinkView:
    def test_unmatches_link(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        link = _make_reconciliation_link(society, user, status=ReconciliationLink.Status.MATCHED, match_type=ReconciliationLink.MatchType.EXACT)

        response = client.post(
            reverse("reconciliation:unlink-match", kwargs={"link_id": link.pk})
        )

        assert response.status_code == 302
        link.refresh_from_db()
        assert link.status == ReconciliationLink.Status.REVERSED

    def test_ajax_returns_json(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        link = _make_reconciliation_link(society, user, status=ReconciliationLink.Status.MATCHED, match_type=ReconciliationLink.MatchType.EXACT)

        response = client.post(
            reverse("reconciliation:unlink-match", kwargs={"link_id": link.pk}),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        assert response.status_code == 200
        assert response.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# run_matching_view
# ---------------------------------------------------------------------------

class TestRunMatchingView:
    def test_no_society_redirects(self, client, user):
        client.force_login(user)
        response = client.get(reverse("reconciliation:run-matching"))
        assert response.status_code == 302
        assert response.url == reverse("reconciliation:dashboard")

    def test_runs_matching_and_redirects(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:run-matching"))

        assert response.status_code == 302
        assert response.url == reverse("reconciliation:workspace")

    def test_ajax_returns_json(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(
            reverse("reconciliation:run-matching"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "result" in data
        assert "auto_matched" in data["result"]

    def test_handles_rule_stats_payload(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        with patch(
            "reconciliation.views.MatchingEngine.run_matching",
            return_value={
                "candidates": [],
                "auto_matched": [],
                "suggested": [],
                "stats": {
                    "Rule A": {"count": 2, "avg_confidence": 91.5},
                    "Rule B": {"count": 0, "avg_confidence": 0.0},
                },
            },
        ):
            response = client.get(reverse("reconciliation:run-matching"))

        assert response.status_code == 302
        assert response.url == reverse("reconciliation:workspace")


# ---------------------------------------------------------------------------
# mark_duplicate_view
# ---------------------------------------------------------------------------

class TestMarkDuplicateView:
    def test_marks_duplicate(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        link = _make_reconciliation_link(society, user)

        response = client.post(
            reverse("reconciliation:mark-duplicate", kwargs={"link_id": link.pk})
        )

        assert response.status_code == 302
        link.refresh_from_db()
        assert link.status == ReconciliationLink.Status.DUPLICATE

    def test_ajax_returns_json(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        link = _make_reconciliation_link(society, user)

        response = client.post(
            reverse("reconciliation:mark-duplicate", kwargs={"link_id": link.pk}),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        assert response.status_code == 200
        assert response.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# ForceMatchView
# ---------------------------------------------------------------------------

class TestForceMatchView:
    def test_requires_authentication(self, client):
        response = client.get(reverse("reconciliation:force-match"))
        assert response.status_code == 302

    def test_no_society_redirects(self, client, user):
        client.force_login(user)
        response = client.get(reverse("reconciliation:force-match"))
        assert response.status_code == 302
        assert response.url == reverse("reconciliation:dashboard")

    def test_renders_template(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:force-match"))

        assert response.status_code == 200
        assert "reconciliation/force_match.html" in [t.name for t in response.templates]

    def test_form_valid_creates_force_match(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        # Create a bank transaction
        statement = BankStatementImportFactory(society=society)
        bank_tx = BankTransactionFactory(
            bank_statement_import=statement,
            transaction_date=date.today(),
            amount=Decimal("1000.00"),
            narration="Force match test",
        )

        # Create a posted voucher with ledger entry
        fy = FinancialYearFactory(society=society)
        bank_account = BankAccountFactory(society=society)
        voucher = VoucherFactory(
            society=society,
            voucher_type=Voucher.VoucherType.PAYMENT,
            payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
            voucher_date=date.today(),
        )
        ledger_entry = LedgerEntryFactory(
            voucher=voucher,
            account=bank_account,
            debit=Decimal("0.00"),
            credit=Decimal("1000.00"),
        )

        response = client.post(
            reverse("reconciliation:force-match"),
            {
                "bank_transaction": bank_tx.pk,
                "ledger_entry": ledger_entry.pk,
                "remarks": "Manual force match",
            },
        )

        assert response.status_code == 302
        assert response.url == reverse("reconciliation:workspace")
        link = ReconciliationLink.objects.filter(
            society=society,
            bank_transaction=bank_tx,
        ).first()
        assert link is not None
        assert link.status == ReconciliationLink.Status.FORCE_MATCHED
        assert link.match_type == ReconciliationLink.MatchType.FORCE
        assert link.confidence_score == 100


# ---------------------------------------------------------------------------
# ExceptionListView
# ---------------------------------------------------------------------------

class TestExceptionListView:
    def test_requires_authentication(self, client):
        response = client.get(reverse("reconciliation:exceptions"))
        assert response.status_code == 302

    def test_renders_with_exceptions(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        _make_reconciliation_link(
            society, user,
            status=ReconciliationLink.Status.EXCEPTION,
        )

        response = client.get(reverse("reconciliation:exceptions"))

        assert response.status_code == 200
        assert "reconciliation/exceptions.html" in [t.name for t in response.templates]
        assert response.context["exceptions"].count() >= 1

    def test_has_orphan_bank_txs_in_context(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        statement = BankStatementImportFactory(society=society)
        BankTransactionFactory(
            bank_statement_import=statement,
            transaction_date=date.today(),
            amount=Decimal("500.00"),
            narration="Orphan transaction",
        )

        response = client.get(reverse("reconciliation:exceptions"))

        assert "orphan_bank_txs" in response.context

    def test_has_exception_summary(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:exceptions"))

        assert "exception_summary" in response.context


# ---------------------------------------------------------------------------
# create_adjustment_view
# ---------------------------------------------------------------------------

class TestAdjustmentForExceptionView:
    def test_requires_post(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        link = _make_reconciliation_link(society, user)

        response = client.get(
            reverse("reconciliation:create-adjustment", kwargs={"link_id": link.pk})
        )
        assert response.status_code == 405

    def test_requires_exception_status(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        link = _make_reconciliation_link(society, user, status=ReconciliationLink.Status.MATCHED)

        response = client.post(
            reverse("reconciliation:create-adjustment", kwargs={"link_id": link.pk})
        )

        assert response.status_code == 400

    def test_creates_adjustment_for_bank_only_exception(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        # Need bank account, income/expense accounts for adjustment
        BankAccountFactory(society=society, name="Bank Account", code="BANK001")
        IncomeAccountFactory(society=society, name="Interest Income", code="INC001")

        statement = BankStatementImportFactory(society=society)
        bank_tx = BankTransactionFactory(
            bank_statement_import=statement,
            transaction_date=date.today(),
            amount=Decimal("250.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
            narration="Interest credit",
        )
        link = _make_reconciliation_link(
            society, user,
            bank_transaction=bank_tx,
            status=ReconciliationLink.Status.EXCEPTION,
        )
        link.exception_type = ReconciliationLink.ExceptionType.BANK_ONLY
        link.save()

        response = client.post(
            reverse("reconciliation:create-adjustment", kwargs={"link_id": link.pk})
        )

        assert response.status_code == 302
        link.refresh_from_db()
        assert link.status == ReconciliationLink.Status.MATCHED
        assert link.voucher_entry is not None

    def test_ajax_returns_json(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        BankAccountFactory(society=society, name="Bank Account", code="BANK001")
        IncomeAccountFactory(society=society, name="Interest Income", code="INC001")

        statement = BankStatementImportFactory(society=society)
        bank_tx = BankTransactionFactory(
            bank_statement_import=statement,
            transaction_date=date.today(),
            amount=Decimal("250.00"),
            dr_cr=BankTransaction.DrCr.CREDIT,
            narration="Interest credit",
        )
        link = _make_reconciliation_link(
            society, user,
            bank_transaction=bank_tx,
            status=ReconciliationLink.Status.EXCEPTION,
        )
        link.exception_type = ReconciliationLink.ExceptionType.BANK_ONLY
        link.save()

        response = client.post(
            reverse("reconciliation:create-adjustment", kwargs={"link_id": link.pk}),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "voucher_id" in data


# ---------------------------------------------------------------------------
# create_adjustment_for_orphan_view
# ---------------------------------------------------------------------------

class TestAdjustmentForOrphanView:
    def test_requires_bank_tx_id(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.post(reverse("reconciliation:adjust-orphan"), {})

        assert response.status_code == 400

    def test_creates_adjustment_for_orphan(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        BankAccountFactory(society=society, name="Bank Account", code="BANK001")
        ExpenseAccountFactory(society=society, name="Bank Charges", code="EXP001")

        statement = BankStatementImportFactory(society=society)
        bank_tx = BankTransactionFactory(
            bank_statement_import=statement,
            transaction_date=date.today(),
            amount=Decimal("75.00"),
            dr_cr=BankTransaction.DrCr.DEBIT,
            narration="Bank charges",
            is_duplicate=False,
        )

        response = client.post(
            reverse("reconciliation:adjust-orphan"),
            {"bank_tx_id": str(bank_tx.pk)},
        )

        assert response.status_code == 302
        link = ReconciliationLink.objects.filter(
            society=society, bank_transaction=bank_tx,
        ).first()
        assert link is not None
        assert link.status == ReconciliationLink.Status.MATCHED

    def test_rejects_already_linked_transaction(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        BankAccountFactory(society=society, name="Bank Account", code="BANK001")
        ExpenseAccountFactory(society=society, name="Bank Charges", code="EXP001")

        statement = BankStatementImportFactory(society=society)
        bank_tx = BankTransactionFactory(
            bank_statement_import=statement,
            transaction_date=date.today(),
            amount=Decimal("75.00"),
            dr_cr=BankTransaction.DrCr.DEBIT,
            narration="Bank charges",
            is_duplicate=False,
        )
        _make_reconciliation_link(society, user, bank_transaction=bank_tx, status=ReconciliationLink.Status.MATCHED, match_type=ReconciliationLink.MatchType.EXACT)

        response = client.post(
            reverse("reconciliation:adjust-orphan"),
            {"bank_tx_id": str(bank_tx.pk)},
        )

        assert response.status_code == 400

    def test_ajax_returns_json(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        BankAccountFactory(society=society, name="Bank Account", code="BANK001")
        ExpenseAccountFactory(society=society, name="Bank Charges", code="EXP001")

        statement = BankStatementImportFactory(society=society)
        bank_tx = BankTransactionFactory(
            bank_statement_import=statement,
            transaction_date=date.today(),
            amount=Decimal("75.00"),
            dr_cr=BankTransaction.DrCr.DEBIT,
            narration="Bank charges",
            is_duplicate=False,
        )

        response = client.post(
            reverse("reconciliation:adjust-orphan"),
            {"bank_tx_id": str(bank_tx.pk)},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "link_id" in data


# ---------------------------------------------------------------------------
# BRSReportView
# ---------------------------------------------------------------------------

class TestBRSReportView:
    def test_requires_authentication(self, client):
        response = client.get(reverse("reconciliation:report-brs"))
        assert response.status_code == 302

    def test_no_society_redirects(self, client, user):
        client.force_login(user)
        response = client.get(reverse("reconciliation:report-brs"))
        assert response.status_code == 302

    def test_renders_brs_template(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:report-brs"))

        assert response.status_code == 200
        assert "reconciliation/report_brs.html" in [t.name for t in response.templates]

    def test_context_has_brs_data(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:report-brs"))

        ctx = response.context
        assert "book_balance" in ctx
        assert "bank_balance" in ctx
        assert "is_balanced" in ctx
        assert "as_of_date" in ctx
        assert "society_name" in ctx

    def test_accepts_as_of_date_param(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        target_date = date(2025, 6, 30)

        response = client.get(
            reverse("reconciliation:report-brs") + f"?as_of_date={target_date.isoformat()}"
        )

        assert response.status_code == 200
        assert response.context["as_of_date"] == target_date

    def test_handles_invalid_date_gracefully(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(
            reverse("reconciliation:report-brs") + "?as_of_date=not-a-date"
        )

        assert response.status_code == 200
        assert response.context["as_of_date"] == date.today()


# ---------------------------------------------------------------------------
# UnmatchedReportView
# ---------------------------------------------------------------------------

class TestUnmatchedReportView:
    def test_renders_unmatched_template(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:report-unmatched"))

        assert response.status_code == 200
        assert "reconciliation/report_unmatched.html" in [t.name for t in response.templates]

    def test_context_has_report_data(self, client, user):
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
    def test_renders_duplicate_template(self, client, user):
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
# LinkAuditView
# ---------------------------------------------------------------------------

class TestLinkAuditView:
    def test_renders_audit_timeline(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        link = _make_reconciliation_link(society, user)

        response = client.get(
            reverse("reconciliation:link-audit", kwargs={"link_id": link.pk})
        )

        assert response.status_code == 200
        assert "reconciliation/link_audit.html" in [t.name for t in response.templates]
        assert response.context["link"] == link
        assert "history_entries" in response.context

    def test_returns_404_for_wrong_society(self, client, user):
        society_a = SocietyFactory(name="Society A")
        society_b = SocietyFactory(name="Society B")
        _login_and_select_society(client, user, society_a)
        link = _make_reconciliation_link(society_b, user)

        response = client.get(
            reverse("reconciliation:link-audit", kwargs={"link_id": link.pk})
        )

        assert response.status_code == 404

    def test_history_entries_include_signal_created(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        link = _make_reconciliation_link(society, user)

        response = client.get(
            reverse("reconciliation:link-audit", kwargs={"link_id": link.pk})
        )

        # Signal auto-creates a CREATED history entry
        assert response.context["history_entries"].count() >= 1


# ---------------------------------------------------------------------------
# AuditLogView
# ---------------------------------------------------------------------------

class TestAuditLogView:
    def test_renders_audit_log(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        _make_reconciliation_link(society, user)

        response = client.get(reverse("reconciliation:audit-log"))

        assert response.status_code == 200
        assert "reconciliation/audit_log.html" in [t.name for t in response.templates]

    def test_context_has_filters_and_stats(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(reverse("reconciliation:audit-log"))

        ctx = response.context
        assert "history_entries" in ctx
        assert "users" in ctx
        assert "status_choices" in ctx
        assert "match_type_choices" in ctx
        assert "total_changes" in ctx
        assert "most_recent_change" in ctx

    def test_filters_by_status(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        _make_reconciliation_link(society, user)

        response = client.get(
            reverse("reconciliation:audit-log") + "?status=CREATED"
        )

        assert response.status_code == 200

    def test_filters_by_user(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)
        _make_reconciliation_link(society, user)

        response = client.get(
            reverse("reconciliation:audit-log") + f"?user={user.username}"
        )

        assert response.status_code == 200

    def test_filters_by_date_range(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(
            reverse("reconciliation:audit-log")
            + "?from_date=2025-01-01&to_date=2025-12-31"
        )

        assert response.status_code == 200

    def test_filters_by_link_type(self, client, user):
        society = SocietyFactory()
        _login_and_select_society(client, user, society)

        response = client.get(
            reverse("reconciliation:audit-log") + "?link_type=EXACT"
        )

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# URL Resolution
# ---------------------------------------------------------------------------

class TestURLResolution:
    """Verify all reconciliation URL patterns resolve correctly."""

    URLS = [
        ("reconciliation:dashboard", {}, "/reconciliation/"),
        ("reconciliation:statement-import", {}, "/reconciliation/import/"),
        ("reconciliation:import-history", {}, "/reconciliation/imports/"),
        ("reconciliation:workspace", {}, "/reconciliation/workspace/"),
        ("reconciliation:run-matching", {}, "/reconciliation/run-matching/"),
        ("reconciliation:force-match", {}, "/reconciliation/force-match/"),
        ("reconciliation:exceptions", {}, "/reconciliation/exceptions/"),
        ("reconciliation:adjust-orphan", {}, "/reconciliation/adjust-orphan/"),
        ("reconciliation:audit-log", {}, "/reconciliation/audit/"),
        ("reconciliation:report-brs", {}, "/reconciliation/reports/brs/"),
        ("reconciliation:report-unmatched", {}, "/reconciliation/reports/unmatched/"),
        ("reconciliation:report-duplicates", {}, "/reconciliation/reports/duplicates/"),
    ]

    PARAM_URLS = [
        (
            "reconciliation:statement-import-detail",
            {"pk": 1},
            "/reconciliation/import/1/",
        ),
        (
            "reconciliation:confirm-match",
            {"link_id": 42},
            "/reconciliation/match/42/confirm/",
        ),
        (
            "reconciliation:unlink-match",
            {"link_id": 42},
            "/reconciliation/match/42/unlink/",
        ),
        (
            "reconciliation:mark-duplicate",
            {"link_id": 42},
            "/reconciliation/match/42/duplicate/",
        ),
        (
            "reconciliation:create-adjustment",
            {"link_id": 42},
            "/reconciliation/match/42/adjust/",
        ),
        (
            "reconciliation:link-audit",
            {"link_id": 42},
            "/reconciliation/match/42/audit/",
        ),
    ]

    @pytest.mark.parametrize("name,kwargs,expected_path", URLS)
    def test_simple_url_resolves(self, name, kwargs, expected_path):
        assert reverse(name, kwargs=kwargs) == expected_path

    @pytest.mark.parametrize("name,kwargs,expected_path", PARAM_URLS)
    def test_param_url_resolves(self, name, kwargs, expected_path):
        assert reverse(name, kwargs=kwargs) == expected_path

    def test_dashboard_is_root(self):
        """The reconciliation dashboard should also resolve from ''."""
        assert reverse("reconciliation:dashboard") == "/reconciliation/"

    def test_url_count_matches_pattern_count(self):
        """Ensure every URL pattern defined has a test entry."""
        from reconciliation.urls import urlpatterns
        # We test 18 of 18 patterns (12 simple + 6 param)
        assert len(self.URLS) + len(self.PARAM_URLS) == len(urlpatterns)
