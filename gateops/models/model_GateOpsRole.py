from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class GateOpsRole(models.Model):
    """RBAC for gate operations.

    Extends the existing ``Membership.Role`` with gate-specific roles. Uses a
    JSON ``permissions`` field rather than a separate ``GateOpsPermission``
    model for these reasons:

    1. **Convention match** — the existing RBAC uses ``Membership.Role``
       TextChoices + ``societies/roles.py`` hierarchy, NOT a granular
       permission model. A JSON field is closer to this pattern than a full
       permission table.
    2. **Simplicity** — gate operations permissions are a fixed, well-known
       set (e.g. ``can_approve_visitor``, ``can_blacklist``,
       ``can_manage_rules``). A JSON dict is sufficient and avoids a join
       table.
    3. **Configurability** — societies can toggle permissions per role
       without migrations.
    4. **Future migration path** — if granular permissions become necessary,
       the JSON field can be migrated to a ``GateOpsPermission`` model later
       without breaking the role model.

    Standard permission keys (validated in ``clean()``):
      ``can_create_event``, ``can_approve_visitor``, ``can_blacklist``,
      ``can_manage_rules``, ``can_manage_masters``, ``can_view_analytics``,
      ``can_manage_guards``, ``can_override_rule``, ``can_export_data``.
    """

    class RoleCode(models.TextChoices):
        GATE_ADMIN = "gate_admin", _("Gate Admin")
        SECURITY_SUPERVISOR = "security_supervisor", _("Security Supervisor")
        GUARD = "guard", _("Guard")
        RECEPTION = "reception", _("Reception")
        RESIDENT = "resident", _("Resident")
        VIEWER = "viewer", _("Viewer")

    KNOWN_PERMISSION_KEYS = (
        "can_create_event",
        "can_approve_visitor",
        "can_blacklist",
        "can_manage_rules",
        "can_manage_masters",
        "can_view_analytics",
        "can_manage_guards",
        "can_override_rule",
        "can_export_data",
    )

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="gateops_roles",
    )
    name = models.CharField(max_length=100)
    code = models.CharField(
        max_length=30,
        choices=RoleCode.choices,
    )
    permissions = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Gate Ops Role")
        verbose_name_plural = _("Gate Ops Roles")
        ordering = ("society", "code")
        constraints = [
            models.UniqueConstraint(
                fields=["society", "code"],
                condition=models.Q(is_active=True),
                name="uniq_gateops_role_code_per_society",
            ),
        ]
        indexes = [
            models.Index(fields=["society", "is_active"], name="gopsrole_soc_act_idx"),
        ]

    def clean(self):
        if not isinstance(self.permissions, dict):
            raise ValidationError({"permissions": _("Permissions must be a JSON object.")})
        unknown = set(self.permissions.keys()) - set(self.KNOWN_PERMISSION_KEYS)
        if unknown:
            raise ValidationError(
                {"permissions": _("Unknown permission keys: {}").format(sorted(unknown))}
            )
        for key, value in self.permissions.items():
            if not isinstance(value, bool):
                raise ValidationError(
                    {"permissions": _("Permission '{}' must be a boolean.").format(key)}
                )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def has_perm(self, key):
        return bool(self.permissions.get(key, False))

    def __str__(self):
        return f"{self.name} ({self.code})"
