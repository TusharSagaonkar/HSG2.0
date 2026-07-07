from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class GuardShiftAssignment(models.Model):
    """Per-day assignment of a guard to a shift at a gate.

    ``society`` is denormalized for query efficiency (avoids joins through
    guard/shift/gate when filtering assignments per tenant).
    """

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="guard_shift_assignments",
    )
    guard = models.ForeignKey(
        "gateops.SecurityGuard",
        on_delete=models.PROTECT,
        related_name="shift_assignments",
    )
    shift = models.ForeignKey(
        "gateops.GuardShift",
        on_delete=models.PROTECT,
        related_name="assignments",
    )
    gate = models.ForeignKey(
        "gateops.Gate",
        on_delete=models.PROTECT,
        related_name="shift_assignments",
    )
    date = models.DateField()
    check_in_at = models.DateTimeField(null=True, blank=True)
    check_out_at = models.DateTimeField(null=True, blank=True)
    handover_notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_guard_shift_assignments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Guard Shift Assignment")
        verbose_name_plural = _("Guard Shift Assignments")
        ordering = ("-date", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=["society", "guard", "date", "shift"],
                name="uniq_guard_shift_per_day",
            ),
        ]
        indexes = [
            models.Index(fields=["society", "date"], name="gsa_soc_date_idx"),
            models.Index(fields=["society", "guard"], name="gsa_soc_guard_idx"),
        ]

    def clean(self):
        if (
            self.check_in_at
            and self.check_out_at
            and self.check_out_at <= self.check_in_at
        ):
            raise ValidationError(
                {"check_out_at": _("Check-out must be after check-in.")}
            )
        if self.guard.society_id != self.society_id:
            raise ValidationError({"guard": _("Guard must belong to the same society.")})
        if self.shift.society_id != self.society_id:
            raise ValidationError({"shift": _("Shift must belong to the same society.")})
        if self.gate.society_id != self.society_id:
            raise ValidationError({"gate": _("Gate must belong to the same society.")})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.guard} @ {self.gate} on {self.date} ({self.shift})"
