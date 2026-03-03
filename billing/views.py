from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import DecimalField
from django.db.models import ExpressionWrapper
from django.db.models import F
from django.db.models import Sum
from django.db.models import Value
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView
from django.views.generic import ListView

from billing.models import Bill
from billing.models import ChargeTemplate
from housing_accounting.selection import get_selected_scope


class ChargeTemplateListView(LoginRequiredMixin, ListView):
    model = ChargeTemplate
    template_name = "billing/charge_template_list.html"
    context_object_name = "charge_templates"

    def get_queryset(self):
        selected_society, _ = get_selected_scope(self.request)
        queryset = ChargeTemplate.objects.select_related(
            "society",
            "income_account",
            "receivable_account",
        ).order_by("society__name", "name", "-effective_from", "-version_no")
        if selected_society:
            queryset = queryset.filter(society=selected_society)
        return queryset


charge_template_list_view = ChargeTemplateListView.as_view()


class ChargeTemplateStatusUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        template = get_object_or_404(ChargeTemplate, pk=pk)
        selected_society, _ = get_selected_scope(request)
        if selected_society and template.society_id != selected_society.id:
            message = "Template not found in selected scope."
            raise Http404(message)

        status = (request.POST.get("status") or "").strip().upper()
        if status not in {"ACTIVE", "INACTIVE"}:
            messages.error(request, "Invalid status selection.")
            return redirect("billing:charge-template-list")

        was_active = template.is_active
        target_is_active = status == "ACTIVE"
        if target_is_active == was_active:
            message = f"{template.name} status is already {status.title()}."
            messages.info(request, message)
            return redirect("billing:charge-template-list")

        template.is_active = target_is_active
        template.effective_to = None if target_is_active else timezone.localdate()

        try:
            template.save(update_fields=["is_active", "effective_to"])
        except ValidationError as exc:
            messages.error(request, str(exc))
            return redirect("billing:charge-template-list")

        if target_is_active:
            messages.success(request, f"{template.name} marked active.")
        else:
            messages.success(
                request,
                (
                    f"{template.name} marked inactive with effective-to date "
                    f"{template.effective_to}."
                ),
            )
        return redirect("billing:charge-template-list")


charge_template_status_update_view = ChargeTemplateStatusUpdateView.as_view()


class ChargeTemplateDeactivateAndCreateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        template = get_object_or_404(ChargeTemplate, pk=pk)
        selected_society, _ = get_selected_scope(request)
        if selected_society and template.society_id != selected_society.id:
            message = "Template not found in selected scope."
            raise Http404(message)

        today = timezone.localdate()
        if template.is_active:
            template.is_active = False
            template.effective_to = today
            template.save(update_fields=["is_active", "effective_to"])

        create_url = reverse("housing:charge-template-add")
        next_effective_from = (today + timedelta(days=1)).isoformat()
        redirect_url = (
            f"{create_url}?society={template.society_id}"
            f"&clone_from={template.id}&effective_from={next_effective_from}"
        )
        messages.success(
            request,
            f"{template.name} set inactive. Create the new version below.",
        )
        return redirect(redirect_url)


charge_template_deactivate_and_create_view = (
    ChargeTemplateDeactivateAndCreateView.as_view()
)


class BillListView(LoginRequiredMixin, ListView):
    model = Bill
    template_name = "billing/bill_list.html"
    context_object_name = "bills"
    paginate_by = 50

    def get_queryset(self):
        selected_society, _ = get_selected_scope(self.request)
        money_field = DecimalField(max_digits=12, decimal_places=2)
        zero = Value(0, output_field=money_field)
        allocated_amount = Coalesce(Sum("receipt_allocations__amount"), zero)
        outstanding_amount = ExpressionWrapper(
            F("total_amount") - allocated_amount,
            output_field=money_field,
        )
        queryset = Bill.objects.select_related(
            "society",
            "member",
            "unit",
            "voucher",
            "receivable_account",
        ).annotate(
            allocated_amount_value=allocated_amount,
            outstanding_amount_value=outstanding_amount,
        ).order_by("-bill_date", "-id")
        if selected_society:
            queryset = queryset.filter(society=selected_society)
        return queryset


bill_list_view = BillListView.as_view()


class BillDetailView(LoginRequiredMixin, DetailView):
    model = Bill
    template_name = "billing/bill_detail.html"
    context_object_name = "bill"

    def get_object(self, queryset=None):
        bill = super().get_object(queryset)
        selected_society, _ = get_selected_scope(self.request)
        if selected_society and bill.society_id != selected_society.id:
            message = "Bill not found in selected scope."
            raise Http404(message)
        return bill

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        bill = self.object
        context["bill_lines"] = bill.lines.select_related(
            "charge_template",
            "income_account",
        ).order_by("id")
        context["allocations"] = bill.receipt_allocations.select_related(
            "receipt",
            "receipt__voucher",
            "receipt__deposited_account",
        ).order_by("-receipt__receipt_date", "-id")
        context["reminders"] = bill.reminders.select_related("member").order_by(
            "-scheduled_for",
            "-id",
        )
        return context


bill_detail_view = BillDetailView.as_view()
