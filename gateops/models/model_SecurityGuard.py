import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class SecurityGuard(models.Model):
    """Guard profile.

    Links to ``users.User`` if the guard has a login, OR standalone with
    name/phone/photo for agency-supplied guards without app accounts.
    """

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="security_guards",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="guard_profiles",
    )
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20, blank=True)
    badge_number = models.CharField(max_length=50, blank=True)
    photo = models.ImageField(null=True, blank=True, upload_to="gateops/guards/")
    agency_name = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Security Guard")
        verbose_name_plural = _("Security Guards")
        ordering = ("society", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["society", "badge_number"],
                condition=models.Q(is_active=True, badge_number__gt=""),
                name="uniq_badge_per_society",
            ),
        ]
        indexes = [
            models.Index(fields=["society", "is_active"], name="guard_soc_act_idx"),
        ]

    def clean(self):
        if not self.user and not self.name:
            raise ValidationError(_("Either a user or a name must be set for a guard."))
        if self.phone:
            digits = re.sub(r"\D", "", self.phone)
            if len(digits) != 10:
                raise ValidationError({"phone": _("Phone must contain exactly 10 digits.")})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.badge_number})" if self.badge_number else self.name
