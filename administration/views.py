from django.core.cache import cache
from django.db import models
from django.http import JsonResponse
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_GET
from django.utils.decorators import method_decorator
from django.views import View

from .models import Shortcut


@require_GET
@cache_page(60 * 5)  # Cache for 5 minutes
def get_shortcuts_api(request):
    """
    API endpoint to fetch shortcuts for the current user and page.
    
    Query parameters:
    - page: (optional) Page identifier for page-specific shortcuts
    
    Returns JSON list of shortcuts matching:
    1. is_active=True
    2. User role matches OR role is null (for all roles)
    3. Scope is GLOBAL OR (PAGE and page matches)
    4. Ordered by priority (highest first)
    """
    # Get user role (assuming User model has role field)
    user_role = None
    if request.user.is_authenticated:
        user_role = getattr(request.user, 'role', None)
    
    # Get page parameter from query string
    page = request.GET.get('page', None)
    
    # Build cache key
    cache_key = f'shortcuts:{user_role or "anonymous"}:{page or "global"}'
    
    # Try to get from cache
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return JsonResponse(cached_data, safe=False)
    
    # Build query
    shortcuts = Shortcut.objects.filter(is_active=True)
    
    # Role filtering: either role is null OR empty string (for all) or matches user role
    if user_role:
        shortcuts = shortcuts.filter(
            models.Q(role__isnull=True) | models.Q(role='') | models.Q(role=user_role)
        )
    else:
        # For anonymous users, only shortcuts with no role restriction
        shortcuts = shortcuts.filter(models.Q(role__isnull=True) | models.Q(role=''))
    
    # Scope filtering
    if page:
        shortcuts = shortcuts.filter(
            models.Q(scope=Shortcut.Scope.GLOBAL) |
            models.Q(scope=Shortcut.Scope.PAGE, page=page)
        )
    else:
        # If no page specified, only global shortcuts
        shortcuts = shortcuts.filter(scope=Shortcut.Scope.GLOBAL)
    
    # Order by priority (highest first), then by name
    shortcuts = shortcuts.order_by('-priority', 'name')
    
    # Prepare response data
    data = [
        {
            'key': shortcut.normalized_key,
            'type': shortcut.action_type,
            'value': shortcut.action_value,
            'name': shortcut.name,
            'scope': shortcut.scope,
            'page': shortcut.page,
        }
        for shortcut in shortcuts
    ]
    
    # Cache the result
    cache.set(cache_key, data, 60 * 5)  # 5 minutes cache
    
    return JsonResponse(data, safe=False)


class ShortcutAPIView(View):
    """
    Class-based view for shortcuts API (alternative to function view).
    """
    @method_decorator(cache_page(60 * 5))
    def get(self, request):
        return get_shortcuts_api(request)
