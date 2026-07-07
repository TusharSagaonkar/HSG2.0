from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class GateOpsAuditLog(models.Model):
    """Centralized append-only audit log for ALL gate operations.

    Built in Phase 1 to satisfy Phase 16 audit/security requirements. Follows
    the ``PeriodStatusLog`` / ``EmailLog`` append-only pattern but with richer
    before/after JSON, GPS, device info, and rule linkage.

    Immutability is enforced at the model level:
      - ``save()`` rejects updates (only inserts allowed).
      - ``delete()`` raises ``PermissionError`` (no deletion path).

    Use the ``log()`` classmethod for ergonomic creation.
    """

    class Action(models.TextChoices):
        CREATE = "create", _("Create")
        UPDATE = "update", _("Update")
        DELETE = "delete", _("Delete")
        APPROVE = "approve", _("Approve")
        REJECT = "reject", _("Reject")
        ENTRY = "entry", _("Entry")
        EXIT = "exit", _("Exit")
        RULE_EVALUATED = "rule_evaluated", _("Rule Evaluated")
        STATE_TRANSITION = "state_transition", _("State Transition")
        BLACKLIST = "blacklist", _("Blacklist")
        ESCALATE = "escalate", _("Escalate")

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="gateops_audit_logs",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gateops_actions",
    )
    action = models.CharField(max_length=30, choices=Action.choices)
    entity_type = models.CharField(max_length=50)
    entity_id = models.CharField(max_length=50)
    before_value = models.JSONField(null=True, blank=True)
    after_value = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    device_info = models.JSONField(default=dict, blank=True)
    gps_lat = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )
    gps_lng = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )
    # NOTE: `rule` FK to gateops.Rule is intentionally omitted in Phase 1.
    # `Rule` is a Phase 2 model; a string FK to a non-existent model breaks app
    # loading (Django resolves lazy relations at registry-ready time). Phase 2
    # will add this FK via an additive migration:
    #   rule = models.ForeignKey("gateops.Rule", on_delete=models.SET_NULL,
    #       null=True, blank=True, related_name="audit_logs")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Gate Ops Audit Log")
        verbose_name_plural = _("Gate Ops Audit Logs")
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["society", "entity_type", "entity_id"], name="gopsaudit_ent_idx"),
            models.Index(fields=["society", "created_at"], name="gopsaudit_created_idx"),
            models.Index(fields=["society", "action"], name="gopsaudit_action_idx"),
        ]

    @classmethod
    def log(
        cls,
        *,
        society,
        action,
        entity_type,
        entity_id,
        actor=None,
        before_value=None,
        after_value=None,
        ip_address=None,
        device_info=None,
        gps_lat=None,
        gps_lng=None,
    ):
        """Create an append-only audit log entry.

        This is the canonical way to write audit records. It always performs an
        INSERT (never an update) and returns the created instance.

        ``device_info`` accepts a dict (stored as JSON). When omitted, an empty
        dict is persisted via the field default.

        Note: a ``rule`` parameter will be added in Phase 2 once the ``Rule``
        model exists; the FK is intentionally omitted in Phase 1 (see field
        comment above).
        """
        return cls.objects.create(
            society=society,
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            before_value=before_value,
            after_value=after_value,
            ip_address=ip_address,
            device_info=device_info if device_info is not None else {},
            gps_lat=gps_lat,
            gps_lng=gps_lng,
        )

    def save(self, *args, **kwargs):
        """Append-only: reject updates, allow inserts only."""
        if self.pk is not None and not kwargs.pop("_force_insert", False):
            raise PermissionError(
                "GateOpsAuditLog is append-only; updating existing records is not permitted."
            )
        kwargs["force_insert"] = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Append-only: deletion is forbidden."""
        raise PermissionError(
            "GateOpsAuditLog is append-only; deletion is not permitted."
        )

    def __str__(self):
        return f"{self.action} {self.entity_type}:{self.entity_id} @ {self.created_at:%Y-%m-%d %H:%M:%S}"
