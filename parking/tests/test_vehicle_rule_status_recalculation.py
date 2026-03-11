from datetime import timedelta
import importlib

import pytest
from django.utils import timezone

from housing.models import Member
from housing.models import Society
from housing.models import Structure
from housing.models import Unit
from housing.models import UnitOccupancy
from parking.models import ParkingVehicleLimit
from parking.models import Vehicle
from parking.services.recalculate_vehicle_rule_status import recalculate_vehicle_rule_status


pytestmark = pytest.mark.django_db


def _setup_owner_context():
    society = Society.objects.create(name="Rule Recalc Society")
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
    UnitOccupancy.objects.create(
        unit=unit_101,
        occupancy_type=UnitOccupancy.OccupancyType.OWNER,
        start_date=timezone.localdate() - timedelta(days=30),
        end_date=None,
    )
    UnitOccupancy.objects.create(
        unit=unit_102,
        occupancy_type=UnitOccupancy.OccupancyType.OWNER,
        start_date=timezone.localdate() - timedelta(days=30),
        end_date=None,
    )
    ParkingVehicleLimit.create_new_version(
        society=society,
        member_role=ParkingVehicleLimit.MemberRole.OWNER,
        vehicle_type=ParkingVehicleLimit.VehicleType.CAR,
        max_allowed=1,
    )
    return society, unit_101, unit_102, owner_101


def test_rule_violation_reactivates_after_limit_increase_when_eligible():
    society, unit_101, _, owner_101 = _setup_owner_context()
    old_vehicle = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-AA-1101",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    new_vehicle = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-AA-1102",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    old_vehicle.refresh_from_db()
    new_vehicle.refresh_from_db()
    assert old_vehicle.rule_status == Vehicle.RuleStatus.RULE_VIOLATION
    assert new_vehicle.rule_status == Vehicle.RuleStatus.ACTIVE

    ParkingVehicleLimit.create_new_version(
        society=society,
        member_role=ParkingVehicleLimit.MemberRole.OWNER,
        vehicle_type=ParkingVehicleLimit.VehicleType.CAR,
        max_allowed=2,
    )
    old_vehicle.refresh_from_db()
    new_vehicle.refresh_from_db()
    assert old_vehicle.rule_status == Vehicle.RuleStatus.ACTIVE
    assert new_vehicle.rule_status == Vehicle.RuleStatus.ACTIVE


def test_resident_mismatch_when_member_moves_unit():
    society, unit_101, unit_102, owner_101 = _setup_owner_context()
    vehicle = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-BB-1101",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    owner_101.unit = unit_102
    owner_101.save(update_fields=["unit"])
    vehicle.refresh_from_db()

    assert vehicle.rule_status == Vehicle.RuleStatus.RESIDENT_MISMATCH
    assert vehicle.is_active is False


def test_resident_mismatch_when_member_inactive():
    society, unit_101, _, owner_101 = _setup_owner_context()
    vehicle = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-BB-1102",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    owner_101.status = Member.MemberStatus.INACTIVE
    owner_101.save(update_fields=["status"])
    vehicle.refresh_from_db()

    assert vehicle.rule_status == Vehicle.RuleStatus.RESIDENT_MISMATCH
    assert vehicle.is_active is False


def test_unit_vacant_status_when_active_occupancy_is_vacant():
    society, unit_101, _, owner_101 = _setup_owner_context()
    vehicle = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-CC-1101",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    UnitOccupancy.objects.filter(unit=unit_101, end_date__isnull=True).update(
        end_date=timezone.localdate() - timedelta(days=1)
    )
    UnitOccupancy.objects.create(
        unit=unit_101,
        occupancy_type=UnitOccupancy.OccupancyType.VACANT,
        start_date=timezone.localdate(),
        end_date=None,
    )
    vehicle.refresh_from_db()

    assert vehicle.rule_status == Vehicle.RuleStatus.UNIT_VACANT
    assert vehicle.is_active is False


def test_permit_expired_status_from_vehicle_valid_until():
    society, unit_101, _, owner_101 = _setup_owner_context()
    vehicle = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-DD-1101",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
        valid_until=timezone.localdate() - timedelta(days=1),
    )
    recalculate_vehicle_rule_status(society.id)
    vehicle.refresh_from_db()

    assert vehicle.rule_status == Vehicle.RuleStatus.PERMIT_EXPIRED
    assert vehicle.is_active is False


def test_vehicle_inactive_status_when_flag_is_false():
    society, unit_101, _, owner_101 = _setup_owner_context()
    vehicle = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-EE-1101",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    Vehicle.objects.filter(pk=vehicle.pk).update(
        is_active=False,
        rule_status=Vehicle.RuleStatus.ACTIVE,
        valid_until=None,
        deactivated_at=None,
    )
    recalculate_vehicle_rule_status(society.id)
    vehicle.refresh_from_db()

    assert vehicle.rule_status == Vehicle.RuleStatus.VEHICLE_INACTIVE
    assert vehicle.is_active is False


def test_data_inconsistent_when_member_missing():
    society, unit_101, _, owner_101 = _setup_owner_context()
    vehicle = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-FF-1101",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    Vehicle.objects.filter(pk=vehicle.pk).update(member=None)
    recalculate_vehicle_rule_status(society.id)
    vehicle.refresh_from_db()

    assert vehicle.rule_status == Vehicle.RuleStatus.DATA_INCONSISTENT
    assert vehicle.is_active is False


def test_data_inconsistent_when_society_unit_mismatch():
    society, unit_101, _, owner_101 = _setup_owner_context()
    other_society = Society.objects.create(name="Other")
    other_structure = Structure.objects.create(
        society=other_society,
        structure_type=Structure.StructureType.BUILDING,
        name="B",
    )
    other_unit = Unit.objects.create(
        structure=other_structure,
        unit_type=Unit.UnitType.FLAT,
        identifier="201",
    )
    vehicle = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-FF-1102",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    Vehicle.objects.filter(pk=vehicle.pk).update(unit=other_unit)
    recalculate_vehicle_rule_status(society.id)
    vehicle.refresh_from_db()

    assert vehicle.rule_status == Vehicle.RuleStatus.DATA_INCONSISTENT
    assert vehicle.is_active is False


def test_admin_blocked_from_permit_hook(monkeypatch):
    society, unit_101, _, owner_101 = _setup_owner_context()
    vehicle = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-GG-1101",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )

    service = importlib.import_module("parking.services.recalculate_vehicle_rule_status")

    monkeypatch.setattr(service, "_get_parking_permit_model", lambda: object())
    monkeypatch.setattr(
        service,
        "_permit_status_for_vehicle",
        lambda vehicle, permit_model: Vehicle.RuleStatus.ADMIN_BLOCKED,
    )
    recalculate_vehicle_rule_status(society.id)
    vehicle.refresh_from_db()

    assert vehicle.rule_status == Vehicle.RuleStatus.ADMIN_BLOCKED
    assert vehicle.is_active is False


def test_non_rule_violation_not_auto_fixed_on_rule_increase():
    society, unit_101, unit_102, owner_101 = _setup_owner_context()
    vehicle = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-HH-1101",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    owner_101.unit = unit_102
    owner_101.save(update_fields=["unit"])

    ParkingVehicleLimit.create_new_version(
        society=society,
        member_role=ParkingVehicleLimit.MemberRole.OWNER,
        vehicle_type=ParkingVehicleLimit.VehicleType.CAR,
        max_allowed=3,
    )
    vehicle.refresh_from_db()

    assert vehicle.rule_status == Vehicle.RuleStatus.RESIDENT_MISMATCH
    assert vehicle.is_active is False
