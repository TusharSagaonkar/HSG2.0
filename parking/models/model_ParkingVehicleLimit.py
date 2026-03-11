from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db import transaction
from django.db.models import Q
from django.utils import timezone


class ParkingVehicleLimit(models.Model):
    class MemberRole(models.TextChoices):
        OWNER = "OWNER", "Owner"
        TENANT = "TENANT", "Tenant"

    class VehicleType(models.TextChoices):
        CAR = "CAR", "Car (4 Wheeler)"
        BIKE = "BIKE", "Bike (2 Wheeler)"
        OTHER = "OTHER", "Other"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="parking_vehicle_limits",
    )

    member_role = models.CharField(
        max_length=20,
        choices=MemberRole.choices,
    )

    vehicle_type = models.CharField(
        max_length=20,
        choices=VehicleType.choices,
    )

    max_allowed = models.PositiveIntegerField(default=0)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    start_date = models.DateField(default=timezone.localdate)
    end_date = models.DateField(null=True, blank=True)
    changed_reason = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_parking_vehicle_limits",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "parking"
        ordering = ("society", "member_role", "vehicle_type", "-start_date", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("society", "member_role", "vehicle_type"),
                condition=Q(status="ACTIVE"),
                name="uniq_active_parking_limit_per_scope",
            )
        ]

    def clean(self):
        if self.max_allowed < 0:
            raise ValidationError("Max allowed cannot be negative.")
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError("End date cannot be earlier than start date.")

    @classmethod
    def create_new_version(
        cls,
        *,
        society,
        member_role,
        vehicle_type,
        max_allowed,
        changed_reason="",
        created_by=None,
    ):
        today = timezone.localdate()
        with transaction.atomic():
            current_active = (
                cls.objects.select_for_update()
                .filter(
                    society=society,
                    member_role=member_role,
                    vehicle_type=vehicle_type,
                    status=cls.Status.ACTIVE,
                )
                .first()
            )
            if current_active:
                current_active.status = cls.Status.INACTIVE
                current_active.end_date = today
                current_active.save(update_fields=["status", "end_date"])

            new_version = cls.objects.create(
                society=society,
                member_role=member_role,
                vehicle_type=vehicle_type,
                max_allowed=max_allowed,
                status=cls.Status.ACTIVE,
                start_date=today,
                changed_reason=changed_reason,
                created_by=created_by,
            )
        from parking.services.recalculate_vehicle_rule_status import (
            recalculate_vehicle_rule_status,
        )

        recalculate_vehicle_rule_status(society.id)
        return new_version

    def save(self, *args, **kwargs):
        is_create = self.pk is None
        super().save(*args, **kwargs)

        if is_create and self.status == self.Status.ACTIVE:
            from parking.services.recalculate_vehicle_rule_status import (
                recalculate_vehicle_rule_status,
            )

            recalculate_vehicle_rule_status(self.society_id)

    def __str__(self):
        return (
            f"{self.society.name} | {self.member_role} | {self.vehicle_type} "
            f"→ {self.max_allowed} ({self.status})"
        )
