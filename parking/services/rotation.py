import random
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.db.models import Max
from django.db.models import Q
from django.utils import timezone

from billing.models import Bill
from parking.models import ParkingPermit
from parking.models import ParkingRotationAllocation
from parking.models import ParkingRotationApplication
from parking.models import ParkingRotationCycle
from parking.models import ParkingRotationPolicy
from parking.models import ParkingRotationQueue
from parking.models import ParkingSlot
from parking.models import Vehicle


def _add_months(value: date, months: int) -> date:
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    day = min(
        value.day,
        (
            date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
            - timedelta(days=1)
        ).day,
    )
    return date(year, month, day)


def get_active_rotation_policy(*, society_id, as_of_date=None):
    as_of_date = as_of_date or timezone.localdate()
    return (
        ParkingRotationPolicy.objects.filter(
            society_id=society_id,
            is_active=True,
            effective_from__lte=as_of_date,
        )
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=as_of_date))
        .order_by("-effective_from", "-id")
        .first()
    )


@transaction.atomic
def generate_next_rotation_cycle(*, society_id, as_of_date=None):
    as_of_date = as_of_date or timezone.localdate()
    policy = get_active_rotation_policy(society_id=society_id, as_of_date=as_of_date)
    if policy is None:
        return None

    last_cycle = (
        ParkingRotationCycle.objects.select_for_update()
        .filter(society_id=society_id)
        .order_by("-cycle_number", "-id")
        .first()
    )
    cycle_number = 1 if last_cycle is None else last_cycle.cycle_number + 1
    if last_cycle is None:
        cycle_start_date = max(policy.effective_from, as_of_date)
    else:
        cycle_start_date = last_cycle.cycle_end_date + timedelta(days=1)
    cycle_end_date = _add_months(cycle_start_date, policy.rotation_period_months) - timedelta(days=1)
    total_rotational_spots = ParkingSlot.objects.filter(
        society_id=society_id,
        is_active=True,
        is_rotational=True,
    ).exclude(parking_model=ParkingSlot.ParkingModel.SOLD).count()
    return ParkingRotationCycle.objects.create(
        society_id=society_id,
        policy=policy,
        cycle_number=cycle_number,
        cycle_start_date=cycle_start_date,
        cycle_end_date=cycle_end_date,
        total_rotational_spots=total_rotational_spots,
        allocation_status=ParkingRotationCycle.AllocationStatus.DRAFT,
    )


def _has_unit_outstanding_dues(unit_id):
    return Bill.objects.filter(unit_id=unit_id).exclude(status=Bill.BillStatus.PAID).exists()


def _has_unit_parking_violation(unit_id):
    return Vehicle.objects.filter(
        unit_id=unit_id,
        rule_status__in=[Vehicle.RuleStatus.RULE_VIOLATION, Vehicle.RuleStatus.ADMIN_BLOCKED],
    ).exists()


def _current_total_parking_for_unit(*, society_id, unit_id):
    today = timezone.localdate()
    now = timezone.now()
    sold_count = ParkingPermit.objects.filter(
        society_id=society_id,
        unit_id=unit_id,
        permit_type=ParkingPermit.PermitType.SOLD,
        status=ParkingPermit.Status.ACTIVE,
    ).count()
    open_count = ParkingPermit.objects.filter(
        society_id=society_id,
        unit_id=unit_id,
        permit_type=ParkingPermit.PermitType.OPEN,
        status=ParkingPermit.Status.ACTIVE,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gte=now)).count()
    rotational_count = ParkingRotationAllocation.objects.filter(
        cycle__society_id=society_id,
        unit_id=unit_id,
        cycle__allocation_status=ParkingRotationCycle.AllocationStatus.ACTIVE,
        cycle__cycle_start_date__lte=today,
        cycle__cycle_end_date__gte=today,
        expires_at__gte=now,
    ).count()
    return sold_count + open_count + rotational_count


def validate_rotation_application(*, cycle, unit, vehicle=None, applicant_member=None):
    policy = cycle.policy
    if policy.vehicle_required_before_apply and vehicle is None:
        return False, "Vehicle is required before application."
    if vehicle is not None:
        if vehicle.society_id != cycle.society_id:
            return False, "Vehicle must belong to cycle society."
        if vehicle.unit_id != unit.id:
            return False, "Vehicle must belong to application unit."

    if not policy.allow_sold_parking_owner:
        has_sold_slot = ParkingSlot.objects.filter(
            society_id=cycle.society_id,
            parking_model=ParkingSlot.ParkingModel.SOLD,
            owned_unit_id=unit.id,
            is_active=True,
        ).exists()
        if has_sold_slot:
            return False, "Units owning sold parking cannot apply."

    if not policy.allow_tenant_application:
        is_tenant = False
        if applicant_member is not None:
            is_tenant = applicant_member.role == applicant_member.MemberRole.TENANT
        elif vehicle is not None and vehicle.member is not None:
            is_tenant = vehicle.member.role == vehicle.member.MemberRole.TENANT
        if is_tenant:
            return False, "Tenant applications are not allowed by policy."

    if policy.max_total_parking_per_unit > 0:
        total_current = _current_total_parking_for_unit(
            society_id=cycle.society_id,
            unit_id=unit.id,
        )
        if total_current >= policy.max_total_parking_per_unit:
            return False, "Unit has reached maximum total parking limit."

    return True, ""


