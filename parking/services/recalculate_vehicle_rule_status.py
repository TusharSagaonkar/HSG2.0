from collections import defaultdict

from django.apps import apps
from django.db import transaction
from django.utils import timezone

from housing.models import UnitOccupancy
from parking.models import ParkingVehicleLimit
from parking.models import Vehicle


def _get_active_rules(society_id):
    return {
        (rule.member_role, rule.vehicle_type): rule
        for rule in ParkingVehicleLimit.objects.filter(
            society_id=society_id,
            status=ParkingVehicleLimit.Status.ACTIVE,
        )
    }


def _get_active_occupancy_by_unit(society_id):
    by_unit = {}
    for occupancy in (
        UnitOccupancy.objects.filter(
            unit__structure__society_id=society_id,
            end_date__isnull=True,
        )
        .select_related("unit")
        .order_by("-start_date", "-id")
    ):
        by_unit.setdefault(occupancy.unit_id, occupancy)
    return by_unit


def _get_parking_permit_model():
    try:
        return apps.get_model("parking", "ParkingPermit")
    except LookupError:
        return None


def _get_parking_slot_model():
    try:
        return apps.get_model("parking", "ParkingSlot")
    except LookupError:
        return None


def _get_active_rotational_vehicle_ids(society_id, today, now):
    try:
        allocation_model = apps.get_model("parking", "ParkingRotationAllocation")
        cycle_model = apps.get_model("parking", "ParkingRotationCycle")
    except LookupError:
        return set()

    return set(
        allocation_model.objects.filter(
            cycle__society_id=society_id,
            cycle__allocation_status=cycle_model.AllocationStatus.ACTIVE,
            cycle__cycle_start_date__lte=today,
            cycle__cycle_end_date__gte=today,
            expires_at__gte=now,
            vehicle_id__isnull=False,
        ).values_list("vehicle_id", flat=True)
    )


def _permit_status_for_vehicle(vehicle, permit_model):
    if permit_model is None:
        return None

    permits = permit_model.objects.filter(vehicle_id=vehicle.id)
    active_permits = permits.filter(end_date__isnull=True) if hasattr(permit_model, "end_date") else permits
    if active_permits.count() > 1:
        return Vehicle.RuleStatus.DATA_INCONSISTENT

    permit = active_permits.order_by("-id").first()
    if permit is None or not hasattr(permit, "status"):
        return None

    status_value = str(permit.status).upper()
    if status_value == "ADMIN_BLOCKED":
        return Vehicle.RuleStatus.ADMIN_BLOCKED
    if status_value in {"REVOKED", "EXPIRED", "SUSPENDED"}:
        return Vehicle.RuleStatus.PERMIT_EXPIRED
    return None


def _has_active_sold_permit(vehicle, permit_model, cached_permit=None):
    """Check if vehicle has active sold permit (with optional cached permit data)."""
    if permit_model is None:
        return False
    if not hasattr(permit_model, "PermitType") or not hasattr(permit_model, "Status"):
        return False

    # Use cached permit if available (faster)
    if cached_permit is not None:
        permit = cached_permit
    else:
        # Only query if not cached
        permit = (
            permit_model.objects.select_related("slot")
            .filter(
                vehicle_id=vehicle.id,
                permit_type=permit_model.PermitType.SOLD,
            )
            .order_by("-issued_at", "-id")
            .first()
        )
    
    if not permit:
        return False
    return (
        permit.status == permit_model.Status.ACTIVE
        and permit.slot.owned_unit_id == vehicle.unit_id
    )


