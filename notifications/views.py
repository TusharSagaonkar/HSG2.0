from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView

from housing_accounting.selection import get_selected_scope
from notifications.models import ReminderLog


class ReminderLogListView(LoginRequiredMixin, ListView):
    model = ReminderLog
    template_name = "notifications/reminder_list.html"
    context_object_name = "reminders"

    def get_queryset(self):
        selected_society, _ = get_selected_scope(self.request)
        queryset = ReminderLog.objects.select_related(
            "society",
            "member",
            "bill",
        ).order_by("-scheduled_for", "-id")
        if selected_society:
            queryset = queryset.filter(society=selected_society)
        return queryset


reminder_list_view = ReminderLogListView.as_view()
