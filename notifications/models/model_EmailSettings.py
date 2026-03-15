from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from notifications.crypto import decrypt_email_secret
from notifications.crypto import encrypt_email_secret


class EmailProviderType(models.TextChoices):
    SMTP = "SMTP", "SMTP"
    SES = "SES", "SES"
    SENDGRID = "SENDGRID", "SendGrid"
    MAILGUN = "MAILGUN", "Mailgun"


class BaseEmailSettings(models.Model):
    provider_type = models.CharField(
        max_length=20,
        choices=EmailProviderType.choices,
        default=EmailProviderType.SMTP,
    )
    smtp_host = models.CharField(max_length=255)
    smtp_port = models.PositiveIntegerField(default=587)
    smtp_username = models.CharField(max_length=255, blank=True)
    smtp_password_encrypted = models.TextField(blank=True)
    use_tls = models.BooleanField(default=True)
    use_ssl = models.BooleanField(default=False)
    default_from_email = models.CharField(max_length=255)
    default_reply_to = models.EmailField(blank=True)
    daily_limit = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def clean(self):
        if self.use_tls and self.use_ssl:
            msg = "TLS and SSL cannot both be enabled."
            raise ValidationError(msg)

    @property
    def smtp_password(self) -> str:
        if not self.smtp_password_encrypted:
            return ""
        return decrypt_email_secret(self.smtp_password_encrypted)

    @smtp_password.setter
    def smtp_password(self, raw_value: str) -> None:
        self.smtp_password_encrypted = encrypt_email_secret(raw_value or "")

    def set_smtp_password(self, raw_value: str) -> None:
        self.smtp_password = raw_value


class GlobalEmailSettings(BaseEmailSettings):
    active = models.BooleanField(default=True)

    class Meta:
        app_label = "housing"
        ordering = ("-updated_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("active",),
                condition=Q(active=True),
                name="uniq_active_global_email_settings",
            ),
        ]

    def __str__(self):
        return f"Global email settings ({self.provider_type})"


class SocietyEmailSettings(BaseEmailSettings):
    society = models.OneToOneField(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="email_settings",
    )
    is_active = models.BooleanField(default=False)

    class Meta:
        app_label = "housing"
        ordering = ("society__name",)

    def __str__(self):
        return f"{self.society} email settings ({self.provider_type})"
