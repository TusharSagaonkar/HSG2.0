from django import template

register = template.Library()


@register.filter
def attr_value(obj, field_name):
    display_method = getattr(obj, f"get_{field_name}_display", None)
    if callable(display_method):
        value = display_method()
    else:
        value = getattr(obj, field_name, "")
        if callable(value):
            value = value()
    if value is None:
        return ""
    return value


@register.filter
def get_bound_field(form, field_name):
    return form[field_name]
