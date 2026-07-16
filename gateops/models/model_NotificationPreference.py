from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class NotificationPreference(models.Model):
    """Per-society, per-visitor-category notification configuration.

    Drives the "no spam" philosophy (Phase 10). Each visitor category can have
    a preferred channel and trigger, with optional silent mode and bundling.
    """

    class Channel(models.TextChoices):
        PUSH = "push", _("Push")
        SMS = "sms", _("SMS")
        WHATSAPP = "whatsapp", _("WhatsApp")
        EMAIL = "email", _("Email")
        VOICE = "voice", _("Voice Call")
        NONE = "none", _("None")

    class Trigger(models.TextChoices):
        ARRIVAL = "arrival", _("On Arrival")
        ENTRY = "entry", _("On Entry")
        EXIT = "exit", _("On Exit")
        NEVER = "never", _("Never")
        # Phase 11: AI Recommendation Engine
        ANOMALY = "anomaly", _("On Anomaly")

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )
    visitor_category = models.ForeignKey(
        "gateops.VisitorCategory",
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )
    channel = models.CharField(
        max_length=20,
        choices=Channel.choices,
        default=Channel.PUSH,
    )
    trigger = models.CharField(
        max_length=20,
        choices=Trigger.choices,
        default=Trigger.ARRIVAL,
    )
    is_silent = models.BooleanField(default=False)
    bundle_window_minutes = models.PositiveIntegerField(
        default=0,
        help_text=_("Bundle notifications within this window. 0 = no bundling."),
    )
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Notification Preference")
        verbose_name_plural = _("Notification Preferences")
        ordering = ("society", "visitor_category", "channel")
        constraints = [
            models.UniqueConstraint(
                fields=["society", "visitor_category", "channel"],
                condition=models.Q(is_active=True),
                name="uniq_notif_pref_per_cat",
            ),
        ]
        indexes = [
            models.Index(fields=["society", "is_active"], name="notifpref_soc_act_idx"),
        ]

    def clean(self):
        if self.visitor_category.society_id != self.society_id:
            raise ValidationError(
                {"visitor_category": _("Visitor category must belong to the same society.")}
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.visitor_category} → {self.get_channel_display()} on {self.get_trigger_display()}"
