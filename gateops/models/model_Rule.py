from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Rule(models.Model):
    """A configurable gate-operations rule.

    Rules are evaluated by the :class:`RuleEngineService` in priority order
    (lower ``priority`` value = evaluated first). The first matching rule's
    actions win. A rule may be scoped to a specific visitor/vehicle/material
    category and/or gate; a ``None`` scope means "applies to all".

    Soft-delete follows the established ``is_active`` + ``deleted_at`` pattern.
    The conditional unique constraint on ``(society, code)`` only enforces
    uniqueness among active rules, so a soft-deleted code can be reused.
    """

    class AppliesOn(models.TextChoices):
        ENTRY = "entry", _("Entry")
        EXIT = "exit", _("Exit")
        BOTH = "both", _("Both")

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="gateops_rules",
    )
    name = models.CharField(max_length=200)
    code = models.CharField(
        max_length=50,
        help_text=_("Uppercase alphanumeric code, unique per society among active rules."),
    )
    description = models.TextField(blank=True)
    priority = models.IntegerField(
        default=100,
        help_text=_("Lower number = higher priority (evaluated first)."),
    )
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    # Scope filters — null means "applies to all".
    visitor_category = models.ForeignKey(
        "gateops.VisitorCategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rules",
    )
    vehicle_category = models.ForeignKey(
        "gateops.VehicleCategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rules",
    )
    material_category = models.ForeignKey(
        "gateops.MaterialCategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rules",
    )
    gate = models.ForeignKey(
        "gateops.Gate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rules",
    )

    valid_from = models.DateField(default=timezone.localdate)
    valid_until = models.DateField(null=True, blank=True)
    applies_on = models.CharField(
        max_length=10,
        choices=AppliesOn.choices,
        default=AppliesOn.BOTH,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gateops_rules_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Gate Operations Rule")
        verbose_name_plural = _("Gate Operations Rules")
        ordering = ("society", "priority", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["society", "code"],
                condition=models.Q(is_active=True),
                name="uniq_rule_code_per_society",
            ),
        ]
        indexes = [
            models.Index(fields=["society", "is_active"], name="rule_soc_act_idx"),
            models.Index(fields=["society", "priority"], name="rule_soc_prio_idx"),
            models.Index(fields=["society", "applies_on"], name="rule_soc_appl_idx"),
        ]

    def clean(self):
        # Code must be uppercase alphanumeric (allow underscores for readability).
        if not self.code:
            raise ValidationError({"code": _("Code is required.")})
        if not self.code.isupper() or not all(
            ch.isalnum() or ch == "_" for ch in self.code
        ):
            raise ValidationError(
                {"code": _("Code must be uppercase alphanumeric (underscores allowed).")}
            )
        # Validity window: valid_until must be strictly after valid_from.
        if self.valid_until is not None and self.valid_from is not None:
            if self.valid_until <= self.valid_from:
                raise ValidationError(
                    {"valid_until": _("valid_until must be after valid_from.")}
                )
        # Priority must be non-negative.
        if self.priority is None or self.priority < 0:
            raise ValidationError({"priority": _("priority must be >= 0.")})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} — {self.name} (priority={self.priority})"
