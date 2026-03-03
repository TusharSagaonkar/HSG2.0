from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from housing.models import Member
from housing.models import Society
from housing.models import Structure
from housing.models import Unit
from housing.models import UnitOccupancy
from parking.models import Vehicle


pytestmark = pytest.mark.django_db


def _setup_unit_with_member(member_role):
    society = Society.objects.create(name="Verification Society")
    structure = Structure.objects.create(
        society=society,
        structure_type=Structure.StructureType.BUILDING,
        name="A Wing",
    )
    unit = Unit.objects.create(
        structure=structure,
        unit_type=Unit.UnitType.FLAT,
        identifier="101",
    )
    member = Member.objects.create(
        society=society,
        unit=unit,
        full_name="John Resident",
        role=member_role,
    )
    return society, unit, member


def _create_vehicle(
    member_role,
    occupancy_type,
    valid_until=None,
    is_active=True,
    vehicle_type=Vehicle.VehicleType.CAR,
):
    society, unit, member = _setup_unit_with_member(member_role)
    UnitOccupancy.objects.create(
        unit=unit,
        occupancy_type=occupancy_type,
        start_date=timezone.localdate() - timedelta(days=30),
        end_date=None,
    )
    return Vehicle.objects.create(
        society=society,
        unit=unit,
        member=member,
        vehicle_number=f"MH-01-QR-{uuid4().hex[:6].upper()}",
        vehicle_type=vehicle_type,
        is_active=is_active,
        valid_until=valid_until,
    )


def test_valid_vehicle_shows_verification_data(client):
    vehicle = _create_vehicle(
        member_role=Member.MemberRole.OWNER,
        occupancy_type=UnitOccupancy.OccupancyType.OWNER,
        valid_until=timezone.localdate() + timedelta(days=30),
    )

    response = client.get(reverse("vehicle_verify", kwargs={"token": vehicle.verification_token}))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Verification Society" in content
    assert "A Wing | 101" in content
    assert "John Resident" in content
    assert vehicle.vehicle_number in content
    assert "Active" in content
    assert "activity-badge activity-active" in content


def test_expired_vehicle_shows_expired_badge(client):
    vehicle = _create_vehicle(
        member_role=Member.MemberRole.OWNER,
        occupancy_type=UnitOccupancy.OccupancyType.OWNER,
        valid_until=timezone.localdate() - timedelta(days=1),
    )

    response = client.get(reverse("vehicle_verify", kwargs={"token": vehicle.verification_token}))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Inactive" in content
    assert "activity-badge activity-inactive" in content
    assert "⛔" in content


def test_owner_living_status_is_green(client):
    vehicle = _create_vehicle(
        member_role=Member.MemberRole.OWNER,
        occupancy_type=UnitOccupancy.OccupancyType.OWNER,
        valid_until=timezone.localdate() + timedelta(days=7),
    )

    response = client.get(reverse("vehicle_verify", kwargs={"token": vehicle.verification_token}))

    assert response.status_code == 200
    content = response.content.decode()
    assert "activity-badge activity-active" in content
    assert "Owner living" in content
    assert "✅" in content


def test_tenant_status_is_yellow(client):
    vehicle = _create_vehicle(
        member_role=Member.MemberRole.TENANT,
        occupancy_type=UnitOccupancy.OccupancyType.TENANT,
        valid_until=timezone.localdate() + timedelta(days=7),
    )

    response = client.get(reverse("vehicle_verify", kwargs={"token": vehicle.verification_token}))

    assert response.status_code == 200
    content = response.content.decode()
    assert "activity-badge activity-active" in content
    assert "Active" in content
    assert "Tenant" in content
    assert "✅" in content


def test_owner_not_living_status_is_red(client):
    vehicle = _create_vehicle(
        member_role=Member.MemberRole.OWNER,
        occupancy_type=UnitOccupancy.OccupancyType.TENANT,
        valid_until=timezone.localdate() + timedelta(days=7),
    )

    response = client.get(reverse("vehicle_verify", kwargs={"token": vehicle.verification_token}))

    assert response.status_code == 200
    content = response.content.decode()
    assert "activity-badge activity-active" in content
    assert "Active" in content
    assert "Not living" in content
    assert "✅" in content


def test_invalid_token_returns_404(client):
    response = client.get(reverse("vehicle_verify", kwargs={"token": uuid4()}))
    assert response.status_code == 404


def test_get_verification_url_uses_secure_token_path(settings):
    vehicle = _create_vehicle(
        member_role=Member.MemberRole.OWNER,
        occupancy_type=UnitOccupancy.OccupancyType.OWNER,
        valid_until=timezone.localdate() + timedelta(days=7),
    )
    settings.BASE_URL = "https://example.com"

    verification_url = vehicle.get_verification_url()

    assert verification_url == f"https://example.com/vehicle/verify/{vehicle.verification_token}/"


