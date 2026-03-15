from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from housing.models import Member
from housing.models import Society
from housing.models import Structure
from housing.models import Unit
from parking.models import ParkingRotationAllocation
from parking.models import ParkingRotationCycle
from parking.models import ParkingRotationPolicy
from parking.models import ParkingRotationPolicyAudit
from parking.models import ParkingRotationQueue
from parking.models import ParkingSlot
from parking.models import ParkingVehicleLimit
from parking.models import Vehicle
from parking.services.parking_access import has_any_parking_access
from parking.services.rotation import allocate_rotation_cycle
from parking.services.rotation import auto_complete_due_rotation_cycles
from parking.services.rotation import generate_next_rotation_cycle
from parking.services.rotation import submit_rotation_application


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


def test_rotation_policy_versioning_closes_previous_and_creates_audit():
    society = Society.objects.create(name="Rotation Society")
    first = ParkingRotationPolicy.create_new_version(
        society=society,
        policy_name="Policy v1",
        rotation_period_months=2,
        change_reason="Initial",
    )
    second = ParkingRotationPolicy.create_new_version(
        society=society,
        policy_name="Policy v2",
        rotation_period_months=3,
        effective_from=timezone.localdate() + timedelta(days=10),
        change_reason="Changed period",
    )

    first.refresh_from_db()
    second.refresh_from_db()

    assert first.is_active is False
    assert first.effective_to == second.effective_from - timedelta(days=1)
    assert second.is_active is True
    assert ParkingRotationPolicyAudit.objects.filter(policy=first).count() == 1
    assert ParkingRotationPolicyAudit.objects.filter(policy=second).count() == 1


def test_queue_allocation_moves_allocated_units_to_queue_bottom():
    society = Society.objects.create(name="Queue Rotation Society")
    unit_101 = _build_unit(society, "A Wing", "101")
    unit_102 = _build_unit(society, "A Wing", "102")
    unit_103 = _build_unit(society, "A Wing", "103")

    ParkingSlot.objects.create(
        society=society,
        slot_number="R1",
        is_rotational=True,
        parking_model=ParkingSlot.ParkingModel.COMMON,
    )
    ParkingSlot.objects.create(
        society=society,
        slot_number="R2",
        is_rotational=True,
        parking_model=ParkingSlot.ParkingModel.COMMON,
    )

    ParkingRotationPolicy.create_new_version(
        society=society,
        policy_name="Queue Policy",
        rotation_method=ParkingRotationPolicy.RotationMethod.QUEUE,
        vehicle_required_before_apply=False,
        max_total_parking_per_unit=5,
        change_reason="Queue setup",
    )
    cycle = generate_next_rotation_cycle(society_id=society.id)
    assert cycle is not None

    submit_rotation_application(cycle=cycle, unit=unit_101)
    submit_rotation_application(cycle=cycle, unit=unit_102)
    submit_rotation_application(cycle=cycle, unit=unit_103)

    allocations = allocate_rotation_cycle(cycle=cycle)
    allocated_unit_ids = {allocation.unit_id for allocation in allocations}
    assert len(allocations) == 2
    assert allocated_unit_ids == {unit_101.id, unit_102.id}

    queue = list(
        ParkingRotationQueue.objects.filter(society=society).order_by("queue_position").values_list(
            "unit_id",
            flat=True,
        )
    )
    assert queue == [unit_103.id, unit_101.id, unit_102.id]


def test_vehicle_verify_is_active_when_rotational_allocation_exists(client):
    society = Society.objects.create(name="Rotation Verify Society")
    unit = _build_unit(society, "A Wing", "101")
    member = Member.objects.create(
        society=society,
        unit=unit,
        full_name="Owner 101",
        role=Member.MemberRole.OWNER,
    )
    vehicle = Vehicle.objects.create(
        society=society,
        unit=unit,
        member=member,
        vehicle_number="MH-01-RR-0001",
        vehicle_type=Vehicle.VehicleType.CAR,
        valid_until=timezone.localdate() - timedelta(days=2),
    )

    ParkingSlot.objects.create(
        society=society,
        slot_number="R9",
        is_rotational=True,
        parking_model=ParkingSlot.ParkingModel.COMMON,
    )
    policy = ParkingRotationPolicy.create_new_version(
        society=society,
        policy_name="Verify Rotation Policy",
        rotation_method=ParkingRotationPolicy.RotationMethod.LOTTERY,
        vehicle_required_before_apply=True,
        max_total_parking_per_unit=5,
    )
    cycle = generate_next_rotation_cycle(society_id=society.id)
    assert cycle is not None

    application = submit_rotation_application(cycle=cycle, unit=unit, vehicle=vehicle)
    assert application.application_status == application.ApplicationStatus.APPROVED
    allocate_rotation_cycle(cycle=cycle, seed=7)

    allocation = ParkingRotationAllocation.objects.get(cycle=cycle, unit=unit)
    assert allocation.vehicle == vehicle
    response = client.get(reverse("vehicle_verify", kwargs={"token": vehicle.verification_token}))
    assert response.status_code == 200
    assert "activity-badge activity-active" in response.content.decode()
    assert policy.is_active is True


