from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class ParkingRotationAllocation(models.Model):
    class AllocationMethod(models.TextChoices):
        QUEUE = "QUEUE", "Queue"
        LOTTERY = "LOTTERY", "Lottery"
        MANUAL_OVERRIDE = "MANUAL_OVERRIDE", "Manual Override"

    cycle = models.ForeignKey(
        "parking.ParkingRotationCycle",
        on_delete=models.CASCADE,
        related_name="allocations",
    )
    unit = models.ForeignKey(
        "housing.Unit",
        on_delete=models.CASCADE,
        related_name="parking_rotation_allocations",
    )
    parking_spot = models.ForeignKey(
        "parking.ParkingSlot",
        on_delete=models.PROTECT,
        related_name="parking_rotation_allocations",
    )
    vehicle = models.ForeignKey(
        "parking.Vehicle",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="parking_rotation_allocations",
    )
    allocation_method = models.CharField(
        max_length=20,
        choices=AllocationMethod.choices,
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_rotation_allocations",
    )

    class Meta:
        app_label = "parking"
        ordering = ("-assigned_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("cycle", "parking_spot"),
                name="uniq_rotation_allocation_per_cycle_spot",
            ),
        ]

    def clean(self):
        if self.unit_id and self.unit.structure.society_id != self.cycle.society_id:
            raise ValidationError("Allocation unit must belong to cycle society.")
        if self.parking_spot_id and self.parking_spot.society_id != self.cycle.society_id:
            raise ValidationError("Allocation parking spot must belong to cycle society.")
        if self.vehicle_id:
            if self.vehicle.society_id != self.cycle.society_id:
                raise ValidationError("Allocation vehicle must belong to cycle society.")
            if self.vehicle.unit_id != self.unit_id:
                raise ValidationError("Allocation vehicle must belong to allocation unit.")

    def __str__(self):
        return (
            f"Cycle #{self.cycle.cycle_number} | "
            f"{self.unit.identifier} -> {self.parking_spot.slot_number}"
        )
