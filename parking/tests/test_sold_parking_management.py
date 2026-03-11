from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from housing.models import Member
from housing.models import Society
from housing.models import Structure
from housing.models import Unit
from parking.models import ParkingPermit
from parking.models import ParkingSlot
from parking.models import ParkingSlotOwnershipHistory
from parking.models import Vehicle
from parking.services.create_sold_parking_permit import create_sold_parking_permit
from parking.services.create_sold_parking_permit import switch_active_vehicle
from parking.services.recalculate_vehicle_rule_status import recalculate_vehicle_rule_status


pytestmark = pytest.mark.django_db


def _build_unit(society, structure_name, identifier):
    structure = Structure.objects.create(
        society=society,
        structure_type=Structure.StructureType.BUILDING,
        name=structure_name,
    )
    return Unit.objects.create(
        structure=structure,
        unit_type=Unit.UnitType.FLAT,
        identifier=identifier,
    )


def _create_vehicle(society, unit, suffix):
    member = Member.objects.create(
        society=society,
        unit=unit,
        full_name=f"Member {suffix}",
        role=Member.MemberRole.OWNER,
    )
    return Vehicle.objects.create(
        society=society,
        unit=unit,
        member=member,
        vehicle_number=f"MH01AB{suffix:04d}",
        vehicle_type=Vehicle.VehicleType.CAR,
        valid_until=timezone.localdate() + timedelta(days=365),
    )


def test_case_1_one_owned_slot_three_vehicles_switches_active_and_revokes_previous():
    society = Society.objects.create(name="Sold Permit Society")
    unit = _build_unit(society, "A Wing", "A101")
    slot = ParkingSlot.objects.create(
        society=society,
        slot_number="S1",
        parking_model=ParkingSlot.ParkingModel.SOLD,
        owned_unit=unit,
    )
    assert slot.owned_unit == unit

    v1 = _create_vehicle(society, unit, 1)
    v2 = _create_vehicle(society, unit, 2)
    v3 = _create_vehicle(society, unit, 3)

    p1 = create_sold_parking_permit(v1.id)
    p2 = create_sold_parking_permit(v2.id)
    p3 = create_sold_parking_permit(v3.id)
    p1.refresh_from_db()
    p2.refresh_from_db()
    p3.refresh_from_db()

    assert p1.status == ParkingPermit.Status.REVOKED
    assert p2.status == ParkingPermit.Status.REVOKED
    assert p3.status == ParkingPermit.Status.ACTIVE
    assert (
        ParkingPermit.objects.filter(
            unit=unit,
            permit_type=ParkingPermit.PermitType.SOLD,
            status=ParkingPermit.Status.ACTIVE,
        ).count()
        == 1
    )


def test_case_2_switch_active_vehicle_revokes_old_and_activates_new():
    society = Society.objects.create(name="Switch Society")
    unit = _build_unit(society, "A Wing", "A101")
    slot = ParkingSlot.objects.create(
        society=society,
        slot_number="S1",
        parking_model=ParkingSlot.ParkingModel.SOLD,
        owned_unit=unit,
    )
    old_vehicle = _create_vehicle(society, unit, 10)
    new_vehicle = _create_vehicle(society, unit, 11)

    old_permit = create_sold_parking_permit(old_vehicle.id)
    assert old_permit.slot == slot
    assert old_permit.status == ParkingPermit.Status.ACTIVE

    new_permit = switch_active_vehicle(slot.id, new_vehicle.id)
    old_permit.refresh_from_db()

    assert old_permit.status == ParkingPermit.Status.REVOKED
    assert new_permit.status == ParkingPermit.Status.ACTIVE
    assert new_permit.slot == slot
    assert (
        ParkingPermit.objects.filter(slot=slot, status=ParkingPermit.Status.ACTIVE).count()
        == 1
    )


