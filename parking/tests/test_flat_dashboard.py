from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from housing.models import Member
from housing.models import Society
from housing.models import Structure
from housing.models import Unit
from parking.models import ParkingSlot
from parking.models import ParkingRotationApplication
from parking.models import ParkingRotationPolicy
from parking.models import Vehicle
from parking.services.create_sold_parking_permit import create_sold_parking_permit
from parking.services.rotation import generate_next_rotation_cycle


pytestmark = pytest.mark.django_db


def _setup_flat_data():
    society = Society.objects.create(name="Flat Dashboard Society")
    structure = Structure.objects.create(
        society=society,
        structure_type=Structure.StructureType.BUILDING,
        name="A Wing",
    )
    unit = Unit.objects.create(
        structure=structure,
        unit_type=Unit.UnitType.FLAT,
        identifier="A101",
    )
    member = Member.objects.create(
        society=society,
        unit=unit,
        full_name="Flat Member",
        role=Member.MemberRole.OWNER,
    )
    slot = ParkingSlot.objects.create(
        society=society,
        slot_number="S4",
        parking_model=ParkingSlot.ParkingModel.SOLD,
        owned_unit=unit,
    )
    vehicle = Vehicle.objects.create(
        society=society,
        unit=unit,
        member=member,
        vehicle_number="MH01FLAT1001",
        vehicle_type=Vehicle.VehicleType.CAR,
        valid_until=timezone.localdate() + timedelta(days=365),
    )
    permit = create_sold_parking_permit(vehicle.id)
    return unit, vehicle, slot, permit


def test_flat_dashboard_list_view_shows_flat_row(client):
    unit, _, slot, _ = _setup_flat_data()
    del slot
    user = get_user_model().objects.create_user(
        email="flat-dashboard-list@example.com",
        password="testpass123",
    )
    client.force_login(user)

    response = client.get(reverse("parking:flat-dashboard-list"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Flat Parking Dashboard" in content
    assert unit.identifier in content


def test_flat_dashboard_detail_view_shows_vehicle_and_pass_history(client):
    unit, vehicle, slot, permit = _setup_flat_data()
    user = get_user_model().objects.create_user(
        email="flat-dashboard-detail@example.com",
        password="testpass123",
    )
    client.force_login(user)

    response = client.get(reverse("parking:flat-dashboard-detail", kwargs={"pk": unit.pk}))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Parking Pass Usage History" in content
    assert vehicle.vehicle_number in content
    assert slot.slot_number in content
    assert str(permit.qr_token) in content


def test_vehicle_list_flat_number_opens_modal_summary(client):
    unit, vehicle, slot, _ = _setup_flat_data()
    del slot
    user = get_user_model().objects.create_user(
        email="flat-modal-trigger@example.com",
        password="testpass123",
    )
    client.force_login(user)

    list_response = client.get(reverse("parking:vehicle-list"))
    assert list_response.status_code == 200
    list_content = list_response.content.decode()
    assert "flat-summary-trigger" in list_content
    assert reverse("parking:flat-summary-modal", kwargs={"pk": unit.pk}) in list_content

    modal_response = client.get(reverse("parking:flat-summary-modal", kwargs={"pk": unit.pk}))
    assert modal_response.status_code == 200
    modal_content = modal_response.content.decode()
    assert unit.identifier in modal_content
    assert vehicle.vehicle_number in modal_content


def test_flat_dashboard_uses_effective_active_status_from_sold_permit(client):
    unit, vehicle, _, permit = _setup_flat_data()
    assert permit.status == "ACTIVE"
    Vehicle.objects.filter(pk=vehicle.pk).update(is_active=False)

    user = get_user_model().objects.create_user(
        email="flat-effective-active@example.com",
        password="testpass123",
    )
    client.force_login(user)

    response = client.get(reverse("parking:flat-dashboard-detail", kwargs={"pk": unit.pk}))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Active Vehicles" in content
    assert "text-bg-success" in content


def test_flat_dashboard_shows_rotation_apply_option_when_applicable(client):
    unit, vehicle, _, _ = _setup_flat_data()
    ParkingSlot.objects.create(
        society=unit.structure.society,
        slot_number="R1",
        parking_model=ParkingSlot.ParkingModel.COMMON,
        is_rotational=True,
    )
    ParkingRotationPolicy.create_new_version(
        society=unit.structure.society,
        policy_name="Flat Apply Policy",
        vehicle_required_before_apply=True,
        allow_sold_parking_owner=True,
        max_total_parking_per_unit=5,
    )
    cycle = generate_next_rotation_cycle(society_id=unit.structure.society_id)
    assert cycle is not None

    user = get_user_model().objects.create_user(
        email="flat-rotation-visible@example.com",
        password="testpass123",
    )
    client.force_login(user)

    response = client.get(reverse("parking:flat-dashboard-detail", kwargs={"pk": unit.pk}))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Rotational Parking Application" in content
    assert "Apply Now" in content
    assert vehicle.vehicle_number in content


def test_flat_dashboard_apply_rotation_creates_application(client):
    unit, vehicle, _, _ = _setup_flat_data()
    ParkingSlot.objects.create(
        society=unit.structure.society,
        slot_number="R2",
        parking_model=ParkingSlot.ParkingModel.COMMON,
        is_rotational=True,
    )
    ParkingRotationPolicy.create_new_version(
        society=unit.structure.society,
        policy_name="Flat Apply Submit Policy",
        vehicle_required_before_apply=True,
        allow_sold_parking_owner=True,
        max_total_parking_per_unit=5,
    )
    cycle = generate_next_rotation_cycle(society_id=unit.structure.society_id)
    assert cycle is not None

    user = get_user_model().objects.create_user(
        email="flat-rotation-submit@example.com",
        password="testpass123",
    )
    client.force_login(user)

    response = client.post(
        reverse("parking:flat-rotation-apply", kwargs={"pk": unit.pk}),
        data={"vehicle_id": str(vehicle.id)},
    )
    assert response.status_code == 302
    application = ParkingRotationApplication.objects.get(cycle=cycle, unit=unit)
    assert application.vehicle == vehicle
    assert application.application_status == ParkingRotationApplication.ApplicationStatus.APPROVED
