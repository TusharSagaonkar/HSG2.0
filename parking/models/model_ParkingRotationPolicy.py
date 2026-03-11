from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db import transaction
from django.utils import timezone


class ParkingRotationPolicy(models.Model):
    class RotationMethod(models.TextChoices):
        QUEUE = "QUEUE", "Queue"
        LOTTERY = "LOTTERY", "Lottery"

    class PriorityRule(models.TextChoices):
        NO_PARKING_FIRST = "NO_PARKING_FIRST", "No Parking First"
        QUEUE_ORDER = "QUEUE_ORDER", "Queue Order"
        LOTTERY_ONLY = "LOTTERY_ONLY", "Lottery Only"

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="parking_rotation_policies",
    )
    policy_name = models.CharField(max_length=255, default="Default Rotational Policy")
    rotation_period_months = models.PositiveIntegerField(default=1)
    rotation_method = models.CharField(
        max_length=20,
        choices=RotationMethod.choices,
        default=RotationMethod.QUEUE,
    )
    vehicle_required_before_apply = models.BooleanField(default=True)
    allow_sold_parking_owner = models.BooleanField(default=True)
    allow_tenant_application = models.BooleanField(default=True)
    max_rotational_slots_per_unit = models.PositiveIntegerField(default=1)
    max_total_parking_per_unit = models.PositiveIntegerField(default=1)
    skip_units_with_outstanding_dues = models.BooleanField(default=False)
    skip_units_with_parking_violation = models.BooleanField(default=False)
    unused_parking_reassignment_days = models.PositiveIntegerField(default=0)
    application_window_days = models.PositiveIntegerField(default=7)
    priority_rule = models.CharField(
        max_length=30,
        choices=PriorityRule.choices,
        default=PriorityRule.QUEUE_ORDER,
    )
    effective_from = models.DateField(default=timezone.localdate)
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_parking_rotation_policies",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "parking"
        ordering = ("society", "-effective_from", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("society",),
                condition=models.Q(is_active=True),
                name="uniq_active_rotation_policy_per_society",
            ),
        ]

    def clean(self):
        if self.rotation_period_months <= 0:
            raise ValidationError("Rotation period months must be greater than zero.")
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError("Effective to date cannot be before effective from date.")

    def to_audit_payload(self):
        return {
            "id": self.id,
            "society_id": self.society_id,
            "policy_name": self.policy_name,
            "rotation_period_months": self.rotation_period_months,
            "rotation_method": self.rotation_method,
            "vehicle_required_before_apply": self.vehicle_required_before_apply,
            "allow_sold_parking_owner": self.allow_sold_parking_owner,
            "allow_tenant_application": self.allow_tenant_application,
            "max_rotational_slots_per_unit": self.max_rotational_slots_per_unit,
            "max_total_parking_per_unit": self.max_total_parking_per_unit,
            "skip_units_with_outstanding_dues": self.skip_units_with_outstanding_dues,
            "skip_units_with_parking_violation": self.skip_units_with_parking_violation,
            "unused_parking_reassignment_days": self.unused_parking_reassignment_days,
            "application_window_days": self.application_window_days,
            "priority_rule": self.priority_rule,
            "effective_from": self.effective_from.isoformat() if self.effective_from else None,
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "is_active": self.is_active,
        }

    @classmethod
    def create_new_version(
        cls,
        *,
        society,
        policy_name="Default Rotational Policy",
        rotation_period_months=1,
        rotation_method=RotationMethod.QUEUE,
        vehicle_required_before_apply=True,
        allow_sold_parking_owner=True,
        allow_tenant_application=True,
        max_rotational_slots_per_unit=1,
        max_total_parking_per_unit=1,
        skip_units_with_outstanding_dues=False,
        skip_units_with_parking_violation=False,
        unused_parking_reassignment_days=0,
        application_window_days=7,
        priority_rule=PriorityRule.QUEUE_ORDER,
        effective_from=None,
        changed_by=None,
        change_reason="",
    ):
        effective_from = effective_from or timezone.localdate()
        with transaction.atomic():
            active_policy = (
                cls.objects.select_for_update()
                .filter(
                    society=society,
                    is_active=True,
                )
                .first()
            )
            old_values = active_policy.to_audit_payload() if active_policy else {}

            if active_policy:
                close_date = effective_from - timedelta(days=1)
                cls.objects.filter(pk=active_policy.pk).update(
                    effective_to=close_date,
                    is_active=False,
                )

            new_policy = cls.objects.create(
                society=society,
                policy_name=policy_name,
                rotation_period_months=rotation_period_months,
                rotation_method=rotation_method,
                vehicle_required_before_apply=vehicle_required_before_apply,
                allow_sold_parking_owner=allow_sold_parking_owner,
                allow_tenant_application=allow_tenant_application,
                max_rotational_slots_per_unit=max_rotational_slots_per_unit,
                max_total_parking_per_unit=max_total_parking_per_unit,
                skip_units_with_outstanding_dues=skip_units_with_outstanding_dues,
                skip_units_with_parking_violation=skip_units_with_parking_violation,
                unused_parking_reassignment_days=unused_parking_reassignment_days,
                application_window_days=application_window_days,
                priority_rule=priority_rule,
                effective_from=effective_from,
                effective_to=None,
                is_active=True,
                created_by=changed_by,
            )

            from parking.models import ParkingRotationPolicyAudit

            ParkingRotationPolicyAudit.objects.create(
                policy=new_policy,
                old_values=old_values,
                new_values=new_policy.to_audit_payload(),
                changed_by=changed_by,
                change_reason=change_reason,
            )
            return new_policy

    def save(self, *args, **kwargs):
        allow_update = kwargs.pop("allow_update", False)
        if self.pk and not allow_update:
            raise ValidationError(
                "Parking rotation policies are immutable. Create a new version instead.",
            )
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.society.name} | {self.policy_name} ({self.effective_from})"
