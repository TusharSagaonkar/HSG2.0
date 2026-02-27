from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.views.generic import DetailView

from billing.models import Bill
from housing_accounting.selection import get_selected_scope
from members.models import Member
from notifications.models import ReminderLog
from receipts.models import PaymentReceipt


class MemberDetailView(LoginRequiredMixin, DetailView):
    model = Member
    template_name = "members/member_detail.html"
    context_object_name = "member"

    def get_object(self, queryset=None):
        member = super().get_object(queryset)
        selected_society, _ = get_selected_scope(self.request)
        if selected_society and member.society_id != selected_society.id:
            message = "Member not found in selected scope."
            raise Http404(message)
        return member

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        member = self.object
        context["member_bills"] = (
            Bill.objects.filter(member=member)
            .select_related("voucher")
            .order_by("-bill_date", "-id")
        )
        context["member_receipts"] = (
            PaymentReceipt.objects.filter(member=member)
            .select_related("voucher", "deposited_account")
            .prefetch_related("allocations__bill")
            .order_by("-receipt_date", "-id")
        )
        context["member_reminders"] = (
            ReminderLog.objects.filter(member=member)
            .select_related("bill")
            .order_by("-scheduled_for", "-id")
        )[:10]
        return context


member_detail_view = MemberDetailView.as_view()
