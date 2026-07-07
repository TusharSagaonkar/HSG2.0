from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class MaterialCategory(models.Model):
    """Configurable material type for material gate pass (Phase 7).

    Each category has a default direction (inbound/outbound) and a default
    approval requirement, both overridable per gate event.
    """

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="material_categories",
    )
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=30)
    is_inbound_default = models.BooleanField(default=True)
    requires_approval_default = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Material Category")
        verbose_name_plural = _("Material Categories")
        ordering = ("society", "sort_order", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["society", "code"],
                condition=models.Q(is_active=True),
                name="uniq_material_cat_code_per_society",
            ),
        ]
        indexes = [
            models.Index(fields=["society", "is_active"], name="matcat_soc_act_idx"),
        ]

    def clean(self):
        if not self.code:
            raise ValidationError({"code": _("Code is required.")})
        if not self.code.isupper():
            raise ValidationError({"code": _("Code must be uppercase.")})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.code})"