def test_case_3_transfer_slot_ownership_revokes_old_permit_and_generates_new():
    society = Society.objects.create(name="Transfer Society")
    old_unit = _build_unit(society, "A Wing", "A101")
    new_unit = _build_unit(society, "B Wing", "B201")
    slot = ParkingSlot.objects.create(
        society=society,
        slot_number="S9",
        parking_model=ParkingSlot.ParkingModel.SOLD,
        owned_unit=old_unit,
    )

    old_vehicle = _create_vehicle(society, old_unit, 20)
    new_vehicle = _create_vehicle(society, new_unit, 21)
    old_permit = create_sold_parking_permit(old_vehicle.id)
    assert old_permit.status == ParkingPermit.Status.ACTIVE

    slot.owned_unit = new_unit
    slot.save()

    old_permit.refresh_from_db()
    assert old_permit.status == ParkingPermit.Status.REVOKED

    new_permit = ParkingPermit.objects.filter(
        unit=new_unit,
        slot=slot,
        permit_type=ParkingPermit.PermitType.SOLD,
    ).exclude(pk=old_permit.pk).latest("id")
    assert new_permit.status == ParkingPermit.Status.ACTIVE
    assert new_permit.vehicle == new_vehicle

    history = ParkingSlotOwnershipHistory.objects.filter(slot=slot).order_by("id")
    assert history.count() == 2
    assert history.first().unit == old_unit
    assert history.first().end_date is not None
    assert history.last().unit == new_unit
    assert history.last().end_date is None


def test_case_4_vehicle_removed_marks_permit_expired():
    society = Society.objects.create(name="Vehicle Removal Society")
    unit = _build_unit(society, "A Wing", "A101")
    ParkingSlot.objects.create(
        society=society,
        slot_number="S1",
        parking_model=ParkingSlot.ParkingModel.SOLD,
        owned_unit=unit,
    )
    vehicle = _create_vehicle(society, unit, 31)
    permit = create_sold_parking_permit(vehicle.id)
    assert permit.status == ParkingPermit.Status.ACTIVE

    vehicle.is_active = False
    vehicle.save()
    permit.refresh_from_db()

    assert permit.status == ParkingPermit.Status.EXPIRED
    assert permit.expires_at is not None


def test_same_vehicle_same_flat_reuses_same_qr_and_does_not_create_new_permit():
    society = Society.objects.create(name="No Duplicate Permit Society")
    unit = _build_unit(society, "A Wing", "A101")
    ParkingSlot.objects.create(
        society=society,
        slot_number="S1",
        parking_model=ParkingSlot.ParkingModel.SOLD,
        owned_unit=unit,
    )
    vehicle = _create_vehicle(society, unit, 51)

    first = create_sold_parking_permit(vehicle.id)
    second = create_sold_parking_permit(vehicle.id)

    assert first.id == second.id
    assert first.qr_token == second.qr_token
    assert (
        ParkingPermit.objects.filter(
            society=society,
            unit=unit,
            vehicle=vehicle,
            permit_type=ParkingPermit.PermitType.SOLD,
        ).count()
        == 1
    )


def test_new_vehicle_gets_new_qr_when_switched_on_same_slot():
    society = Society.objects.create(name="QR Change Society")
    unit = _build_unit(society, "A Wing", "A101")
    slot = ParkingSlot.objects.create(
        society=society,
        slot_number="S7",
        parking_model=ParkingSlot.ParkingModel.SOLD,
        owned_unit=unit,
    )
    v1 = _create_vehicle(society, unit, 61)
    v2 = _create_vehicle(society, unit, 62)

    p1 = create_sold_parking_permit(v1.id)
    p2 = switch_active_vehicle(slot.id, v2.id)
    p1.refresh_from_db()

    assert p1.status == ParkingPermit.Status.REVOKED
    assert p2.status == ParkingPermit.Status.ACTIVE
    assert p1.qr_token != p2.qr_token


def test_existing_standby_vehicle_promoted_to_active_keeps_same_qr():
    society = Society.objects.create(name="Promote Standby Society")
    unit = _build_unit(society, "A Wing", "A101")
    slot = ParkingSlot.objects.create(
        society=society,
        slot_number="S8",
        parking_model=ParkingSlot.ParkingModel.SOLD,
        owned_unit=unit,
    )
    v1 = _create_vehicle(society, unit, 71)
    v2 = _create_vehicle(society, unit, 72)

    _ = create_sold_parking_permit(v1.id)
    promoted = create_sold_parking_permit(v2.id)
    promoted_qr = promoted.qr_token

    v1_permit = ParkingPermit.objects.get(vehicle=v1, slot=slot)
    v1_permit.refresh_from_db()
    promoted.refresh_from_db()

    assert v1_permit.status == ParkingPermit.Status.REVOKED
    assert promoted.status == ParkingPermit.Status.ACTIVE
    assert promoted.qr_token == promoted_qr


