from django.db import models


class Unit(models.Model):
    class UnitType(models.TextChoices):
        FLAT = "FLAT", "Flat"
        SHOP = "SHOP", "Shop"
        OFFICE = "OFFICE", "Office"
        OTHER = "OTHER", "Other"

    structure = models.ForeignKey(
        "housing.Structure",
        on_delete=models.CASCADE,
        related_name="units",
    )
    unit_type = models.CharField(max_length=20, choices=UnitType.choices)
    identifier = models.CharField(
        max_length=50,
        help_text="Flat number / Shop number / Unit code",
    )
    area_sqft = models.DecimalField(max_digits=8, decimal_places=2, blank=True, null=True)
    chargeable_area_sqft = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        blank=True,
        null=True,
        help_text="Authoritative area used for billing calculations.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "housing"
        unique_together = ("structure", "identifier")
        ordering = ("id",)

    def __str__(self):
        return f"{self.structure} -> {self.identifier} ({self.unit_type})"

    @property
    def billing_area_sqft(self):
        return self.chargeable_area_sqft if self.chargeable_area_sqft is not None else self.area_sqft
