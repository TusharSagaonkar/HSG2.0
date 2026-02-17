from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists
from django.db.models import OuterRef
from django.db.models import Sum
from django.forms import formset_factory
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.http import Http404
from django.http import HttpResponse
from django.views import View
from django.views.generic import ListView
from django.views.generic import TemplateView
from decimal import Decimal
from datetime import date
import csv

from accounting.forms import LedgerEntryRowForm
from accounting.forms import LedgerEntryRowBaseFormSet
from accounting.forms import VoucherForm
from accounting.models import Account
from accounting.models import AccountingPeriod
from accounting.models import LedgerEntry
from accounting.models import Voucher
from accounting.services.reporting import build_account_ledger
from accounting.services.reporting import build_trial_balance
from housing_accounting.selection import get_selected_scope
from societies.models import Society


class AccountingDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "accounting/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_society, selected_financial_year = get_selected_scope(self.request)

        accounts_qs = Account.objects.all()
        vouchers_qs = Voucher.objects.all()
        periods_qs = AccountingPeriod.objects.filter(is_open=True)

        if selected_society:
            accounts_qs = accounts_qs.filter(society=selected_society)
            vouchers_qs = vouchers_qs.filter(society=selected_society)
            periods_qs = periods_qs.filter(society=selected_society)

        if selected_financial_year:
            vouchers_qs = vouchers_qs.filter(
                voucher_date__gte=selected_financial_year.start_date,
                voucher_date__lte=selected_financial_year.end_date,
            )
            periods_qs = periods_qs.filter(financial_year=selected_financial_year)

        context["total_accounts"] = accounts_qs.count()
        context["total_vouchers"] = vouchers_qs.count()
        context["posted_vouchers"] = vouchers_qs.filter(posted_at__isnull=False).count()
        context["draft_vouchers"] = vouchers_qs.filter(posted_at__isnull=True).count()
        context["open_periods"] = periods_qs.count()
        context["recent_vouchers"] = (
            vouchers_qs.select_related("society")
            .annotate(
                has_reversal=Exists(
                    Voucher.objects.filter(reversal_of=OuterRef("pk"))
                )
            )
            .order_by(
                "-voucher_date",
                "-id",
            )[:8]
        )
        context["recent_accounts"] = accounts_qs.select_related(
            "society",
            "category",
        ).order_by("name")[:8]
        return context


accounting_dashboard_view = AccountingDashboardView.as_view()


class AccountListView(LoginRequiredMixin, ListView):
    model = Account
    template_name = "accounting/account_list.html"
    context_object_name = "accounts"

    def get_queryset(self):
        selected_society, _ = get_selected_scope(self.request)
        queryset = Account.objects.select_related(
            "society",
            "category",
            "parent",
        ).order_by("society__name", "name")
        if selected_society:
            queryset = queryset.filter(society=selected_society)
        return queryset


account_list_view = AccountListView.as_view()


class AccountLedgerView(LoginRequiredMixin, TemplateView):
    template_name = "accounting/account_ledger.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_society, selected_financial_year = get_selected_scope(self.request)
        account = get_object_or_404(
            Account.objects.select_related("society", "category"),
            pk=self.kwargs["pk"],
        )

        if selected_society and account.society_id != selected_society.id:
            raise Http404("Account not found in selected scope.")

        to_date = None
        to_date_raw = self.request.GET.get("to_date")
        if to_date_raw:
            try:
                to_date = date.fromisoformat(to_date_raw)
            except ValueError:
                to_date = None

        lines = build_account_ledger(
            account,
            society=selected_society or account.society,
            financial_year=selected_financial_year,
            to_date=to_date,
        )

        context["account"] = account
        context["ledger_lines"] = lines
        context["to_date"] = to_date
        return context


account_ledger_view = AccountLedgerView.as_view()


