from django.shortcuts import get_object_or_404
from django.shortcuts import render

from housing.models import Member
from housing.models import UnitOccupancy
from parking.models import ParkingPermit
from parking.models import Vehicle
from parking.services.parking_access import has_any_parking_access


def verify_vehicle(request, token):
    vehicle = get_object_or_404(
        Vehicle.objects.select_related(
            "society",
            "unit",
            "unit__structure",
            "member",
        ),
        verification_token=token,
    )

    active_occupancy = (
        UnitOccupancy.objects.filter(unit_id=vehicle.unit_id, end_date__isnull=True)
        .order_by("-start_date", "-id")
        .first()
    )
    occupancy_type = active_occupancy.occupancy_type if active_occupancy else None

    member_role = vehicle.member.role if vehicle.member else None
    if (
        member_role == Member.MemberRole.OWNER
        and occupancy_type == UnitOccupancy.OccupancyType.OWNER
    ):
        occupancy_status = "Owner living"
    elif member_role == Member.MemberRole.TENANT:
        occupancy_status = "Tenant"
    elif (
        member_role == Member.MemberRole.OWNER
        and occupancy_type != UnitOccupancy.OccupancyType.OWNER
    ):
        occupancy_status = "Not living"
    else:
        occupancy_status = "Not valid / not living"

    is_vehicle_valid = vehicle.is_valid() or has_any_parking_access(vehicle)
    status_color = "GREEN" if is_vehicle_valid else "RED"
    activity_status = "Active" if is_vehicle_valid else "Inactive"
    status_icon = "✅" if is_vehicle_valid else "⛔"
    activity_class = "activity-active" if is_vehicle_valid else "activity-inactive"

    if member_role == Member.MemberRole.OWNER:
        status_class = "status-owner"
        member_type = "Owner"
    elif member_role == Member.MemberRole.TENANT:
        status_class = "status-tenant"
        member_type = "Tenant"
    else:
        status_class = "status-notliving"
        member_type = "Unknown"

    if vehicle.vehicle_type == Vehicle.VehicleType.CAR:
        layout_class = "layout-car"
        vehicle_symbol = "🚗"
    elif vehicle.vehicle_type == Vehicle.VehicleType.BIKE:
        layout_class = "layout-bike"
        vehicle_symbol = "🏍"
    else:
        layout_class = "layout-other"
        vehicle_symbol = "🚘"

    flat_vehicles = []
    member_vehicles = []
    flat_active_count = 0
    member_active_count = 0
    same_category_active_flat_vehicles = []
    if not is_vehicle_valid:
        flat_vehicles = list(
            Vehicle.objects.select_related("member")
            .filter(unit_id=vehicle.unit_id)
            .order_by("vehicle_number")
        )
        flat_active_count = sum(1 for item in flat_vehicles if item.is_valid())
        same_category_active_flat_vehicles = [
            item
            for item in flat_vehicles
            if item.vehicle_type == vehicle.vehicle_type and item.is_valid()
        ]
        if vehicle.member_id:
            member_vehicles = list(
                Vehicle.objects.select_related("unit", "unit__structure")
                .filter(member_id=vehicle.member_id)
                .order_by("vehicle_number")
            )
            member_active_count = sum(1 for item in member_vehicles if item.is_valid())

    structure = vehicle.unit.structure
    context = {
        "society_name": vehicle.society.name,
        "building_name": structure.name,
        "flat_number": vehicle.unit.identifier,
        "owner_name": vehicle.member.full_name if vehicle.member else "Unassigned",
        "vehicle_number": vehicle.vehicle_number,
        "valid_until": vehicle.valid_until,
        "vehicle_type_label": vehicle.get_vehicle_type_display(),
        "vehicle_symbol": vehicle_symbol,
        "member_type": member_type,
        "layout_class": layout_class,
        "status_class": status_class,
        "status_color": status_color,
        "status_label": activity_status,
        "activity_class": activity_class,
        "occupancy_status": occupancy_status,
        "status_icon": status_icon,
        "is_active": is_vehicle_valid,
        "show_related_vehicles": not is_vehicle_valid,
        "flat_vehicles": flat_vehicles,
        "member_vehicles": member_vehicles,
        "flat_active_count": flat_active_count,
        "member_active_count": member_active_count,
        "flat_total_count": len(flat_vehicles),
        "member_total_count": len(member_vehicles),
        "same_category_active_flat_vehicles": same_category_active_flat_vehicles,
        "same_category_active_flat_count": len(same_category_active_flat_vehicles),
    }
    return render(request, "parking/vehicle_verify.html", context)


def verify_parking_permit(request, qr_token):
    permit = get_object_or_404(
        ParkingPermit.objects.select_related(
            "society",
            "unit",
            "unit__structure",
            "slot",
            "vehicle",
            "vehicle__member",
        ),
        qr_token=qr_token,
    )

    is_permit_valid = (
        permit.status == ParkingPermit.Status.ACTIVE
        and permit.vehicle.is_active
        and permit.slot.owned_unit_id == permit.unit_id
    )
    member_role = permit.vehicle.member.role if permit.vehicle.member else None
    if member_role == Member.MemberRole.OWNER:
        status_class = "status-owner"
        member_type = "Owner"
    elif member_role == Member.MemberRole.TENANT:
        status_class = "status-tenant"
        member_type = "Tenant"
    else:
        status_class = "status-notliving"
        member_type = "Unknown"

    if permit.vehicle.vehicle_type == Vehicle.VehicleType.CAR:
        layout_class = "layout-car"
        vehicle_symbol = "🚗"
    elif permit.vehicle.vehicle_type == Vehicle.VehicleType.BIKE:
        layout_class = "layout-bike"
        vehicle_symbol = "🏍"
    else:
        layout_class = "layout-other"
        vehicle_symbol = "🚘"

    status_label = "Active" if is_permit_valid else "Inactive"
    status_icon = "✅" if is_permit_valid else "⛔"
    activity_class = "activity-active" if is_permit_valid else "activity-inactive"

    if is_permit_valid:
        occupancy_status = f"Permit {permit.get_status_display()}"
    elif permit.status != ParkingPermit.Status.ACTIVE:
        occupancy_status = f"Permit {permit.get_status_display()}"
    elif not permit.vehicle.is_active:
        occupancy_status = "Vehicle Inactive"
    else:
        occupancy_status = "Slot Ownership Mismatch"

    context = {
        "society_name": permit.society.name,
        "building_name": permit.unit.structure.name,
        "flat_number": permit.unit.identifier,
        "owner_name": permit.vehicle.member.full_name if permit.vehicle.member else "Unassigned",
        "vehicle_number": permit.vehicle.vehicle_number,
        "valid_until": permit.expires_at.date() if permit.expires_at else permit.vehicle.valid_until,
        "vehicle_type_label": permit.vehicle.get_vehicle_type_display(),
        "vehicle_symbol": vehicle_symbol,
        "member_type": member_type,
        "layout_class": layout_class,
        "status_class": status_class,
        "status_color": "GREEN" if is_permit_valid else "RED",
        "status_label": status_label,
        "activity_class": activity_class,
        "occupancy_status": occupancy_status,
        "status_icon": status_icon,
        "is_active": is_permit_valid,
        "show_related_vehicles": False,
        "flat_vehicles": [],
        "member_vehicles": [],
        "flat_active_count": 0,
        "member_active_count": 0,
        "flat_total_count": 0,
        "member_total_count": 0,
        "same_category_active_flat_vehicles": [],
        "same_category_active_flat_count": 0,
    }
    return render(request, "parking/vehicle_verify.html", context)
