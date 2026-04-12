from housing_accounting.selection import get_selected_scope
from societies.utils import get_user_membership


class SocietyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.current_society = None
        request.current_membership = None

        if getattr(request, "user", None) and request.user.is_authenticated:
            society, _ = get_selected_scope(request, persist=True)
            request.current_society = society
            request.current_membership = get_user_membership(request.user, society)

        return self.get_response(request)
