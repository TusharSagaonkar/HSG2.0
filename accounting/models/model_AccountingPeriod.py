from django.db import models
from django.core.exceptions import ValidationError
from datetime import date


class AccountingPeriod(models.Model):
    year = models.PositiveIntegerField()
    month = models.PositiveIntegerField()  # 1 = Jan, 12 = Dec
    is_open = models.BooleanField(default=True)

    class Meta:
        unique_together = ("year", "month")
        ordering = ("year", "month")

    def clean(self):
        if not 1 <= self.month <= 12:
            raise ValidationError("Month must be between 1 and 12.")

    @classmethod
    def is_period_open(cls, d: date) -> bool:
        period = cls.objects.filter(
            year=d.year,
            month=d.month,
            is_open=True,
        ).first()
        return bool(period)

    def __str__(self):
        status = "OPEN" if self.is_open else "CLOSED"
        return f"{self.year}-{self.month:02d} ({status})"
