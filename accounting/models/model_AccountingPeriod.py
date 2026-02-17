# accounting/models/model_AccountingPeriod.py

from django.db import models
from django.core.exceptions import ValidationError
from societies.models import Society


class AccountingPeriod(models.Model):
    society = models.ForeignKey(
        Society,
        on_delete=models.CASCADE,
        related_name="accounting_periods",
    )

    financial_year = models.ForeignKey(
        "accounting.FinancialYear",
        on_delete=models.CASCADE,
        related_name="periods",
    )

    start_date = models.DateField()
    end_date = models.DateField()

    is_open = models.BooleanField(default=False)

    class Meta:
        ordering = ("start_date",)
        unique_together = (
            "society",
            "financial_year",
            "start_date",
            "end_date",
        )

    def clean(self):
        if self.start_date >= self.end_date:
            raise ValidationError("Period start date must be before end date.")

        if (
            self.start_date < self.financial_year.start_date
            or self.end_date > self.financial_year.end_date
        ):
            raise ValidationError(
                "Accounting period must fall entirely within the financial year."
            )

    @classmethod
    def is_period_open(cls, society, date):
        return cls.objects.filter(
            society=society,
            start_date__lte=date,
            end_date__gte=date,
            is_open=True,
        ).exists()

    def __str__(self):
        status = "OPEN" if self.is_open else "CLOSED"
        return f"{self.start_date} → {self.end_date} ({status})"
