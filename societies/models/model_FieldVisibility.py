from django.db import models
from django.utils.translation import gettext_lazy as _


class FieldVisibility(models.Model):
    """Defines which fields are visible to which roles.

    A rule with ``visible=True`` grants visibility; ``visible=False`` denies it.
    If no rule exists for a field, the field is visible by default (default-allow
    for field visibility; default-deny is enforced at the permission/action level).

    Sensitive fields (PAN, salary, bank details, API secrets) should have
    ``visible=False`` rules for roles that should not see them.
    """

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="field_visibility_rules",
        null=True,
        blank=True,
        help_text=_(
            "If null, this rule applies globally. If set, society-specific override.",
        ),
    )
    model_name = models.CharField(
        max_length=100,
        help_text=_("Full model label, e.g., 'housing.Member', 'accounting.Voucher'."),
    )
    field_name = models.CharField(
        max_length=100,
        help_text=_(
            "Field path, e.g., 'pan_number', 'salary_amount', 'bank_account'.",
        ),
    )
    role = models.CharField(
        max_length=20,
        help_text=_("Role this rule applies to. Use '*' for all roles."),
    )
    visible = models.BooleanField(
        default=True,
        help_text=_("True = field visible to this role; False = field hidden."),
    )

    class Meta:
        app_label = "societies"
        verbose_name = _("Field Visibility Rule")
        verbose_name_plural = _("Field Visibility Rules")
        unique_together = [("society", "model_name", "field_name", "role")]
        indexes = [
            models.Index(fields=["model_name", "field_name"]),
            models.Index(fields=["society", "model_name"]),
        ]

    def __str__(self):
        scope = f"Society:{self.society_id}" if self.society_id else "Global"
        state = "visible" if self.visible else "hidden"
        return f"[{scope}] {self.model_name}.{self.field_name} → {self.role}: {state}"
