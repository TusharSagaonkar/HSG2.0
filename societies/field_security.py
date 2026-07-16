"""Field-level security utilities.

Provides functions to determine which fields a user can see based on their role
and the FieldVisibility rules. Used at the serialization/template layer to strip
inaccessible fields before rendering.
"""

from societies.models import FieldVisibility
from societies.utils import get_user_role


def visible_fields(model_instance, user, society=None):
    """Return the set of field names visible to the user for this model instance.

    Args:
        model_instance: A Django model instance.
        user: The authenticated user.
        society: The current society context.

    Returns:
        set: Set of field names that are visible to the user.
    """
    model_label = model_instance._meta.label
    all_fields = {
        f.name for f in model_instance._meta.get_fields() if hasattr(f, "name")
    }

    role = get_user_role(user, society) if society else None
    if role is None:
        role = "*"

    # Start with all fields visible (default-allow for field visibility)
    visible = set(all_fields)

    # Apply global rules (society=None)
    global_rules = FieldVisibility.objects.filter(
        model_name=model_label,
        society__isnull=True,
    )

    # Apply society-specific rules (override global)
    society_rules = FieldVisibility.objects.none()
    if society:
        society_rules = FieldVisibility.objects.filter(
            model_name=model_label,
            society=society,
        )

    # Process rules: most specific wins (society-specific > global)
    # For each (field_name, role) pair, the most specific rule applies
    rules_by_field = {}
    for rule in list(global_rules) + list(society_rules):
        key = (rule.field_name, rule.role)
        # Society-specific rules override global
        if rule.society_id is not None:
            rules_by_field[key] = rule
        elif key not in rules_by_field:
            rules_by_field[key] = rule

    # Apply rules
    for (field_name, rule_role), rule in rules_by_field.items():
        # A rule applies to a user only when it targets their exact role or the
        # wildcard "*". Higher roles do NOT inherit restrictions targeting lower
        # roles (e.g. a field hidden from "viewer" remains visible to "owner"),
        # so a rule for "viewer" never cascades upward to "owner"/"admin".
        applies = rule_role in ("*", role)
        if applies:
            if not rule.visible:
                visible.discard(field_name)
            else:
                visible.add(field_name)

    return visible


def hidden_fields(model_instance, user, society=None):
    """Return the set of field names hidden from the user for this model instance."""
    all_fields = {
        f.name for f in model_instance._meta.get_fields() if hasattr(f, "name")
    }
    return all_fields - visible_fields(model_instance, user, society)


def filter_dict_by_visibility(data, model_instance, user, society=None):
    """Filter a dictionary to only include visible fields.

    Useful for serializing model instances for API responses or template context.
    """
    allowed = visible_fields(model_instance, user, society)
    return {k: v for k, v in data.items() if k in allowed}
