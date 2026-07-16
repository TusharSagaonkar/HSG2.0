from societies.roles import ROLE_HIERARCHY


def has_role_or_above(user_role, required_role):
    return ROLE_HIERARCHY.get(user_role, 0) >= ROLE_HIERARCHY.get(required_role, 0)


def can_assign_role(assigner_role, target_role):
    return ROLE_HIERARCHY.get(assigner_role, 0) > ROLE_HIERARCHY.get(target_role, 0)


def has_permission(user, permission_code, society=None):
    """Check if a user has a specific permission in a society.

    This is the canonical authorization primitive. Phase 1: delegates to
    PermissionRegistry default role-permission mapping. Phase 2: will check
    the RolePermission table for per-society overrides.

    Args:
        user: The authenticated user.
        permission_code: Dot-namespaced permission string (e.g., "accounting.voucher.post").
        society: The society to check access in. If None, returns False.

    Returns:
        bool: True if the user has the permission.
    """
    from societies.permissions_registry import PermissionRegistry
    from societies.utils import get_user_role

    if not getattr(user, "is_authenticated", False):
        return False

    # Super admin bypass (will be replaced by impersonation in Phase 3)
    if getattr(user, "is_super_admin", False) or getattr(user, "is_superuser", False):
        return True

    if society is None:
        return False

    role = get_user_role(user, society)
    if role is None:
        return False

    return PermissionRegistry.role_has_permission(role, permission_code)
