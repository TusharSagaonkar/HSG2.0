import pytest
from django.utils import timezone

from housing.models import Member
from housing.models import Society
from housing.models import Structure
from housing.models import Unit
from parking.models import ParkingVehicleLimit
from parking.models import Vehicle


pytestmark = pytest.mark.django_db


def _setup_base():
    society = Society.objects.create(name="Limit Society")
    structure = Structure.objects.create(
        society=society,
        structure_type=Structure.StructureType.BUILDING,
        name="A",
    )
    unit_101 = Unit.objects.create(
        structure=structure,
        unit_type=Unit.UnitType.FLAT,
        identifier="101",
    )
    unit_102 = Unit.objects.create(
        structure=structure,
        unit_type=Unit.UnitType.FLAT,
        identifier="102",
    )
    owner_101 = Member.objects.create(
        society=society,
        unit=unit_101,
        full_name="Owner 101",
        role=Member.MemberRole.OWNER,
    )
    tenant_101 = Member.objects.create(
        society=society,
        unit=unit_101,
        full_name="Tenant 101",
        role=Member.MemberRole.TENANT,
    )
    owner_102 = Member.objects.create(
        society=society,
        unit=unit_102,
        full_name="Owner 102",
        role=Member.MemberRole.OWNER,
    )
    return society, unit_101, unit_102, owner_101, tenant_101, owner_102


def _owner_car_statuses(unit):
    return list(
        Vehicle.objects.filter(
            unit=unit,
            member__role=Member.MemberRole.OWNER,
            vehicle_type=Vehicle.VehicleType.CAR,
        )
        .order_by("created_at", "id")
        .values_list("rule_status", flat=True)
    )


def test_rule_status_marks_overflow_for_owner_cars():
    society, unit_101, _, owner_101, _, _ = _setup_base()
    ParkingVehicleLimit.create_new_version(
        society=society,
        member_role=ParkingVehicleLimit.MemberRole.OWNER,
        vehicle_type=ParkingVehicleLimit.VehicleType.CAR,
        max_allowed=2,
    )

    Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-AA-0001",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-AA-0002",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-AA-0003",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )

    assert _owner_car_statuses(unit_101) == [
        Vehicle.RuleStatus.RULE_VIOLATION,
        Vehicle.RuleStatus.ACTIVE,
        Vehicle.RuleStatus.ACTIVE,
    ]


def test_limit_enforcement_isolated_by_member_role_and_unit():
    society, unit_101, unit_102, owner_101, tenant_101, owner_102 = _setup_base()
    ParkingVehicleLimit.create_new_version(
        society=society,
        member_role=ParkingVehicleLimit.MemberRole.OWNER,
        vehicle_type=ParkingVehicleLimit.VehicleType.CAR,
        max_allowed=1,
    )
    ParkingVehicleLimit.create_new_version(
        society=society,
        member_role=ParkingVehicleLimit.MemberRole.TENANT,
        vehicle_type=ParkingVehicleLimit.VehicleType.CAR,
        max_allowed=2,
    )

    owner_old = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-BB-0001",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    owner_new = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-BB-0002",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    tenant_vehicle = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=tenant_101,
        vehicle_number="MH-01-BB-0003",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    other_unit_owner_vehicle = Vehicle.objects.create(
        society=society,
        unit=unit_102,
        member=owner_102,
        vehicle_number="MH-01-BB-0004",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )

    owner_old.refresh_from_db()
    owner_new.refresh_from_db()
    tenant_vehicle.refresh_from_db()
    other_unit_owner_vehicle.refresh_from_db()

    assert owner_old.rule_status == Vehicle.RuleStatus.RULE_VIOLATION
    assert owner_new.rule_status == Vehicle.RuleStatus.ACTIVE
    assert tenant_vehicle.rule_status == Vehicle.RuleStatus.ACTIVE
    assert other_unit_owner_vehicle.rule_status == Vehicle.RuleStatus.ACTIVE


