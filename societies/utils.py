from societies.models import Membership


def get_user_membership(user, society):
    if not getattr(user, "is_authenticated", False) or society is None:
        return None
    return (
        Membership.objects.filter(
            user=user,
            society=society,
            is_active=True,
        )
        .select_related("society", "user")
        .first()
    )


def get_user_role(user, society):
    membership = get_user_membership(user, society)
    return membership.role if membership else None


def is_owner(user, society):
    return get_user_role(user, society) == Membership.Role.OWNER


from contextlib import contextmanager
from django.shortcuts import get_object_or_404 as _django_get_object_or_404


def get_tenant_object_or_404(model, society, **kwargs):
    """Fetch a single object scoped to a specific society.

    This is the canonical way to fetch a single tenant-scoped object by PK.
    It prevents IDOR by ensuring the object belongs to the specified society.

    Usage:
        voucher = get_tenant_object_or_404(Voucher, request.current_society, pk=pk)

    Raises:
        Http404: If the object does not exist or does not belong to the society.
    """
    return _django_get_object_or_404(model, society=society, **kwargs)


@contextmanager
def tenant_context(society):
    """Set the current tenant context for background jobs and management commands.

    Usage:
        with tenant_context(society):
            vouchers = Voucher.objects.filter(is_deleted=False)
            # These are automatically scoped to `society` if using TenantManager

    This sets the context variable that TenantManager reads to auto-filter querysets.
    """
    from societies.managers import _current_tenant

    token = _current_tenant.set(society)
    try:
        yield
    finally:
        _current_tenant.reset(token)
