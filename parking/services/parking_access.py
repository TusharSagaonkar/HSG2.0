from django.utils import timezone
from django.db.models import Q

from parking.models import ParkingPermit
from parking.models import ParkingRotationAllocation
from parking.models import ParkingRotationCycle


def has_active_sold_parking(vehicle, *, as_of_date=None):
    _ = as_of_date or timezone.localdate()
    return ParkingPermit.objects.filter(
        vehicle_id=vehicle.id,
        permit_type=ParkingPermit.PermitType.SOLD,
        status=ParkingPermit.Status.ACTIVE,
        slot__owned_unit_id=vehicle.unit_id,
    ).exists()


def has_active_open_parking(vehicle, *, as_of_date=None):
    as_of_date = as_of_date or timezone.localdate()
    now = timezone.now()
    return ParkingPermit.objects.filter(
        vehicle_id=vehicle.id,
        permit_type=ParkingPermit.PermitType.OPEN,
        status=ParkingPermit.Status.ACTIVE,
    ).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gte=now),
    ).exists()


def has_active_rotational_parking(vehicle, *, as_of_date=None):
    as_of_date = as_of_date or timezone.localdate()
    now = timezone.now()
    return ParkingRotationAllocation.objects.filter(
        unit_id=vehicle.unit_id,
        cycle__allocation_status=ParkingRotationCycle.AllocationStatus.ACTIVE,
        cycle__cycle_start_date__lte=as_of_date,
        cycle__cycle_end_date__gte=as_of_date,
        expires_at__gte=now,
    ).filter(
        vehicle_id=vehicle.id,
    ).exists() or ParkingRotationAllocation.objects.filter(
        unit_id=vehicle.unit_id,
        vehicle__isnull=True,
        cycle__allocation_status=ParkingRotationCycle.AllocationStatus.ACTIVE,
        cycle__cycle_start_date__lte=as_of_date,
        cycle__cycle_end_date__gte=as_of_date,
        expires_at__gte=now,
    ).exists()


def has_any_parking_access(vehicle, *, as_of_date=None):
    return (
        has_active_sold_parking(vehicle, as_of_date=as_of_date)
        or has_active_rotational_parking(vehicle, as_of_date=as_of_date)
        or has_active_open_parking(vehicle, as_of_date=as_of_date)
    )
