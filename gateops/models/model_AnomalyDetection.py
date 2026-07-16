from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class AnomalyDetection(models.Model):
    """Immutable audit record for a detected anomaly (Phase 11 — AI Engine).

    Each row records one suspicious event detected by the AI engine: forgotten
    exits, after-hours entries, frequency spikes, blacklist bypasses, off-pattern
    visits, duplicate entries, abnormally long stays, or suspicious patterns.

    A small state machine governs the lifecycle:
    ``OPEN`` → ``ACKNOWLEDGED`` → ``RESOLVED`` / ``FALSE_POSITIVE``.

    Soft-delete follows the established ``is_active`` + ``deleted_at`` pattern.
    """

    class AnomalyType(models.TextChoices):
        FORGOTTEN_EXIT = "forgotten_exit", _("Forgotten Exit")
        AFTER_HOURS_ENTRY = "after_hours_entry", _("After-Hours Entry")
        UNUSUAL_FREQUENCY = "unusual_frequency", _("Unusual Frequency Spike")
        BLACKLIST_BYPASS = "blacklist_bypass", _("Blacklist Bypass Attempt")
        OFF_PATTERN_VISIT = "off_pattern_visit", _("Off-Pattern Visit")
        DUPLICATE_ENTRY = "duplicate_entry", _("Duplicate Entry")
        LONG_STAY = "long_stay", _("Abnormally Long Stay")
        SUSPICIOUS_PATTERN = "suspicious_pattern", _("Suspicious Pattern")

    class Severity(models.TextChoices):
        LOW = "low", _("Low")
        MEDIUM = "medium", _("Medium")
        HIGH = "high", _("High")
        CRITICAL = "critical", _("Critical")

    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        ACKNOWLEDGED = "acknowledged", _("Acknowledged")
        RESOLVED = "resolved", _("Resolved")
        FALSE_POSITIVE = "false_positive", _("False Positive")

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="anomaly_detections",
        verbose_name=_("society"),
    )
    anomaly_type = models.CharField(
        _("anomaly type"), max_length=30, choices=AnomalyType.choices
    )
    severity = models.CharField(
        _("severity"),
        max_length=10,
        choices=Severity.choices,
        default=Severity.MEDIUM,
    )
    gate_event = models.ForeignKey(
        "gateops.GateEvent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="anomaly_detections",
        verbose_name=_("gate event"),
    )
    person = models.ForeignKey(
        "gateops.Person",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="anomaly_detections",
        verbose_name=_("person"),
    )
    gate_vehicle = models.ForeignKey(
        "gateops.GateVehicle",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="anomaly_detections",
        verbose_name=_("gate vehicle"),
    )
    description = models.TextField(_("description"))
    context = models.JSONField(_("context"), default=dict)
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    detected_at = models.DateTimeField(_("detected at"), auto_now_add=True)
    resolved_at = models.DateTimeField(_("resolved at"), null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_anomalies",
        verbose_name=_("resolved by"),
    )
    resolution_notes = models.TextField(_("resolution notes"), blank=True)
    is_active = models.BooleanField(_("active"), default=True)
    deleted_at = models.DateTimeField(_("deleted at"), null=True, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("Anomaly Detection")
        verbose_name_plural = _("Anomaly Detections")
        ordering = ("-detected_at",)
        indexes = [
            models.Index(fields=["society", "status"], name="anom_soc_status_idx"),
            models.Index(fields=["society", "anomaly_type"], name="anom_soc_type_idx"),
            models.Index(fields=["society", "severity"], name="anom_soc_sev_idx"),
            models.Index(fields=["society", "detected_at"], name="anom_soc_detected_idx"),
            models.Index(fields=["society", "is_active"], name="anom_soc_active_idx"),
        ]

    def __str__(self):
        return (
            f"Anomaly {self.pk} — {self.get_anomaly_type_display()} — "
            f"{self.severity} — {self.status}"
        )

    def clean(self):
        super().clean()
        # Cross-society guard: person must belong to the same society.
        if self.person_id is not None and self.person.society_id != self.society_id:
            raise ValidationError(
                {"person": _("Person must belong to the same society.")}
            )
        # resolved_at implies status is RESOLVED or FALSE_POSITIVE.
        if self.resolved_at is not None and self.status not in {
            self.Status.RESOLVED,
            self.Status.FALSE_POSITIVE,
        }:
            raise ValidationError(
                {"resolved_at": _("resolved_at requires status RESOLVED or FALSE_POSITIVE.")}
            )
        # status RESOLVED/FALSE_POSITIVE implies resolved_at is set.
        if (
            self.status in {self.Status.RESOLVED, self.Status.FALSE_POSITIVE}
            and self.resolved_at is None
        ):
            raise ValidationError(
                {"status": _("RESOLVED/FALSE_POSITIVE requires resolved_at to be set.")}
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
