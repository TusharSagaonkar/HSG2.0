from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal
from housing.models import Society

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
        
