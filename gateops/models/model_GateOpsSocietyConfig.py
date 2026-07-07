from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class GateOpsSocietyConfig(models.Model):
    """Society-level gate operations configuration.

    One-to-one with ``Society``. Every society tunes its gate ops behavior
    (approval timeouts, photo requirements, OTP length, retention, offline
    sync window, night-mode restrictions) without code changes.
    """

    society = models.OneToOneField(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="gateops_config",
    )
    default_approval_timeout_minutes = models.PositiveIntegerField(
        default=15,
        help_text=_("Minutes before an approval request escalates."),
    )
    photo_required = models.BooleanField(
        default=True,
        help_text=_("Whether arrival photos are mandatory."),
    )
    otp_length = models.PositiveIntegerField(
        default=6,
        help_text=_("OTP digit count for OTP passes."),
    )
    data_retention_days = models.PositiveIntegerField(
        default=365,
        help_text=_("Days before visitor data is anonymized (Phase 16)."),
    )
    offline_sync_window_hours = models.PositiveIntegerField(
        default=24,
        help_text=_("Max hours a guard app can operate offline before forcing sync."),
    )
    require_id_verification = models.BooleanField(
        default=False,
        help_text=_("Whether ID verification is mandatory for all visitors."),
    )
    enable_qr_pass = models.BooleanField(default=True)
    enable_otp_pass = models.BooleanField(default=True)
    enable_pin_pass = models.BooleanField(default=False)
    auto_close_enabled = models.BooleanField(
        default=True,
        help_text=_("Whether forgotten exits auto-close."),
    )
    auto_close_after_hours = models.PositiveIntegerField(
        default=12,
        help_text=_("Hours after entry before auto-close triggers."),
    )
    max_concurrent_visitors = models.PositiveIntegerField(
        default=0,
        help_text=_("Cap on visitors inside simultaneously. 0 = unlimited."),
    )
    night_mode_start = models.TimeField(null=True, blank=True)
    night_mode_end = models.TimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Gate Operations Configuration")
        verbose_name_plural = _("Gate Operations Configurations")
        indexes = [
            models.Index(fields=["society"], name="gateops_cfg_soc_idx"),
        ]

    def clean(self):
        if not 4 <= self.otp_length <= 8:
            raise ValidationError({"otp_length": _("OTP length must be between 4 and 8.")})
        if self.data_retention_days < 30:
            raise ValidationError(
                {"data_retention_days": _("Data retention must be at least 30 days.")}
            )
        if self.auto_close_after_hours < 1:
            raise ValidationError(
                {"auto_close_after_hours": _("Auto-close hours must be at least 1.")}
            )
        if bool(self.night_mode_start) != bool(self.night_mode_end):
            raise ValidationError(
                _("Night mode start and end must both be set or both be null.")
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"GateOps Config — {self.society}"
