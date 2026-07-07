from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class VehicleCategory(models.Model):
    """Configurable vehicle type (separate from ``parking.Vehicle``).

    Tracks visitor/delivery/commercial vehicles crossing the gate, not
    resident-owned vehicles (those live in the ``parking`` app).
    """

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="vehicle_categories",
    )
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=30)
    is_commercial = models.BooleanField(default=False)
    is_delivery = models.BooleanField(default=False)
    is_emergency = models.BooleanField(default=False)
    is_electric = models.BooleanField(default=False)
    is_oversized = models.BooleanField(default=False)
    requires_approval_default = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Vehicle Category")
        verbose_name_plural = _("Vehicle Categories")
        ordering = ("society", "sort_order", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["society", "code"],
                condition=models.Q(is_active=True),
                name="uniq_vehicle_cat_code_per_society",
            ),
        ]
        indexes = [
            models.Index(fields=["society", "is_active"], name="vehcat_soc_act_idx"),
        ]

    def clean(self):
        if not self.code:
            raise ValidationError({"code": _("Code is required.")})
        if not self.code.isupper():
            raise ValidationError({"code": _("Code must be uppercase.")})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.code})"
