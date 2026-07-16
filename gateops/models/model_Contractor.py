from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class Contractor(models.Model):
    """The contracting company master record.

    Phase 9 — Contractor Management. A ``Contractor`` represents an external
    company engaged by a society to perform work (construction, plumbing,
    security staffing, etc.). :class:`Contract` engagements and
    :class:`WorkPermit` authorisations hang off this master record, while
    individual labourers are tracked as :class:`Worker` rows linked to a
    contract.

    Soft-delete follows the established ``is_active`` + ``deleted_at``
    pattern. The conditional unique constraint on ``(society, company_name)``
    only enforces uniqueness among active contractors, so a soft-deleted
    company name can be reused.
    """

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="contractors",
    )
    company_name = models.CharField(max_length=200)
    supervisor_name = models.CharField(max_length=200, blank=True)
    supervisor_phone = models.CharField(max_length=20, blank=True)
    contact_person = models.CharField(max_length=200, blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    gst_number = models.CharField(max_length=20, blank=True)
    pan_number = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["society", "is_active"], name="contractor_soc_active_idx",
            ),
            models.Index(
                fields=["society", "company_name"], name="contractor_soc_name_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["society", "company_name"],
                condition=Q(is_active=True),
                name="unique_active_contractor_name_per_society",
            ),
        ]

    def __str__(self):
        return f"{self.company_name} ({self.society.name})"

    def clean(self):
        super().clean()
        # company_name is mandatory; a blank value makes the contractor
        # unidentifiable and breaks the (society, company_name) lookup path.
        if not self.company_name or not self.company_name.strip():
            raise ValidationError({"company_name": "Company name is required."})

    def save(self, *args, **kwargs):
        # Model-level validation runs on every save() (single-row writes).
        # Bulk operations (update()/bulk_create) bypass save() and therefore
        # bypass clean() — the service layer is responsible for validating
        # those paths.
        self.clean()
        super().save(*args, **kwargs)
