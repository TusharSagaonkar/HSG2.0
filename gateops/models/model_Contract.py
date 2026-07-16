from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class Contract(models.Model):
    """A work engagement under a :class:`Contractor`.

    Phase 9 — Contractor Management. A ``Contract`` represents a specific piece
    of work (e.g. "Plumbing work", "Tower A construction") commissioned from a
    contractor. It carries a date window, a labour-count limit
    (``max_workers``) and a status that drives the engagement lifecycle.

    Soft-delete follows the established ``is_active`` + ``deleted_at``
    pattern. The conditional unique constraint on
    ``(society, contractor, title)`` only enforces uniqueness among active
    contracts, so a soft-deleted title can be reused.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        SUSPENDED = "suspended", "Suspended"

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="contracts",
    )
    contractor = models.ForeignKey(
        "gateops.Contractor",
        on_delete=models.CASCADE,
        related_name="contracts",
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    max_workers = models.PositiveIntegerField(default=10)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["society", "is_active"], name="contract_soc_active_idx",
            ),
            models.Index(
                fields=["society", "contractor", "is_active"],
                name="contract_soc_ctr_active_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["society", "contractor", "title"],
                condition=Q(is_active=True),
                name="unique_active_contract_title_per_society",
            ),
        ]

    def __str__(self):
        return f"{self.title} — {self.contractor.company_name}"

    def clean(self):
        super().clean()
        # end_date must be on or after start_date — an inverted window is
        # nonsensical and would break expiry-based queries.
        if self.end_date is not None and self.start_date is not None:
            if self.end_date < self.start_date:
                raise ValidationError(
                    {"end_date": "end_date must be on or after start_date."}
                )
        # max_workers must be positive — a zero/negative limit would block
        # all worker enrolment on the contract.
        if self.max_workers is not None and self.max_workers <= 0:
            raise ValidationError(
                {"max_workers": "max_workers must be greater than zero."}
            )

    def save(self, *args, **kwargs):
        # Model-level validation runs on every save() (single-row writes).
        # Bulk operations (update()/bulk_create) bypass save() and therefore
        # bypass clean() — the service layer is responsible for validating
        # those paths.
        self.clean()
        super().save(*args, **kwargs)