@transaction.atomic
def submit_rotation_application(*, cycle, unit, vehicle=None, applicant_member=None):
    allowed, reason = validate_rotation_application(
        cycle=cycle,
        unit=unit,
        vehicle=vehicle,
        applicant_member=applicant_member,
    )
    status = (
        ParkingRotationApplication.ApplicationStatus.APPROVED
        if allowed
        else ParkingRotationApplication.ApplicationStatus.REJECTED
    )
    return ParkingRotationApplication.objects.create(
        cycle=cycle,
        unit=unit,
        vehicle=vehicle,
        application_status=status,
        rejection_reason="" if allowed else reason,
    )


def _eligible_applications(cycle):
    policy = cycle.policy
    apps = list(
        ParkingRotationApplication.objects.select_related("unit", "vehicle")
        .filter(
            cycle=cycle,
            application_status=ParkingRotationApplication.ApplicationStatus.APPROVED,
        )
        .order_by("applied_at", "id")
    )
    eligible = []
    for application in apps:
        if policy.skip_units_with_outstanding_dues and _has_unit_outstanding_dues(application.unit_id):
            continue
        if policy.skip_units_with_parking_violation and _has_unit_parking_violation(application.unit_id):
            continue
        eligible.append(application)
    return eligible


@transaction.atomic
def ensure_rotation_queue(*, society_id):
    current_max = (
        ParkingRotationQueue.objects.select_for_update()
        .filter(society_id=society_id)
        .aggregate(max_pos=Max("queue_position"))["max_pos"]
        or 0
    )
    existing_unit_ids = set(
        ParkingRotationQueue.objects.filter(society_id=society_id).values_list("unit_id", flat=True)
    )
    from members.models import Unit

    units = Unit.objects.filter(structure__society_id=society_id).order_by("identifier", "id")
    to_create = []
    for unit in units:
        if unit.id in existing_unit_ids:
            continue
        current_max += 1
        to_create.append(
            ParkingRotationQueue(
                society_id=society_id,
                unit_id=unit.id,
                queue_position=current_max,
            )
        )
    if to_create:
        ParkingRotationQueue.objects.bulk_create(to_create)


def _allocate_by_queue(*, cycle, eligible_apps, spots, assigned_by):
    ensure_rotation_queue(society_id=cycle.society_id)
    queue_entries = list(
        ParkingRotationQueue.objects.select_for_update()
        .filter(society_id=cycle.society_id)
        .order_by("queue_position", "id")
    )
    app_by_unit = {}
    for app in eligible_apps:
        app_by_unit.setdefault(app.unit_id, app)

    selected = []
    for entry in queue_entries:
        app = app_by_unit.get(entry.unit_id)
        if app is None:
            continue
        selected.append((entry, app))
        if len(selected) >= len(spots):
            break

    allocations = _create_allocations(
        cycle=cycle,
        selected_apps=[item[1] for item in selected],
        spots=spots,
        method=ParkingRotationAllocation.AllocationMethod.QUEUE,
        assigned_by=assigned_by,
    )
    if not selected:
        return allocations

    allocated_ids = [entry.id for entry, _ in selected]
    ordered_ids = [entry.id for entry in queue_entries]
    rotated_ids = [queue_id for queue_id in ordered_ids if queue_id not in allocated_ids] + allocated_ids
    ParkingRotationQueue.objects.filter(society_id=cycle.society_id).update(
        queue_position=F("queue_position") + len(rotated_ids),
    )
    for idx, queue_id in enumerate(rotated_ids, start=1):
        ParkingRotationQueue.objects.filter(pk=queue_id).update(
            queue_position=idx,
        )
    ParkingRotationQueue.objects.filter(pk__in=allocated_ids).update(last_allocated_cycle=cycle)
    return allocations


