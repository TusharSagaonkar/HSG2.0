from django.db import models


class ParkingRotationQueue(models.Model):
    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="parking_rotation_queue_entries",
    )
    unit = models.ForeignKey(
        "housing.Unit",
        on_delete=models.CASCADE,
        related_name="parking_rotation_queue_entries",
    )
    queue_position = models.PositiveIntegerField()
    last_allocated_cycle = models.ForeignKey(
        "parking.ParkingRotationCycle",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="queue_allocations",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "parking"
        ordering = ("queue_position", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("society", "unit"),
                name="uniq_rotation_queue_unit_per_society",
            ),
            models.UniqueConstraint(
                fields=("society", "queue_position"),
                name="uniq_rotation_queue_position_per_society",
            ),
        ]

    def __str__(self):
        return f"{self.society.name} | {self.unit.identifier} @ {self.queue_position}"
