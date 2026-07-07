from django.db import models
from django.utils.translation import gettext_lazy as _


class HolidayCalendar(models.Model):
    """Society holiday calendar for rule conditions.

    Used by the rule engine (Phase 2) for conditions like "restrict
    contractor entry on holidays". One holiday per date per society.
    """

    class Affects(models.TextChoices):
        ALL = "all", _("All")
        CONTRACTORS = "contractors", _("Contractors")
        DELIVERIES = "deliveries", _("Deliveries")
        VISITORS = "visitors", _("Visitors")

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="holidays",
    )
    name = models.CharField(max_length=100)
    date = models.DateField()
    is_recurring_annually = models.BooleanField(default=False)
    affects = models.CharField(
        max_length=20,
        choices=Affects.choices,
        default=Affects.ALL,
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Holiday Calendar Entry")
        verbose_name_plural = _("Holiday Calendar Entries")
        ordering = ("society", "date")
        constraints = [
            models.UniqueConstraint(
                fields=["society", "date"],
                name="uniq_holiday_per_society_date",
            ),
        ]
        indexes = [
            models.Index(fields=["society", "date"], name="holiday_soc_date_idx"),
        ]

    def __str__(self):
        return f"{self.name} ({self.date})"
