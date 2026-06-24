from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """
    Return a dictionary item by key in templates.

    Falls back to an empty string when the lookup cannot be resolved.
    """
    if dictionary is None:
        return ""
    try:
        return dictionary.get(key, "")
    except AttributeError:
        try:
            return dictionary[key]
        except Exception:
            return ""

