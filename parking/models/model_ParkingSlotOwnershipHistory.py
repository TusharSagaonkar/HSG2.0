from django.db import models
from django.utils import timezone


class ParkingSlotOwnershipHistory(models.Model):
    slot = models.ForeignKey(
        "parking.ParkingSlot",
        on_delete=models.CASCADE,
        related_name="ownership_history",
    )
    unit = models.ForeignKey(
        "housing.Unit",
        on_delete=models.CASCADE,
        related_name="parking_slot_ownership_history",
    )
    start_date = models.DateField(default=timezone.localdate)
    end_date = models.DateField(null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        app_label = "parking"
        ordering = ("-start_date", "-id")

    def __str__(self):
        return f"{self.slot.slot_number} -> {self.unit.identifier}"
