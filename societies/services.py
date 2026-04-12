from django.core.exceptions import PermissionDenied
from django.db import transaction

from housing_accounting.users.models import User
from societies.models import Membership
from societies.models import Society
from societies.permissions import can_assign_role
from societies.roles import ROLE_OWNER


def get_accessible_societies_qs(user):
    queryset = Society.objects.order_by("name")
    if not getattr(user, "is_authenticated", False):
        return queryset.none()
    if getattr(user, "is_super_admin", False) or getattr(user, "is_superuser", False):
        return queryset
    return queryset.filter(
        memberships__user=user,
        memberships__is_active=True,
    ).distinct()


def user_has_society_access(user, society):
    if not getattr(user, "is_authenticated", False) or society is None:
        return False
    if getattr(user, "is_super_admin", False) or getattr(user, "is_superuser", False):
        return True
    return Membership.objects.filter(
        user=user,
        society=society,
        is_active=True,
    ).exists()


@transaction.atomic
def create_society(*, user, name, registration_number="", address=""):
    society = Society.objects.create(
        name=name,
        registration_number=registration_number,
        address=address,
        created_by=user,
    )
    Membership.objects.create(
        user=user,
        society=society,
        role=ROLE_OWNER,
        invited_by=user,
        is_active=True,
    )
    return society


@transaction.atomic
def create_user_by_admin(*, admin_user, society, email, password, role, **extra_fields):
    from societies.utils import get_user_role

    assigner_role = get_user_role(admin_user, society)
    if not (
        getattr(admin_user, "is_super_admin", False)
        or getattr(admin_user, "is_superuser", False)
    ) and not can_assign_role(
        assigner_role,
        role,
    ):
        raise PermissionDenied("Cannot assign this role.")

    user = User.objects.create_user(email=email, password=password, **extra_fields)
    Membership.objects.create(
        user=user,
        society=society,
        role=role,
        invited_by=admin_user,
        is_active=True,
    )
    return user


@transaction.atomic
def transfer_ownership(*, current_owner, new_owner, society):
    from societies.utils import is_owner

    if not is_owner(current_owner, society):
        raise PermissionDenied("Only owner can transfer ownership.")

    old_membership = Membership.objects.get(user=current_owner, society=society)
    new_membership = Membership.objects.get(
        user=new_owner,
        society=society,
        is_active=True,
    )

    old_membership.role = Membership.Role.ADMIN
    new_membership.role = Membership.Role.OWNER
    old_membership.save(update_fields=["role"])
    new_membership.save(update_fields=["role"])