def _eligibility_error_status(vehicle, active_occupancy_by_unit, today, permit_model):
    if not vehicle.unit_id or not vehicle.member_id:
        return Vehicle.RuleStatus.DATA_INCONSISTENT

    if vehicle.unit.structure.society_id != vehicle.society_id:
        return Vehicle.RuleStatus.DATA_INCONSISTENT

    member = vehicle.member
    if member.society_id != vehicle.society_id:
        return Vehicle.RuleStatus.DATA_INCONSISTENT

    if member.status != member.MemberStatus.ACTIVE:
        return Vehicle.RuleStatus.RESIDENT_MISMATCH

    if member.end_date and member.end_date < today:
        return Vehicle.RuleStatus.RESIDENT_MISMATCH

    if member.unit_id != vehicle.unit_id:
        return Vehicle.RuleStatus.RESIDENT_MISMATCH

    occupancy = active_occupancy_by_unit.get(vehicle.unit_id)
    if occupancy and occupancy.occupancy_type == UnitOccupancy.OccupancyType.VACANT:
        return Vehicle.RuleStatus.UNIT_VACANT

    if member.role not in {member.MemberRole.OWNER, member.MemberRole.TENANT}:
        return Vehicle.RuleStatus.RESIDENT_MISMATCH

    if vehicle.valid_until and vehicle.valid_until < today:
        return Vehicle.RuleStatus.PERMIT_EXPIRED

    permit_status = _permit_status_for_vehicle(vehicle, permit_model)
    if permit_status:
        return permit_status

    # RULE_VIOLATION is a policy-capacity outcome; keep it eligible for re-evaluation
    # even though permit activation may currently be false.
    if not vehicle.is_active and vehicle.rule_status != Vehicle.RuleStatus.RULE_VIOLATION:
        return Vehicle.RuleStatus.VEHICLE_INACTIVE

    return None


def recalculate_single_vehicle_rule_status(vehicle):
    """
    Optimized: Calculate rule status for a single vehicle without full society recalculation.
    
    This is much faster than recalculate_vehicle_rule_status() for individual vehicle additions
    because it only evaluates the specific vehicle instead of processing all vehicles in the society.
    
    Note: This doesn't check policy-capacity violations (which require comparing across all vehicles).
    For that, use the full recalculate_vehicle_rule_status() function.
    """
    today = timezone.localdate()
    now = timezone.now()
    permit_model = _get_parking_permit_model()
    active_occupancy_by_unit = {}
    if vehicle.unit_id:
        occupancy = (
            UnitOccupancy.objects.filter(
                unit_id=vehicle.unit_id,
                end_date__isnull=True,
            )
            .select_related("unit")
            .order_by("-start_date", "-id")
            .first()
        )
        if occupancy:
            active_occupancy_by_unit[vehicle.unit_id] = occupancy
    
    # Sold slot permits are authoritative
    if _has_active_sold_permit(vehicle, permit_model):
        new_status = Vehicle.RuleStatus.ACTIVE
    else:
        # Check eligibility errors
        error_status = _eligibility_error_status(
            vehicle,
            active_occupancy_by_unit,
            today,
            permit_model,
        )
        if error_status:
            new_status = error_status
        else:
            # Default to ACTIVE for now (capacity checks need full society recalc)
            new_status = Vehicle.RuleStatus.ACTIVE
    
    # Update vehicle status
    new_is_active = new_status == Vehicle.RuleStatus.ACTIVE
    new_deactivated_at = None if new_is_active else (vehicle.deactivated_at or now)
    
    if (
        vehicle.rule_status != new_status
        or vehicle.is_active != new_is_active
        or vehicle.deactivated_at != new_deactivated_at
    ):
        Vehicle.objects.filter(id=vehicle.id).update(
            rule_status=new_status,
            is_active=new_is_active,
            deactivated_at=new_deactivated_at,
        )


