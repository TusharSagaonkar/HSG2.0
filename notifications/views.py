from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.views.generic import ListView

from housing_accounting.selection import get_selected_scope
from notifications.models import ReminderLog


class ReminderLogListView(LoginRequiredMixin, ListView):
    model = ReminderLog
    template_name = "notifications/reminder_list.html"
    context_object_name = "reminders"
    paginate_by = 50

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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        reminders_qs = self.get_queryset()
        context["scheduled_count"] = reminders_qs.count()
        context["queued_count"] = reminders_qs.filter(status="QUEUED").count()
        context["sent_count"] = reminders_qs.filter(status="SENT").count()
        context["failed_count"] = reminders_qs.filter(status="FAILED").count()
        return context


reminder_list_view = ReminderLogListView.as_view()
