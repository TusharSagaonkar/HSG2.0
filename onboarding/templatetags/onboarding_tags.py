"""Template tags for the onboarding wizard templates."""
from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Return ``dictionary[key]`` safely from a template.

    Django templates cannot do ``dict[key]`` lookups when the key is a
    variable. This filter bridges that gap.
    """
    if dictionary is None:
        return None
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None


@register.filter
def status_badge_class(status):
    """Map a staging/validation status string to a Bootstrap badge class."""
    if not status:
        return "bg-secondary"
    status_upper = str(status).upper()
    if status_upper in ("APPROVED", "COMMITTED", "VALIDATED"):
        return "bg-success"
    if status_upper in ("UPLOADED",):
        return "bg-info"
    if status_upper in ("DELETED",):
        return "bg-danger"
    if status_upper in ("PENDING",):
        return "bg-warning text-dark"
    return "bg-secondary"
