from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from members.models import Member
from members.models import UnitOccupancy
from members.models import UnitOwnership

User = get_user_model()


def _resolve_owner_user(member):
    if member.user_id:
        return member.user

    email = (member.email or "").strip()
    if not email:
        return None

    user = User.objects.filter(email__iexact=email).first()
    if user:
        return user

    user = User.objects.create(
        email=email,
        name=member.full_name or email,
        is_active=True,
    )
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return user


def sync_member_unit_lifecycle(member):
    """
    Apply ownership and occupancy side effects for a newly created member.

    Ownership is created for owner members and will auto-provision a user from
    the member email if needed.
    Occupancy follows the active member role:
    - owner additions create ownership and initialize an owner occupancy only if the unit is empty/vacant
    - tenant additions always replace the current active occupancy
    """

    if member.role not in {Member.MemberRole.OWNER, Member.MemberRole.TENANT}:
        return

    unit = member.unit
    start_date = member.start_date or timezone.localdate()
    owner_user = _resolve_owner_user(member)

    with transaction.atomic():
        if member.role == Member.MemberRole.OWNER and owner_user:
            _sync_ownership(unit=unit, owner_user=owner_user, start_date=start_date)

        _sync_occupancy(
            unit=unit,
            member=member,
            occupant_user=owner_user,
            start_date=start_date,
        )


def _sync_ownership(*, unit, owner_user, start_date):
    active_ownerships = (
        UnitOwnership.objects.select_for_update()
        .filter(unit=unit, end_date__isnull=True)
        .order_by("-start_date", "-id")
    )
    active_same_owner = active_ownerships.filter(owner=owner_user).first()
    if active_same_owner:
        if active_same_owner.start_date != start_date:
            active_same_owner.start_date = start_date
            active_same_owner.save(update_fields=["start_date"])
        return

    if UnitOwnership.objects.filter(
        unit=unit,
        owner=owner_user,
        end_date__isnull=True,
    ).exists():
        return

    UnitOwnership.objects.create(
        unit=unit,
        owner=owner_user,
        role=(
            UnitOwnership.OwnershipRole.SECONDARY
            if active_ownerships.exists()
            else UnitOwnership.OwnershipRole.PRIMARY
        ),
        start_date=start_date,
    )


def _sync_occupancy(*, unit, member, occupant_user, start_date):
    active_occupancy = (
        UnitOccupancy.objects.select_for_update()
        .filter(unit=unit, end_date__isnull=True)
        .order_by("-start_date", "-id")
        .first()
    )

    if member.role == Member.MemberRole.OWNER:
        if active_occupancy and active_occupancy.occupancy_type != UnitOccupancy.OccupancyType.VACANT:
            return

        if active_occupancy:
            active_occupancy.end_date = start_date - timedelta(days=1)
            active_occupancy.save(update_fields=["end_date"])

        UnitOccupancy.objects.create(
            unit=unit,
            occupant=occupant_user,
            occupancy_type=UnitOccupancy.OccupancyType.OWNER,
            start_date=start_date,
        )
        return

    # Tenant additions always replace the current active occupancy.
    if active_occupancy:
        active_occupancy.end_date = start_date - timedelta(days=1)
        active_occupancy.save(update_fields=["end_date"])

    UnitOccupancy.objects.create(
        unit=unit,
        occupant=occupant_user,
        occupancy_type=UnitOccupancy.OccupancyType.TENANT,
        start_date=start_date,
    )
