from functools import wraps

from django.http import HttpResponseForbidden

from societies.permissions import has_role_or_above
from societies.services import user_has_society_access
from societies.utils import get_user_role


def society_access_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        society = getattr(request, "current_society", None)
        if getattr(request.user, "is_super_admin", False):
            return view_func(request, *args, **kwargs)
        if society is None:
            return HttpResponseForbidden("No society selected")
        if not user_has_society_access(request.user, society):
            return HttpResponseForbidden("Access denied")
        return view_func(request, *args, **kwargs)

    return wrapper


def role_required(min_role):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if getattr(request.user, "is_super_admin", False):
                return view_func(request, *args, **kwargs)

            society = getattr(request, "current_society", None)
            if society is None:
                return HttpResponseForbidden("No society selected")

            user_role = get_user_role(request.user, society)
            if not user_role:
                return HttpResponseForbidden("No membership")
            if not has_role_or_above(user_role, min_role):
                return HttpResponseForbidden("Insufficient permissions")
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator
