from django.core.exceptions import ValidationError
from django.db import transaction

from parking.models import ParkingPermit
from parking.models import ParkingSlot
from parking.models import Vehicle
from parking.services.recalculate_vehicle_rule_status import recalculate_vehicle_rule_status


@transaction.atomic
def create_sold_parking_permit(vehicle_id, *, created_by=None):
    vehicle = Vehicle.objects.select_related("unit", "society").get(pk=vehicle_id)
    unit = vehicle.unit

    owned_slots = list(
        ParkingSlot.objects.select_for_update()
        .filter(
            society_id=vehicle.society_id,
            parking_model=ParkingSlot.ParkingModel.SOLD,
            owned_unit_id=unit.id,
            is_active=True,
        )
        .order_by("slot_number", "id")
    )
    if not owned_slots:
        raise ValidationError("No sold parking slots are owned by this unit.")

    active_count = ParkingPermit.objects.filter(
        society_id=vehicle.society_id,
        unit_id=unit.id,
        permit_type=ParkingPermit.PermitType.SOLD,
        status=ParkingPermit.Status.ACTIVE,
    ).count()

    active_slot_ids = set(
        ParkingPermit.objects.filter(
            society_id=vehicle.society_id,
            unit_id=unit.id,
            permit_type=ParkingPermit.PermitType.SOLD,
            status=ParkingPermit.Status.ACTIVE,
            slot_id__in=[slot.id for slot in owned_slots],
        ).values_list("slot_id", flat=True)
    )
    available_slots = [slot for slot in owned_slots if slot.id not in active_slot_ids]
    selected_slot = available_slots[0] if available_slots else owned_slots[0]

    previous_active_for_slot = (
        ParkingPermit.objects.select_for_update()
        .filter(
            society_id=vehicle.society_id,
            slot_id=selected_slot.id,
            permit_type=ParkingPermit.PermitType.SOLD,
            status=ParkingPermit.Status.ACTIVE,
        )
        .order_by("-issued_at", "-id")
        .first()
    )
    if previous_active_for_slot and previous_active_for_slot.vehicle_id != vehicle.id:
        previous_active_for_slot.status = ParkingPermit.Status.REVOKED
        previous_active_for_slot.save(update_fields=["status"])

    next_status = ParkingPermit.Status.ACTIVE

    existing_permit = (
        ParkingPermit.objects.select_for_update()
        .filter(
            society_id=vehicle.society_id,
            vehicle_id=vehicle.id,
            unit_id=unit.id,
            permit_type=ParkingPermit.PermitType.SOLD,
        )
        .order_by("-issued_at", "-id")
        .first()
    )
    if existing_permit:
        if (
            existing_permit.status == ParkingPermit.Status.ACTIVE
            and existing_permit.slot_id == selected_slot.id
        ):
            recalculate_vehicle_rule_status(vehicle.society_id)
            return existing_permit
        existing_permit.slot = selected_slot
        existing_permit.status = next_status
        existing_permit.expires_at = None
        existing_permit.save(update_fields=["slot", "status", "expires_at"])
        recalculate_vehicle_rule_status(vehicle.society_id)
        return existing_permit

    permit = ParkingPermit.objects.create(
        society_id=vehicle.society_id,
        vehicle=vehicle,
        unit=unit,
        slot=selected_slot,
        permit_type=ParkingPermit.PermitType.SOLD,
        status=next_status,
        created_by=created_by,
    )
    recalculate_vehicle_rule_status(vehicle.society_id)
    return permit


@transaction.atomic
def switch_active_vehicle(slot_id, vehicle_id, *, created_by=None):
    slot = ParkingSlot.objects.select_for_update().get(pk=slot_id)
    vehicle = Vehicle.objects.select_related("unit").get(pk=vehicle_id)

    if slot.parking_model != ParkingSlot.ParkingModel.SOLD:
        raise ValidationError("Vehicle switching is only allowed for sold parking slots.")
    if not slot.owned_unit_id:
        raise ValidationError("Slot must have an owned unit.")
    if vehicle.unit_id != slot.owned_unit_id:
        raise ValidationError("Vehicle unit does not match slot owned unit.")

    current_active = ParkingPermit.objects.filter(
        slot_id=slot.id,
        permit_type=ParkingPermit.PermitType.SOLD,
        status=ParkingPermit.Status.ACTIVE,
    ).first()
    if current_active and current_active.vehicle_id == vehicle.id:
        recalculate_vehicle_rule_status(slot.society_id)
        return current_active

    ParkingPermit.objects.filter(
        slot_id=slot.id,
        permit_type=ParkingPermit.PermitType.SOLD,
        status=ParkingPermit.Status.ACTIVE,
    ).update(status=ParkingPermit.Status.REVOKED)

    ParkingPermit.objects.filter(
        vehicle_id=vehicle.id,
        permit_type=ParkingPermit.PermitType.SOLD,
        status=ParkingPermit.Status.ACTIVE,
    ).exclude(slot_id=slot.id).update(status=ParkingPermit.Status.REVOKED)

    existing_for_vehicle = (
        ParkingPermit.objects.select_for_update()
        .filter(
            society_id=slot.society_id,
            vehicle_id=vehicle.id,
            unit_id=slot.owned_unit_id,
            permit_type=ParkingPermit.PermitType.SOLD,
        )
        .order_by("-issued_at", "-id")
        .first()
    )
    if existing_for_vehicle:
        existing_for_vehicle.slot = slot
        existing_for_vehicle.status = ParkingPermit.Status.ACTIVE
        existing_for_vehicle.expires_at = None
        existing_for_vehicle.save(update_fields=["slot", "status", "expires_at"])
        recalculate_vehicle_rule_status(slot.society_id)
        return existing_for_vehicle

    created = ParkingPermit.objects.create(
        society_id=slot.society_id,
        vehicle=vehicle,
        unit=slot.owned_unit,
        slot=slot,
        permit_type=ParkingPermit.PermitType.SOLD,
        status=ParkingPermit.Status.ACTIVE,
        created_by=created_by,
    )
    recalculate_vehicle_rule_status(slot.society_id)
    return created
