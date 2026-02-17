from django.db import models
from django.utils import timezone


class ReminderLog(models.Model):
    class Channel(models.TextChoices):
        EMAIL = "EMAIL", "Email"
        SMS = "SMS", "SMS"
        WHATSAPP = "WHATSAPP", "WhatsApp"

    class ReminderStatus(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="reminder_logs",
    )
    member = models.ForeignKey(
        "housing.Member",
        on_delete=models.PROTECT,
        related_name="reminders",
    )
    bill = models.ForeignKey(
        "housing.Bill",
        on_delete=models.PROTECT,
        related_name="reminders",
    )
    channel = models.CharField(max_length=20, choices=Channel.choices)
    message = models.TextField()
    scheduled_for = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, choices=ReminderStatus.choices, default=ReminderStatus.QUEUED)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "housing"
        ordering = ("-created_at", "-id")

    def __str__(self):
        return f"{self.channel} reminder for {self.member.full_name}"
