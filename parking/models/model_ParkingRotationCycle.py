from django.db import models


class ParkingRotationCycle(models.Model):
    class AllocationStatus(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ALLOCATED = "ALLOCATED", "Allocated"
        ACTIVE = "ACTIVE", "Active"
        COMPLETED = "COMPLETED", "Completed"

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="parking_rotation_cycles",
    )
    policy = models.ForeignKey(
        "parking.ParkingRotationPolicy",
        on_delete=models.PROTECT,
        related_name="cycles",
    )
    cycle_number = models.PositiveIntegerField()
    cycle_start_date = models.DateField()
    cycle_end_date = models.DateField()
    total_rotational_spots = models.PositiveIntegerField(default=0)
    allocation_status = models.CharField(
        max_length=20,
        choices=AllocationStatus.choices,
        default=AllocationStatus.DRAFT,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "parking"
        ordering = ("-cycle_start_date", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("society", "cycle_number"),
                name="uniq_rotation_cycle_number_per_society",
            ),
        ]

    def __str__(self):
        return f"{self.society.name} | Cycle #{self.cycle_number}"
