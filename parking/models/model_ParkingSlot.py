from django.core.exceptions import ValidationError
from django.db import models


class ParkingSlot(models.Model):
    class ParkingModel(models.TextChoices):
        COMMON = "COMMON", "Common"
        ASSIGNED = "ASSIGNED", "Assigned"
        SOLD = "SOLD", "Sold"

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

    parking_model = models.CharField(
        max_length=20,
        choices=ParkingModel.choices,
        default=ParkingModel.COMMON,
    )

    slot_type = models.CharField(
        max_length=20,
        choices=SlotType.choices,
        default=SlotType.OPEN,
    )

    owned_unit = models.ForeignKey(
        "housing.Unit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_parking_slots",
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
        if self.parking_model == self.ParkingModel.SOLD and not self.owned_unit:
            raise ValidationError("Owned unit is required for sold parking slots.")
        if self.parking_model != self.ParkingModel.SOLD and self.owned_unit is not None:
            raise ValidationError("Owned unit must be empty unless parking model is sold.")
        if self.owned_unit and self.owned_unit.structure.society_id != self.society_id:
            raise ValidationError("Owned unit must belong to selected society.")

    def __str__(self):
        return f"{self.society.name} - {self.slot_number} ({self.slot_type})"