def test_permit_verification_endpoint_returns_expected_payload(client):
    society = Society.objects.create(name="Verification Society")
    unit = _build_unit(society, "A Wing", "A101")
    ParkingSlot.objects.create(
        society=society,
        slot_number="S1",
        parking_model=ParkingSlot.ParkingModel.SOLD,
        owned_unit=unit,
    )
    vehicle = _create_vehicle(society, unit, 41)
    permit = create_sold_parking_permit(vehicle.id)

    response = client.get(reverse("parking:permit-verify", kwargs={"qr_token": permit.qr_token}))

    assert response.status_code == 200
    content = response.content.decode()
    assert society.name in content
    assert unit.identifier in content
    assert vehicle.vehicle_number in content
    assert "Permit Active" in content
    assert "Active" in content


def test_vehicle_verify_is_active_when_sold_permit_active_even_if_vehicle_date_expired(client):
    society = Society.objects.create(name="Permit Override Verify Society")
    unit = _build_unit(society, "A Wing", "A101")
    ParkingSlot.objects.create(
        society=society,
        slot_number="S1",
        parking_model=ParkingSlot.ParkingModel.SOLD,
        owned_unit=unit,
    )
    vehicle = _create_vehicle(society, unit, 81)
    Vehicle.objects.filter(pk=vehicle.pk).update(valid_until=timezone.localdate() - timedelta(days=3))
    permit = create_sold_parking_permit(vehicle.id)
    assert permit.status == ParkingPermit.Status.ACTIVE

    response = client.get(reverse("vehicle_verify", kwargs={"token": vehicle.verification_token}))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Active" in content
    assert "activity-badge activity-active" in content


