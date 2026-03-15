from django.db import models
from django.utils import timezone


class EmailQueue(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROCESSING = "PROCESSING", "Processing"
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"
        RETRY = "RETRY", "Retry"

    class EmailType(models.TextChoices):
        AUTHENTICATION = "AUTHENTICATION", "Authentication"
        BILLING = "BILLING", "Billing"
        RECEIPT = "RECEIPT", "Receipt"
        NOTICE = "NOTICE", "Notice"
        OTHER = "OTHER", "Other"

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="queued_emails",
        null=True,
        blank=True,
    )
    recipient_email = models.EmailField()
    subject = models.CharField(max_length=255)
    body = models.TextField()
    body_html = models.TextField(blank=True)
    template = models.ForeignKey(
        "housing.EmailTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="queued_emails",
    )
    context = models.JSONField(default=dict, blank=True)
    email_type = models.CharField(
        max_length=32,
        choices=EmailType.choices,
        default=EmailType.OTHER,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    retry_count = models.PositiveIntegerField(default=0)
    scheduled_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)
    smtp_used = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    from_email = models.CharField(max_length=255, blank=True)
    reply_to_email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "housing"
        ordering = ("scheduled_at", "id")

    def __str__(self):
        return f"{self.recipient_email} ({self.status})"
