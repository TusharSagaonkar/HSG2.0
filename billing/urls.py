from django.urls import path

from billing.views import bill_detail_view
from billing.views import bill_list_view
from billing.views import charge_template_deactivate_and_create_view
from billing.views import charge_template_list_view
from billing.views import charge_template_status_update_view

app_name = "billing"

urlpatterns = [
    path("templates/", view=charge_template_list_view, name="charge-template-list"),
    path(
        "templates/<int:pk>/status/",
        view=charge_template_status_update_view,
        name="charge-template-status",
    ),
    path(
        "templates/<int:pk>/inactive-and-new/",
        view=charge_template_deactivate_and_create_view,
        name="charge-template-inactive-and-new",
    ),
    path("bills/", view=bill_list_view, name="bill-list"),
    path("bills/<int:pk>/", view=bill_detail_view, name="bill-detail"),
]