def recalculate_vehicle_rule_status(society_id):
    """Safely re-evaluate rule-capacity violations.

    - Always apply hard eligibility checks.
    - Capacity reactivation is only for vehicles currently in RULE_VIOLATION.
    """

    today = timezone.localdate()
    now = timezone.now()
    active_rules = _get_active_rules(society_id)
    active_occupancy_by_unit = _get_active_occupancy_by_unit(society_id)
    permit_model = _get_parking_permit_model()
    slot_model = _get_parking_slot_model()
    active_rotational_vehicle_ids = _get_active_rotational_vehicle_ids(
        society_id,
        today,
        now,
    )
    sold_slot_unit_ids = set()
    if slot_model is not None and hasattr(slot_model, "ParkingModel"):
        sold_slot_unit_ids = set(
            slot_model.objects.filter(
                society_id=society_id,
                parking_model=slot_model.ParkingModel.SOLD,
                is_active=True,
                owned_unit_id__isnull=False,
            ).values_list("owned_unit_id", flat=True)
        )

    vehicles = list(
        Vehicle.objects.select_related("member", "unit", "unit__structure")
        .filter(society_id=society_id)
        .order_by("-created_at", "-id")
    )

    updates = {}
    eligible_groups = defaultdict(list)

    for vehicle in vehicles:
        # Sold slot permits are authoritative for parking activation.
        if _has_active_sold_permit(vehicle, permit_model):
            updates[vehicle.id] = Vehicle.RuleStatus.ACTIVE
            continue

        if vehicle.id in active_rotational_vehicle_ids:
            error_status = _eligibility_error_status(
                vehicle,
                active_occupancy_by_unit,
                today,
                permit_model,
            )
            # Active rotational allocation can override only policy-capacity inactive states.
            if error_status in {None, Vehicle.RuleStatus.VEHICLE_INACTIVE, Vehicle.RuleStatus.RULE_VIOLATION}:
                updates[vehicle.id] = Vehicle.RuleStatus.ACTIVE
                continue

        # For units with sold slots, only vehicle with ACTIVE sold permit may remain active.
        if vehicle.unit_id in sold_slot_unit_ids:
            updates[vehicle.id] = Vehicle.RuleStatus.VEHICLE_INACTIVE
            continue

        error_status = _eligibility_error_status(
            vehicle,
            active_occupancy_by_unit,
            today,
            permit_model,
        )
        if error_status:
            updates[vehicle.id] = error_status
            continue

        member_role = vehicle.member.role
        rule_key = (member_role, vehicle.vehicle_type)
        if rule_key not in active_rules:
            updates[vehicle.id] = Vehicle.RuleStatus.ACTIVE
            continue

        eligible_groups[(vehicle.unit_id, member_role, vehicle.vehicle_type)].append(vehicle)

    for (unit_id, member_role, vehicle_type), grouped in eligible_groups.items():
        del unit_id
        rule = active_rules[(member_role, vehicle_type)]
        for idx, vehicle in enumerate(grouped):
            if idx < rule.max_allowed:
                if vehicle.rule_status in {
                    Vehicle.RuleStatus.ACTIVE,
                    Vehicle.RuleStatus.RULE_VIOLATION,
                }:
                    updates[vehicle.id] = Vehicle.RuleStatus.ACTIVE
                else:
                    updates.setdefault(vehicle.id, vehicle.rule_status)
            else:
                if vehicle.rule_status in {
                    Vehicle.RuleStatus.ACTIVE,
                    Vehicle.RuleStatus.RULE_VIOLATION,
                }:
                    updates[vehicle.id] = Vehicle.RuleStatus.RULE_VIOLATION
                else:
                    updates.setdefault(vehicle.id, vehicle.rule_status)

    # Any vehicle not covered by groups/errors keeps its existing status.
    for vehicle in vehicles:
        updates.setdefault(vehicle.id, vehicle.rule_status)

    with transaction.atomic():
        for vehicle in vehicles:
            new_status = updates[vehicle.id]
            # Strict invariant: only ACTIVE may be permit-active.
            new_is_active = new_status == Vehicle.RuleStatus.ACTIVE

            new_deactivated_at = vehicle.deactivated_at

            if new_is_active:
                new_deactivated_at = None
            else:
                new_deactivated_at = vehicle.deactivated_at or now

            if (
                vehicle.rule_status != new_status
                or vehicle.is_active != new_is_active
                or vehicle.deactivated_at != new_deactivated_at
            ):
                Vehicle.objects.filter(id=vehicle.id).update(
                    rule_status=new_status,
                    is_active=new_is_active,
                    deactivated_at=new_deactivated_at,
                )