class TrialBalanceView(LoginRequiredMixin, TemplateView):
    template_name = "accounting/trial_balance.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_society, selected_financial_year = get_selected_scope(self.request)
        if not selected_society:
            context["trial_balance"] = None
            return context

        to_date = None
        to_date_raw = self.request.GET.get("to_date")
        if to_date_raw:
            try:
                to_date = date.fromisoformat(to_date_raw)
            except ValueError:
                to_date = None

        context["trial_balance"] = build_trial_balance(
            society=selected_society,
            financial_year=selected_financial_year,
            to_date=to_date,
        )
        context["to_date"] = to_date
        return context


trial_balance_view = TrialBalanceView.as_view()


class AccountLedgerExportCsvView(LoginRequiredMixin, View):
    def get(self, request, pk):
        selected_society, selected_financial_year = get_selected_scope(self.request)
        account = get_object_or_404(
            Account.objects.select_related("society", "category"),
            pk=pk,
        )
        if selected_society and account.society_id != selected_society.id:
            raise Http404("Account not found in selected scope.")

        to_date = None
        to_date_raw = request.GET.get("to_date")
        if to_date_raw:
            try:
                to_date = date.fromisoformat(to_date_raw)
            except ValueError:
                to_date = None

        lines = build_account_ledger(
            account,
            society=selected_society or account.society,
            financial_year=selected_financial_year,
            to_date=to_date,
        )

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="ledger_{account.id}_{to_date or "all"}.csv"'
        )
        writer = csv.writer(response)
        writer.writerow(
            [
                "Date",
                "Voucher Number",
                "Voucher Type",
                "Narration",
                "Debit",
                "Credit",
                "Running Balance",
                "Balance Side",
            ]
        )
        for line in lines:
            writer.writerow(
                [
                    line.entry.voucher.voucher_date.isoformat(),
                    line.entry.voucher.display_number,
                    line.entry.voucher.voucher_type,
                    line.entry.voucher.narration,
                    line.entry.debit,
                    line.entry.credit,
                    line.running_balance,
                    line.balance_side,
                ]
            )
        return response


account_ledger_export_csv_view = AccountLedgerExportCsvView.as_view()


class TrialBalanceExportCsvView(LoginRequiredMixin, View):
    def get(self, request):
        selected_society, selected_financial_year = get_selected_scope(self.request)
        if not selected_society:
            raise Http404("No selected society for trial balance export.")

        to_date = None
        to_date_raw = request.GET.get("to_date")
        if to_date_raw:
            try:
                to_date = date.fromisoformat(to_date_raw)
            except ValueError:
                to_date = None

        trial_balance = build_trial_balance(
            society=selected_society,
            financial_year=selected_financial_year,
            to_date=to_date,
        )

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="trial_balance_{selected_society.id}_{to_date or "all"}.csv"'
        )
        writer = csv.writer(response)
        writer.writerow(
            [
                "Account",
                "Account Type",
                "Total Debit",
                "Total Credit",
                "Balance Debit",
                "Balance Credit",
            ]
        )
        for row in trial_balance["rows"]:
            writer.writerow(
                [
                    row["account_name"],
                    row["account_type"],
                    row["total_debit"],
                    row["total_credit"],
                    row["balance_debit"],
                    row["balance_credit"],
                ]
            )
        writer.writerow(
            [
                "TOTALS",
                "",
                trial_balance["grand_total_debit"],
                trial_balance["grand_total_credit"],
                trial_balance["total_balance_debit"],
                trial_balance["total_balance_credit"],
            ]
        )
        return response


trial_balance_export_csv_view = TrialBalanceExportCsvView.as_view()


class VoucherListView(LoginRequiredMixin, ListView):
    model = Voucher
    template_name = "accounting/voucher_list.html"
    context_object_name = "vouchers"

    def get_queryset(self):
        selected_society, selected_financial_year = get_selected_scope(self.request)
        queryset = (
            Voucher.objects.select_related("society", "reversal_of")
            .annotate(
                has_reversal=Exists(
                    Voucher.objects.filter(reversal_of=OuterRef("pk"))
                )
            )
            .order_by("-voucher_date", "-id")
        )
        if selected_society:
            queryset = queryset.filter(society=selected_society)
        if selected_financial_year:
            queryset = queryset.filter(
                voucher_date__gte=selected_financial_year.start_date,
                voucher_date__lte=selected_financial_year.end_date,
            )
        return queryset


