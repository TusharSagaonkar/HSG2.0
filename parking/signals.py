from django.core.exceptions import ValidationError
from django.db.models.signals import pre_save
from django.db.models.signals import post_delete
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from members.models import Member
from members.models import UnitOccupancy
from members.models import UnitOwnership
from parking.models import ParkingPermit
from parking.models import ParkingSlot
from parking.models import ParkingSlotOwnershipHistory
from parking.models import Vehicle
from parking.services.create_sold_parking_permit import create_sold_parking_permit
from parking.services.recalculate_vehicle_rule_status import (
    recalculate_vehicle_rule_status,
)


@receiver(post_save, sender=Member)
def recalculate_vehicle_status_on_member_change(sender, instance, **kwargs):
    del sender, kwargs
    today = timezone.localdate()
    if instance.status == Member.MemberStatus.ACTIVE:
        Vehicle.objects.filter(
            society_id=instance.society_id,
            member_id=instance.id,
            rule_status__in=[
                Vehicle.RuleStatus.RESIDENT_MISMATCH,
                Vehicle.RuleStatus.VEHICLE_INACTIVE,
            ],
        ).update(
            is_active=True,
            rule_status=Vehicle.RuleStatus.ACTIVE,
            deactivated_at=None,
        )
    if (
        instance.status == Member.MemberStatus.ACTIVE
        and instance.end_date is not None
        and instance.end_date < today
    ):
        Member.objects.filter(pk=instance.pk).update(end_date=None)
    recalculate_vehicle_rule_status(instance.society_id)


@receiver(post_save, sender=UnitOccupancy)
def recalculate_vehicle_status_on_occupancy_change(sender, instance, **kwargs):
    del sender, kwargs
    recalculate_vehicle_rule_status(instance.unit.structure.society_id)


@receiver(post_delete, sender=UnitOccupancy)
def recalculate_vehicle_status_on_occupancy_delete(sender, instance, **kwargs):
    del sender, kwargs
    recalculate_vehicle_rule_status(instance.unit.structure.society_id)


@receiver(post_save, sender=UnitOwnership)
def recalculate_vehicle_status_on_ownership_change(sender, instance, **kwargs):
    del sender, kwargs
    recalculate_vehicle_rule_status(instance.unit.structure.society_id)


@receiver(post_delete, sender=UnitOwnership)
def recalculate_vehicle_status_on_ownership_delete(sender, instance, **kwargs):
    del sender, kwargs
    recalculate_vehicle_rule_status(instance.unit.structure.society_id)


@receiver(pre_save, sender=ParkingSlot)
def cache_slot_owner_for_history(sender, instance, **kwargs):
    del sender, kwargs
    instance._previous_owned_unit_id = None
    instance._previous_parking_model = None
    if not instance.pk:
        return
    previous = ParkingSlot.objects.filter(pk=instance.pk).values(
        "owned_unit_id",
        "parking_model",
    ).first()
    if previous:
        instance._previous_owned_unit_id = previous["owned_unit_id"]
        instance._previous_parking_model = previous["parking_model"]


@receiver(post_save, sender=ParkingSlot)
def maintain_slot_ownership_history_and_permits(sender, instance, created, **kwargs):
    del sender, kwargs
    today = timezone.localdate()

    previous_owner = getattr(instance, "_previous_owned_unit_id", None)
    previous_model = getattr(instance, "_previous_parking_model", None)
    changed = (
        created
        or previous_owner != instance.owned_unit_id
        or previous_model != instance.parking_model
    )
    if not changed:
        return

    if created and instance.parking_model == ParkingSlot.ParkingModel.SOLD and instance.owned_unit_id:
        ParkingSlotOwnershipHistory.objects.create(
            slot=instance,
            unit=instance.owned_unit,
            start_date=today,
            reason="Initial ownership assignment",
        )
    elif not created:
        ParkingSlotOwnershipHistory.objects.filter(
            slot=instance,
            end_date__isnull=True,
        ).update(end_date=today)
        if instance.parking_model == ParkingSlot.ParkingModel.SOLD and instance.owned_unit_id:
            reason = (
                "Ownership transfer"
                if previous_owner and previous_owner != instance.owned_unit_id
                else "Ownership assignment updated"
            )
            ParkingSlotOwnershipHistory.objects.create(
                slot=instance,
                unit=instance.owned_unit,
                start_date=today,
                reason=reason,
            )

    ParkingPermit.objects.filter(
        slot=instance,
        permit_type=ParkingPermit.PermitType.SOLD,
        status__in=[ParkingPermit.Status.ACTIVE, ParkingPermit.Status.STANDBY],
    ).update(status=ParkingPermit.Status.REVOKED)
    recalculate_vehicle_rule_status(instance.society_id)

    if instance.parking_model != ParkingSlot.ParkingModel.SOLD or not instance.owned_unit_id:
        return

    vehicles = Vehicle.objects.filter(
        society_id=instance.society_id,
        unit_id=instance.owned_unit_id,
    ).order_by("created_at", "id")
    for vehicle in vehicles:
        has_open_permit = ParkingPermit.objects.filter(
            vehicle=vehicle,
            permit_type=ParkingPermit.PermitType.SOLD,
            status__in=[ParkingPermit.Status.ACTIVE, ParkingPermit.Status.STANDBY],
        ).exists()
        if has_open_permit:
            continue
        try:
            create_sold_parking_permit(vehicle.id)
        except ValidationError:
            continue


@receiver(pre_save, sender=Vehicle)
def cache_vehicle_is_active(sender, instance, **kwargs):
    del sender, kwargs
    instance._was_active = None
    if not instance.pk:
        return
    instance._was_active = Vehicle.objects.filter(pk=instance.pk).values_list("is_active", flat=True).first()


@receiver(post_save, sender=Vehicle)
def expire_permits_for_deactivated_vehicle(sender, instance, **kwargs):
    del sender, kwargs
    if instance.is_active:
        return
    if getattr(instance, "_was_active", False) is False:
        return
    ParkingPermit.objects.filter(
        vehicle=instance,
        permit_type=ParkingPermit.PermitType.SOLD,
        status__in=[ParkingPermit.Status.ACTIVE, ParkingPermit.Status.STANDBY],
    ).update(
        status=ParkingPermit.Status.EXPIRED,
        expires_at=timezone.now(),
    )
    recalculate_vehicle_rule_status(instance.society_id)


@receiver(post_save, sender=Vehicle)
def activate_sold_permit_for_active_vehicle(sender, instance, **kwargs):
    del sender, kwargs
    if not instance.is_active:
        return
    has_sold_slot = ParkingSlot.objects.filter(
        society_id=instance.society_id,
        parking_model=ParkingSlot.ParkingModel.SOLD,
        owned_unit_id=instance.unit_id,
        is_active=True,
    ).exists()
    if not has_sold_slot:
        return
    try:
        create_sold_parking_permit(instance.id)
    except ValidationError:
        return


@receiver(post_delete, sender=ParkingPermit)
def recalculate_vehicle_status_on_permit_delete(sender, instance, **kwargs):
    del sender, kwargs
    recalculate_vehicle_rule_status(instance.society_id)
