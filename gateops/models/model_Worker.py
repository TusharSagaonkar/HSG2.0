from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class Worker(models.Model):
    """Individual labourer/worker under a :class:`Contract`.

    Phase 9 — Contractor Management. A ``Worker`` links a deduplicated
    :class:`Person` master record to a specific :class:`Contract`, recording
    the person's role (``designation``) and ID-document details for the
    duration of that engagement.

    Uses composition (FK to ``Person`` with ``PROTECT``) rather than
    inheritance — a person may be a worker on one contract and a regular
    visitor on another, and deleting a worker profile must not destroy the
    underlying person record.

    Soft-delete follows the established ``is_active`` + ``deleted_at``
    pattern. The conditional unique constraint on
    ``(society, contract, person)`` prevents enrolling the same person twice
    on the same active contract.
    """

    class IdType(models.TextChoices):
        AADHAAR = "aadhaar", "Aadhaar"
        PAN = "pan", "PAN"
        VOTER_ID = "voter_id", "Voter ID"
        DRIVING_LICENSE = "driving_license", "Driving License"
        OTHER = "other", "Other"

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="gate_workers",
    )
    contract = models.ForeignKey(
        "gateops.Contract",
        on_delete=models.CASCADE,
        related_name="workers",
    )
    person = models.ForeignKey(
        "gateops.Person",
        on_delete=models.PROTECT,
        related_name="worker_profiles",
    )
    designation = models.CharField(max_length=100, blank=True)
    id_type = models.CharField(
        max_length=20,
        choices=IdType.choices,
        blank=True,
    )
    id_number = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["society", "is_active"], name="worker_soc_active_idx",
            ),
            models.Index(
                fields=["society", "contract", "is_active"],
                name="worker_soc_ctr_active_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["society", "contract", "person"],
                condition=Q(is_active=True),
                name="unique_active_worker_per_contract",
            ),
        ]

    def __str__(self):
        return f"{self.person.name} — {self.contract.title}"

    def clean(self):
        super().clean()
        # Cross-society data-leak prevention: the person must belong to the
        # same society as the worker profile. Without this check a worker
        # could be linked to a person from a different society, leaking
        # visitor data across tenants.
        if self.person_id is not None and self.society_id is not None:
            if self.person.society_id != self.society_id:
                raise ValidationError(
                    {"person": "Person must belong to the same society as the worker."}
                )

    def save(self, *args, **kwargs):
        # Model-level validation runs on every save() (single-row writes).
        # Bulk operations (update()/bulk_create) bypass save() and therefore
        # bypass clean() — the service layer is responsible for validating
        # those paths.
        self.clean()
        super().save(*args, **kwargs)
