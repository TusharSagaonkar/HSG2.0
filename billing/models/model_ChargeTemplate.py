from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Max
from django.db.models import Q
from django.utils import timezone


class ChargeTemplate(models.Model):
    class Frequency(models.TextChoices):
        MONTHLY = "MONTHLY", "Monthly"
        QUARTERLY = "QUARTERLY", "Quarterly"
        YEARLY = "YEARLY", "Yearly"

    class ChargeType(models.TextChoices):
        FIXED = "FIXED", "Fixed Amount"
        PER_SQFT = "PER_SQFT", "Per Square Foot"
        PER_PERSON = "PER_PERSON", "Per Person"
        CUSTOM_FORMULA = "CUSTOM_FORMULA", "Custom Formula"

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="charge_templates",
    )
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    charge_type = models.CharField(
        max_length=20,
        choices=ChargeType.choices,
        default=ChargeType.FIXED,
    )
    rate = models.DecimalField(max_digits=12, decimal_places=4)
    frequency = models.CharField(max_length=20, choices=Frequency.choices)
    due_days = models.PositiveIntegerField(default=15)
    late_fee_percent = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    effective_from = models.DateField(default=timezone.localdate)
    effective_to = models.DateField(blank=True, null=True)
    version_no = models.PositiveIntegerField(default=1, editable=False)
    previous_version = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="next_versions",
        null=True,
        blank=True,
    )
    income_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="charge_templates_as_income",
    )
    receivable_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="charge_templates_as_receivable",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "housing"
        ordering = ("name", "-effective_from", "-version_no")
        constraints = [
            models.UniqueConstraint(
                fields=("society", "name", "version_no"),
                name="uniq_charge_template_name_version",
            ),
        ]

    def clean(self):
        if self.income_account and self.income_account.society_id != self.society_id:
            raise ValidationError("Income account must belong to the selected society.")
        if self.receivable_account and self.receivable_account.society_id != self.society_id:
            raise ValidationError("Receivable account must belong to the selected society.")
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError("Effective end date cannot be before effective start date.")
        if self.previous_version_id:
            if self.previous_version_id == self.id:
                raise ValidationError("Previous version cannot point to the same template.")
            if self.previous_version.society_id != self.society_id:
                raise ValidationError("Previous version must belong to the same society.")
            if self.previous_version.name != self.name:
                raise ValidationError("Previous version must have the same template name.")

        overlap_query = ChargeTemplate.objects.filter(
            society_id=self.society_id,
            name=self.name,
        ).exclude(pk=self.pk)
        if self.effective_to:
            overlap_query = overlap_query.filter(
                Q(effective_to__isnull=True) | Q(effective_to__gte=self.effective_from),
                effective_from__lte=self.effective_to,
            )
        else:
            overlap_query = overlap_query.filter(
                Q(effective_to__isnull=True) | Q(effective_to__gte=self.effective_from)
            )
        if overlap_query.exists():
            raise ValidationError(
                "Effective period overlaps with another version of this charge template."
            )

    @property
    def amount(self):
        return self.rate

    def save(self, *args, **kwargs):
        if self.pk:
            previous = ChargeTemplate.objects.get(pk=self.pk)
            if previous.bill_lines.exists():
                protected_fields = (
                    "name",
                    "charge_type",
                    "rate",
                    "frequency",
                    "due_days",
                    "late_fee_percent",
                    "income_account_id",
                    "receivable_account_id",
                    "effective_from",
                )
                changed = [
                    field for field in protected_fields if getattr(previous, field) != getattr(self, field)
                ]
                if changed:
                    raise ValidationError(
                        "Cannot modify a used charge template. Deactivate it and create a new version."
                    )
            if previous.version_no != self.version_no:
                raise ValidationError("Template version number is immutable.")
        elif self.society_id and self.name:
            max_version = (
                ChargeTemplate.objects.filter(society_id=self.society_id, name=self.name).aggregate(
                    max_version=Max("version_no")
                )["max_version"]
                or 0
            )
            self.version_no = max_version + 1
            if self.previous_version_id and self.version_no <= self.previous_version.version_no:
                self.version_no = self.previous_version.version_no + 1

        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.bill_lines.exists():
            raise ValidationError("Cannot delete a charge template that has been used in bills.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.name} (v{self.version_no})"
