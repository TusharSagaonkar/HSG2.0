from django.core.exceptions import ValidationError
from django.db import models


class ParkingVehicleLimit(models.Model):
    class MemberRole(models.TextChoices):
        OWNER = "OWNER", "Owner"
        TENANT = "TENANT", "Tenant"

    class VehicleType(models.TextChoices):
        CAR = "CAR", "Car (4 Wheeler)"
        BIKE = "BIKE", "Bike (2 Wheeler)"
        OTHER = "OTHER", "Other"

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="parking_vehicle_limits",
    )

    member_role = models.CharField(
        max_length=20,
        choices=MemberRole.choices,
    )

    vehicle_type = models.CharField(
        max_length=20,
        choices=VehicleType.choices,
    )

    max_allowed = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "parking"
        unique_together = ("society", "member_role", "vehicle_type")
        ordering = ("society", "member_role", "vehicle_type")

    def clean(self):
        if self.max_allowed < 0:
            raise ValidationError("Max allowed cannot be negative.")

    def __str__(self):
        return f"{self.society.name} | {self.member_role} | {self.vehicle_type} → {self.max_allowed}"