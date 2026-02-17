from django.core.exceptions import ValidationError
from django.db import models


class Structure(models.Model):
    class StructureType(models.TextChoices):
        BUILDING = "BUILDING", "Building"
        WING = "WING", "Wing"
        BLOCK = "BLOCK", "Block"
        TOWER = "TOWER", "Tower"
        FLOOR = "FLOOR", "Floor"

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="structures",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        related_name="children",
        blank=True,
        null=True,
    )
    structure_type = models.CharField(max_length=20, choices=StructureType.choices)
    name = models.CharField(max_length=100)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "housing"
        unique_together = ("society", "parent", "name")
        ordering = ("display_order", "id")

    def clean(self):
        if self.parent and self.parent.society_id != self.society_id:
            raise ValidationError("Parent structure must belong to same society.")
        if self.parent and self.parent.parent and self.parent.parent.parent:
            raise ValidationError("Structure nesting too deep. Review hierarchy.")

    def __str__(self):
        if self.parent:
            return f"{self.parent} -> {self.name}"
        return f"{self.society.name} -> {self.name}"
