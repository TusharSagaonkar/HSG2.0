from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class PassType(models.Model):
    """Configurable pass type defining validation method and duration.

    A pass type is a template (e.g. "QR Pass", "OTP Pass", "One Time Pass").
    Concrete passes are issued in Phase 5 against a person/gate event.
    """

    class ValidationMethod(models.TextChoices):
        QR = "qr", _("QR Code")
        OTP = "otp", _("OTP")
        PIN = "pin", _("PIN")
        DIGITAL = "digital", _("Digital Pass")
        NONE = "none", _("None")

    class DurationType(models.TextChoices):
        ONE_TIME = "one_time", _("One Time")
        DAILY = "daily", _("Daily")
        WEEKLY = "weekly", _("Weekly")
        MONTHLY = "monthly", _("Monthly")
        ANNUAL = "annual", _("Annual")
        RECURRING = "recurring", _("Recurring")

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="pass_types",
    )
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=30)
    validation_method = models.CharField(
        max_length=20,
        choices=ValidationMethod.choices,
        default=ValidationMethod.QR,
    )
    duration_type = models.CharField(
        max_length=20,
        choices=DurationType.choices,
        default=DurationType.ONE_TIME,
    )
    default_validity_hours = models.PositiveIntegerField(
        default=24,
        help_text=_("Default validity window in hours."),
    )
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Pass Type")
        verbose_name_plural = _("Pass Types")
        ordering = ("society", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["society", "code"],
                condition=models.Q(is_active=True),
                name="uniq_pass_type_code_per_society",
            ),
        ]
        indexes = [
            models.Index(fields=["society", "is_active"], name="passtype_soc_act_idx"),
        ]

    def clean(self):
        if not self.code:
            raise ValidationError({"code": _("Code is required.")})
        if not self.code.isupper():
            raise ValidationError({"code": _("Code must be uppercase.")})
        if (
            self.duration_type != self.DurationType.ONE_TIME
            and self.default_validity_hours is not None
            and self.default_validity_hours <= 0
        ):
            raise ValidationError(
                {"default_validity_hours": _("Validity hours must be > 0 for non one-time passes.")}
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.code})"
