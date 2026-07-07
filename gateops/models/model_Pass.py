from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Pass(models.Model):
    """Concrete pass issued to a person against a :class:`PassType` template.

    A ``Pass`` is the actual credential presented at the gate (QR string, OTP,
    PIN, or digital token). It is issued in Phase 5 against a person and a pass
    type, carries its own validity window and usage counters, and tracks a
    lifecycle status (active → expired / suspended / revoked).

    The ``code`` is the credential string the visitor presents; uniqueness is
    enforced per society among active passes only (a soft-deleted code may be
    reused). The ``usage_count`` / ``max_usage`` pair enforces one-time or
    limited-use passes (``max_usage=None`` means unlimited).

    Soft-delete follows the established ``is_active`` + ``deleted_at`` pattern.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        EXPIRED = "expired", _("Expired")
        SUSPENDED = "suspended", _("Suspended")
        REVOKED = "revoked", _("Revoked")

    society = models.ForeignKey(
        "housing.Society",
        on_delete=models.CASCADE,
        related_name="passes",
        verbose_name=_("society"),
    )
    person = models.ForeignKey(
        "gateops.Person",
        on_delete=models.PROTECT,
        related_name="passes",
        verbose_name=_("person"),
    )
    pass_type = models.ForeignKey(
        "gateops.PassType",
        on_delete=models.PROTECT,
        related_name="passes",
        verbose_name=_("pass type"),
    )
    code = models.CharField(_("code"), max_length=100, db_index=True)
    valid_from = models.DateTimeField(_("valid from"))
    valid_until = models.DateTimeField(_("valid until"))
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    usage_count = models.PositiveIntegerField(_("usage count"), default=0)
    max_usage = models.PositiveIntegerField(
        _("max usage"), null=True, blank=True,
        help_text=_("Maximum number of uses. Null means unlimited."),
    )
    is_active = models.BooleanField(_("active"), default=True)
    deleted_at = models.DateTimeField(_("deleted at"), null=True, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("Pass")
        verbose_name_plural = _("Passes")
        ordering = ("society", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=["society", "code"],
                condition=models.Q(is_active=True),
                name="uniq_pass_code_per_society",
            ),
        ]
        indexes = [
            models.Index(fields=["society", "status"], name="pass_soc_status_idx"),
            models.Index(fields=["society", "person"], name="pass_soc_person_idx"),
        ]

    def __str__(self):
        return f"{self.code} ({self.get_status_display()})"

    @property
    def is_valid(self):
        """Return ``True`` if the pass is currently usable at the gate.

        A pass is valid when it is ACTIVE, within its validity window, and has
        not exhausted its usage quota (``max_usage`` of ``None`` means
        unlimited). This is a read-only check; it does not mutate state.
        """
        if self.status != self.Status.ACTIVE:
            return False
        now = timezone.now()
        if not (self.valid_from <= now <= self.valid_until):
            return False
        if self.max_usage is not None and self.usage_count >= self.max_usage:
            return False
        return True

    def clean(self):
        super().clean()
        # Code is the credential string; an empty code is never valid even for
        # validation_method=NONE passes (those use a placeholder, not blank).
        if not self.code:
            raise ValidationError({"code": _("Code is required.")})
        # Validity window must be strictly ordered.
        if self.valid_from is not None and self.valid_until is not None:
            if self.valid_until <= self.valid_from:
                raise ValidationError(
                    {"valid_until": _("valid_until must be after valid_from.")}
                )
        # max_usage, when set, must be a positive integer (0 is meaningless).
        if self.max_usage is not None and self.max_usage <= 0:
            raise ValidationError(
                {"max_usage": _("max_usage must be greater than 0.")}
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
