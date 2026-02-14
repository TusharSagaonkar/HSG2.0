from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal
from housing.models import Society

from django.db import models, transaction
#from django.core.exceptions import ValidationError
from datetime import date, timedelta
#from housing.models import Society

class FinancialYear(models.Model):
    society = models.ForeignKey(
        Society,
        on_delete=models.CASCADE,
        related_name="financial_years",
        #null=True,        # ✅ TEMPORARY
        #blank=True, 
    )
    name = models.CharField(max_length=20, unique=True)
    start_date = models.DateField()
    end_date = models.DateField()
    is_open = models.BooleanField(default=True)

    class Meta:
        ordering = ("-start_date",)
        unique_together = ("society", "start_date", "end_date")

    def clean(self):
        if self.start_date >= self.end_date:
            raise ValidationError("Start date must be before end date.")

    def __str__(self):
        return self.name
    
    @classmethod
    def get_open_year_for_date(cls, date):
        return cls.objects.filter(
            start_date__lte=date,
            end_date__gte=date,
            is_open=True,
        ).first()
    
    # Save method overridden to create accounting periods upon creation        
    def save(self, *args, **kwargs):
        is_new = self.pk is None

        super().save(*args, **kwargs)

        if is_new:
            self._create_accounting_periods()

    def _create_accounting_periods(self):
        from accounting.models.model_AccountingPeriod import AccountingPeriod

        periods = []
        current_start = self.start_date

        while current_start <= self.end_date:
            # Calculate month end safely
            next_month = (current_start.replace(day=28) + timedelta(days=4)).replace(day=1)
            current_end = min(next_month - timedelta(days=1), self.end_date)

            periods.append(
                AccountingPeriod(
                    society=self.society,
                    financial_year=self,
                    start_date=current_start,
                    end_date=current_end,
                )
            )

            current_start = current_end + timedelta(days=1)

        with transaction.atomic():
            AccountingPeriod.objects.bulk_create(periods)

            # Open only the first period
            AccountingPeriod.objects.filter(
                financial_year=self,
                start_date=periods[0].start_date,
            ).update(is_open=True)

    def __str__(self):
        return self.name