def test_rule_change_recalculates_vehicle_status_and_preserves_history():
    society, unit_101, _, owner_101, _, _ = _setup_base()

    first_rule = ParkingVehicleLimit.create_new_version(
        society=society,
        member_role=ParkingVehicleLimit.MemberRole.OWNER,
        vehicle_type=ParkingVehicleLimit.VehicleType.CAR,
        max_allowed=2,
        changed_reason="Initial policy",
    )

    Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-CC-0001",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-CC-0002",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-CC-0003",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )

    assert _owner_car_statuses(unit_101) == [
        Vehicle.RuleStatus.RULE_VIOLATION,
        Vehicle.RuleStatus.ACTIVE,
        Vehicle.RuleStatus.ACTIVE,
    ]

    second_rule = ParkingVehicleLimit.create_new_version(
        society=society,
        member_role=ParkingVehicleLimit.MemberRole.OWNER,
        vehicle_type=ParkingVehicleLimit.VehicleType.CAR,
        max_allowed=1,
        changed_reason="Reduce slots",
    )

    first_rule.refresh_from_db()
    assert first_rule.status == ParkingVehicleLimit.Status.INACTIVE
    assert first_rule.end_date is not None
    assert second_rule.status == ParkingVehicleLimit.Status.ACTIVE

    assert _owner_car_statuses(unit_101) == [
        Vehicle.RuleStatus.RULE_VIOLATION,
        Vehicle.RuleStatus.RULE_VIOLATION,
        Vehicle.RuleStatus.ACTIVE,
    ]

    third_rule = ParkingVehicleLimit.create_new_version(
        society=society,
        member_role=ParkingVehicleLimit.MemberRole.OWNER,
        vehicle_type=ParkingVehicleLimit.VehicleType.CAR,
        max_allowed=3,
        changed_reason="Increase slots",
    )

    second_rule.refresh_from_db()
    third_rule.refresh_from_db()
    assert second_rule.status == ParkingVehicleLimit.Status.INACTIVE
    assert third_rule.status == ParkingVehicleLimit.Status.ACTIVE

    assert _owner_car_statuses(unit_101) == [
        Vehicle.RuleStatus.ACTIVE,
        Vehicle.RuleStatus.ACTIVE,
        Vehicle.RuleStatus.ACTIVE,
    ]

    owner_cars = list(
        Vehicle.objects.filter(
            unit=unit_101,
            member__role=Member.MemberRole.OWNER,
            vehicle_type=Vehicle.VehicleType.CAR,
        ).order_by("created_at", "id")
    )
    assert [v.is_active for v in owner_cars] == [True, True, True]
    assert [v.valid_until for v in owner_cars] == [None, None, None]


def test_member_leaving_unit_marks_vehicle_inactive_automatically():
    society, unit_101, unit_102, owner_101, _, _ = _setup_base()
    ParkingVehicleLimit.create_new_version(
        society=society,
        member_role=ParkingVehicleLimit.MemberRole.OWNER,
        vehicle_type=ParkingVehicleLimit.VehicleType.CAR,
        max_allowed=2,
    )
    vehicle = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-DD-0001",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    vehicle.refresh_from_db()
    assert vehicle.is_active is True
    assert vehicle.rule_status == Vehicle.RuleStatus.ACTIVE

    owner_101.unit = unit_102
    owner_101.save(update_fields=["unit"])

    vehicle.refresh_from_db()
    assert vehicle.is_active is False
    assert vehicle.rule_status == Vehicle.RuleStatus.RESIDENT_MISMATCH
    assert vehicle.valid_until is None


def test_rule_increase_reactivates_only_rule_violation_vehicle():
    society, unit_101, _, owner_101, _, _ = _setup_base()
    ParkingVehicleLimit.create_new_version(
        society=society,
        member_role=ParkingVehicleLimit.MemberRole.OWNER,
        vehicle_type=ParkingVehicleLimit.VehicleType.CAR,
        max_allowed=1,
    )
    v1 = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-EE-0001",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    v2 = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-EE-0002",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    v1.refresh_from_db()
    v2.refresh_from_db()
    assert v1.rule_status == Vehicle.RuleStatus.RULE_VIOLATION
    assert v2.rule_status == Vehicle.RuleStatus.ACTIVE

    ParkingVehicleLimit.create_new_version(
        society=society,
        member_role=ParkingVehicleLimit.MemberRole.OWNER,
        vehicle_type=ParkingVehicleLimit.VehicleType.CAR,
        max_allowed=2,
    )
    v1.refresh_from_db()
    v2.refresh_from_db()
    assert v1.rule_status == Vehicle.RuleStatus.ACTIVE
    assert v2.rule_status == Vehicle.RuleStatus.ACTIVE


def test_rule_increase_does_not_reactivate_resident_mismatch():
    society, unit_101, unit_102, owner_101, _, _ = _setup_base()
    ParkingVehicleLimit.create_new_version(
        society=society,
        member_role=ParkingVehicleLimit.MemberRole.OWNER,
        vehicle_type=ParkingVehicleLimit.VehicleType.CAR,
        max_allowed=1,
    )
    v1 = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-FF-0001",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    v2 = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-FF-0002",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    owner_101.unit = unit_102
    owner_101.save(update_fields=["unit"])

    ParkingVehicleLimit.create_new_version(
        society=society,
        member_role=ParkingVehicleLimit.MemberRole.OWNER,
        vehicle_type=ParkingVehicleLimit.VehicleType.CAR,
        max_allowed=2,
    )
    v1.refresh_from_db()
    v2.refresh_from_db()
    assert v1.rule_status == Vehicle.RuleStatus.RESIDENT_MISMATCH
    assert v2.rule_status == Vehicle.RuleStatus.RESIDENT_MISMATCH
    assert v1.is_active is False
    assert v2.is_active is False