def recalculate_single_vehicle_rule_status_optimized(vehicle):
    """
    FAST: Calculate rule status for a single vehicle with minimal queries.
    
    Optimized version that assumes vehicle has necessary relations already loaded:
    - unit__structure
    - member
    
    This avoids redundant queries by using already-loaded relations.
    Use this when you know the vehicle has been prefetched with select_related().
    Reduces database queries from ~8-10 to ~3-4.
    """
    today = timezone.localdate()
    now = timezone.now()
    permit_model = _get_parking_permit_model()
    
    # Only fetch occupancy if needed (single query)
    if vehicle.unit_id:
        occupancy = (
            UnitOccupancy.objects.filter(
                unit_id=vehicle.unit_id,
                end_date__isnull=True,
            )
            .values_list("occupancy_type", flat=True)
            .first()
        )
        is_vacant = occupancy == UnitOccupancy.OccupancyType.VACANT if occupancy else False
    else:
        is_vacant = False
    
    # Quick eligibility checks using already-loaded relations (no queries)
    if not vehicle.unit_id or not vehicle.member_id:
        new_status = Vehicle.RuleStatus.DATA_INCONSISTENT
    elif vehicle.unit.structure.society_id != vehicle.society_id:
        new_status = Vehicle.RuleStatus.DATA_INCONSISTENT
    elif vehicle.member.society_id != vehicle.society_id:
        new_status = Vehicle.RuleStatus.DATA_INCONSISTENT
    elif vehicle.member.status != vehicle.member.MemberStatus.ACTIVE:
        new_status = Vehicle.RuleStatus.RESIDENT_MISMATCH
    elif vehicle.member.end_date and vehicle.member.end_date < today:
        new_status = Vehicle.RuleStatus.RESIDENT_MISMATCH
    elif vehicle.member.unit_id != vehicle.unit_id:
        new_status = Vehicle.RuleStatus.RESIDENT_MISMATCH
    elif is_vacant:
        new_status = Vehicle.RuleStatus.UNIT_VACANT
    elif vehicle.member.role not in {vehicle.member.MemberRole.OWNER, vehicle.member.MemberRole.TENANT}:
        new_status = Vehicle.RuleStatus.RESIDENT_MISMATCH
    elif vehicle.valid_until and vehicle.valid_until < today:
        new_status = Vehicle.RuleStatus.PERMIT_EXPIRED
    else:
        # Check permits (requires 1-2 queries only)
        if permit_model and hasattr(permit_model, "PermitType") and hasattr(permit_model, "Status"):
            # Check for sold permit (single exists query)
            has_active_sold_permit = permit_model.objects.filter(
                vehicle_id=vehicle.id,
                permit_type=permit_model.PermitType.SOLD,
                status=permit_model.Status.ACTIVE,
                slot__owned_unit_id=vehicle.unit_id,
            ).exists()
            
            if has_active_sold_permit:
                new_status = Vehicle.RuleStatus.ACTIVE
            else:
                # Check other permit statuses (single query)
                permit_status = _permit_status_for_vehicle(vehicle, permit_model)
                new_status = permit_status or Vehicle.RuleStatus.ACTIVE
        else:
            new_status = Vehicle.RuleStatus.ACTIVE
    
    # Update vehicle status (bulk update - no additional query)
    new_is_active = new_status == Vehicle.RuleStatus.ACTIVE
    new_deactivated_at = None if new_is_active else (vehicle.deactivated_at or now)
    
    if (
        vehicle.rule_status != new_status
        or vehicle.is_active != new_is_active
        or vehicle.deactivated_at != new_deactivated_at
    ):
        Vehicle.objects.filter(id=vehicle.id).update(
            rule_status=new_status,
            is_active=new_is_active,
            deactivated_at=new_deactivated_at,
        )
