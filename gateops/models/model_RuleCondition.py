from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class RuleCondition(models.Model):
    """A single predicate within a :class:`Rule`.

    Conditions are evaluated in ``sort_order``. Each condition's
    ``logical_connector`` joins it to the *next* condition in the chain
    (the first condition has no incoming connector). The rule matches when the
    full chained boolean expression evaluates to ``True``.

    The ``value`` JSONField stores the comparison operand(s). For most
    operators this is a scalar (e.g. ``"DELIVERY"`` for ``eq``); for ``in`` /
    ``not_in`` it is a list; for ``between`` it is either a dict with
    ``{"start", "end"}`` or a two-element list ``[start, end]``. The boolean
    operators ``is_true`` / ``is_false`` ignore ``value``.
    """

    class ConditionField(models.TextChoices):
        VISITOR_TYPE = "visitor_type", _("Visitor Type")
        TIME = "time", _("Time of Day")
        DATE = "date", _("Date")
        TOWER = "tower", _("Tower")
        WING = "wing", _("Wing")
        FLAT = "flat", _("Flat")
        RESIDENT = "resident", _("Resident")
        VEHICLE = "vehicle", _("Vehicle")
        GUARD = "guard", _("Guard")
        GATE = "gate", _("Gate")
        MAX_VISITORS = "max_visitors", _("Max Visitors Inside")
        MAX_STAY = "max_stay", _("Maximum Stay Hours")
        HOLIDAY = "holiday", _("Holiday")
        BLACKLIST = "blacklist", _("Blacklist Status")
        CONTRACTOR_EXPIRY = "contractor_expiry", _("Contractor Expiry")
        PASS_VALID = "pass_valid", _("Pass Validity")
        VISITOR_CATEGORY = "visitor_category", _("Visitor Category")
        VEHICLE_CATEGORY = "vehicle_category", _("Vehicle Category")
        IS_EMERGENCY = "is_emergency", _("Is Emergency")
        IS_VIP = "is_vip", _("Is VIP")

    class Operator(models.TextChoices):
        EQ = "eq", _("Equals")
        NEQ = "neq", _("Not Equals")
        GT = "gt", _("Greater Than")
        GTE = "gte", _("Greater Than or Equal")
        LT = "lt", _("Less Than")
        LTE = "lte", _("Less Than or Equal")
        IN = "in", _("In")
        NOT_IN = "not_in", _("Not In")
        CONTAINS = "contains", _("Contains")
        REGEX = "regex", _("Regex Match")
        BETWEEN = "between", _("Between")
        IS_TRUE = "is_true", _("Is True")
        IS_FALSE = "is_false", _("Is False")

    class LogicalConnector(models.TextChoices):
        AND = "and", _("AND")
        OR = "or", _("OR")

    rule = models.ForeignKey(
        "gateops.Rule",
        on_delete=models.CASCADE,
        related_name="conditions",
    )
    field = models.CharField(max_length=30, choices=ConditionField.choices)
    operator = models.CharField(max_length=20, choices=Operator.choices)
    value = models.JSONField(default=dict)
    logical_connector = models.CharField(
        max_length=5,
        choices=LogicalConnector.choices,
        default=LogicalConnector.AND,
        help_text=_("Connects this condition to the NEXT condition in sort order."),
    )
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Rule Condition")
        verbose_name_plural = _("Rule Conditions")
        ordering = ("rule", "sort_order")
        indexes = [
            models.Index(fields=["rule", "sort_order"], name="rulecond_sort_idx"),
        ]

    def clean(self):
        # Boolean operators do not require a value; all others do.
        boolean_operators = {
            self.Operator.IS_TRUE,
            self.Operator.IS_FALSE,
        }
        if self.operator not in boolean_operators:
            if self.value in (None, "", {}, []):
                raise ValidationError(
                    {"value": _("value is required for this operator.")}
                )
        # BETWEEN requires a start/end structure.
        if self.operator == self.Operator.BETWEEN:
            if isinstance(self.value, dict):
                if "start" not in self.value or "end" not in self.value:
                    raise ValidationError(
                        {"value": _("between value must have 'start' and 'end' keys.")}
                    )
            elif isinstance(self.value, (list, tuple)):
                if len(self.value) != 2:
                    raise ValidationError(
                        {"value": _("between value list must have exactly 2 elements.")}
                    )
            else:
                raise ValidationError(
                    {"value": _("between value must be a dict or a 2-element list.")}
                )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.field} {self.operator} {self.value!r}"
