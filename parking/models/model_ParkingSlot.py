from django.core.exceptions import ValidationError
from django.db import models


class ParkingSlot(models.Model):
    class SlotType(models.TextChoices):
        OPEN = "OPEN", "Open"
        COVERED = "COVERED", "Covered"
        BASEMENT = "BASEMENT", "Basement"
        VISITOR = "VISITOR", "Visitor"
        SOLD = "SOLD", "Sold"

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="parking_slots",
    )

    slot_number = models.CharField(
        max_length=50,
        help_text="Display identifier like P1, B-12, Slot-45",
    )

    slot_type = models.CharField(
        max_length=20,
        choices=SlotType.choices,
        default=SlotType.OPEN,
    )

    is_active = models.BooleanField(default=True)

    # Future-proof flags
    is_rotational = models.BooleanField(default=False)
    is_transferable = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "parking"
        unique_together = ("society", "slot_number")
        ordering = ("slot_number",)

    def clean(self):
        if not self.slot_number:
            raise ValidationError("Slot number is required.")

    def __str__(self):
        return f"{self.society.name} - {self.slot_number} ({self.slot_type})"