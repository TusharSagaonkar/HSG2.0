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

    def _member_role_for_limit(self):
        if not self.member_id:
            return None
        role = self.member.role
        if role in {"OWNER", "TENANT"}:
            return role
        return None

    def _enforce_unit_role_vehicle_limit(self):
        if not self.is_active:
            return

        member_role = self._member_role_for_limit()
        if not member_role:
            return

        from parking.models.model_ParkingVehicleLimit import ParkingVehicleLimit

        limit = ParkingVehicleLimit.objects.filter(
            society_id=self.society_id,
            member_role=member_role,
            vehicle_type=self.vehicle_type,
        ).first()
        if not limit:
            return

        active_vehicles_qs = (
            Vehicle.objects.select_for_update()
            .filter(
                society_id=self.society_id,
                unit_id=self.unit_id,
                vehicle_type=self.vehicle_type,
                is_active=True,
                member__role=member_role,
            )
            .order_by("created_at", "id")
        )

        overflow = active_vehicles_qs.count() - limit.max_allowed
        if overflow <= 0:
            return

        deactivate_ids = list(active_vehicles_qs.values_list("id", flat=True)[:overflow])
        Vehicle.objects.filter(id__in=deactivate_ids, is_active=True).update(
            is_active=False,
            deactivated_at=timezone.now(),
        )

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

    def generate_qr_image(self):
        return generate_vehicle_verification_qr(self)

    def save(self, *args, **kwargs):
        if self.is_active:
            self.deactivated_at = None
        elif self.deactivated_at is None:
            self.deactivated_at = timezone.now()

        with transaction.atomic():
            super().save(*args, **kwargs)
            self._enforce_unit_role_vehicle_limit()
            if self.pk:
                self.refresh_from_db(fields=["is_active", "deactivated_at"])

    def __str__(self):
        return f"{self.vehicle_number} ({self.vehicle_type})"
