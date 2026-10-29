from housing_accounting.selection import get_selected_scope
from societies.utils import get_user_membership


class SocietyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.current_society = None
        request.current_membership = None
        from societies.managers import _current_tenant

        society = None
        if getattr(request, "user", None) and request.user.is_authenticated:
            society, _ = get_selected_scope(request, persist=True)
            request.current_society = society
            request.current_membership = get_user_membership(request.user, society)

        # Context variables are reused across requests in a worker. Always set
        # and reset the scope so a request with no selected society cannot
        # inherit the previous request's tenant.
        token = _current_tenant.set(society)
        try:
            return self.get_response(request)
        finally:
            _current_tenant.reset(token)