def test_vehicle_list_shows_permitted_and_disables_create_button_when_permit_exists(client):
    society = Society.objects.create(name="Permitted Button Society")
    unit = _build_unit(society, "A Wing", "A101")
    ParkingSlot.objects.create(
        society=society,
        slot_number="S11",
        parking_model=ParkingSlot.ParkingModel.SOLD,
        owned_unit=unit,
    )
    vehicle = _create_vehicle(society, unit, 82)
    permit = create_sold_parking_permit(vehicle.id)
    assert permit.status == ParkingPermit.Status.ACTIVE

    user = get_user_model().objects.create_user(
        email="vehicle-list-permit@example.com",
        password="testpass123",
    )
    client.force_login(user)

    response = client.get(reverse("parking:vehicle-list"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Permitted" in content
    assert permit.slot.slot_number in content


def test_recalc_marks_vehicle_active_when_sold_permit_is_active():
    society = Society.objects.create(name="Permit Rule Override Society")
    unit = _build_unit(society, "A Wing", "A101")
    ParkingSlot.objects.create(
        society=society,
        slot_number="S12",
        parking_model=ParkingSlot.ParkingModel.SOLD,
        owned_unit=unit,
    )
    vehicle = _create_vehicle(society, unit, 83)
    permit = create_sold_parking_permit(vehicle.id)
    assert permit.status == ParkingPermit.Status.ACTIVE

    Vehicle.objects.filter(pk=vehicle.pk).update(
        is_active=False,
        rule_status=Vehicle.RuleStatus.VEHICLE_INACTIVE,
        deactivated_at=timezone.now(),
    )
    recalculate_vehicle_rule_status(society.id)
    vehicle.refresh_from_db()

    assert vehicle.rule_status == Vehicle.RuleStatus.ACTIVE
    assert vehicle.is_active is True


def test_data_reflects_active_when_member_mismatch_but_sold_permit_active():
    society = Society.objects.create(name="Permit Data Reflection Society")
    unit = _build_unit(society, "A Wing", "A101")
    other_unit = _build_unit(society, "B Wing", "B202")
    ParkingSlot.objects.create(
        society=society,
        slot_number="S14",
        parking_model=ParkingSlot.ParkingModel.SOLD,
        owned_unit=unit,
    )
    vehicle = _create_vehicle(society, unit, 84)
    permit = create_sold_parking_permit(vehicle.id)
    assert permit.status == ParkingPermit.Status.ACTIVE

    # Force a mismatch condition, then recalc must still persist ACTIVE due to sold permit.
    vehicle.member.unit = other_unit
    vehicle.member.save(update_fields=["unit"])
    recalculate_vehicle_rule_status(society.id)
    vehicle.refresh_from_db()

    assert vehicle.rule_status == Vehicle.RuleStatus.ACTIVE
    assert vehicle.is_active is True


def test_vehicle_without_active_sold_permit_in_sold_slot_unit_is_inactive():
    society = Society.objects.create(name="Sold Slot Requires Permit Society")
    unit = _build_unit(society, "A Wing", "A101")
    ParkingSlot.objects.create(
        society=society,
        slot_number="S15",
        parking_model=ParkingSlot.ParkingModel.SOLD,
        owned_unit=unit,
    )
    vehicle = _create_vehicle(society, unit, 85)
    ParkingPermit.objects.filter(
        vehicle=vehicle,
        permit_type=ParkingPermit.PermitType.SOLD,
        status=ParkingPermit.Status.ACTIVE,
    ).update(status=ParkingPermit.Status.REVOKED)

    recalculate_vehicle_rule_status(society.id)
    vehicle.refresh_from_db()

    assert vehicle.rule_status == Vehicle.RuleStatus.VEHICLE_INACTIVE
    assert vehicle.is_active is False


def test_creating_permit_for_new_vehicle_switches_slot_and_updates_vehicle_statuses():
    society = Society.objects.create(name="Switch On Create Society")
    unit = _build_unit(society, "A Wing", "A101")
    ParkingSlot.objects.create(
        society=society,
        slot_number="S16",
        parking_model=ParkingSlot.ParkingModel.SOLD,
        owned_unit=unit,
    )
    first_vehicle = _create_vehicle(society, unit, 86)
    second_vehicle = _create_vehicle(society, unit, 87)

    first_permit = create_sold_parking_permit(first_vehicle.id)
    assert first_permit.status == ParkingPermit.Status.ACTIVE

    second_permit = create_sold_parking_permit(second_vehicle.id)
    first_permit.refresh_from_db()
    first_vehicle.refresh_from_db()
    second_vehicle.refresh_from_db()

    assert first_permit.status == ParkingPermit.Status.REVOKED
    assert second_permit.status == ParkingPermit.Status.ACTIVE
    assert first_vehicle.rule_status == Vehicle.RuleStatus.VEHICLE_INACTIVE
    assert first_vehicle.is_active is False
    assert second_vehicle.rule_status == Vehicle.RuleStatus.ACTIVE
    assert second_vehicle.is_active is True


def test_third_active_vehicle_save_switches_slot_and_inactivates_others():
    society = Society.objects.create(name="Third Active Save Society")
    unit = _build_unit(society, "A Wing", "A101")
    slot = ParkingSlot.objects.create(
        society=society,
        slot_number="S17",
        parking_model=ParkingSlot.ParkingModel.SOLD,
        owned_unit=unit,
    )
    v1 = _create_vehicle(society, unit, 88)
    v2 = _create_vehicle(society, unit, 89)
    v3 = _create_vehicle(society, unit, 90)

    # Force explicit active save path on third vehicle.
    v3.is_active = True
    v3.save()

    active_permit = ParkingPermit.objects.get(
        slot=slot,
        permit_type=ParkingPermit.PermitType.SOLD,
        status=ParkingPermit.Status.ACTIVE,
    )
    v1.refresh_from_db()
    v2.refresh_from_db()
    v3.refresh_from_db()

    assert active_permit.vehicle_id == v3.id
    assert v3.is_active is True
    assert v3.rule_status == Vehicle.RuleStatus.ACTIVE
    assert v1.is_active is False
    assert v1.rule_status == Vehicle.RuleStatus.VEHICLE_INACTIVE
    assert v2.is_active is False
    assert v2.rule_status == Vehicle.RuleStatus.VEHICLE_INACTIVE
