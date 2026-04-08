import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from parking.utils.qr import generate_permit_verification_qr


class ParkingPermit(models.Model):
    class PermitType(models.TextChoices):
        SOLD = "SOLD", "Sold"
        OPEN = "OPEN", "Open"
        ROTATIONAL = "ROTATIONAL", "Rotational"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        STANDBY = "STANDBY", "Standby"
        REVOKED = "REVOKED", "Revoked"
        EXPIRED = "EXPIRED", "Expired"

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="parking_permits",
    )
    vehicle = models.ForeignKey(
        "parking.Vehicle",
        on_delete=models.CASCADE,
        related_name="parking_permits",
    )
    unit = models.ForeignKey(
        "housing.Unit",
        on_delete=models.CASCADE,
        related_name="parking_permits",
    )
    slot = models.ForeignKey(
        "parking.ParkingSlot",
        on_delete=models.CASCADE,
        related_name="parking_permits",
    )
    permit_type = models.CharField(
        max_length=20,
        choices=PermitType.choices,
        default=PermitType.SOLD,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    issued_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    qr_token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_parking_permits",
    )

    class Meta:
        app_label = "parking"
        ordering = ("-issued_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("slot",),
                condition=models.Q(status="ACTIVE"),
                name="uniq_active_parking_permit_per_slot",
            ),
        ]

    def clean(self):
        if self.unit_id and self.vehicle_id and self.vehicle.unit_id != self.unit_id:
            raise ValidationError("Vehicle must belong to permit unit.")
        if self.permit_type == self.PermitType.SOLD:
            if self.slot.owned_unit_id != self.unit_id:
                raise ValidationError("Sold permit slot must belong to permit unit.")
            if self.status == self.Status.ACTIVE and self.vehicle.unit_id != self.slot.owned_unit_id:
                raise ValidationError("Active permit vehicle must match slot ownership unit.")
        if self.society_id:
            if self.unit and self.unit.structure.society_id != self.society_id:
                raise ValidationError("Permit unit must belong to selected society.")
            if self.vehicle and self.vehicle.society_id != self.society_id:
                raise ValidationError("Permit vehicle must belong to selected society.")
            if self.slot and self.slot.society_id != self.society_id:
                raise ValidationError("Permit slot must belong to selected society.")

    def get_verification_url(self):
        base_url = getattr(settings, "BASE_URL", "").rstrip("/")
        return f"{base_url}/parking/verify/{self.qr_token}/"

    def generate_qr_image(self, request=None):
        """
        Generate QR code image for parking permit verification.
        
        Args:
            request: Optional Django request object to build absolute URL 
                    based on the current host. If not provided, uses BASE_URL setting.
        
        Returns:
            ContentFile with PNG image data
        """
        return generate_permit_verification_qr(self, request=request)

    def save(self, *args, **kwargs):
        self.full_clean()
        result = super().save(*args, **kwargs)
        from parking.services.recalculate_vehicle_rule_status import (
            recalculate_vehicle_rule_status,
        )

        recalculate_vehicle_rule_status(self.society_id)
        return result

    def __str__(self):
        return f"{self.slot.slot_number} - {self.vehicle.vehicle_number} ({self.status})"