def _allocate_by_lottery(*, cycle, eligible_apps, spots, assigned_by, seed=None):
    shuffled = list(eligible_apps)
    rng = random.Random(seed)
    rng.shuffle(shuffled)
    selected_apps = shuffled[: len(spots)]
    return _create_allocations(
        cycle=cycle,
        selected_apps=selected_apps,
        spots=spots,
        method=ParkingRotationAllocation.AllocationMethod.LOTTERY,
        assigned_by=assigned_by,
    )


def _cycle_expiry_datetime(cycle):
    naive = datetime.combine(cycle.cycle_end_date, time.max)
    if timezone.is_naive(naive):
        return timezone.make_aware(naive, timezone.get_current_timezone())
    return naive


def _create_allocations(*, cycle, selected_apps, spots, method, assigned_by):
    created = []
    expires_at = _cycle_expiry_datetime(cycle)
    for idx, app in enumerate(selected_apps):
        if idx >= len(spots):
            break
        created.append(
            ParkingRotationAllocation.objects.create(
                cycle=cycle,
                unit=app.unit,
                parking_spot=spots[idx],
                vehicle=app.vehicle,
                allocation_method=method,
                expires_at=expires_at,
                assigned_by=assigned_by,
            )
        )
    return created


@transaction.atomic
def allocate_rotation_cycle(*, cycle, assigned_by=None, seed=None):
    eligible_apps = _eligible_applications(cycle)
    if not eligible_apps:
        cycle.allocation_status = ParkingRotationCycle.AllocationStatus.ACTIVE
        cycle.save(update_fields=["allocation_status"])
        return []

    slots = list(
        ParkingSlot.objects.select_for_update()
        .filter(
            society_id=cycle.society_id,
            is_active=True,
            is_rotational=True,
        )
        .exclude(parking_model=ParkingSlot.ParkingModel.SOLD)
        .order_by("slot_number", "id")
    )
    if not slots:
        cycle.allocation_status = ParkingRotationCycle.AllocationStatus.ACTIVE
        cycle.save(update_fields=["allocation_status"])
        return []

    cycle.allocation_status = ParkingRotationCycle.AllocationStatus.ALLOCATED
    cycle.total_rotational_spots = len(slots)
    cycle.save(update_fields=["allocation_status", "total_rotational_spots"])

    if cycle.policy.rotation_method == ParkingRotationPolicy.RotationMethod.QUEUE:
        allocations = _allocate_by_queue(
            cycle=cycle,
            eligible_apps=eligible_apps,
            spots=slots,
            assigned_by=assigned_by,
        )
    else:
        allocations = _allocate_by_lottery(
            cycle=cycle,
            eligible_apps=eligible_apps,
            spots=slots,
            assigned_by=assigned_by,
            seed=seed,
        )

    cycle.allocation_status = ParkingRotationCycle.AllocationStatus.ACTIVE
    cycle.save(update_fields=["allocation_status"])
    from parking.services.recalculate_vehicle_rule_status import (
        recalculate_vehicle_rule_status,
    )

    recalculate_vehicle_rule_status(cycle.society_id)
    return allocations


@transaction.atomic
def complete_rotation_cycle(*, cycle, generate_next=True):
    cycle.allocation_status = ParkingRotationCycle.AllocationStatus.COMPLETED
    cycle.save(update_fields=["allocation_status"])
    now = timezone.now()
    ParkingRotationAllocation.objects.filter(
        cycle=cycle,
        expires_at__gt=now,
    ).update(expires_at=now)
    from parking.services.recalculate_vehicle_rule_status import (
        recalculate_vehicle_rule_status,
    )

    recalculate_vehicle_rule_status(cycle.society_id)
    if not generate_next:
        return None
    return generate_next_rotation_cycle(
        society_id=cycle.society_id,
        as_of_date=cycle.cycle_end_date + timedelta(days=1),
    )


@transaction.atomic
def auto_complete_due_rotation_cycles(*, society_id, as_of_date=None):
    as_of_date = as_of_date or timezone.localdate()
    now = timezone.now()
    due_cycles = list(
        ParkingRotationCycle.objects.select_for_update()
        .filter(
            society_id=society_id,
            allocation_status=ParkingRotationCycle.AllocationStatus.ACTIVE,
            cycle_end_date__lt=as_of_date,
        )
        .order_by("cycle_end_date", "id")
    )
    if not due_cycles:
        return 0

    for cycle in due_cycles:
        cycle.allocation_status = ParkingRotationCycle.AllocationStatus.COMPLETED
        cycle.save(update_fields=["allocation_status"])
        ParkingRotationAllocation.objects.filter(
            cycle=cycle,
            expires_at__gt=now,
        ).update(expires_at=now)

    from parking.services.recalculate_vehicle_rule_status import (
        recalculate_vehicle_rule_status,
    )

    recalculate_vehicle_rule_status(society_id)
    return len(due_cycles)
