from django.urls import path

from parking.views import create_sold_permit_for_vehicle_view
from parking.views import flat_parking_dashboard_detail_view
from parking.views import flat_parking_dashboard_list_view
from parking.views import flat_rotation_application_create_view
from parking.views import flat_parking_summary_modal_view
from parking.views import parking_dashboard_view
from parking.views import parking_permit_list_view
from parking.views import parking_permit_qr_code_view
from parking.views import parking_permit_sticker_view
from parking.views import parking_rotation_application_create_view
from parking.views import parking_rotation_cycle_allocate_view
from parking.views import parking_rotation_cycle_complete_view
from parking.views import parking_rotation_cycle_detail_view
from parking.views import parking_rotation_cycle_generate_view
from parking.views import parking_rotation_cycle_list_view
from parking.views import parking_rotation_policy_create_view
from parking.views import parking_rotation_policy_list_view
from parking.views import parking_slot_create_view
from parking.views import parking_slot_list_view
from parking.views import parking_vehicle_limit_create_view
from parking.views import parking_vehicle_limit_list_view
from parking.views import vehicle_create_view
from parking.views import vehicle_list_view
from parking.views import vehicle_members_by_unit_view
from parking.views import vehicle_qr_code_view
from parking.views import vehicle_sticker_view
from parking.views import verify_parking_permit
from parking.views import verify_vehicle

app_name = "parking"

urlpatterns = [
    path("verify/<uuid:qr_token>/", view=verify_parking_permit, name="permit-verify"),
    path("vehicle/verify/<uuid:token>/", view=verify_vehicle, name="vehicle_verify"),
    path("", view=parking_dashboard_view, name="dashboard"),
    path("flats/", view=flat_parking_dashboard_list_view, name="flat-dashboard-list"),
    path(
        "flats/<int:pk>/dashboard/",
        view=flat_parking_dashboard_detail_view,
        name="flat-dashboard-detail",
    ),
    path(
        "flats/<int:pk>/summary-modal/",
        view=flat_parking_summary_modal_view,
        name="flat-summary-modal",
    ),
    path(
        "flats/<int:pk>/apply-rotation/",
        view=flat_rotation_application_create_view,
        name="flat-rotation-apply",
    ),
    path("slots/", view=parking_slot_list_view, name="slot-list"),
    path("slots/add/", view=parking_slot_create_view, name="slot-add"),
    path("vehicles/", view=vehicle_list_view, name="vehicle-list"),
    path(
        "vehicles/<int:pk>/create-sold-permit/",
        view=create_sold_permit_for_vehicle_view,
        name="vehicle-create-sold-permit",
    ),
    path("vehicles/add/", view=vehicle_create_view, name="vehicle-add"),
    path("vehicles/<int:pk>/qr/", view=vehicle_qr_code_view, name="vehicle-qr"),
    path("vehicles/<int:pk>/sticker/", view=vehicle_sticker_view, name="vehicle-sticker"),
    path("permits/", view=parking_permit_list_view, name="permit-list"),
    path("permits/<int:pk>/qr/", view=parking_permit_qr_code_view, name="permit-qr"),
    path(
        "permits/<int:pk>/sticker/",
        view=parking_permit_sticker_view,
        name="permit-sticker",
    ),
    path(
        "vehicles/members/",
        view=vehicle_members_by_unit_view,
        name="vehicle-members",
    ),
    path("limits/", view=parking_vehicle_limit_list_view, name="limit-list"),
    path("limits/add/", view=parking_vehicle_limit_create_view, name="limit-add"),
    path("rotation/policies/", view=parking_rotation_policy_list_view, name="rotation-policy-list"),
    path("rotation/policies/add/", view=parking_rotation_policy_create_view, name="rotation-policy-add"),
    path("rotation/cycles/", view=parking_rotation_cycle_list_view, name="rotation-cycle-list"),
    path("rotation/cycles/generate/", view=parking_rotation_cycle_generate_view, name="rotation-cycle-generate"),
    path("rotation/cycles/<int:pk>/", view=parking_rotation_cycle_detail_view, name="rotation-cycle-detail"),
    path(
        "rotation/cycles/<int:pk>/applications/add/",
        view=parking_rotation_application_create_view,
        name="rotation-application-add",
    ),
    path(
        "rotation/cycles/<int:pk>/allocate/",
        view=parking_rotation_cycle_allocate_view,
        name="rotation-cycle-allocate",
    ),
    path(
        "rotation/cycles/<int:pk>/complete/",
        view=parking_rotation_cycle_complete_view,
        name="rotation-cycle-complete",
    ),
]
