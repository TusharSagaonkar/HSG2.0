from django.urls import path

from parking.views import parking_dashboard_view
from parking.views import parking_slot_create_view
from parking.views import parking_slot_list_view
from parking.views import parking_vehicle_limit_create_view
from parking.views import parking_vehicle_limit_list_view
from parking.views import vehicle_create_view
from parking.views import vehicle_list_view
from parking.views import vehicle_members_by_unit_view
from parking.views import vehicle_qr_code_view
from parking.views import vehicle_sticker_view
from parking.views import verify_vehicle

app_name = "parking"

urlpatterns = [
    path("vehicle/verify/<uuid:token>/", view=verify_vehicle, name="vehicle_verify"),
    path("", view=parking_dashboard_view, name="dashboard"),
    path("slots/", view=parking_slot_list_view, name="slot-list"),
    path("slots/add/", view=parking_slot_create_view, name="slot-add"),
    path("vehicles/", view=vehicle_list_view, name="vehicle-list"),
    path("vehicles/add/", view=vehicle_create_view, name="vehicle-add"),
    path("vehicles/<int:pk>/qr/", view=vehicle_qr_code_view, name="vehicle-qr"),
    path("vehicles/<int:pk>/sticker/", view=vehicle_sticker_view, name="vehicle-sticker"),
    path(
        "vehicles/members/",
        view=vehicle_members_by_unit_view,
        name="vehicle-members",
    ),
    path("limits/", view=parking_vehicle_limit_list_view, name="limit-list"),
    path("limits/add/", view=parking_vehicle_limit_create_view, name="limit-add"),
]