def test_open_parking_rule_allows_vehicle_without_sold_or_rotational_mapping():
    society = Society.objects.create(name="Open Rule Society")
    unit = _build_unit(society, "A Wing", "102")
    member = Member.objects.create(
        society=society,
        unit=unit,
        full_name="Owner 102",
        role=Member.MemberRole.OWNER,
    )
    ParkingVehicleLimit.create_new_version(
        society=society,
        member_role=ParkingVehicleLimit.MemberRole.OWNER,
        vehicle_type=ParkingVehicleLimit.VehicleType.CAR,
        max_allowed=2,
    )
    vehicle = Vehicle.objects.create(
        society=society,
        unit=unit,
        member=member,
        vehicle_number="MH-01-OP-0001",
        vehicle_type=Vehicle.VehicleType.CAR,
        valid_until=timezone.localdate() + timedelta(days=30),
    )
    vehicle.refresh_from_db()

    assert vehicle.rule_status == Vehicle.RuleStatus.ACTIVE
    assert has_any_parking_access(vehicle) is True


def test_rotation_allocation_activates_allocated_vehicle_and_reverts_after_cycle_end():
    society = Society.objects.create(name="Rotation Status Society")
    unit = _build_unit(society, "A Wing", "103")
    member = Member.objects.create(
        society=society,
        unit=unit,
        full_name="Owner 103",
        role=Member.MemberRole.OWNER,
    )
    ParkingVehicleLimit.create_new_version(
        society=society,
        member_role=ParkingVehicleLimit.MemberRole.OWNER,
        vehicle_type=ParkingVehicleLimit.VehicleType.CAR,
        max_allowed=1,
    )
    v1 = Vehicle.objects.create(
        society=society,
        unit=unit,
        member=member,
        vehicle_number="MH-01-RS-0001",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    v2 = Vehicle.objects.create(
        society=society,
        unit=unit,
        member=member,
        vehicle_number="MH-01-RS-0002",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    v1.refresh_from_db()
    v2.refresh_from_db()
    assert v1.rule_status == Vehicle.RuleStatus.RULE_VIOLATION
    assert v2.rule_status == Vehicle.RuleStatus.ACTIVE

    ParkingSlot.objects.create(
        society=society,
        slot_number="RR1",
        is_rotational=True,
        parking_model=ParkingSlot.ParkingModel.COMMON,
    )
    ParkingRotationPolicy.create_new_version(
        society=society,
        policy_name="Status Rotation Policy",
        rotation_method=ParkingRotationPolicy.RotationMethod.QUEUE,
        vehicle_required_before_apply=True,
        max_total_parking_per_unit=5,
    )
    cycle = generate_next_rotation_cycle(society_id=society.id)
    assert cycle is not None
    submit_rotation_application(cycle=cycle, unit=unit, vehicle=v1)
    allocate_rotation_cycle(cycle=cycle)

    v1.refresh_from_db()
    assert v1.rule_status == Vehicle.RuleStatus.ACTIVE
    assert v1.is_active is True

    ParkingRotationCycle.objects.filter(pk=cycle.pk).update(
        cycle_end_date=timezone.localdate() - timedelta(days=1),
        allocation_status=ParkingRotationCycle.AllocationStatus.ACTIVE,
    )
    completed_count = auto_complete_due_rotation_cycles(society_id=society.id)
    assert completed_count == 1
    cycle.refresh_from_db()
    v1.refresh_from_db()

    assert cycle.allocation_status == ParkingRotationCycle.AllocationStatus.COMPLETED
    assert v1.rule_status == Vehicle.RuleStatus.RULE_VIOLATION
