from django.conf import settings as django_settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class MasterSettings(models.Model):
    """Society master settings umbrella.

    A flexible JSON store for miscellaneous configurable items not covered by
    ``GateOpsSocietyConfig`` (e.g. ``default_language``, ``enable_face_match``).
    Allows future settings without schema changes.
    """

    society = models.OneToOneField(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="gateops_master_settings",
    )
    settings = models.JSONField(default=dict)
    updated_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gateops_master_settings_updates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Master Settings")
        verbose_name_plural = _("Master Settings")
        indexes = [
            models.Index(fields=["society"], name="gopsmaster_soc_idx"),
        ]

    def __str__(self):
        return f"Master Settings — {self.society}"
