from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class GuardShift(models.Model):
    """Shift definition (e.g. Morning, Night).

    A shift is a reusable time window that can be assigned to guards on
    specific days at specific gates via ``GuardShiftAssignment``.
    """

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="guard_shifts",
    )
    name = models.CharField(max_length=100)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Guard Shift")
        verbose_name_plural = _("Guard Shifts")
        ordering = ("society", "start_time")
        indexes = [
            models.Index(fields=["society", "is_active"], name="gshift_soc_act_idx"),
        ]

    def clean(self):
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({"end_time": _("End time must be after start time.")})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.start_time}–{self.end_time})"
