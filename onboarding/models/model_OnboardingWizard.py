from django.conf import settings
from django.db import models

from societies.managers import TenantManager


class OnboardingWizard(models.Model):
    """Wizard session/state for the Society Creation & Accounting Migration Wizard.

    One record per wizard attempt. Tracks the current step, society type
    (brand new vs. migrating), selected modules, accumulated step data, and
    lifecycle timestamps. Tenant-scoped via :class:`TenantManager` once a
    society is associated (Step 1).
    """

    class SocietyType(models.TextChoices):
        NEW = "NEW", "Brand New Society"
        EXISTING = "EXISTING", "Existing Society (Migrating)"

    class Status(models.TextChoices):
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        COMPLETED = "COMPLETED", "Completed"
        ABANDONED = "ABANDONED", "Abandoned"

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.SET_NULL,
        null=True,
        related_name="onboarding_wizards",
    )
    current_step = models.PositiveIntegerField(default=1)
    society_type = models.CharField(
        max_length=20,
        choices=SocietyType.choices,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.IN_PROGRESS,
    )
    selected_modules = models.JSONField(default=list)
    wizard_data = models.JSONField(default=dict)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
    )
    resumed_count = models.PositiveIntegerField(default=0)
    is_finalized = models.BooleanField(default=False)

    objects = TenantManager()

    class Meta:
        app_label = "onboarding"
        ordering = ["-started_at"]

    def __str__(self):
        society_name = self.society.name if self.society_id else "Unstarted"
        return f"Wizard #{self.pk} — {society_name} ({self.status})"
