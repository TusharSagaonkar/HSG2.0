from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class UnitOccupancy(models.Model):
    class OccupancyType(models.TextChoices):
        OWNER = "OWNER", "Owner Occupied"
        TENANT = "TENANT", "Tenant Occupied"
        VACANT = "VACANT", "Vacant"

    unit = models.ForeignKey(
        "housing.Unit",
        on_delete=models.CASCADE,
        related_name="occupancies",
    )
    occupant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="occupied_units",
    )
    occupancy_type = models.CharField(max_length=20, choices=OccupancyType.choices)
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "housing"
        ordering = ("-start_date",)

    def clean(self):
        overlapping = UnitOccupancy.objects.filter(unit=self.unit, end_date__isnull=True)
        if self.pk:
            overlapping = overlapping.exclude(pk=self.pk)

        if overlapping.exists():
            raise ValidationError("This unit already has an active occupancy.")

        if self.occupancy_type == self.OccupancyType.VACANT and self.occupant:
            raise ValidationError("Vacant unit cannot have an occupant.")

    def __str__(self):
        return f"{self.unit} - {self.occupancy_type}"
