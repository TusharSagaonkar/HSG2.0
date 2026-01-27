from django.db import models
from django.core.exceptions import ValidationError
from django.conf import settings


class Society(models.Model):
    name = models.CharField(max_length=200)
    registration_number = models.CharField(
        max_length=100, blank=True, null=True
    )
    address = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Structure(models.Model):
    class StructureType(models.TextChoices):
        BUILDING = "BUILDING", "Building"
        WING = "WING", "Wing"
        BLOCK = "BLOCK", "Block"
        TOWER = "TOWER", "Tower"
        FLOOR = "FLOOR", "Floor"

    society = models.ForeignKey(
        Society,
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

    structure_type = models.CharField(
        max_length=20,
        choices=StructureType.choices,
    )

    name = models.CharField(max_length=100)

    display_order = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("society", "parent", "name")
        ordering = ("display_order", "id")

    def clean(self):
        # Root-level structures must not have parent
        if self.parent and self.parent.society_id != self.society_id:
            raise ValidationError("Parent structure must belong to same society.")

        # Prevent infinite depth insanity (soft rule)
        if self.parent and self.parent.parent and self.parent.parent.parent:
            raise ValidationError("Structure nesting too deep. Review hierarchy.")

    def __str__(self):
        if self.parent:
            return f"{self.parent} → {self.name}"
        return f"{self.society.name} → {self.name}"


class Unit(models.Model):
    class UnitType(models.TextChoices):
        FLAT = "FLAT", "Flat"
        SHOP = "SHOP", "Shop"
        OFFICE = "OFFICE", "Office"
        OTHER = "OTHER", "Other"

    structure = models.ForeignKey(
        Structure,
        on_delete=models.CASCADE,
        related_name="units",
    )

    unit_type = models.CharField(
        max_length=20,
        choices=UnitType.choices,
    )

    identifier = models.CharField(
        max_length=50,
        help_text="Flat number / Shop number / Unit code",
    )

    area_sqft = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        blank=True,
        null=True,
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("structure", "identifier")
        ordering = ("id",)

    def __str__(self):
        return f"{self.structure} → {self.identifier} ({self.unit_type})"

####### OWNERSHIP #########

class UnitOwnership(models.Model):
    class OwnershipRole(models.TextChoices):
        PRIMARY = "PRIMARY", "Primary Owner"
        SECONDARY = "SECONDARY", "Secondary Owner"

    unit = models.ForeignKey(
        Unit,
        on_delete=models.CASCADE,
        related_name="ownerships",
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="unit_ownerships",
    )

    role = models.CharField(
        max_length=20,
        choices=OwnershipRole.choices,
    )

    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-start_date",)

    def clean(self):
        # Only one PRIMARY owner at a time per unit
        if self.role == self.OwnershipRole.PRIMARY:
            qs = UnitOwnership.objects.filter(
                unit=self.unit,
                role=self.OwnershipRole.PRIMARY,
                end_date__isnull=True,
            )
            if self.pk:
                qs = qs.exclude(pk=self.pk)

            if qs.exists():
                raise ValidationError(
                    "This unit already has an active primary owner."
                )

    def __str__(self):
        return f"{self.unit} - {self.owner} ({self.role})"



class UnitOccupancy(models.Model):
    class OccupancyType(models.TextChoices):
        OWNER = "OWNER", "Owner Occupied"
        TENANT = "TENANT", "Tenant Occupied"
        VACANT = "VACANT", "Vacant"

    unit = models.ForeignKey(
        Unit,
        on_delete=models.CASCADE,
        related_name="occupancies",
    )

    occupant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="occupied_units",
    )

    occupancy_type = models.CharField(
        max_length=20,
        choices=OccupancyType.choices,
    )

    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-start_date",)

    def clean(self):
        # At most one active occupancy
        overlapping = UnitOccupancy.objects.filter(
            unit=self.unit,
            end_date__isnull=True,
        )
        if self.pk:
            overlapping = overlapping.exclude(pk=self.pk)

        if overlapping.exists():
            raise ValidationError("This unit already has an active occupancy.")

        if self.occupancy_type == self.OccupancyType.VACANT and self.occupant:
            raise ValidationError("Vacant unit cannot have an occupant.")

    def __str__(self):
        return f"{self.unit} - {self.occupancy_type}"