def test_inactive_vehicle_verification_shows_flat_and_member_vehicle_lists(client):
    society, unit, member = _setup_unit_with_member(Member.MemberRole.OWNER)
    structure = unit.structure
    other_unit = Unit.objects.create(
        structure=structure,
        unit_type=Unit.UnitType.FLAT,
        identifier="102",
    )
    other_member = Member.objects.create(
        society=society,
        unit=unit,
        full_name="Other Member",
        role=Member.MemberRole.TENANT,
    )
    UnitOccupancy.objects.create(
        unit=unit,
        occupancy_type=UnitOccupancy.OccupancyType.OWNER,
        start_date=timezone.localdate() - timedelta(days=30),
        end_date=None,
    )

    scanned_vehicle = Vehicle.objects.create(
        society=society,
        unit=unit,
        member=member,
        vehicle_number="MH-01-XY-0001",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=False,
    )
    Vehicle.objects.create(
        society=society,
        unit=unit,
        member=other_member,
        vehicle_number="MH-01-XY-0002",
        vehicle_type=Vehicle.VehicleType.BIKE,
        is_active=True,
    )
    Vehicle.objects.create(
        society=society,
        unit=other_unit,
        member=member,
        vehicle_number="MH-01-XY-0003",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    Vehicle.objects.create(
        society=society,
        unit=unit,
        member=member,
        vehicle_number="MH-01-XY-0004",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )

    response = client.get(reverse("vehicle_verify", kwargs={"token": scanned_vehicle.verification_token}))

    assert response.status_code == 200
    content = response.content.decode()
    assert "All Vehicles In This Flat" in content
    assert "Active Car (4 Wheeler) In This Flat" in content
    assert "Count: 1" in content
    assert "Numbers:" in content
    assert "MH-01-XY-0004" in content
    assert "MH-01-XY-0001" in content
    assert "MH-01-XY-0002" in content
    assert "All Vehicles For This Member (Any Unit)" in content
    assert "MH-01-XY-0003" in content
    assert content.count("Active:") >= 2
    assert "✅ Active" in content
    assert "⛔ Inactive" in content


def test_vehicle_qr_view_returns_png_for_logged_in_user(client):
    vehicle = _create_vehicle(
        member_role=Member.MemberRole.OWNER,
        occupancy_type=UnitOccupancy.OccupancyType.OWNER,
        valid_until=timezone.localdate() + timedelta(days=7),
    )
    user = get_user_model().objects.create_user(
        email="verify-qr@example.com",
        password="testpass123",
    )
    client.force_login(user)

    response = client.get(reverse("parking:vehicle-qr", kwargs={"pk": vehicle.pk}))

    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert "vehicle-verification-" in response["Content-Disposition"]


def test_vehicle_qr_view_requires_login(client):
    vehicle = _create_vehicle(
        member_role=Member.MemberRole.OWNER,
        occupancy_type=UnitOccupancy.OccupancyType.OWNER,
        valid_until=timezone.localdate() + timedelta(days=7),
    )

    response = client.get(reverse("parking:vehicle-qr", kwargs={"pk": vehicle.pk}))

    assert response.status_code == 302


def test_vehicle_sticker_view_renders_template_for_logged_in_user(client):
    vehicle = _create_vehicle(
        member_role=Member.MemberRole.OWNER,
        occupancy_type=UnitOccupancy.OccupancyType.OWNER,
        valid_until=timezone.localdate() + timedelta(days=7),
    )
    user = get_user_model().objects.create_user(
        email="sticker-user@example.com",
        password="testpass123",
    )
    client.force_login(user)

    response = client.get(reverse("parking:vehicle-sticker", kwargs={"pk": vehicle.pk}))

    assert response.status_code == 200
    content = response.content.decode()
    assert "sticker-card status-owner" in content
    assert "circle-sticker-container" in content
    assert "Print Rectangular" in content
    assert "Print Circular" in content
    assert "Member Type: Owner" in content
    assert reverse("parking:vehicle-qr", kwargs={"pk": vehicle.pk}) in content
    assert vehicle.vehicle_number in content


def test_vehicle_sticker_view_uses_tenant_color_and_label(client):
    vehicle = _create_vehicle(
        member_role=Member.MemberRole.TENANT,
        occupancy_type=UnitOccupancy.OccupancyType.TENANT,
        valid_until=timezone.localdate() + timedelta(days=7),
    )
    user = get_user_model().objects.create_user(
        email="sticker-tenant@example.com",
        password="testpass123",
    )
    client.force_login(user)

    response = client.get(reverse("parking:vehicle-sticker", kwargs={"pk": vehicle.pk}))

    assert response.status_code == 200
    content = response.content.decode()
    assert "sticker-card status-tenant" in content
    assert "Member Type: Tenant" in content


def test_vehicle_sticker_view_uses_bike_layout_for_bike_vehicle(client):
    vehicle = _create_vehicle(
        member_role=Member.MemberRole.TENANT,
        occupancy_type=UnitOccupancy.OccupancyType.TENANT,
        valid_until=timezone.localdate() + timedelta(days=7),
        vehicle_type=Vehicle.VehicleType.BIKE,
    )
    user = get_user_model().objects.create_user(
        email="sticker-bike@example.com",
        password="testpass123",
    )
    client.force_login(user)

    response = client.get(reverse("parking:vehicle-sticker", kwargs={"pk": vehicle.pk}))

    assert response.status_code == 200
    content = response.content.decode()
    assert "layout-bike" in content
    assert "Vehicle Type: Bike (2 Wheeler)" in content
