from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class WorkPermit(models.Model):
    """Time-bound work authorization with safety documentation.

    Phase 9 — Contractor Management. A ``WorkPermit`` authorises work on a
    specific :class:`Contract` for a bounded time window (``issued_at`` →
    ``expires_at``), recording that safety documents were verified and a
    safety briefing was given before work commenced.

    The ``hazard_level`` and ``status`` fields drive filtering and expiry
    sweeps. Soft-delete follows the established ``is_active`` +
    ``deleted_at`` pattern. The conditional unique constraint on
    ``(society, permit_number)`` prevents duplicate active permit numbers
    within a society.
    """

    class HazardLevel(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        REVOKED = "revoked", "Revoked"

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="work_permits",
    )
    contract = models.ForeignKey(
        "gateops.Contract",
        on_delete=models.CASCADE,
        related_name="work_permits",
    )
    permit_number = models.CharField(max_length=50)
    issued_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    safety_docs_verified = models.BooleanField(default=False)
    safety_briefing_given = models.BooleanField(default=False)
    work_area = models.CharField(max_length=200, blank=True)
    hazard_level = models.CharField(
        max_length=10,
        choices=HazardLevel.choices,
        default=HazardLevel.LOW,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["society", "is_active"], name="workpermit_soc_active_idx",
            ),
            models.Index(
                fields=["society", "contract", "is_active"],
                name="workpermit_soc_ctr_active_idx",
            ),
            models.Index(
                fields=["society", "expires_at"], name="workpermit_soc_expiry_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["society", "permit_number"],
                condition=Q(is_active=True),
                name="unique_active_workpermit_number_per_society",
            ),
        ]

    def __str__(self):
        return f"WP-{self.permit_number} — {self.contract.title}"

    def clean(self):
        super().clean()
        # expires_at must be strictly after issued_at — a permit that expires
        # at or before issuance has zero validity and would break expiry
        # sweep queries.
        if self.issued_at is not None and self.expires_at is not None:
            if self.expires_at <= self.issued_at:
                raise ValidationError(
                    {"expires_at": "expires_at must be after issued_at."}
                )

    def save(self, *args, **kwargs):
        # Model-level validation runs on every save() (single-row writes).
        # Bulk operations (update()/bulk_create) bypass save() and therefore
        # bypass clean() — the service layer is responsible for validating
        # those paths.
        self.clean()
        super().save(*args, **kwargs)
