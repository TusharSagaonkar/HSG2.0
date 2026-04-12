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
