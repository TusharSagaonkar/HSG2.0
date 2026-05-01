"""
Template tags for navigation helpers.
Simplifies active state detection in sidebar navigation.
"""

from django import template
from django.urls import resolve

register = template.Library()


@register.simple_tag
def nav_active(request, namespace, *url_names):
    """
    Check if current page matches given namespace and url names.
    
    Usage:
        {% load nav_helpers %}
        <li class="nav-item {% nav_active request 'housing' 'society-list' 'society-detail' %}">
    
    Returns 'active' if match, empty string otherwise.
    """
    if not request or not hasattr(request, 'resolver_match'):
        return ''
    
    current = request.resolver_match
    if not current:
        return ''
    
    # Check namespace match
    if current.namespace != namespace:
        return ''
    
    # Check URL name match
    if current.url_name in url_names:
        return 'active'
    
    return ''


@register.simple_tag
def nav_active_exact(request, *url_names):
    """
    Check if current page exactly matches given URL names (any namespace).
    
    Usage:
        {% nav_active_exact request 'home' 'account_login' %}
    """
    if not request or not hasattr(request, 'resolver_match'):
        return ''
    
    current = request.resolver_match
    if not current:
        return ''
    
    if current.url_name in url_names:
        return 'active'
    
    return ''


@register.simple_tag
def nav_active_startswith(request, namespace, prefix):
    """
    Check if current URL name starts with given prefix.
    Useful for submenu sections.
    
    Usage:
        {% nav_active_startswith request 'accounting' 'voucher' %}
    """
    if not request or not hasattr(request, 'resolver_match'):
        return ''
    
    current = request.resolver_match
    if not current:
        return ''
    
    if current.namespace == namespace and current.url_name.startswith(prefix):
        return 'active'
    
    return ''


@register.inclusion_tag('housing_accounting/tags/breadcrumb.html')
def breadcrumb(*items):
    """
    Render breadcrumb navigation.
    
    Usage:
        {% breadcrumb 'Home' 'housing:society-list' 'Societies' %}
    """
    breadcrumb_items = []
    for i in range(0, len(items), 2):
        if i + 1 < len(items):
            breadcrumb_items.append({
                'label': items[i],
                'url': items[i + 1] if i + 1 < len(items) else None,
                'active': i + 2 >= len(items)
            })
    
    return {'items': breadcrumb_items}
