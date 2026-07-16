from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _


class VisitorPattern(models.Model):
    """Aggregated visit-history pattern for a person (Phase 11 — AI Engine).

    One row per ``(society, person)`` — updated incrementally as new gate
    events arrive. Captures visit frequency, typical schedule, risk score, and
    an AI-suggested visitor category for faster gate processing.

    Soft-delete follows the established ``is_active`` + ``deleted_at`` pattern.
    The conditional unique constraint on ``(society, person)`` only enforces
    uniqueness among active patterns, so a soft-deleted pattern can be replaced
    by a fresh one for the same person.
    """

    class RiskLevel(models.TextChoices):
        LOW = "low", _("Low")            # 0.00 – 0.24
        MEDIUM = "medium", _("Medium")    # 0.25 – 0.49
        HIGH = "high", _("High")          # 0.50 – 0.74
        CRITICAL = "critical", _("Critical")  # 0.75 – 1.00

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="visitor_patterns",
        verbose_name=_("society"),
    )
    person = models.ForeignKey(
        "gateops.Person",
        on_delete=models.PROTECT,
        related_name="visitor_patterns",
        verbose_name=_("person"),
    )
    gate_vehicle = models.ForeignKey(
        "gateops.GateVehicle",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visitor_patterns",
        verbose_name=_("gate vehicle"),
    )
    visitor_category = models.ForeignKey(
        "gateops.VisitorCategory",
        on_delete=models.PROTECT,
        related_name="visitor_patterns",
        verbose_name=_("visitor category"),
    )
    suggested_category = models.ForeignKey(
        "gateops.VisitorCategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="suggested_in_patterns",
        verbose_name=_("suggested category"),
    )
    visit_count = models.PositiveIntegerField(_("visit count"), default=0)
    first_visit_at = models.DateTimeField(_("first visit at"), null=True, blank=True)
    last_visit_at = models.DateTimeField(_("last visit at"), null=True, blank=True)
    last_event = models.ForeignKey(
        "gateops.GateEvent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visitor_patterns",
        verbose_name=_("last event"),
    )
    avg_visit_duration_minutes = models.PositiveIntegerField(
        _("average visit duration (minutes)"), null=True, blank=True
    )
    typical_visit_days = models.JSONField(_("typical visit days"), default=list)
    typical_time_window = models.JSONField(_("typical time window"), default=dict)
    is_frequent = models.BooleanField(_("is frequent"), default=False)
    frequency_score = models.FloatField(_("frequency score"), default=0.0)
    risk_score = models.FloatField(_("risk score"), default=0.0)
    risk_level = models.CharField(
        _("risk level"),
        max_length=10,
        choices=RiskLevel.choices,
        default=RiskLevel.LOW,
    )
    last_analyzed_at = models.DateTimeField(_("last analyzed at"), null=True, blank=True)
    is_active = models.BooleanField(_("active"), default=True)
    deleted_at = models.DateTimeField(_("deleted at"), null=True, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("Visitor Pattern")
        verbose_name_plural = _("Visitor Patterns")
        ordering = ("-last_visit_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["society", "person"],
                condition=Q(is_active=True),
                name="unique_active_visitor_pattern_per_society",
            ),
        ]
        indexes = [
            models.Index(fields=["society", "is_frequent"], name="vpat_soc_freq_idx"),
            models.Index(fields=["society", "risk_level"], name="vpat_soc_risk_idx"),
            models.Index(fields=["society", "last_visit_at"], name="vpat_soc_last_idx"),
            models.Index(fields=["society", "is_active"], name="vpat_soc_active_idx"),
        ]

    def __str__(self):
        return (
            f"Pattern — {self.person} — {self.visit_count} visits — "
            f"{self.risk_level}"
        )

    def clean(self):
        super().clean()
        # Cross-society guard: person must belong to the same society.
        if self.person_id is not None and self.person.society_id != self.society_id:
            raise ValidationError(
                {"person": _("Person must belong to the same society.")}
            )
        # Cross-society guard: visitor_category must belong to the same society.
        if (
            self.visitor_category_id is not None
            and self.visitor_category.society_id != self.society_id
        ):
            raise ValidationError(
                {"visitor_category": _("Visitor category must belong to the same society.")}
            )
        # Cross-society guard: suggested_category must belong to the same society.
        if (
            self.suggested_category_id is not None
            and self.suggested_category.society_id != self.society_id
        ):
            raise ValidationError(
                {"suggested_category": _("Suggested category must belong to the same society.")}
            )
        # risk_score must be in [0.0, 1.0].
        if not (0.0 <= self.risk_score <= 1.0):
            raise ValidationError(
                {"risk_score": _("risk_score must be between 0.0 and 1.0.")}
            )
        # frequency_score must be in [0.0, 1.0].
        if not (0.0 <= self.frequency_score <= 1.0):
            raise ValidationError(
                {"frequency_score": _("frequency_score must be between 0.0 and 1.0.")}
            )
        # risk_level must be consistent with risk_score.
        expected = VisitorPattern._risk_level_for_score(self.risk_score)
        if self.risk_level != expected:
            raise ValidationError(
                {"risk_level": _(f"risk_level {self.risk_level} does not match "
                                 f"risk_score {self.risk_score} (expected {expected}).")}
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    @staticmethod
    def _risk_level_for_score(score: float) -> str:
        """Map a 0.0–1.0 risk score to its RiskLevel label."""
        if score >= 0.75:
            return VisitorPattern.RiskLevel.CRITICAL
        if score >= 0.50:
            return VisitorPattern.RiskLevel.HIGH
        if score >= 0.25:
            return VisitorPattern.RiskLevel.MEDIUM
        return VisitorPattern.RiskLevel.LOW