voucher_list_view = VoucherListView.as_view()


class VoucherEntryView(LoginRequiredMixin, TemplateView):
    template_name = "accounting/voucher_entry.html"
    row_formset_class = formset_factory(
        LedgerEntryRowForm,
        formset=LedgerEntryRowBaseFormSet,
        extra=2,
    )

    def _resolve_society(self, society_id):
        if not society_id:
            return None
        try:
            return Society.objects.get(pk=int(society_id))
        except (Society.DoesNotExist, TypeError, ValueError):
            return None

    def _build_row_formset(self, data=None, society=None):
        if society is None and data:
            society_id = data.get("society")
            if society_id:
                voucher_form = VoucherForm(data)
                if voucher_form.is_valid():
                    society = voucher_form.cleaned_data["society"]
                else:
                    society = self._resolve_society(society_id)
        return self.row_formset_class(
            data=data,
            prefix="entries",
            form_kwargs={"society": society},
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        voucher_form = kwargs.get("voucher_form")
        entry_formset = kwargs.get("entry_formset")

        if voucher_form is None or entry_formset is None:
            selected_society = self._resolve_society(self.request.GET.get("society"))
            if selected_society is None:
                selected_society, _ = get_selected_scope(self.request)
            initial = {"society": selected_society} if selected_society else None
            voucher_form = voucher_form or VoucherForm(initial=initial)
            entry_formset = entry_formset or self._build_row_formset(
                society=selected_society
            )

        context["voucher_form"] = voucher_form
        context["entry_formset"] = entry_formset
        return context

    def post(self, request, *args, **kwargs):
        voucher_form = VoucherForm(request.POST)
        entry_formset = self._build_row_formset(request.POST)

        if not voucher_form.is_valid() or not entry_formset.is_valid():
            messages.warning(
                request,
                "Voucher draft not saved. Please fix highlighted issues.",
            )
            return self.render_to_response(
                self.get_context_data(voucher_form=voucher_form, entry_formset=entry_formset)
            )

        rows_to_create = []
        draft_voucher = Voucher(
            society=voucher_form.cleaned_data["society"],
            voucher_type=voucher_form.cleaned_data["voucher_type"],
            voucher_date=voucher_form.cleaned_data["voucher_date"],
            payment_mode=voucher_form.cleaned_data.get("payment_mode", ""),
            reference_number=voucher_form.cleaned_data.get("reference_number", ""),
            narration=voucher_form.cleaned_data.get("narration", ""),
        )

        for index, row in enumerate(entry_formset.cleaned_data):
            if not row:
                continue
            account = row.get("account")
            debit = row.get("debit")
            credit = row.get("credit")
            unit = row.get("unit")

            if not account and not debit and not credit:
                continue

            entry = LedgerEntry(
                voucher=draft_voucher,
                account=account,
                unit=unit,
                debit=debit or 0,
                credit=credit or 0,
            )
            try:
                entry.clean()
            except ValidationError as exc:
                current_form = entry_formset.forms[index]
                if hasattr(exc, "message_dict"):
                    for field, errors in exc.message_dict.items():
                        target_field = field if field in current_form.fields else None
                        for error in errors:
                            current_form.add_error(target_field, error)
                else:
                    current_form.add_error(None, "; ".join(exc.messages))
                messages.warning(
                    request,
                    "Voucher draft not saved. Please fix highlighted ledger entry issues.",
                )
                return self.render_to_response(
                    self.get_context_data(voucher_form=voucher_form, entry_formset=entry_formset)
                )
            rows_to_create.append(entry)

        with transaction.atomic():
            voucher = voucher_form.save()
            for entry in rows_to_create:
                entry.voucher = voucher
                entry.save()

        messages.success(request, "Voucher draft saved successfully.")
        return redirect("accounting:voucher-posting")


voucher_entry_view = VoucherEntryView.as_view()


class VoucherPostingMenuView(LoginRequiredMixin, ListView):
    model = Voucher
    template_name = "accounting/voucher_posting.html"
    context_object_name = "draft_vouchers"

    def get_queryset(self):
        selected_society, selected_financial_year = get_selected_scope(self.request)
        queryset = (
            Voucher.objects.filter(posted_at__isnull=True)
            .select_related("society")
            .annotate(
                total_debit=Sum("entries__debit"),
                total_credit=Sum("entries__credit"),
            )
            .order_by("-voucher_date", "-id")
        )
        if selected_society:
            queryset = queryset.filter(society=selected_society)
        if selected_financial_year:
            queryset = queryset.filter(
                voucher_date__gte=selected_financial_year.start_date,
                voucher_date__lte=selected_financial_year.end_date,
            )
        return queryset


voucher_posting_menu_view = VoucherPostingMenuView.as_view()


class VoucherPostView(LoginRequiredMixin, View):
    def post(self, request, pk):
        voucher = get_object_or_404(Voucher, pk=pk)
        try:
            voucher.post()
            messages.success(request, f"{voucher.display_number} posted successfully.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect("accounting:voucher-posting")


voucher_post_view = VoucherPostView.as_view()


class VoucherDeleteDraftView(LoginRequiredMixin, View):
    def post(self, request, pk):
        voucher = get_object_or_404(Voucher, pk=pk)
        if voucher.posted_at is not None:
            messages.error(request, "Only draft vouchers can be deleted.")
            return redirect("accounting:voucher-posting")

        voucher_label = voucher.display_number
        voucher.delete()
        messages.success(request, f"{voucher_label} deleted successfully.")
        return redirect("accounting:voucher-posting")


voucher_delete_draft_view = VoucherDeleteDraftView.as_view()


class VoucherReverseView(LoginRequiredMixin, View):
    def post(self, request, pk):
        voucher = get_object_or_404(
            Voucher.objects.select_related("society").prefetch_related("entries__account"),
            pk=pk,
        )

        if not voucher.posted_at:
            messages.error(request, "Only posted vouchers can be reversed.")
            return redirect("accounting:voucher-list")
        if voucher.reversal_of_id:
            messages.error(request, "Reversal vouchers cannot be reversed again.")
            return redirect("accounting:voucher-list")
        if Voucher.objects.filter(reversal_of=voucher).exists():
            messages.error(request, "This voucher has already been reversed.")
            return redirect("accounting:voucher-list")

        try:
            with transaction.atomic():
                reversal = Voucher.objects.create(
                    society=voucher.society,
                    voucher_type=voucher.voucher_type,
                    voucher_date=voucher.voucher_date,
                    narration=f"Auto reversal of {voucher.display_number}",
                    reversal_of=voucher,
                )

                for entry in voucher.entries.all():
                    LedgerEntry.objects.create(
                        voucher=reversal,
                        account=entry.account,
                        unit=entry.unit,
                        debit=entry.credit,
                        credit=entry.debit,
                    )

                reversal.post()
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("accounting:voucher-list")

        messages.success(
            request,
            f"{voucher.display_number} reversed successfully as {reversal.display_number}.",
        )
        return redirect("accounting:voucher-list")


voucher_reverse_view = VoucherReverseView.as_view()


class VoucherDetailView(LoginRequiredMixin, View):
    def get(self, request, pk):
        voucher = get_object_or_404(
            Voucher.objects.select_related("society", "reversal_of").prefetch_related(
                "entries__account__category",
                "entries__unit",
            ),
            pk=pk,
        )
        reversal_voucher = Voucher.objects.filter(reversal_of=voucher).first()
        entries = list(voucher.entries.all().order_by("id"))
        total_debit = sum((entry.debit for entry in entries), start=Decimal("0.00"))
        total_credit = sum((entry.credit for entry in entries), start=Decimal("0.00"))

        return render(
            request,
            "accounting/partials/voucher_detail_body.html",
            {
                "voucher": voucher,
                "entries": entries,
                "total_debit": total_debit,
                "total_credit": total_credit,
                "reversal_voucher": reversal_voucher,
            },
        )


voucher_detail_view = VoucherDetailView.as_view()
