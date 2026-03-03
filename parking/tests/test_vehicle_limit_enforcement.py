import pytest

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


def test_adding_vehicle_deactivates_oldest_when_unit_role_limit_crossed():
    society, unit_101, _, owner_101, _, _ = _setup_base()
    ParkingVehicleLimit.objects.create(
        society=society,
        member_role=ParkingVehicleLimit.MemberRole.OWNER,
        vehicle_type=ParkingVehicleLimit.VehicleType.CAR,
        max_allowed=2,
    )

    v1 = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-AA-0001",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    v2 = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-AA-0002",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )
    v3 = Vehicle.objects.create(
        society=society,
        unit=unit_101,
        member=owner_101,
        vehicle_number="MH-01-AA-0003",
        vehicle_type=Vehicle.VehicleType.CAR,
        is_active=True,
    )

    v1.refresh_from_db()
    v2.refresh_from_db()
    v3.refresh_from_db()
    assert v1.is_active is False
    assert v1.deactivated_at is not None
    assert v2.is_active is True
    assert v3.is_active is True


def test_limit_enforcement_isolated_by_member_role_and_unit():
    society, unit_101, unit_102, owner_101, tenant_101, owner_102 = _setup_base()
    ParkingVehicleLimit.objects.create(
        society=society,
        member_role=ParkingVehicleLimit.MemberRole.OWNER,
        vehicle_type=ParkingVehicleLimit.VehicleType.CAR,
        max_allowed=1,
    )
    ParkingVehicleLimit.objects.create(
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

    assert owner_old.is_active is False
    assert owner_new.is_active is True
    assert tenant_vehicle.is_active is True
    assert other_unit_owner_vehicle.is_active is True
