from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class VisitorCategory(models.Model):
    """Configurable visitor type.

    This is the "do not hardcode visitor types" enforcer — every category is
    data, not code. Societies seed defaults but can edit, add, or deactivate
    categories without schema changes.
    """

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="visitor_categories",
    )
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=30)
    icon = models.CharField(max_length=50, blank=True)
    is_delivery = models.BooleanField(default=False)
    is_domestic_help = models.BooleanField(default=False)
    is_contractor = models.BooleanField(default=False)
    is_emergency = models.BooleanField(default=False)
    is_resident = models.BooleanField(default=False)
    requires_approval_default = models.BooleanField(default=False)
    default_pass_type = models.ForeignKey(
        "gateops.PassType",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visitor_categories_default",
    )
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Visitor Category")
        verbose_name_plural = _("Visitor Categories")
        ordering = ("society", "sort_order", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["society", "code"],
                condition=models.Q(is_active=True),
                name="uniq_visitor_cat_code_per_society",
            ),
        ]
        indexes = [
            models.Index(fields=["society", "is_active"], name="vcat_soc_act_idx"),
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
