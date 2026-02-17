from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class UnitOwnership(models.Model):
    class OwnershipRole(models.TextChoices):
        PRIMARY = "PRIMARY", "Primary Owner"
        SECONDARY = "SECONDARY", "Secondary Owner"

    unit = models.ForeignKey(
        "housing.Unit",
        on_delete=models.CASCADE,
        related_name="ownerships",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="unit_ownerships",
    )
    role = models.CharField(max_length=20, choices=OwnershipRole.choices)
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "housing"
        ordering = ("-start_date",)

    def clean(self):
        if self.role == self.OwnershipRole.PRIMARY:
            queryset = UnitOwnership.objects.filter(
                unit=self.unit,
                role=self.OwnershipRole.PRIMARY,
                end_date__isnull=True,
            )
            if self.pk:
                queryset = queryset.exclude(pk=self.pk)
            if queryset.exists():
                raise ValidationError("This unit already has an active primary owner.")

    def __str__(self):
        return f"{self.unit} - {self.owner} ({self.role})"
