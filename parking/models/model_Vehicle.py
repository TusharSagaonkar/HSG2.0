import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db import transaction
from django.utils import timezone

from parking.utils.qr import generate_vehicle_verification_qr


class Vehicle(models.Model):
    class VehicleType(models.TextChoices):
        CAR = "CAR", "Car (4 Wheeler)"
        BIKE = "BIKE", "Bike (2 Wheeler)"
        OTHER = "OTHER", "Other"

    class RuleStatus(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        RULE_VIOLATION = "RULE_VIOLATION", "Rule Violation"
        RESIDENT_MISMATCH = "RESIDENT_MISMATCH", "Resident Mismatch"
        UNIT_VACANT = "UNIT_VACANT", "Unit Vacant"
        PERMIT_EXPIRED = "PERMIT_EXPIRED", "Permit Expired"
        VEHICLE_INACTIVE = "VEHICLE_INACTIVE", "Vehicle Inactive"
        ADMIN_BLOCKED = "ADMIN_BLOCKED", "Admin Blocked"
        DATA_INCONSISTENT = "DATA_INCONSISTENT", "Data Inconsistent"

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="vehicles",
    )

    unit = models.ForeignKey(
        "housing.Unit",
        on_delete=models.CASCADE,
        related_name="vehicles",
    )

    member = models.ForeignKey(
        "housing.Member",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vehicles",
        help_text="Optional: Who primarily uses this vehicle",
    )

    vehicle_number = models.CharField(
        max_length=20,
        help_text="Vehicle registration number",
    )

    vehicle_type = models.CharField(
        max_length=20,
        choices=VehicleType.choices,
    )

    color = models.CharField(max_length=50, blank=True)

    is_active = models.BooleanField(default=True)
    rule_status = models.CharField(
        max_length=20,
        choices=RuleStatus.choices,
        default=RuleStatus.ACTIVE,
    )
    verification_token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
    )
    valid_from = models.DateField(default=timezone.localdate)
    valid_until = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "parking"
        unique_together = ("society", "vehicle_number")
        ordering = ("vehicle_number",)

    def clean(self):
        # Ensure vehicle belongs to same society as unit
        if self.unit.structure.society_id != self.society_id:
            raise ValidationError("Vehicle unit must belong to selected society.")

        # Ensure member (if provided) belongs to same society
        if self.member and self.member.society_id != self.society_id:
            raise ValidationError("Vehicle member must belong to selected society.")

    def is_valid(self):
        today = timezone.localdate()
        if not self.is_active:
            return False
        if self.valid_until and self.valid_until < today:
            return False
        return True

    def get_verification_url(self):
        base_url = getattr(settings, "BASE_URL", "").rstrip("/")
        return f"{base_url}/vehicle/verify/{self.verification_token}/"

    def generate_qr_image(self, request=None):
        """
        Generate QR code image for vehicle verification.
        
        Args:
            request: Optional Django request object to build absolute URL 
                    based on the current host. If not provided, uses BASE_URL setting.
        
        Returns:
            ContentFile with PNG image data
        """
        return generate_vehicle_verification_qr(self, request=request)

    def save(self, *args, **kwargs):
        if self.is_active:
            self.deactivated_at = None
        elif self.deactivated_at is None:
            self.deactivated_at = timezone.now()

        with transaction.atomic():
            super().save(*args, **kwargs)

            from parking.services.recalculate_vehicle_rule_status import (
                recalculate_single_vehicle_rule_status_optimized,
            )

            # OPTIMIZATION: Avoid redundant database fetch - pass self directly
            # The instance already has id set after save(), and related objects
            # will be fetched by recalculate_single_vehicle_rule_status_optimized if needed
            recalculate_single_vehicle_rule_status_optimized(self)

    def __str__(self):
        return f"{self.vehicle_number} ({self.vehicle_type})"
