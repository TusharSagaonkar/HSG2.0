from societies.roles import ROLE_HIERARCHY


def has_role_or_above(user_role, required_role):
    return ROLE_HIERARCHY.get(user_role, 0) >= ROLE_HIERARCHY.get(required_role, 0)


def can_assign_role(assigner_role, target_role):
    return ROLE_HIERARCHY.get(assigner_role, 0) > ROLE_HIERARCHY.get(target_role, 0)
