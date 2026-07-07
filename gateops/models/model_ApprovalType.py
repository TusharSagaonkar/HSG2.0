from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class ApprovalType(models.Model):
    """Configurable approval workflow definition.

    Defines who approves a gate event (auto/resident/security/admin/committee)
    and how long before the request escalates.
    """

    class Approver(models.TextChoices):
        AUTO = "auto", _("Auto Approve")
        RESIDENT = "resident", _("Resident")
        SECURITY = "security", _("Security Supervisor")
        ADMIN = "admin", _("Society Admin")
        COMMITTEE = "committee", _("Committee")

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="approval_types",
    )
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=30)
    approver = models.CharField(
        max_length=20,
        choices=Approver.choices,
        default=Approver.RESIDENT,
    )
    escalation_timeout_minutes = models.PositiveIntegerField(default=15)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Approval Type")
        verbose_name_plural = _("Approval Types")
        ordering = ("society", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["society", "code"],
                condition=models.Q(is_active=True),
                name="uniq_approval_type_code_per_society",
            ),
        ]
        indexes = [
            models.Index(fields=["society", "is_active"], name="apptype_soc_act_idx"),
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
