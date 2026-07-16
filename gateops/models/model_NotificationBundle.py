from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from gateops.models.model_NotificationPreference import NotificationPreference


class NotificationBundle(models.Model):
    """A bundled set of gate notifications dispatched within a time window.

    Phase 10 — Smart Notification Engine. When a visitor category is
    configured with a non-zero ``bundle_window_minutes`` (see
    :class:`NotificationPreference`), individual gate events for the same
    host unit are accumulated into a single bundle and dispatched together
    once the window elapses. This model is the audit record of that bundle:
    which events were grouped, who received the notification, which channel
    was used, and whether it was sent or skipped.

    The ``society`` FK scopes every bundle to a tenant (matching the
    established pattern in :class:`Parcel` / :class:`GateEvent`). The
    ``host_unit`` FK uses ``SET_NULL`` so historical bundles survive even if
    the unit is later deleted. ``gate_events`` is a many-to-many link to the
    :class:`GateEvent` rows that were bundled together.

    Soft-delete follows the established ``is_active`` + ``deleted_at``
    pattern. The ``status`` field drives a small state machine
    (``PENDING`` → ``SENT`` / ``SKIPPED``).
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        SENT = "sent", _("Sent")
        SKIPPED = "skipped", _("Skipped")

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="notification_bundles",
        verbose_name=_("society"),
    )
    visitor_category = models.ForeignKey(
        "gateops.VisitorCategory",
        on_delete=models.CASCADE,
        related_name="notification_bundles",
        verbose_name=_("visitor category"),
    )
    host_unit = models.ForeignKey(
        "housing.Unit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notification_bundles",
        verbose_name=_("host unit"),
    )
    trigger = models.CharField(
        _("trigger"),
        max_length=20,
        choices=NotificationPreference.Trigger.choices,
        help_text=_("The trigger this bundle was created for."),
    )
    gate_events = models.ManyToManyField(
        "gateops.GateEvent",
        related_name="notification_bundles",
        blank=True,
        verbose_name=_("gate events"),
    )
    recipient_email = models.EmailField(
        _("recipient email"), blank=True,
    )
    channel = models.CharField(
        _("channel"),
        max_length=20,
        choices=NotificationPreference.Channel.choices,
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    bundle_window_minutes = models.PositiveIntegerField(
        _("bundle window minutes"),
        default=0,
        help_text=_("The bundling window (in minutes) that was used for this bundle."),
    )
    dispatched_at = models.DateTimeField(
        _("dispatched at"), null=True, blank=True,
    )
    email_queue = models.ForeignKey(
        "housing.EmailQueue",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notification_bundles",
        verbose_name=_("email queue"),
    )
    is_active = models.BooleanField(_("active"), default=True)
    deleted_at = models.DateTimeField(_("deleted at"), null=True, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("Notification Bundle")
        verbose_name_plural = _("Notification Bundles")
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["society", "is_active"],
                name="notifbundle_soc_active_idx",
            ),
            models.Index(
                fields=["society", "host_unit", "is_active"],
                name="nb_soc_unit_active_idx",
            ),
            models.Index(
                fields=["society", "status", "is_active"],
                name="notifbundle_soc_status_idx",
            ),
        ]

    def __str__(self):
        return f"Bundle {self.pk} — {self.visitor_category.code} — {self.get_status_display()}"

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    def clean(self):
        super().clean()
        # Cross-society guard: the visitor category must belong to the same
        # society as the bundle (prevents tenant data leakage).
        if self.visitor_category_id is not None and self.society_id is not None:
            if self.visitor_category.society_id != self.society_id:
                raise ValidationError(
                    {"visitor_category": _("Visitor category must belong to the same society.")}
                )

    def save(self, *args, **kwargs):
        # Model-level validation runs on every save() (single-row writes).
        # Bulk operations (update()/bulk_create) bypass save() and therefore
        # bypass clean() — the service layer is responsible for validating
        # those paths.
        self.clean()
        super().save(*args, **kwargs)
