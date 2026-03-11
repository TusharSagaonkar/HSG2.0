from django.core.exceptions import ValidationError
from django.db import models


class ParkingRotationApplication(models.Model):
    class ApplicationStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    cycle = models.ForeignKey(
        "parking.ParkingRotationCycle",
        on_delete=models.CASCADE,
        related_name="applications",
    )
    unit = models.ForeignKey(
        "housing.Unit",
        on_delete=models.CASCADE,
        related_name="parking_rotation_applications",
    )
    vehicle = models.ForeignKey(
        "parking.Vehicle",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="parking_rotation_applications",
    )
    application_status = models.CharField(
        max_length=20,
        choices=ApplicationStatus.choices,
        default=ApplicationStatus.PENDING,
    )
    rejection_reason = models.TextField(blank=True)
    applied_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "parking"
        ordering = ("applied_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("cycle", "unit", "vehicle"),
                name="uniq_rotation_application_per_cycle_unit_vehicle",
            ),
        ]

    def clean(self):
        if self.unit_id and self.unit.structure.society_id != self.cycle.society_id:
            raise ValidationError("Application unit must belong to cycle society.")
        if self.vehicle_id:
            if self.vehicle.society_id != self.cycle.society_id:
                raise ValidationError("Application vehicle must belong to cycle society.")
            if self.vehicle.unit_id != self.unit_id:
                raise ValidationError("Application vehicle must belong to selected unit.")

    def __str__(self):
        return f"Cycle #{self.cycle.cycle_number} | {self.unit.identifier} ({self.application_status})"
