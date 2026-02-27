from django.urls import path

from housing.views import billing_generate_view
from housing.views import charge_template_create_view
from housing.views import housing_dashboard_view
from housing.views import member_create_view
from housing.views import member_list_view
from housing.views import member_update_view
from housing.views import outstanding_dashboard_view
from housing.views import receipt_post_view
from housing.views import reminder_schedule_view
from housing.views import society_create_view
from housing.views import society_detail_view
from housing.views import society_list_view
from housing.views import structure_create_view
from housing.views import structure_unit_dashboard_view
from housing.views import unit_create_view
from housing.views import unit_occupancy_create_view
from housing.views import unit_ownership_create_view

app_name = "housing"

urlpatterns = [
    path("", view=housing_dashboard_view, name="dashboard"),
    path("societies/add/", view=society_create_view, name="society-add"),
    path("societies/", view=society_list_view, name="society-list"),
    path("societies/<int:pk>/", view=society_detail_view, name="society-detail"),
    path(
        "structures-units/",
        view=structure_unit_dashboard_view,
        name="structure-unit-dashboard",
    ),
    path("structures/add/", view=structure_create_view, name="structure-add"),
    path("units/add/", view=unit_create_view, name="unit-add"),
    path("ownerships/add/", view=unit_ownership_create_view, name="ownership-add"),
    path("occupancies/add/", view=unit_occupancy_create_view, name="occupancy-add"),
    path("members/", view=member_list_view, name="member-list"),
    path("members/add/", view=member_create_view, name="member-add"),
    path("members/<int:pk>/edit/", view=member_update_view, name="member-edit"),
    path(
        "billing/templates/add/",
        view=charge_template_create_view,
        name="charge-template-add",
    ),
    path("billing/generate/", view=billing_generate_view, name="billing-generate"),
    path("receipts/post/", view=receipt_post_view, name="receipt-post"),
    path("outstanding/", view=outstanding_dashboard_view, name="outstanding-dashboard"),
    path("reminders/schedule/", view=reminder_schedule_view, name="reminder-schedule"),
]
