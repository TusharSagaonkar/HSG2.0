"""
Views for the Bank Reconciliation Engine.

Follows project conventions:
  - LoginRequiredMixin on all views
  - get_selected_scope() for society filtering
  - Class-based views exported as module-level callables
"""

import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction as db_transaction
from django.db.models import (
    Case,
    Count,
    DecimalField,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import DetailView, FormView, ListView, TemplateView, View

from accounting.models.model_Account import Account
from accounting.models.model_LedgerEntry import LedgerEntry
from accounting.models.model_Voucher import Voucher
from housing_accounting.selection import get_selected_scope
from reconciliation.forms import (
    ForceMatchForm,
    ManualBatchSaveForm,
    ManualCellUpdateForm,
    ManualEntryBatchForm,
    ManualEntryRowForm,
    ManualStatementImportForm,
    ManualWorkspaceFiltersForm,
    ManualWorkspacePasteForm,
    ManualWorkspaceRowForm,
    StatementImportForm,
)
from reconciliation.models import (
    BankStatementImport,
    BankTransaction,
    BankTransactionNormalized,
    ReconciliationHistory,
    ReconciliationLink,
)
from reconciliation.services import (
    AdjustmentService,
    MatchingEngine,
    ManualEntryRow,
    ManualStatementImportService,
    ManualWorkspaceService,
    NormalizerService,
    ReportService,
    StatementImportService,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "reconciliation/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        society, _ = get_selected_scope(self.request)

        if not society:
            context["no_society"] = True
            return context

        # Summary counts. Use only real reconciliation bank accounts; cash and
        # grouping accounts are not bank-statement rows.
        bank_accounts = (
            Account.objects.filter(
                society=society,
                is_bank=True,
                is_active=True,
                sub_type=Account.SubType.BANK,
            )
            .annotate(active_child_count=Count("children", filter=Q(children__is_active=True)))
            .filter(active_child_count=0)
            .exclude(name__icontains="cash")
            .exclude(name__icontains="fund transfer")
        )
        bank_account_ids = list(bank_accounts.values_list("id", flat=True))
        bank_transactions = BankTransaction.objects.filter(
            bank_statement_import__society=society,
            bank_statement_import__bank_account_id__in=bank_account_ids,
        )
        linked_statuses = [
            ReconciliationLink.Status.MATCHED,
            ReconciliationLink.Status.FORCE_MATCHED,
            ReconciliationLink.Status.PARTIAL,
            ReconciliationLink.Status.DUPLICATE,
            ReconciliationLink.Status.EXCEPTION,
            ReconciliationLink.Status.REVERSED,
            ReconciliationLink.Status.IGNORED,
            ReconciliationLink.Status.PENDING,
            ReconciliationLink.Status.SUGGESTED,
        ]
        unmatched_bank_ledger_entries = LedgerEntry.objects.filter(
            account_id__in=bank_account_ids,
            voucher__society=society,
            voucher__posted_at__isnull=False,
        ).exclude(
            reconciliation_links__status__in=linked_statuses,
        )
        total_bank_txs = bank_transactions.count() + unmatched_bank_ledger_entries.count()

        total_links = ReconciliationLink.objects.filter(society=society).count()

        status_counts = dict(
            ReconciliationLink.objects.filter(society=society)
            .values("status")
            .annotate(count=Count("id"))
            .values_list("status", "count")
        )

        matched_count = (
            status_counts.get(ReconciliationLink.Status.MATCHED, 0)
            + status_counts.get(ReconciliationLink.Status.FORCE_MATCHED, 0)
        )
        pending_count = status_counts.get(ReconciliationLink.Status.PENDING, 0)
        suggested_count = status_counts.get(ReconciliationLink.Status.SUGGESTED, 0)
        exception_count = status_counts.get(ReconciliationLink.Status.EXCEPTION, 0)
        duplicate_count = status_counts.get(ReconciliationLink.Status.DUPLICATE, 0)

        unmatched_bank = max(total_bank_txs - matched_count - duplicate_count, 0)

        # Recent imports
        recent_imports = (
            BankStatementImport.objects.filter(society=society)
            .annotate(transaction_count=Count("transactions"))
            .order_by("-uploaded_at")[:5]
        )

        # Recent activity
        recent_activity = ReconciliationHistory.objects.filter(
            reconciliation_link__society=society,
        ).select_related(
            "reconciliation_link",
            "reconciliation_link__bank_transaction",
            "reconciliation_link__voucher_entry__voucher",
            "performed_by",
        ).order_by("-performed_at")[:10]

        context.update(
            total_bank_txs=total_bank_txs,
            total_links=total_links,
            matched_count=matched_count,
            pending_count=pending_count,
            suggested_count=suggested_count,
            exception_count=exception_count,
            duplicate_count=duplicate_count,
            unmatched_bank=unmatched_bank,
            recent_imports=recent_imports,
            recent_activity=recent_activity,
        )
        return context


dashboard_view = DashboardView.as_view()


# ---------------------------------------------------------------------------
# Statement Import
# ---------------------------------------------------------------------------

class StatementImportView(LoginRequiredMixin, FormView):
    template_name = "reconciliation/import.html"
    form_class = StatementImportForm

    def dispatch(self, request, *args, **kwargs):
        self.society, _ = get_selected_scope(request)
        if not self.society:
            messages.error(request, "Please select a society first.")
            return redirect("reconciliation:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["society"] = self.society
        return kwargs

    def form_valid(self, form):
        uploaded_file = form.cleaned_data["file"]
        bank = form.cleaned_data.get("bank", "")

        try:
            importer = StatementImportService(self.society)
            statement_import = importer.import_statement(
                file_obj=uploaded_file,
                bank=bank,
            )
            messages.success(
                self.request,
                f"Statement imported: {statement_import.transactions.count()} transactions.",
            )
            return redirect(
                "reconciliation:statement-import-detail",
                pk=statement_import.pk,
            )
        except Exception as exc:
            logger.exception("Statement import failed")
            messages.error(self.request, f"Import failed: {exc}")
            return self.form_invalid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["recent_imports"] = BankStatementImport.objects.filter(
            society=self.society,
        ).order_by("-uploaded_at")[:10]
        return context


statement_import_view = StatementImportView.as_view()


class StatementImportDetailView(LoginRequiredMixin, DetailView):
    model = BankStatementImport
    template_name = "reconciliation/import_detail.html"
    context_object_name = "statement_import"

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        society, _ = get_selected_scope(self.request)
        if society and obj.society_id != society.id:
            raise Http404("Statement import not found.")
        return obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["transactions"] = self.object.transactions.select_related(
            "normalized",
        ).order_by("transaction_date", "id")
        return context


statement_import_detail_view = StatementImportDetailView.as_view()


class ManualStatementImportView(LoginRequiredMixin, FormView):
    template_name = "reconciliation/manual_entry.html"
    form_class = ManualStatementImportForm

    def dispatch(self, request, *args, **kwargs):
        self.society, _ = get_selected_scope(request)
        if not self.society:
            messages.error(request, "Please select a society first.")
            return redirect("reconciliation:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        """Keep the old manual-import URL usable while serving the new grid UI."""
        return redirect("reconciliation:manual-entry")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["society"] = self.society
        return kwargs

    def form_valid(self, form):
        bank_account = form.cleaned_data["bank_account"]
        statement_name = form.cleaned_data.get("statement_name") or "manual_statement.csv"
        rows = [
            ManualEntryRow(
                transaction_date=row["transaction_date"],
                narration=row["narration"],
                amount=row["amount"],
                dr_cr=row["dr_cr"],
                reference_no=row["reference_no"],
                cheque_no=row["cheque_no"],
                value_date=row["value_date"] or None,
                balance=row["balance"] or None,
                raw_row=row["raw_row"],
            )
            for row in form.parse_rows()
        ]

        try:
            service = ManualStatementImportService(
                user=self.request.user,
                society=self.society,
                bank_account=bank_account,
            )
            statement_import = service.import_rows(rows, filename=statement_name)
        except Exception as exc:
            messages.error(self.request, f"Manual import failed: {exc}")
            return self.form_invalid(form)

        messages.success(
            self.request,
            f"Manual statement imported: {statement_import.row_count} rows.",
        )
        return redirect("reconciliation:statement-import-detail", pk=statement_import.pk)


manual_statement_import_view = ManualStatementImportView.as_view()


class ImportHistoryView(LoginRequiredMixin, ListView):
    model = BankStatementImport
    template_name = "reconciliation/import_history.html"
    context_object_name = "imports"
    paginate_by = 20

    def get_queryset(self):
        society, _ = get_selected_scope(self.request)
        qs = BankStatementImport.objects.order_by("-uploaded_at")
        if society:
            qs = qs.filter(society=society)
        return qs


import_history_view = ImportHistoryView.as_view()


# ---------------------------------------------------------------------------
# Manual Reconciliation Workspace V1
# ---------------------------------------------------------------------------

def _manual_workspace_bank_accounts(society):
    return Account.objects.filter(
        society=society,
        is_bank=True,
        is_active=True,
    ).order_by("name")


def _manual_workspace_suggestions_queryset(society, bank_transaction):
    return (
        ReconciliationLink.objects.filter(
            society=society,
            bank_transaction=bank_transaction,
            status=ReconciliationLink.Status.SUGGESTED,
        )
        .select_related(
            "voucher_entry",
            "voucher_entry__voucher",
            "voucher_entry__unit",
            "voucher_entry__account",
        )
        .order_by("-confidence_score", "-id")[:10]
    )


class ManualWorkspaceView(LoginRequiredMixin, TemplateView):
    template_name = "reconciliation/manual_workspace.html"

    def dispatch(self, request, *args, **kwargs):
        self.society, _ = get_selected_scope(request)
        if not self.society:
            messages.error(request, "Please select a society first.")
            return redirect("reconciliation:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        """Route legacy manual workspace traffic to the statement-entry grid."""
        return redirect("reconciliation:manual-entry")

    def _resolve_bank_account(self):
        bank_account_id = (
            self.request.GET.get("bank_account")
            or self.request.POST.get("bank_account")
            or self.request.session.get("manual_workspace_bank_account_id")
        )
        bank_accounts = _manual_workspace_bank_accounts(self.society)
        bank_account = None
        if bank_account_id:
            bank_account = bank_accounts.filter(pk=bank_account_id).first()
        if bank_account is None:
            bank_account = bank_accounts.first()
        if bank_account is not None:
            self.request.session["manual_workspace_bank_account_id"] = bank_account.id
            self.request.session.modified = True
        return bank_account

    def _get_workspace_service(self, bank_account):
        return ManualWorkspaceService(
            user=self.request.user,
            society=self.society,
            bank_account=bank_account,
            session=self.request.session,
        )

    def _transaction_status_map(self, transactions):
        tx_ids = [tx.id for tx in transactions]
        if not tx_ids:
            return {}

        status_map = {}
        links = (
            ReconciliationLink.objects.filter(
                society=self.society,
                bank_transaction_id__in=tx_ids,
            )
            .select_related(
                "voucher_entry",
                "voucher_entry__voucher",
                "voucher_entry__unit",
                "voucher_entry__account",
            )
            .order_by("bank_transaction_id", "-matched_at", "-id")
        )

        for link in links:
            status_map.setdefault(
                link.bank_transaction_id,
                link,
            )
        return status_map

    def _workspace_context(self, bank_account=None, selected_tx=None):
        bank_account = bank_account or self._resolve_bank_account()

        bank_accounts = _manual_workspace_bank_accounts(self.society)
        filters_form = ManualWorkspaceFiltersForm(
            self.request.GET or None,
            society=self.society,
            initial={"bank_account": bank_account},
        )
        filters_form.is_valid()

        if not bank_account:
            return {
                "no_bank_accounts": True,
                "bank_accounts": bank_accounts,
                "filters_form": filters_form,
                "row_form": ManualWorkspaceRowForm(),
                "paste_form": ManualWorkspacePasteForm(),
            }

        service = self._get_workspace_service(bank_account)
        statement_import = service.get_or_create_workspace_import()
        transactions = (
            statement_import.transactions.select_related("normalized")
            .prefetch_related("reconciliation_links__voucher_entry__voucher")
            .order_by("transaction_date", "id")
        )

        cleaned_filters = filters_form.cleaned_data if filters_form.is_valid() else {}
        date_from = cleaned_filters.get("statement_date_from")
        date_to = cleaned_filters.get("statement_date_to")
        search_voucher = (cleaned_filters.get("search_voucher") or "").strip()
        show_unmatched_only = cleaned_filters.get("show_unmatched_only", False)
        show_reconciled = cleaned_filters.get("show_reconciled", True)

        if date_from:
            transactions = transactions.filter(transaction_date__gte=date_from)
        if date_to:
            transactions = transactions.filter(transaction_date__lte=date_to)

        if search_voucher:
            transactions = transactions.filter(
                Q(narration__icontains=search_voucher)
                | Q(reference_no__icontains=search_voucher)
                | Q(reconciliation_links__voucher_entry__voucher__reference_number__icontains=search_voucher)
                | Q(reconciliation_links__voucher_entry__voucher__narration__icontains=search_voucher)
            ).distinct()

        selected_tx_id = selected_tx.id if selected_tx else self.request.GET.get("selected_tx_id")
        if selected_tx_id:
            try:
                selected_tx_id = int(selected_tx_id)
            except (TypeError, ValueError):
                selected_tx_id = None

        selected_transaction = None
        if selected_tx_id:
            selected_transaction = transactions.filter(pk=selected_tx_id).first()
        if selected_transaction is None:
            selected_transaction = transactions.last()

        visible_ids = list(transactions.values_list("id", flat=True))
        matched_link_ids = set(
            ReconciliationLink.objects.filter(
                society=self.society,
                bank_transaction_id__in=visible_ids,
                status__in=[
                    ReconciliationLink.Status.MATCHED,
                    ReconciliationLink.Status.FORCE_MATCHED,
                ],
            ).values_list("bank_transaction_id", flat=True)
        )
        if show_unmatched_only:
            transactions = transactions.exclude(id__in=matched_link_ids)
        elif not show_reconciled:
            transactions = transactions.exclude(id__in=matched_link_ids)

        if selected_transaction and not transactions.filter(pk=selected_transaction.id).exists():
            selected_transaction = transactions.last()

        selected_suggestions = []
        if selected_transaction:
            selected_suggestions = (
                ReconciliationLink.objects.filter(
                    society=self.society,
                    bank_transaction=selected_transaction,
                    status=ReconciliationLink.Status.SUGGESTED,
                )
                .select_related(
                    "voucher_entry",
                    "voucher_entry__voucher",
                    "voucher_entry__unit",
                    "voucher_entry__account",
                )
                .order_by("-confidence_score", "-id")[:10]
            )

        matched_count = ReconciliationLink.objects.filter(
            society=self.society,
            bank_transaction__bank_statement_import=statement_import,
            status__in=[
                ReconciliationLink.Status.MATCHED,
                ReconciliationLink.Status.FORCE_MATCHED,
            ],
        ).count()
        exception_count = ReconciliationLink.objects.filter(
            society=self.society,
            bank_transaction__bank_statement_import=statement_import,
            status=ReconciliationLink.Status.EXCEPTION,
        ).count()
        total_entries = transactions.count()
        pending_count = max(total_entries - matched_count - exception_count, 0)

        total_debit = transactions.filter(dr_cr=BankTransaction.DrCr.DEBIT).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        total_credit = transactions.filter(dr_cr=BankTransaction.DrCr.CREDIT).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        difference_amount = total_credit - total_debit
        progress_percent = int((matched_count / total_entries) * 100) if total_entries else 0

        context = {
            "society": self.society,
            "bank_account": bank_account,
            "bank_accounts": bank_accounts,
            "statement_import": statement_import,
            "transactions": transactions,
            "transaction_link_map": self._transaction_status_map(transactions),
            "selected_transaction": selected_transaction,
            "bank_transaction": selected_transaction,
            "selected_suggestions": selected_suggestions,
            "suggested_links": selected_suggestions,
            "filters_form": filters_form,
            "show_unmatched_only": show_unmatched_only,
            "show_reconciled": show_reconciled,
            "row_form": ManualWorkspaceRowForm(),
            "paste_form": ManualWorkspacePasteForm(),
            "total_entries": total_entries,
            "matched_count": matched_count,
            "pending_count": pending_count,
            "exception_count": exception_count,
            "difference_amount": difference_amount,
            "progress_percent": progress_percent,
            "statement_period_start": statement_import.statement_start_date,
            "statement_period_end": statement_import.statement_end_date,
        }
        return context

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self._workspace_context())
        return context


manual_workspace_view = ManualWorkspaceView.as_view()


@require_POST
def manual_workspace_delete_row_view(request, tx_id):
    """Delete a manually entered bank transaction row."""
    from django.http import HttpResponse
    society, _ = get_selected_scope(request)
    if not society:
        return JsonResponse({"error": "No society selected."}, status=400)

    bank_transaction = get_object_or_404(
        BankTransaction.objects.select_related("bank_statement_import"),
        pk=tx_id,
        bank_statement_import__society=society,
    )

    # Check if transaction is already matched - prevent deletion of matched items
    existing_link = ReconciliationLink.objects.filter(
        society=society,
        bank_transaction=bank_transaction,
        status__in=[
            ReconciliationLink.Status.MATCHED,
            ReconciliationLink.Status.FORCE_MATCHED,
        ],
    ).exists()

    if existing_link:
        return JsonResponse(
            {"error": "Cannot delete a matched transaction. Unmatch it first."},
            status=400,
        )

    # Delete the transaction
    statement_import = bank_transaction.bank_statement_import
    bank_transaction.delete()

    # Update statement import row count
    statement_import.row_count = statement_import.transactions.count()
    statement_import.save(update_fields=["row_count"])

    # Return empty response (row will be removed via hx-swap="outerHTML")
    return HttpResponse("", status=200)


@require_GET
def manual_workspace_suggestions_view(request, tx_id):
    society, _ = get_selected_scope(request)
    if not society:
        return HttpResponseBadRequest("Please select a society first.")

    bank_transaction = get_object_or_404(
        BankTransaction.objects.select_related("bank_statement_import"),
        pk=tx_id,
        bank_statement_import__society=society,
    )

    links = _manual_workspace_suggestions_queryset(society, bank_transaction)

    return render(
        request,
        "reconciliation/partials/manual_suggestions.html",
        {
            "bank_transaction": bank_transaction,
            "suggested_links": links,
        },
    )


@require_POST
def manual_workspace_save_row_view(request):
    society, _ = get_selected_scope(request)
    if not society:
        return JsonResponse({"error": "No society selected."}, status=400)

    bank_account = _manual_workspace_bank_accounts(society).filter(
        pk=request.session.get("manual_workspace_bank_account_id"),
    ).first() or _manual_workspace_bank_accounts(society).first()
    if not bank_account:
        return JsonResponse({"error": "No active bank account found."}, status=400)

    service = ManualWorkspaceService(
        user=request.user,
        society=society,
        bank_account=bank_account,
        session=request.session,
    )

    form = ManualWorkspaceRowForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "reconciliation/partials/manual_row_form_errors.html",
            {"form": form},
            status=400,
        )

    row = ManualEntryRow(
        transaction_date=form.cleaned_data["transaction_date"].isoformat(),
        narration=form.cleaned_data["narration"],
        amount=form.cleaned_data["debit"] or form.cleaned_data["credit"],
        dr_cr="DEBIT" if form.cleaned_data["debit"] else "CREDIT",
        reference_no=form.cleaned_data.get("reference_no") or "",
        balance=form.cleaned_data["balance"],
    )
    transaction = service.save_row(row, source_row_index=None)

    workspace_context = ManualWorkspaceView()
    workspace_context.request = request
    workspace_context.society = society
    context = workspace_context._workspace_context(bank_account=bank_account, selected_tx=transaction)
    context["transaction"] = transaction
    context["bank_transaction"] = transaction
    context["suggested_links"] = _manual_workspace_suggestions_queryset(society, transaction)

    return render(
        request,
        "reconciliation/partials/manual_row_result.html",
        context,
    )


@require_POST
def manual_workspace_paste_view(request):
    society, _ = get_selected_scope(request)
    if not society:
        return JsonResponse({"error": "No society selected."}, status=400)

    bank_account = _manual_workspace_bank_accounts(society).filter(
        pk=request.session.get("manual_workspace_bank_account_id"),
    ).first() or _manual_workspace_bank_accounts(society).first()
    if not bank_account:
        return JsonResponse({"error": "No active bank account found."}, status=400)

    form = ManualWorkspacePasteForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "reconciliation/partials/manual_row_form_errors.html",
            {"form": form},
            status=400,
        )

    service = ManualWorkspaceService(
        user=request.user,
        society=society,
        bank_account=bank_account,
        session=request.session,
    )
    created_transactions = service.bulk_save_paste(form.cleaned_data["pasted_rows"])
    latest_transaction = created_transactions[-1] if created_transactions else None

    workspace_context = ManualWorkspaceView()
    workspace_context.request = request
    workspace_context.society = society
    context = workspace_context._workspace_context(bank_account=bank_account, selected_tx=latest_transaction)
    context["created_transactions"] = created_transactions
    if latest_transaction:
        context["bank_transaction"] = latest_transaction
        context["suggested_links"] = _manual_workspace_suggestions_queryset(society, latest_transaction)

    return render(
        request,
        "reconciliation/partials/manual_rows_result.html",
        context,
    )


# ---------------------------------------------------------------------------
# Manual Workspace Grid API Endpoints
# ---------------------------------------------------------------------------

@require_POST
def manual_workspace_cell_update_view(request, import_id):
    """
    POST /reconciliation/api/manual-workspace/cell-update/<import_id>/

    Update a single cell in the manual workspace grid.
    Request body: {"row_id": 123, "field": "narration", "value": "new value"}
    """
    society, _ = get_selected_scope(request)
    if not society:
        return JsonResponse({"status": "error", "error": "No society selected."}, status=400)

    statement_import = get_object_or_404(
        BankStatementImport.objects.filter(society=society),
        pk=import_id,
    )

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "error": "Invalid JSON."}, status=400)

    form = ManualCellUpdateForm(body)
    if not form.is_valid():
        return JsonResponse(
            {"status": "error", "errors": form.errors},
            status=400,
        )

    row_id = form.cleaned_data["row_id"]
    field = form.cleaned_data["field"]
    value = form.cleaned_data["value"]

    # Verify the row belongs to this import
    if not BankTransaction.objects.filter(
        pk=row_id, bank_statement_import_id=import_id,
    ).exists():
        return JsonResponse(
            {"status": "error", "error": f"Row {row_id} not found in this import."},
            status=404,
        )

    try:
        from reconciliation.services.manual_entry_batch_service import update_cell
        result = update_cell(
            transaction_id=row_id,
            field=field,
            value=value,
            import_id=import_id,
            user=request.user,
        )
    except ValidationError as exc:
        return JsonResponse(
            {"status": "error", "error": str(exc)},
            status=400,
        )

    return JsonResponse({"status": "ok", **result})


@require_POST
def manual_workspace_batch_save_view(request, import_id):
    """
    POST /reconciliation/api/manual-workspace/batch-save/<import_id>/

    Save all dirty cells at once in an atomic transaction.
    Request body: {"changes": [{"row_id": 123, "field": "narration", "value": "new"}, ...]}
    """
    society, _ = get_selected_scope(request)
    if not society:
        return JsonResponse({"status": "error", "error": "No society selected."}, status=400)

    statement_import = get_object_or_404(
        BankStatementImport.objects.filter(society=society),
        pk=import_id,
    )

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "error": "Invalid JSON."}, status=400)

    form = ManualBatchSaveForm(body)
    if not form.is_valid():
        return JsonResponse(
            {"status": "error", "errors": form.errors},
            status=400,
        )

    changes = form.cleaned_data["changes"]

    from reconciliation.services.manual_entry_batch_service import batch_save
    result = batch_save(
        import_id=import_id,
        changes=changes,
        user=request.user,
    )

    return JsonResponse({"status": "ok", **result})


@require_POST
def manual_workspace_undo_view(request, import_id):
    """
    POST /reconciliation/api/manual-workspace/undo/<import_id>/

    Undo a specific operation identified by operation_id.
    Request body: {"operation_id": "op_12345"}
    """
    society, _ = get_selected_scope(request)
    if not society:
        return JsonResponse({"status": "error", "error": "No society selected."}, status=400)

    statement_import = get_object_or_404(
        BankStatementImport.objects.filter(society=society),
        pk=import_id,
    )

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "error": "Invalid JSON."}, status=400)

    operation_id = body.get("operation_id", "").strip()
    if not operation_id:
        return JsonResponse(
            {"status": "error", "error": "operation_id is required."},
            status=400,
        )

    from reconciliation.services.manual_entry_batch_service import undo_last_operation
    result = undo_last_operation(import_id, operation_id)

    if result is None:
        return JsonResponse(
            {"status": "error", "error": "Operation not found or already undone."},
            status=404,
        )

    return JsonResponse({"status": "ok", "reverted": result})


@require_POST
def manual_workspace_rows_delete_view(request, import_id):
    """
    POST /reconciliation/api/manual-workspace/rows/delete/<import_id>/

    Batch delete bank transaction rows.
    Request body: {"row_ids": [123, 124]}
    """
    society, _ = get_selected_scope(request)
    if not society:
        return JsonResponse({"status": "error", "error": "No society selected."}, status=400)

    statement_import = get_object_or_404(
        BankStatementImport.objects.filter(society=society),
        pk=import_id,
    )

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "error": "Invalid JSON."}, status=400)

    row_ids = body.get("row_ids", [])
    if not row_ids or not isinstance(row_ids, list):
        return JsonResponse(
            {"status": "error", "error": "row_ids must be a non-empty list."},
            status=400,
        )

    # Validate all IDs are integers
    try:
        row_ids = [int(rid) for rid in row_ids]
    except (TypeError, ValueError):
        return JsonResponse(
            {"status": "error", "error": "All row_ids must be integers."},
            status=400,
        )

    # Prevent deletion of matched transactions
    matched_ids = set(
        ReconciliationLink.objects.filter(
            society=society,
            bank_transaction_id__in=row_ids,
            status__in=[
                ReconciliationLink.Status.MATCHED,
                ReconciliationLink.Status.FORCE_MATCHED,
            ],
        ).values_list("bank_transaction_id", flat=True)
    )

    deletable_ids = [rid for rid in row_ids if rid not in matched_ids]
    blocked_ids = [rid for rid in row_ids if rid in matched_ids]

    # Only delete rows that belong to this import
    deleted_count, _ = BankTransaction.objects.filter(
        pk__in=deletable_ids,
        bank_statement_import=statement_import,
    ).delete()

    # Recalculate row_count
    statement_import.row_count = statement_import.transactions.count()
    statement_import.save(update_fields=["row_count"])

    return JsonResponse({
        "status": "ok",
        "deleted_count": deleted_count,
        "blocked_count": len(blocked_ids),
        "blocked_ids": blocked_ids,
    })


@require_GET
def manual_workspace_grid_data_view(request, import_id):
    """
    GET /reconciliation/api/manual-workspace/grid-data/<import_id>/

    Returns JSON grid data for the frontend with server-side pagination & search.
    Query params: ?offset=0&limit=100&search=...&date_from=...&date_to=...
    """
    society, _ = get_selected_scope(request)
    if not society:
        return JsonResponse({"status": "error", "error": "No society selected."}, status=400)

    statement_import = get_object_or_404(
        BankStatementImport.objects.filter(society=society),
        pk=import_id,
    )

    # Pagination
    try:
        offset = int(request.GET.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = int(request.GET.get("limit", 100))
    except (TypeError, ValueError):
        limit = 100
    limit = min(max(limit, 1), 500)  # clamp between 1 and 500

    # Base queryset
    transactions = statement_import.transactions.select_related("normalized").order_by(
        "transaction_date", "id",
    )

    # Filters
    search = (request.GET.get("search", "") or "").strip()
    if search:
        transactions = transactions.filter(
            Q(narration__icontains=search)
            | Q(reference_no__icontains=search)
            | Q(cheque_no__icontains=search)
        )

    date_from = request.GET.get("date_from", "").strip()
    if date_from:
        transactions = transactions.filter(transaction_date__gte=date_from)

    date_to = request.GET.get("date_to", "").strip()
    if date_to:
        transactions = transactions.filter(transaction_date__lte=date_to)

    total_count = transactions.count()

    # Prefetch reconciliation status
    tx_ids = list(transactions.values_list("id", flat=True)[offset:offset + limit])
    status_map = {}
    if tx_ids:
        links = ReconciliationLink.objects.filter(
            society=society,
            bank_transaction_id__in=tx_ids,
        ).values(
            "bank_transaction_id", "status", "id",
        ).order_by("bank_transaction_id", "-matched_at", "-id")
        for link in links:
            status_map.setdefault(link["bank_transaction_id"], link["status"])

    # Build row data
    rows = []
    paginated = transactions[offset:offset + limit]
    for tx in paginated:
        status = status_map.get(tx.id, "UNMATCHED")
        is_reconciled = status in (
            ReconciliationLink.Status.MATCHED,
            ReconciliationLink.Status.FORCE_MATCHED,
        )
        rows.append({
            "id": tx.id,
            "transaction_date": tx.transaction_date.isoformat() if tx.transaction_date else "",
            "narration": tx.narration or "",
            "reference_no": tx.reference_no or "",
            "dr_cr": tx.dr_cr,
            "amount": str(tx.amount),
            "balance": str(tx.balance) if tx.balance is not None else "",
            "status": status,
            "is_reconciled": is_reconciled,
        })

    return JsonResponse({
        "status": "ok",
        "rows": rows,
        "total_count": total_count,
        "offset": offset,
        "limit": limit,
    })


# ---------------------------------------------------------------------------
# Main Reconciliation Workspace
# ---------------------------------------------------------------------------

class WorkspaceView(LoginRequiredMixin, TemplateView):
    template_name = "reconciliation/workspace.html"

    def dispatch(self, request, *args, **kwargs):
        self.society, _ = get_selected_scope(request)
        if not self.society:
            messages.error(request, "Please select a society first.")
            return redirect("reconciliation:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Filters
        status_filter = self.request.GET.get("status", "")
        date_from = self.request.GET.get("date_from", "")
        date_to = self.request.GET.get("date_to", "")
        bank_filter = self.request.GET.get("bank", "")

        # Base queryset — all reconciliation links for the society
        links_qs = ReconciliationLink.objects.filter(
            society=self.society,
        ).select_related(
            "bank_transaction",
            "bank_transaction__bank_statement_import",
            "bank_transaction__normalized",
            "voucher_entry",
            "voucher_entry__voucher",
            "voucher_entry__account",
            "voucher_entry__unit",
            "matched_by",
        ).order_by("-confidence_score", "-id")

        if status_filter:
            links_qs = links_qs.filter(status=status_filter)

        if date_from:
            links_qs = links_qs.filter(
                bank_transaction__transaction_date__gte=date_from,
            )
        if date_to:
            links_qs = links_qs.filter(
                bank_transaction__transaction_date__lte=date_to,
            )

        # Unmatched bank transactions (no link yet)
        linked_bank_ids = ReconciliationLink.objects.filter(
            society=self.society,
        ).exclude(
            status__in=[
                ReconciliationLink.Status.PENDING,
                ReconciliationLink.Status.SUGGESTED,
            ],
        ).values_list("bank_transaction_id", flat=True)

        unmatched_bank_txs = BankTransaction.objects.filter(
            bank_statement_import__society=self.society,
            is_duplicate=False,
        ).exclude(
            id__in=linked_bank_ids,
        ).select_related(
            "bank_statement_import",
            "normalized",
        ).order_by("-transaction_date", "-id")

        if date_from:
            unmatched_bank_txs = unmatched_bank_txs.filter(
                transaction_date__gte=date_from,
            )
        if date_to:
            unmatched_bank_txs = unmatched_bank_txs.filter(
                transaction_date__lte=date_to,
            )

        # Summary stats
        status_summary = dict(
            ReconciliationLink.objects.filter(society=self.society)
            .values("status")
            .annotate(count=Count("id"))
            .values_list("status", "count")
        )

        context.update(
            links=links_qs,
            unmatched_bank_txs=unmatched_bank_txs[:100],
            status_summary=status_summary,
            status_filter=status_filter,
            date_from=date_from,
            date_to=date_to,
            bank_filter=bank_filter,
            status_choices=ReconciliationLink.Status.choices,
        )
        return context


workspace_view = WorkspaceView.as_view()


# ---------------------------------------------------------------------------
# Match Actions
# ---------------------------------------------------------------------------

@require_POST
def confirm_match_view(request, link_id):
    """Confirm a suggested match or create a manual match."""
    society, _ = get_selected_scope(request)
    if not society:
        return JsonResponse({"error": "No society selected."}, status=400)

    try:
        link = ReconciliationLink.objects.get(
            id=link_id, society=society,
        )
    except ReconciliationLink.DoesNotExist:
        return JsonResponse({"error": "Link not found."}, status=404)

    link.confirm_match(user=request.user)
    messages.success(request, "Match confirmed.")

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"status": "ok", "new_status": link.status})

    if request.headers.get("HX-Request") == "true":
        suggested_links = _manual_workspace_suggestions_queryset(
            society,
            link.bank_transaction,
        )
        return render(
            request,
            "reconciliation/partials/manual_suggestions.html",
            {
                "bank_transaction": link.bank_transaction,
                "suggested_links": suggested_links,
            },
        )

    return redirect("reconciliation:workspace")


@require_POST
def unmatched_link_view(request, link_id):
    """Unmatch a previously matched reconciliation link."""
    society, _ = get_selected_scope(request)
    if not society:
        return JsonResponse({"error": "No society selected."}, status=400)

    try:
        link = ReconciliationLink.objects.get(
            id=link_id, society=society,
        )
    except ReconciliationLink.DoesNotExist:
        return JsonResponse({"error": "Link not found."}, status=404)

    link.unmatch(user=request.user)
    messages.info(request, "Match removed.")

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"status": "ok", "new_status": link.status})

    if request.headers.get("HX-Request") == "true":
        suggested_links = _manual_workspace_suggestions_queryset(
            society,
            link.bank_transaction,
        )
        return render(
            request,
            "reconciliation/partials/manual_suggestions.html",
            {
                "bank_transaction": link.bank_transaction,
                "suggested_links": suggested_links,
            },
        )

    return redirect("reconciliation:workspace")


# ---------------------------------------------------------------------------
# Run Matching & Mark Duplicate
# ---------------------------------------------------------------------------

def run_matching_view(request):
    """
    Trigger the matching engine to run all matching rules against
    unreconciled bank transactions.
    """
    society, _ = get_selected_scope(request)
    if not society:
        messages.error(request, "Please select a society first.")
        return redirect("reconciliation:dashboard")

    engine = MatchingEngine(society)
    result = engine.run_matching(
        auto_confirm=True,
        create_suggestions=True,
    )

    matched_count = len(result["auto_matched"])
    suggested_count = len(result["suggested"])
    stats = result.get("stats", {})

    detail_parts = []
    if matched_count:
        detail_parts.append(f"{matched_count} auto-matched")
    if suggested_count:
        detail_parts.append(f"{suggested_count} suggested")
    if stats:
        rule_summary = ", ".join(
            f"{rule}: {data['count']}"
            for rule, data in stats.items()
            if isinstance(data, dict) and data.get("count", 0) > 0
        )
        if rule_summary:
            detail_parts.append(f"Rules: {rule_summary}")

    if not detail_parts:
        messages.info(request, "Matching completed. No new matches found.")
    else:
        messages.success(
            request, "Matching completed: " + "; ".join(detail_parts)
        )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"status": "ok", "result": {
            "auto_matched": matched_count,
            "suggested": suggested_count,
            "stats": stats,
        }})

    return redirect("reconciliation:workspace")


@require_POST
def mark_duplicate_view(request, link_id):
    """
    Mark a reconciliation link as a duplicate.
    """
    society, _ = get_selected_scope(request)
    if not society:
        return JsonResponse({"error": "No society selected."}, status=400)

    try:
        link = ReconciliationLink.objects.get(
            id=link_id, society=society,
        )
    except ReconciliationLink.DoesNotExist:
        return JsonResponse({"error": "Link not found."}, status=404)

    link.mark_duplicate(user=request.user)
    messages.info(request, "Marked as duplicate.")

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"status": "ok", "new_status": link.status})

    if request.headers.get("HX-Request") == "true":
        suggested_links = _manual_workspace_suggestions_queryset(
            society,
            link.bank_transaction,
        )
        return render(
            request,
            "reconciliation/partials/manual_suggestions.html",
            {
                "bank_transaction": link.bank_transaction,
                "suggested_links": suggested_links,
            },
        )

    return redirect("reconciliation:workspace")


class ForceMatchView(LoginRequiredMixin, FormView):
    template_name = "reconciliation/force_match.html"
    form_class = ForceMatchForm

    def dispatch(self, request, *args, **kwargs):
        self.society, _ = get_selected_scope(request)
        if not self.society:
            messages.error(request, "Please select a society first.")
            return redirect("reconciliation:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["society"] = self.society
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        bank_tx_id = self.request.GET.get("bank_tx")
        if bank_tx_id:
            initial["bank_transaction"] = bank_tx_id
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_bank_transaction = None
        bank_tx_id = self.request.GET.get("bank_tx") or self.request.POST.get("bank_transaction")
        if bank_tx_id:
            selected_bank_transaction = (
                BankTransaction.objects.filter(
                    id=bank_tx_id,
                    bank_statement_import__society=self.society,
                    is_duplicate=False,
                )
                .select_related("bank_statement_import", "normalized")
                .first()
            )
        context["selected_bank_transaction"] = selected_bank_transaction
        context["open_force_match_modal"] = bool(selected_bank_transaction or self.request.GET.get("bank_tx"))
        return context

    def form_valid(self, form):
        bank_tx = form.cleaned_data["bank_transaction"]
        ledger_entry = form.cleaned_data["ledger_entry"]
        remarks = form.cleaned_data.get("remarks", "")

        engine = MatchingEngine(self.society)
        engine.force_match(
            bank_transaction=bank_tx,
            ledger_entry=ledger_entry,
            user=self.request.user,
            remarks=remarks,
        )
        messages.success(self.request, "Force match created.")
        return redirect("reconciliation:workspace")


force_match_view = ForceMatchView.as_view()


# ---------------------------------------------------------------------------
# Exception Management
# ---------------------------------------------------------------------------

class ExceptionListView(LoginRequiredMixin, ListView):
    model = ReconciliationLink
    template_name = "reconciliation/exceptions.html"
    context_object_name = "exceptions"
    paginate_by = 50

    def get_queryset(self):
        society, _ = get_selected_scope(self.request)
        qs = ReconciliationLink.objects.filter(
            society=society,
            status__in=[
                ReconciliationLink.Status.EXCEPTION,
                ReconciliationLink.Status.DUPLICATE,
                ReconciliationLink.Status.PARTIAL,
            ],
        ).select_related(
            "bank_transaction",
            "bank_transaction__bank_statement_import",
            "bank_transaction__normalized",
            "voucher_entry",
            "voucher_entry__voucher",
            "voucher_entry__account",
        ).order_by("status", "-id")
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        society, _ = get_selected_scope(self.request)

        # Exception-type summary via ReportService
        summary = ReportService.get_exception_summary(society)
        context["exception_summary"] = summary

        # Also show unmatched bank transactions that could be exceptions
        linked_ids = ReconciliationLink.objects.filter(
            society=society,
        ).values_list("bank_transaction_id", flat=True)

        context["orphan_bank_txs"] = BankTransaction.objects.filter(
            bank_statement_import__society=society,
            is_duplicate=False,
        ).exclude(
            id__in=linked_ids,
        ).select_related(
            "bank_statement_import",
            "normalized",
        ).order_by("-transaction_date")[:50]

        return context


exception_list_view = ExceptionListView.as_view()


# ---------------------------------------------------------------------------
# Adjustment Workflow
# ---------------------------------------------------------------------------

@require_POST
def create_adjustment_view(request, link_id):
    """Create an adjustment voucher for a BANK_ONLY reconciliation exception."""
    society, _ = get_selected_scope(request)
    if not society:
        return JsonResponse({"error": "No society selected."}, status=400)

    try:
        link = ReconciliationLink.objects.select_related("bank_transaction").get(
            id=link_id, society=society,
        )
    except ReconciliationLink.DoesNotExist:
        return JsonResponse({"error": "Link not found."}, status=404)

    if link.status != ReconciliationLink.Status.EXCEPTION:
        return JsonResponse(
            {"error": f"Link must be in EXCEPTION status, not '{link.status}'."},
            status=400,
        )

    if link.exception_type != ReconciliationLink.ExceptionType.BANK_ONLY:
        return JsonResponse(
            {"error": f"Adjustment only supported for BANK_ONLY exceptions, not '{link.exception_type}'."},
            status=400,
        )

    bank_transaction = link.bank_transaction

    try:
        voucher = AdjustmentService.create_adjustment(
            society=society,
            bank_transaction=bank_transaction,
            user=request.user,
        )
    except (ValueError, ValidationError) as exc:
        logger.exception("Adjustment creation failed for link %s", link_id)
        return JsonResponse({"error": str(exc)}, status=400)

    bank_entry = voucher.entries.filter(account__is_bank=True).first()
    if bank_entry:
        link.voucher_entry = bank_entry

    link.status = ReconciliationLink.Status.MATCHED
    link.matched_by = request.user
    link.matched_at = timezone.now()
    link.remarks = f"Resolved via adjustment voucher {voucher.display_number}"
    link.save(update_fields=[
        "voucher_entry", "status", "matched_by", "matched_at", "remarks",
    ])

    messages.success(
        request,
        f"Adjustment voucher {voucher.display_number} created and linked.",
    )

    if request.headers.get("HX-Request") == "true" or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        suggested_links = _manual_workspace_suggestions_queryset(society, bank_transaction)
        return render(
            request,
            "reconciliation/partials/manual_suggestions.html",
            {
                "bank_transaction": bank_transaction,
                "suggested_links": suggested_links,
            },
        )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({
            "status": "ok",
            "voucher_id": voucher.pk,
            "voucher_display": voucher.display_number,
            "new_status": link.status,
        })

    return redirect("reconciliation:workspace")


@require_POST
def create_adjustment_for_orphan_view(request):
    """Create an adjustment voucher for an orphan bank transaction."""
    society, _ = get_selected_scope(request)
    if not society:
        return JsonResponse({"error": "No society selected."}, status=400)

    bank_tx_id = request.POST.get("bank_tx_id")
    if not bank_tx_id:
        return JsonResponse({"error": "Missing bank_tx_id parameter."}, status=400)

    try:
        bank_tx_id = int(bank_tx_id)
    except (TypeError, ValueError):
        return JsonResponse({"error": "Invalid bank_tx_id."}, status=400)

    try:
        bank_transaction = BankTransaction.objects.select_related(
            "bank_statement_import",
        ).get(
            id=bank_tx_id,
            bank_statement_import__society=society,
            is_duplicate=False,
        )
    except BankTransaction.DoesNotExist:
        return JsonResponse({"error": "Bank transaction not found."}, status=404)

    existing_link = ReconciliationLink.objects.filter(
        society=society,
        bank_transaction=bank_transaction,
    ).exclude(
        status__in=[
            ReconciliationLink.Status.REVERSED,
            ReconciliationLink.Status.IGNORED,
        ],
    ).first()

    if existing_link:
        return JsonResponse({
            "error": f"A reconciliation link already exists (status: {existing_link.status}).",
        }, status=400)

    try:
        voucher = AdjustmentService.create_adjustment(
            society=society,
            bank_transaction=bank_transaction,
            user=request.user,
        )
    except (ValueError, ValidationError) as exc:
        logger.exception("Orphan adjustment failed for bank tx %s", bank_tx_id)
        return JsonResponse({"error": str(exc)}, status=400)

    bank_entry = voucher.entries.filter(account__is_bank=True).first()
    if not bank_entry:
        bank_entry = voucher.entries.first()

    link = ReconciliationLink.objects.create(
        society=society,
        voucher_entry=bank_entry,
        bank_transaction=bank_transaction,
        matched_amount=bank_transaction.amount,
        match_type=ReconciliationLink.MatchType.FORCE,
        confidence_score=100,
        matched_by=request.user,
        matched_at=timezone.now(),
        is_manual=True,
        status=ReconciliationLink.Status.MATCHED,
        remarks=f"Created via orphan adjustment voucher {voucher.display_number}",
    )

    messages.success(
        request,
        f"Adjustment voucher {voucher.display_number} created for orphan transaction.",
    )

    if request.headers.get("HX-Request") == "true" or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        suggested_links = _manual_workspace_suggestions_queryset(society, bank_transaction)
        return render(
            request,
            "reconciliation/partials/manual_suggestions.html",
            {
                "bank_transaction": bank_transaction,
                "suggested_links": suggested_links,
            },
        )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({
            "status": "ok",
            "link_id": link.id,
            "voucher_id": voucher.pk,
            "voucher_display": voucher.display_number,
        })

    return redirect("reconciliation:workspace")


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

class BRSReportView(LoginRequiredMixin, TemplateView):
    """Bank Reconciliation Statement report — uses ReportService."""
    template_name = "reconciliation/report_brs.html"

    def dispatch(self, request, *args, **kwargs):
        self.society, _ = get_selected_scope(request)
        if not self.society:
            messages.error(request, "Please select a society first.")
            return redirect("reconciliation:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        as_of_date_str = self.request.GET.get("as_of_date", "")
        as_of_date = None
        if as_of_date_str:
            try:
                as_of_date = date.fromisoformat(as_of_date_str)
            except (ValueError, TypeError):
                as_of_date = date.today()
        else:
            as_of_date = date.today()

        brs_data = ReportService.get_brs_data(
            self.society,
            as_of_date=as_of_date,
        )

        context.update(
            as_of_date=as_of_date,
            as_of_date_str=as_of_date.isoformat(),
            society_name=self.society.name,
            **brs_data,
        )
        return context


brs_report_view = BRSReportView.as_view()


class UnmatchedReportView(LoginRequiredMixin, TemplateView):
    """Report showing unmatched entries from both sides — uses ReportService."""
    template_name = "reconciliation/report_unmatched.html"

    def dispatch(self, request, *args, **kwargs):
        self.society, _ = get_selected_scope(request)
        if not self.society:
            messages.error(request, "Please select a society first.")
            return redirect("reconciliation:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        report_data = ReportService.get_unmatched_report(self.society)

        # Date-range filter params (optional, for display)
        date_from = self.request.GET.get("date_from", "")
        date_to = self.request.GET.get("date_to", "")

        context.update(
            date_from=date_from,
            date_to=date_to,
            **report_data,
        )
        return context


unmatched_report_view = UnmatchedReportView.as_view()


class DuplicateReportView(LoginRequiredMixin, TemplateView):
    """Report showing duplicate transactions — uses ReportService."""
    template_name = "reconciliation/report_duplicates.html"

    def dispatch(self, request, *args, **kwargs):
        self.society, _ = get_selected_scope(request)
        if not self.society:
            messages.error(request, "Please select a society first.")
            return redirect("reconciliation:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        report_data = ReportService.get_duplicates_report(self.society)

        context.update(**report_data)
        return context


duplicate_report_view = DuplicateReportView.as_view()


# ---------------------------------------------------------------------------
# Audit Trail
# ---------------------------------------------------------------------------

class LinkAuditView(LoginRequiredMixin, DetailView):
    """Timeline view showing all status changes for a single ReconciliationLink."""
    model = ReconciliationLink
    template_name = "reconciliation/link_audit.html"
    context_object_name = "link"
    pk_url_kwarg = "link_id"

    def get_queryset(self):
        society, _ = get_selected_scope(self.request)
        qs = ReconciliationLink.objects.select_related(
            "bank_transaction",
            "bank_transaction__bank_statement_import",
            "bank_transaction__normalized",
            "voucher_entry",
            "voucher_entry__voucher",
            "voucher_entry__account",
            "voucher_entry__unit",
            "matched_by",
        )
        if society:
            qs = qs.filter(society=society)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["history_entries"] = (
            self.object.history.select_related("performed_by")
            .order_by("-performed_at")
        )
        return context


link_audit_view = LinkAuditView.as_view()


class AuditLogView(LoginRequiredMixin, ListView):
    """Full audit log with filters for reconciliation history."""
    model = ReconciliationHistory
    template_name = "reconciliation/audit_log.html"
    context_object_name = "history_entries"
    paginate_by = 50

    def get_queryset(self):
        society, _ = get_selected_scope(self.request)
        qs = ReconciliationHistory.objects.select_related(
            "reconciliation_link",
            "reconciliation_link__bank_transaction",
            "reconciliation_link__voucher_entry__voucher",
            "performed_by",
        ).order_by("-performed_at")

        if society:
            qs = qs.filter(reconciliation_link__society=society)

        # Query param filters
        status = self.request.GET.get("status", "")
        user_filter = self.request.GET.get("user", "")
        from_date = self.request.GET.get("from_date", "")
        to_date = self.request.GET.get("to_date", "")
        link_type = self.request.GET.get("link_type", "")

        if status:
            qs = qs.filter(new_status=status)
        if user_filter:
            qs = qs.filter(
                Q(performed_by__name=user_filter) |
                Q(performed_by__email=user_filter)
            )
        if from_date:
            qs = qs.filter(performed_at__date__gte=from_date)
        if to_date:
            qs = qs.filter(performed_at__date__lte=to_date)
        if link_type:
            qs = qs.filter(reconciliation_link__match_type=link_type)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        society, _ = get_selected_scope(self.request)

        # Users who have made changes (for filter dropdown)
        if society:
            user_ids = (
                ReconciliationHistory.objects
                .filter(reconciliation_link__society=society)
                .values_list("performed_by_id", flat=True)
                .distinct()
            )
        else:
            user_ids = (
                ReconciliationHistory.objects
                .values_list("performed_by_id", flat=True)
                .distinct()
            )
        from django.contrib.auth import get_user_model
        User = get_user_model()
        context["users"] = User.objects.filter(id__in=user_ids).order_by("name", "email")

        # Pass current filter values back for form persistence
        context["filter_status"] = self.request.GET.get("status", "")
        context["filter_user"] = self.request.GET.get("user", "")
        context["filter_from_date"] = self.request.GET.get("from_date", "")
        context["filter_to_date"] = self.request.GET.get("to_date", "")
        context["filter_link_type"] = self.request.GET.get("link_type", "")

        # Status choices for dropdowns
        context["status_choices"] = ReconciliationLink.Status.choices
        context["match_type_choices"] = ReconciliationLink.MatchType.choices

        # Stats summary
        base_qs = ReconciliationHistory.objects.all()
        if society:
            base_qs = base_qs.filter(reconciliation_link__society=society)
        context["total_changes"] = base_qs.count()
        most_recent = base_qs.order_by("-performed_at").first()
        context["most_recent_change"] = most_recent.performed_at if most_recent else None

        return context


audit_log_view = AuditLogView.as_view()


# ---------------------------------------------------------------------------
# Redesigned Manual Bank Statement Entry
# ---------------------------------------------------------------------------

class ManualEntryView(LoginRequiredMixin, TemplateView):
    """Main manual statement entry page with Excel-style grid."""

    template_name = "reconciliation/manual_entry.html"

    def dispatch(self, request, *args, **kwargs):
        self.society, _ = get_selected_scope(request)
        if not self.society:
            messages.error(request, "Please select a society first.")
            return redirect("reconciliation:dashboard")
        return super().dispatch(request, *args, **kwargs)

    @staticmethod
    def _parse_filter_date(value):
        value = (value or "").strip()
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_filter_decimal(value):
        value = (value or "").strip().replace(",", "")
        if not value:
            return None
        try:
            amount = Decimal(value)
        except Exception:
            return None
        return amount if amount >= 0 else None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        recon_filter = self.request.GET.get("recon_filter", "pending")
        recon_filter_choices = (
            ("pending", "Needs Match"),
            ("all", "All Entries"),
            ("reconciled", "Matched"),
            ("manual", "Manual Matches"),
        )
        valid_recon_filters = {choice[0] for choice in recon_filter_choices}
        if recon_filter not in valid_recon_filters:
            recon_filter = "pending"
        selected_recon_filter_label = dict(recon_filter_choices)[recon_filter]
        matched_statuses = [
            ReconciliationLink.Status.MATCHED,
            ReconciliationLink.Status.FORCE_MATCHED,
            ReconciliationLink.Status.PARTIAL,
        ]
        transaction_types = {choice[0] for choice in BankTransaction.DrCr.choices}
        payment_modes = {choice[0] for choice in Voucher.PaymentMode.choices}
        voucher_types = {choice[0] for choice in Voucher.VoucherType.choices}

        filter_values = {
            "date_from": (self.request.GET.get("date_from") or "").strip(),
            "date_to": (self.request.GET.get("date_to") or "").strip(),
            "search": (self.request.GET.get("search") or "").strip(),
            "amount_min": (self.request.GET.get("amount_min") or "").strip(),
            "amount_max": (self.request.GET.get("amount_max") or "").strip(),
            "dr_cr": (self.request.GET.get("dr_cr") or "").strip().upper(),
            "payment_mode": (self.request.GET.get("payment_mode") or "").strip().upper(),
            "voucher_type": (self.request.GET.get("voucher_type") or "").strip().upper(),
        }
        if filter_values["dr_cr"] not in transaction_types:
            filter_values["dr_cr"] = ""
        if filter_values["payment_mode"] not in payment_modes:
            filter_values["payment_mode"] = ""
        if filter_values["voucher_type"] not in voucher_types:
            filter_values["voucher_type"] = ""

        date_from = self._parse_filter_date(filter_values["date_from"])
        date_to = self._parse_filter_date(filter_values["date_to"])
        amount_min = self._parse_filter_decimal(filter_values["amount_min"])
        amount_max = self._parse_filter_decimal(filter_values["amount_max"])
        if date_from and date_to and date_from > date_to:
            date_from, date_to = date_to, date_from
            filter_values["date_from"] = date_from.isoformat()
            filter_values["date_to"] = date_to.isoformat()
        if amount_min is not None and amount_max is not None and amount_min > amount_max:
            amount_min, amount_max = amount_max, amount_min
            filter_values["amount_min"] = str(amount_min)
            filter_values["amount_max"] = str(amount_max)

        bank_accounts = Account.objects.filter(
            Q(is_bank=True) | Q(sub_type=Account.SubType.BANK),
            society=self.society,
        ).order_by("name")

        selected_bank_account = None
        selected_bank_account_id = self.request.GET.get("bank_account")
        if selected_bank_account_id:
            selected_bank_account = bank_accounts.filter(pk=selected_bank_account_id).first()
        if selected_bank_account is None:
            latest_statement = (
                BankStatementImport.objects.filter(
                    society=self.society,
                    bank_account__in=bank_accounts,
                )
                .select_related("bank_account")
                .order_by("-uploaded_at", "-id")
                .first()
            )
            selected_bank_account = latest_statement.bank_account if latest_statement else bank_accounts.first()

        batch_form = ManualEntryBatchForm(society=self.society)
        row_form = ManualEntryRowForm()
        recent_bank_transactions = BankTransaction.objects.none()
        voucher_entries = LedgerEntry.objects.none()

        if selected_bank_account:
            link_queryset = ReconciliationLink.objects.select_related(
                "bank_transaction__bank_statement_import",
                "voucher_entry__voucher",
                "voucher_entry__account",
                "voucher_entry__unit",
            ).order_by("-matched_at", "-id")
            recent_bank_transactions = BankTransaction.objects.filter(
                bank_statement_import__society=self.society,
                bank_statement_import__bank_account=selected_bank_account,
                is_duplicate=False,
            )
            voucher_entries = LedgerEntry.objects.filter(
                voucher__society=self.society,
                voucher__posted_at__isnull=False,
                account=selected_bank_account,
            )

            if recon_filter == "pending":
                recent_bank_transactions = recent_bank_transactions.exclude(
                    reconciliation_links__status__in=matched_statuses,
                )
                voucher_entries = voucher_entries.exclude(
                    reconciliation_links__status__in=matched_statuses,
                )
            elif recon_filter == "reconciled":
                recent_bank_transactions = recent_bank_transactions.filter(
                    reconciliation_links__status__in=matched_statuses,
                )
                voucher_entries = voucher_entries.filter(
                    reconciliation_links__status__in=matched_statuses,
                )
            elif recon_filter == "manual":
                recent_bank_transactions = recent_bank_transactions.filter(
                    reconciliation_links__is_manual=True,
                    reconciliation_links__status__in=matched_statuses,
                )
                voucher_entries = voucher_entries.filter(
                    reconciliation_links__is_manual=True,
                    reconciliation_links__status__in=matched_statuses,
                )

            if date_from:
                recent_bank_transactions = recent_bank_transactions.filter(transaction_date__gte=date_from)
                voucher_entries = voucher_entries.filter(voucher__voucher_date__gte=date_from)
            if date_to:
                recent_bank_transactions = recent_bank_transactions.filter(transaction_date__lte=date_to)
                voucher_entries = voucher_entries.filter(voucher__voucher_date__lte=date_to)
            if amount_min is not None:
                recent_bank_transactions = recent_bank_transactions.filter(amount__gte=amount_min)
                voucher_entries = voucher_entries.filter(Q(debit__gte=amount_min) | Q(credit__gte=amount_min))
            if amount_max is not None:
                recent_bank_transactions = recent_bank_transactions.filter(amount__lte=amount_max)
                voucher_entries = voucher_entries.filter(Q(debit__lte=amount_max, debit__gt=0) | Q(credit__lte=amount_max, credit__gt=0))
            if filter_values["dr_cr"]:
                recent_bank_transactions = recent_bank_transactions.filter(dr_cr=filter_values["dr_cr"])
                if filter_values["dr_cr"] == BankTransaction.DrCr.DEBIT:
                    voucher_entries = voucher_entries.filter(credit__gt=0)
                else:
                    voucher_entries = voucher_entries.filter(debit__gt=0)
            if filter_values["payment_mode"]:
                voucher_entries = voucher_entries.filter(voucher__payment_mode=filter_values["payment_mode"])
            if filter_values["voucher_type"]:
                voucher_entries = voucher_entries.filter(voucher__voucher_type=filter_values["voucher_type"])
            if filter_values["search"]:
                search = filter_values["search"]
                recent_bank_transactions = recent_bank_transactions.filter(
                    Q(narration__icontains=search)
                    | Q(reference_no__icontains=search)
                    | Q(cheque_no__icontains=search)
                    | Q(normalized__extracted_utr__icontains=search)
                    | Q(reconciliation_links__voucher_entry__voucher__reference_number__icontains=search)
                    | Q(reconciliation_links__voucher_entry__voucher__narration__icontains=search)
                )
                voucher_search = (
                    Q(voucher__reference_number__icontains=search)
                    | Q(voucher__narration__icontains=search)
                    | Q(unit__identifier__icontains=search)
                    | Q(reconciliation_links__bank_transaction__reference_no__icontains=search)
                    | Q(reconciliation_links__bank_transaction__narration__icontains=search)
                )
                if search.isdigit():
                    voucher_search |= Q(voucher__voucher_number=int(search))
                voucher_entries = voucher_entries.filter(voucher_search)

            recent_bank_transactions = (
                recent_bank_transactions.select_related(
                    "bank_statement_import",
                    "bank_statement_import__bank_account",
                    "normalized",
                )
                .prefetch_related(Prefetch("reconciliation_links", queryset=link_queryset))
                .distinct()
                .order_by("-transaction_date", "-id")[:500]
            )
            voucher_entries = (
                voucher_entries.select_related("voucher", "account", "unit")
                .prefetch_related(Prefetch("reconciliation_links", queryset=link_queryset))
                .distinct()
                .order_by("-voucher__voucher_date", "-voucher_id", "-id")[:500]
            )

        filter_query = self.request.GET.copy()
        filter_query.pop("recon_filter", None)
        filter_query_without_recon = filter_query.urlencode()

        context.update(
            bank_accounts=bank_accounts,
            selected_bank_account=selected_bank_account,
            recon_filter=recon_filter,
            recon_filter_choices=recon_filter_choices,
            selected_recon_filter_label=selected_recon_filter_label,
            batch_form=batch_form,
            row_form=row_form,
            recent_bank_transactions=recent_bank_transactions,
            voucher_entries=voucher_entries,
            shortcodes_url=reverse("reconciliation:manual-entry-shortcodes"),
            narrations_url=reverse("reconciliation:manual-entry-narrations"),
            filter_values=filter_values,
            filter_query_without_recon=filter_query_without_recon,
            dr_cr_choices=BankTransaction.DrCr.choices,
            payment_mode_choices=Voucher.PaymentMode.choices,
            voucher_type_choices=Voucher.VoucherType.choices,
        )
        return context


manual_entry_view = ManualEntryView.as_view()


class ManualEntryVoucherMatchView(LoginRequiredMixin, View):
    """Create a manual bank entry and immediately link it to a voucher entry."""

    @staticmethod
    def _parse_transaction_date(value):
        raw_value = (value or "").strip()
        for date_format in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(raw_value, date_format).date()
            except ValueError:
                continue
        raise ValidationError("Enter a valid bank date.")

    def post(self, request, *args, **kwargs):
        if not request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Invalid manual match request."}, status=400)

        society, _ = get_selected_scope(request)
        if not society:
            return JsonResponse({"error": "Please select a society first."}, status=400)

        try:
            ledger_entry = (
                LedgerEntry.objects.select_related("voucher", "account")
                .get(
                    pk=request.POST.get("ledger_entry"),
                    voucher__society=society,
                    voucher__posted_at__isnull=False,
                )
            )
        except (LedgerEntry.DoesNotExist, ValueError, TypeError):
            return JsonResponse({"error": "Select a valid posted voucher entry."}, status=400)

        bank_account = ledger_entry.account
        if not (bank_account.is_bank or bank_account.sub_type == Account.SubType.BANK):
            return JsonResponse({"error": "Voucher entry must use a bank account."}, status=400)

        if ReconciliationLink.objects.filter(
            voucher_entry=ledger_entry,
            status__in=[
                ReconciliationLink.Status.MATCHED,
                ReconciliationLink.Status.FORCE_MATCHED,
                ReconciliationLink.Status.PARTIAL,
            ],
        ).exists():
            return JsonResponse({"error": "This voucher entry is already reconciled."}, status=409)

        try:
            transaction_date = self._parse_transaction_date(request.POST.get("transaction_date"))
        except ValidationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        reference_no = (request.POST.get("reference_no") or ledger_entry.voucher.reference_number or "").strip()
        narration = (request.POST.get("narration") or ledger_entry.voucher.narration or "Manual reconciliation").strip()
        remarks = (request.POST.get("remarks") or "Created from voucher-first manual reconciliation.").strip()
        amount = ledger_entry.debit if ledger_entry.debit > 0 else ledger_entry.credit
        dr_cr = BankTransaction.DrCr.CREDIT if ledger_entry.debit > 0 else BankTransaction.DrCr.DEBIT
        seed = f"{society.id}:{ledger_entry.id}:{request.user.id}:{timezone.now().isoformat()}"
        file_hash = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        filename = f"manual_recon_{ledger_entry.id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"
        csv_content = (
            "Date,Narration,Reference,Debit,Credit,Balance\n"
            f"{transaction_date},{narration},{reference_no},"
            f"{amount if dr_cr == BankTransaction.DrCr.DEBIT else ''},"
            f"{amount if dr_cr == BankTransaction.DrCr.CREDIT else ''},\n"
        ).encode("utf-8")

        try:
            with db_transaction.atomic():
                statement_import = BankStatementImport.objects.create(
                    society=society,
                    bank_account=bank_account,
                    file_name=filename,
                    file_hash=file_hash,
                    raw_file=ContentFile(csv_content, name=filename),
                    uploaded_by=request.user,
                    import_status=BankStatementImport.ImportStatus.COMPLETED,
                    source_type="MANUAL_RECON",
                    statement_start_date=transaction_date,
                    statement_end_date=transaction_date,
                    row_count=1,
                )
                bank_transaction = BankTransaction.objects.create(
                    bank_statement_import=statement_import,
                    source_row_index=1,
                    transaction_date=transaction_date,
                    narration=narration,
                    reference_no=reference_no,
                    amount=amount,
                    dr_cr=dr_cr,
                    raw_row_data={
                        "source": "voucher_first_manual_recon",
                        "ledger_entry_id": ledger_entry.id,
                        "voucher_id": ledger_entry.voucher_id,
                    },
                    duplicate_hash=BankTransaction.compute_duplicate_hash(
                        transaction_date,
                        amount,
                        narration,
                        reference_no,
                    ),
                )
                link = ReconciliationLink.objects.create(
                    society=society,
                    voucher_entry=ledger_entry,
                    bank_transaction=bank_transaction,
                    matched_amount=amount,
                    match_type=ReconciliationLink.MatchType.FORCE,
                    confidence_score=100,
                    matched_by=request.user,
                    matched_at=timezone.now(),
                    is_manual=True,
                    remarks=remarks,
                    status=ReconciliationLink.Status.FORCE_MATCHED,
                )
        except ValidationError as exc:
            logger.exception("Manual voucher match validation failed for ledger entry %s", ledger_entry.id)
            return JsonResponse({"error": "; ".join(exc.messages)}, status=400)
        except Exception:
            logger.exception("Manual voucher match failed for ledger entry %s", ledger_entry.id)
            return JsonResponse({"error": "Unable to create bank entry and match. Please retry."}, status=500)

        return JsonResponse(
            {
                "status": "ok",
                "bank_transaction_id": bank_transaction.id,
                "link_id": link.id,
                "import_id": statement_import.id,
                "message": "Bank entry created and reconciled.",
            },
            status=201,
        )


manual_entry_voucher_match_view = ManualEntryVoucherMatchView.as_view()


class ManualEntryVoucherMatchEditView(ManualEntryVoucherMatchView):
    """Reverse a manual voucher match and recreate it with corrected bank details."""

    def post(self, request, link_id, *args, **kwargs):
        if not request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Invalid manual match edit request."}, status=400)

        society, _ = get_selected_scope(request)
        if not society:
            return JsonResponse({"error": "Please select a society first."}, status=400)

        try:
            old_link = (
                ReconciliationLink.objects.select_related(
                    "voucher_entry__voucher",
                    "voucher_entry__account",
                    "bank_transaction__bank_statement_import",
                )
                .get(pk=link_id, society=society)
            )
        except (ReconciliationLink.DoesNotExist, ValueError, TypeError):
            return JsonResponse({"error": "Select a valid manual match to edit."}, status=404)

        if not old_link.is_manual or old_link.bank_transaction.bank_statement_import.source_type != "MANUAL_RECON":
            return JsonResponse({"error": "Only manually-created matches can be edited here."}, status=403)
        if old_link.status not in {
            ReconciliationLink.Status.MATCHED,
            ReconciliationLink.Status.FORCE_MATCHED,
            ReconciliationLink.Status.PARTIAL,
        }:
            return JsonResponse({"error": "Only active manual matches can be edited."}, status=409)

        ledger_entry = old_link.voucher_entry
        if not ledger_entry or not ledger_entry.voucher.posted_at:
            return JsonResponse({"error": "Manual match is no longer linked to a posted voucher."}, status=400)

        try:
            transaction_date = self._parse_transaction_date(request.POST.get("transaction_date"))
        except ValidationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        reference_no = (request.POST.get("reference_no") or ledger_entry.voucher.reference_number or "").strip()
        narration = (request.POST.get("narration") or ledger_entry.voucher.narration or "Manual reconciliation").strip()
        remarks = (request.POST.get("remarks") or f"Edited manual match #{old_link.id}.").strip()
        amount = ledger_entry.debit if ledger_entry.debit > 0 else ledger_entry.credit
        dr_cr = BankTransaction.DrCr.CREDIT if ledger_entry.debit > 0 else BankTransaction.DrCr.DEBIT
        seed = f"{society.id}:{ledger_entry.id}:{old_link.id}:{request.user.id}:{timezone.now().isoformat()}"
        file_hash = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        filename = f"manual_recon_edit_{ledger_entry.id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"
        csv_content = (
            "Date,Narration,Reference,Debit,Credit,Balance\n"
            f"{transaction_date},{narration},{reference_no},"
            f"{amount if dr_cr == BankTransaction.DrCr.DEBIT else ''},"
            f"{amount if dr_cr == BankTransaction.DrCr.CREDIT else ''},\n"
        ).encode("utf-8")

        try:
            with db_transaction.atomic():
                old_link.unmatch(request.user, reason=f"Reversed before manual edit: {remarks}")
                statement_import = BankStatementImport.objects.create(
                    society=society,
                    bank_account=ledger_entry.account,
                    file_name=filename,
                    file_hash=file_hash,
                    raw_file=ContentFile(csv_content, name=filename),
                    uploaded_by=request.user,
                    import_status=BankStatementImport.ImportStatus.COMPLETED,
                    source_type="MANUAL_RECON",
                    statement_start_date=transaction_date,
                    statement_end_date=transaction_date,
                    row_count=1,
                )
                bank_transaction = BankTransaction.objects.create(
                    bank_statement_import=statement_import,
                    source_row_index=1,
                    transaction_date=transaction_date,
                    narration=narration,
                    reference_no=reference_no,
                    amount=amount,
                    dr_cr=dr_cr,
                    raw_row_data={
                        "source": "voucher_first_manual_recon_edit",
                        "previous_link_id": old_link.id,
                        "previous_bank_transaction_id": old_link.bank_transaction_id,
                        "ledger_entry_id": ledger_entry.id,
                        "voucher_id": ledger_entry.voucher_id,
                    },
                    duplicate_hash=BankTransaction.compute_duplicate_hash(
                        transaction_date,
                        amount,
                        narration,
                        reference_no,
                    ),
                )
                new_link = ReconciliationLink.objects.create(
                    society=society,
                    voucher_entry=ledger_entry,
                    bank_transaction=bank_transaction,
                    matched_amount=amount,
                    match_type=ReconciliationLink.MatchType.FORCE,
                    confidence_score=100,
                    matched_by=request.user,
                    matched_at=timezone.now(),
                    is_manual=True,
                    remarks=remarks,
                    status=ReconciliationLink.Status.FORCE_MATCHED,
                )
        except ValidationError as exc:
            logger.exception("Manual voucher match edit validation failed for link %s", old_link.id)
            return JsonResponse({"error": "; ".join(exc.messages)}, status=400)
        except Exception:
            logger.exception("Manual voucher match edit failed for link %s", old_link.id)
            return JsonResponse({"error": "Unable to edit manual match. Please retry."}, status=500)

        return JsonResponse(
            {
                "status": "ok",
                "bank_transaction_id": bank_transaction.id,
                "link_id": new_link.id,
                "reversed_link_id": old_link.id,
                "import_id": statement_import.id,
                "message": "Manual match updated with corrected bank entry.",
            },
            status=201,
        )


manual_entry_voucher_match_edit_view = ManualEntryVoucherMatchEditView.as_view()


class ManualEntryRowAddView(LoginRequiredMixin, View):
    """HTMX endpoint: add a new empty row, returns row partial HTML."""

    def post(self, request, *args, **kwargs):
        society, _ = get_selected_scope(request)
        if not society:
            return HttpResponseBadRequest("Please select a society first.")

        row_index = request.POST.get("row_index", "0")
        try:
            row_index = int(row_index)
        except (TypeError, ValueError):
            row_index = 0

        row_form = ManualEntryRowForm(initial={"row_index": row_index})

        return render(
            request,
            "reconciliation/partials/manual_entry_row.html",
            {
                "row_form": row_form,
                "row_number": row_index,
            },
        )


manual_entry_row_add_view = ManualEntryRowAddView.as_view()


class ManualEntryRowValidateView(LoginRequiredMixin, View):
    """HTMX endpoint: validate a single row, returns updated row partial with errors."""

    def post(self, request, *args, **kwargs):
        society, _ = get_selected_scope(request)
        if not society:
            return HttpResponseBadRequest("Please select a society first.")

        form = ManualEntryRowForm(request.POST)
        row_index = request.POST.get("row_index", "0")
        try:
            row_index = int(row_index)
        except (TypeError, ValueError):
            row_index = 0

        return render(
            request,
            "reconciliation/partials/manual_entry_row.html",
            {
                "row_form": form,
                "row_number": row_index,
            },
            status=200 if form.is_valid() else 422,
        )


manual_entry_row_validate_view = ManualEntryRowValidateView.as_view()


class ManualEntryBatchSaveView(LoginRequiredMixin, View):
    """Save the entire batch: creates BankStatementImport + BankTransaction rows."""

    def post(self, request, *args, **kwargs):
        from reconciliation.services.manual_entry_batch_service import save_batch

        society, _ = get_selected_scope(request)
        if not society:
            return JsonResponse({"error": "Please select a society first."}, status=400)

        batch_form = ManualEntryBatchForm(
            request.POST, society=society,
        )
        if not batch_form.is_valid():
            return JsonResponse(
                {"error": "Batch form invalid.", "errors": batch_form.errors},
                status=400,
            )

        # Parse rows from request
        rows_json = request.POST.get("rows", "[]")
        try:
            rows = json.loads(rows_json)
        except json.JSONDecodeError:
            return JsonResponse(
                {"error": "Invalid rows data."}, status=400,
            )

        if not rows:
            return JsonResponse(
                {"error": "No rows to save."}, status=400,
            )

        bank_account = batch_form.cleaned_data["bank_account"]
        period_start = batch_form.cleaned_data["period_start"]
        period_end = batch_form.cleaned_data["period_end"]
        opening_balance = batch_form.cleaned_data["opening_balance"]
        closing_balance = batch_form.cleaned_data.get("closing_balance")

        try:
            statement_import, transactions, errors = save_batch(
                user=request.user,
                society=society,
                bank_account=bank_account,
                period_start=period_start,
                period_end=period_end,
                opening_balance=opening_balance,
                closing_balance=closing_balance,
                rows=rows,
            )
        except ValidationError as exc:
            row_errors = getattr(exc, "params", {}).get("row_errors", [])
            return JsonResponse(
                {
                    "error": str(exc),
                    "row_errors": row_errors,
                },
                status=400,
            )
        except Exception as exc:
            logger.exception("Batch save failed")
            return JsonResponse(
                {"error": f"Save failed: {exc}"}, status=500,
            )

        detail_url = reverse(
            "reconciliation:statement-import-detail",
            kwargs={"pk": statement_import.pk},
        )

        return JsonResponse(
            {
                "status": "ok",
                "import_id": statement_import.pk,
                "transaction_count": len(transactions),
                "redirect_url": detail_url,
            },
            status=201,
        )


manual_entry_batch_save_view = ManualEntryBatchSaveView.as_view()


class ManualEntryShortcodesView(LoginRequiredMixin, View):
    """Returns JSON of shortcode mappings for the frontend."""

    def get(self, request, *args, **kwargs):
        from reconciliation.services.manual_entry_batch_service import get_shortcodes

        return JsonResponse({"shortcodes": get_shortcodes()})


manual_entry_shortcodes_view = ManualEntryShortcodesView.as_view()


class ManualEntryNarrationsView(LoginRequiredMixin, View):
    """Returns JSON of recent narrations for autocomplete."""

    def get(self, request, *args, **kwargs):
        society, _ = get_selected_scope(request)
        if not society:
            return JsonResponse({"narrations": []})

        # Collect distinct narrations from recent manual entries
        recent_narrations = (
            BankTransaction.objects.filter(
                bank_statement_import__society=society,
                bank_statement_import__source_type="MANUAL",
            )
            .exclude(narration="")
            .values_list("narration", flat=True)
            .distinct()
            .order_by("-id")[:100]
        )

        return JsonResponse({"narrations": list(recent_narrations)})


manual_entry_narrations_view = ManualEntryNarrationsView.as_view()
