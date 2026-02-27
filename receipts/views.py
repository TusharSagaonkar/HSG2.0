from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.views.generic import DetailView
from django.views.generic import ListView

from housing_accounting.selection import get_selected_scope
from receipts.models import PaymentReceipt


class ReceiptListView(LoginRequiredMixin, ListView):
    model = PaymentReceipt
    template_name = "receipts/receipt_list.html"
    context_object_name = "receipts"

    def get_queryset(self):
        selected_society, _ = get_selected_scope(self.request)
        queryset = (
            PaymentReceipt.objects.select_related(
                "society",
                "member",
                "unit",
                "deposited_account",
                "voucher",
            )
            .prefetch_related("allocations__bill")
            .order_by("-receipt_date", "-id")
        )
        if selected_society:
            queryset = queryset.filter(society=selected_society)
        return queryset


receipt_list_view = ReceiptListView.as_view()


class ReceiptDetailView(LoginRequiredMixin, DetailView):
    model = PaymentReceipt
    template_name = "receipts/receipt_detail.html"
    context_object_name = "receipt"

    def get_object(self, queryset=None):
        receipt = super().get_object(queryset)
        selected_society, _ = get_selected_scope(self.request)
        if selected_society and receipt.society_id != selected_society.id:
            message = "Receipt not found in selected scope."
            raise Http404(message)
        return receipt

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["allocations"] = self.object.allocations.select_related(
            "bill",
        ).order_by("id")
        return context


receipt_detail_view = ReceiptDetailView.as_view()
