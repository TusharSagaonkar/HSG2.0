"""Platform-wide append-only audit log.

Models the proven ``GateOpsAuditLog`` pattern (append-only, ``save()`` rejects
updates, ``delete()`` raises ``PermissionError``, ``log()`` classmethod) but
extends it with request tracing, session, user-agent, module, duration and
reason fields for enterprise-grade observability across ALL apps — not just
gate operations.

Immutability is enforced at the model level:
  - ``save()`` rejects updates (only inserts allowed).
  - ``delete()`` raises ``PermissionError`` (no deletion path).

Use the ``log()`` classmethod for ergonomic creation.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class AuditLog(models.Model):
    """Centralized append-only audit log for the entire platform.

    Built in Phase 1 of the Enterprise Security Architecture to provide a
    single, tamper-evident audit trail spanning all modules (accounting,
    housing, gateops, parking, reconciliation, shares, etc.).

    Every mutating business action should write a record here via ``log()``.
    """

    class Action(models.TextChoices):
        CREATE = "create", _("Create")
        UPDATE = "update", _("Update")
        DELETE = "delete", _("Delete")
        APPROVE = "approve", _("Approve")
        REJECT = "reject", _("Reject")
        POST = "post", _("Post")
        REVERSE = "reverse", _("Reverse")
        CANCEL = "cancel", _("Cancel")
        LOCK = "lock", _("Lock")
        UNLOCK = "unlock", _("Unlock")
        EXPORT = "export", _("Export")
        PRINT = "print", _("Print")
        RESTORE = "restore", _("Restore")
        ARCHIVE = "archive", _("Archive")
        LOGIN = "login", _("Login")
        LOGOUT = "logout", _("Logout")
        IMPERSONATE = "impersonate", _("Impersonate")
        TENANT_SWITCH = "tenant_switch", _("Tenant Switch")
        PERMISSION_CHANGE = "permission_change", _("Permission Change")
        ROLE_CHANGE = "role_change", _("Role Change")

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_actions",
    )
    action = models.CharField(max_length=30, choices=Action.choices)
    entity_type = models.CharField(max_length=50)
    entity_id = models.CharField(max_length=50)
    before_value = models.JSONField(null=True, blank=True)
    after_value = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    device_info = models.JSONField(default=dict, blank=True)
    # Enterprise observability fields beyond GateOpsAuditLog:
    request_id = models.CharField(max_length=50, null=True, blank=True)
    session_id = models.CharField(max_length=50, null=True, blank=True)
    user_agent = models.CharField(max_length=500, null=True, blank=True)
    module = models.CharField(max_length=50, null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    reason = models.CharField(max_length=500, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Audit Log")
        verbose_name_plural = _("Audit Logs")
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["society", "entity_type", "entity_id"], name="auditlog_ent_idx"),
            models.Index(fields=["society", "created_at"], name="auditlog_created_idx"),
            models.Index(fields=["society", "action"], name="auditlog_action_idx"),
            models.Index(fields=["actor", "created_at"], name="auditlog_actor_idx"),
            models.Index(fields=["request_id"], name="auditlog_reqid_idx"),
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
        request_id=None,
        session_id=None,
        user_agent=None,
        module=None,
        duration_ms=None,
        reason=None,
    ):
        """Create an append-only audit log entry.

        This is the canonical way to write audit records. It always performs an
        INSERT (never an update) and returns the created instance.

        ``device_info`` accepts a dict (stored as JSON). When omitted, an empty
        dict is persisted via the field default.

        Args:
            society: The society (tenant) the action occurred in.
            action: One of ``Action`` choices.
            entity_type: Logical entity name (e.g. ``"voucher"``).
            entity_id: Primary key (as string) of the affected entity.
            actor: The user performing the action (nullable for system events).
            before_value: JSON snapshot of the entity before the action.
            after_value: JSON snapshot of the entity after the action.
            ip_address: Requester IP address.
            device_info: Arbitrary dict of device/client metadata.
            request_id: Correlation ID for request tracing.
            session_id: Session identifier.
            user_agent: Browser/client identifier string.
            module: Which module/app generated the action (e.g. ``"accounting"``).
            duration_ms: Action duration in milliseconds.
            reason: User-supplied reason for the action.

        Returns:
            The created ``AuditLog`` instance.
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
            request_id=request_id,
            session_id=session_id,
            user_agent=user_agent,
            module=module,
            duration_ms=duration_ms,
            reason=reason,
        )

    def save(self, *args, **kwargs):
        """Append-only: reject updates, allow inserts only."""
        if self.pk is not None and not kwargs.pop("_force_insert", False):
            raise PermissionError(
                "AuditLog is append-only; updating existing records is not permitted."
            )
        kwargs["force_insert"] = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Append-only: deletion is forbidden."""
        raise PermissionError(
            "AuditLog is append-only; deletion is not permitted."
        )

    def __str__(self):
        return f"{self.action} {self.entity_type}:{self.entity_id} @ {self.created_at:%Y-%m-%d %H:%M:%S}"
