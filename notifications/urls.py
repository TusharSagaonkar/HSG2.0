from django.urls import path

from notifications.views import reminder_list_view

app_name = "notifications"

urlpatterns = [
    path("reminders/", view=reminder_list_view, name="reminder-list"),
]
