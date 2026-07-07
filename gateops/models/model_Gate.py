from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class Gate(models.Model):
    """Physical gate definition.

    A society may have multiple gates (main, service, emergency, pedestrian,
    vehicle). Each gate has a short unique code within the society.
    """

    class GateType(models.TextChoices):
        MAIN = "main", _("Main")
        SERVICE = "service", _("Service")
        EMERGENCY = "emergency", _("Emergency")
        PEDESTRIAN = "pedestrian", _("Pedestrian")
        VEHICLE = "vehicle", _("Vehicle")

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="gates",
    )
    name = models.CharField(max_length=100)
    code = models.CharField(
        max_length=20,
        help_text=_("Short uppercase alphanumeric code, e.g. 'MAIN', 'SERV'."),
    )
    gate_type = models.CharField(
        max_length=20,
        choices=GateType.choices,
        default=GateType.MAIN,
    )
    gps_lat = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    gps_lng = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Gate")
        verbose_name_plural = _("Gates")
        ordering = ("society", "code")
        constraints = [
            models.UniqueConstraint(
                fields=["society", "code"],
                condition=models.Q(is_active=True),
                name="uniq_gate_code_per_society",
            ),
        ]
        indexes = [
            models.Index(fields=["society", "is_active"], name="gate_soc_act_idx"),
            models.Index(fields=["society", "gate_type"], name="gate_soc_type_idx"),
        ]

    def clean(self):
        if not self.code:
            raise ValidationError({"code": _("Gate code is required.")})
        if not self.code.isalnum():
            raise ValidationError({"code": _("Gate code must be alphanumeric.")})
        # Enforce uppercase — convert before validation so callers passing
        # lowercase codes are normalized rather than rejected.
        self.code = self.code.upper()
        if self.gps_lat is not None and not (-90 <= float(self.gps_lat) <= 90):
            raise ValidationError({"gps_lat": _("Latitude must be between -90 and 90.")})
        if self.gps_lng is not None and not (-180 <= float(self.gps_lng) <= 180):
            raise ValidationError({"gps_lng": _("Longitude must be between -180 and 180.")})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.code})"
