"""State machine service for the ``Pass`` lifecycle (Phase 5).

This service is the single authority over :class:`Pass` status transitions and
issuance. No caller should mutate ``pass_obj.status`` or ``usage_count``
directly — every operation must flow through :class:`PassService` so that:

1. The transition is validated against the legal state machine.
2. The *before* state is captured as JSON.
3. The transition is applied (race-safe via ``update()``).
4. The *after* state is captured as JSON.
5. A :class:`GateOpsAuditLog` entry is written (append-only).

Design notes
------------
- **Multi-tenant safety:** every query is scoped by ``society``. A pass issued
  in one society can never be validated or mutated from another society's
  context. Issuance additionally asserts ``pass_type.society == person.society``.
- **Race safety:** ``usage_count`` increments and status transitions use
  ``QuerySet.update()`` (not ``save()``) so concurrent scans of the same pass
  cannot lose increments or interleave transitions.
- **Blacklist invariant:** a pass is never issued to a blacklisted person.
- **Audit robustness:** audit-log writes are wrapped so a logging failure never
  blocks a legitimate pass operation (the error is logged loudly instead).
- **All methods are ``@staticmethod``** per the service contract; there is no
  shared mutable state.
"""

from __future__ import annotations

import logging
import secrets
import string
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from gateops.models import (
    GateOpsAuditLog,
    GateOpsSocietyConfig,
    Pass,
    PassType,
    Person,
)

logger = logging.getLogger(__name__)

# Default OTP length when a society has no GateOpsSocietyConfig row. The model
# default is 6, which is also the safe fallback for missing config.
DEFAULT_OTP_LENGTH = 6

# Default PIN length for PIN-type passes (alphanumeric, uppercase).
DEFAULT_PIN_LENGTH = 6


class PassService:
    """State machine service for Pass issuance, validation, and lifecycle.

    Every state-changing operation:
    1. Validates the transition is legal (state machine)
    2. Captures before state as JSON
    3. Applies the transition (race-safe via ``update()``)
    4. Captures after state as JSON
    5. Creates a GateOpsAuditLog entry
    """

    # ------------------------------------------------------------------ #
    # State machine definition
    # ------------------------------------------------------------------ #
    #
    # Maps a *current* status to the set of statuses it may legally move to.
    # Any transition not listed here raises ``ValidationError``.
    _TRANSITIONS: dict[str, set[str]] = {
        Pass.Status.ACTIVE: {
            Pass.Status.EXPIRED,
            Pass.Status.SUSPENDED,
            Pass.Status.REVOKED,
        },
        Pass.Status.SUSPENDED: {
            Pass.Status.ACTIVE,
            Pass.Status.REVOKED,
        },
        # Terminal states: no outgoing transitions.
        Pass.Status.EXPIRED: set(),
        Pass.Status.REVOKED: set(),
    }

    # ------------------------------------------------------------------ #
    # Public lifecycle API
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def generate(
        *,
        pass_type,
        person,
        valid_from=None,
        max_usage=None,
        actor=None,
    ):
        """Issue a new pass against a :class:`PassType` template for a person.

        Validates multi-tenant safety (``pass_type.society == person.society``),
        the pass type is active, and the person is not blacklisted. Computes
        ``valid_until`` from ``valid_from + pass_type.default_validity_hours``
        and generates the credential ``code`` based on the pass type's
        validation method.

        Returns the created :class:`Pass` (status=ACTIVE, usage_count=0).
        """
        # Multi-tenant safety: pass type and person must belong to the same
        # society. A mismatched pair is a programming error, not a user error.
        if pass_type.society_id != person.society_id:
            raise ValidationError(
                "Pass type and person must belong to the same society."
            )
        if not pass_type.is_active:
            raise ValidationError("Cannot issue pass against an inactive pass type.")
        if person.is_blacklisted:
            raise ValidationError("Cannot issue pass to blacklisted person.")

        start = valid_from or timezone.now()
        valid_until = start + timedelta(hours=pass_type.default_validity_hours)
        code = PassService._generate_code(pass_type, person.society)

        pass_obj = Pass(
            society=person.society,
            person=person,
            pass_type=pass_type,
            code=code,
            valid_from=start,
            valid_until=valid_until,
            status=Pass.Status.ACTIVE,
            usage_count=0,
            max_usage=max_usage,
        )
        pass_obj.save()

        after = PassService._serialize(pass_obj)
        PassService._log_audit(
            society=pass_obj.society,
            action=GateOpsAuditLog.Action.CREATE,
            pass_obj=pass_obj,
            before=None,
            after=after,
            actor=actor,
        )
        return pass_obj

    @staticmethod
    def validate(*, society, code):
        """Validate a pass credential at the gate without consuming it.

        Looks up the active pass by ``(society, code)`` and checks that it is
        ACTIVE, within its validity window, and has not exhausted its usage
        quota. Returns the pass on success; raises ``ValidationError`` on any
        failure. Does NOT increment ``usage_count`` — use :meth:`record_usage`
        to consume a use.
        """
        pass_obj = Pass.objects.filter(
            society=society,
            code=code,
            is_active=True,
        ).first()
        if pass_obj is None:
            raise ValidationError("Pass not found")
        if pass_obj.status != Pass.Status.ACTIVE:
            raise ValidationError(
                f"Pass is {pass_obj.get_status_display().lower()}"
            )
        now = timezone.now()
        if not (pass_obj.valid_from <= now <= pass_obj.valid_until):
            raise ValidationError("Pass is not within validity window")
        if pass_obj.max_usage is not None and pass_obj.usage_count >= pass_obj.max_usage:
            raise ValidationError("Pass usage limit reached")
        return pass_obj

    @staticmethod
    @transaction.atomic
    def record_usage(*, pass_obj, actor=None):
        """Record a single use of a pass (increments ``usage_count``).

        Validates the pass is still usable, atomically increments the usage
        counter via ``update()`` (race-safe), and auto-transitions to EXPIRED
        when the usage quota is exhausted. Returns the refreshed pass.
        """
        # Re-validate the pass is still usable (status, window, quota).
        PassService.validate(
            society=pass_obj.society,
            code=pass_obj.code,
        )

        before = PassService._serialize(pass_obj)

        # Race-safe increment: a single UPDATE ... SET usage_count = usage_count + 1
        # avoids lost updates when two scanners read the same pass concurrently.
        Pass.objects.filter(pk=pass_obj.pk).update(usage_count=pass_obj.usage_count + 1)
        pass_obj.usage_count += 1

        # Auto-expire when the quota is exhausted (max_usage=None means unlimited).
        expired = (
            pass_obj.max_usage is not None
            and pass_obj.usage_count >= pass_obj.max_usage
        )
        if expired:
            Pass.objects.filter(pk=pass_obj.pk).update(status=Pass.Status.EXPIRED)
            pass_obj.status = Pass.Status.EXPIRED

        pass_obj.refresh_from_db()
        after = PassService._serialize(pass_obj)

        PassService._log_audit(
            society=pass_obj.society,
            action=GateOpsAuditLog.Action.STATE_TRANSITION,
            pass_obj=pass_obj,
            before=before,
            after=after,
            actor=actor,
        )
        return pass_obj

    @staticmethod
    @transaction.atomic
    def revoke(*, pass_obj, actor=None, reason=""):
        """Transition: ACTIVE/SUSPENDED → REVOKED.

        Revocation is a terminal state; a revoked pass can never be reused.
        The optional ``reason`` is recorded in the audit trail.
        """
        return PassService._apply_transition(
            pass_obj,
            Pass.Status.REVOKED,
            actor=actor,
            extra_notes=reason,
        )

    @staticmethod
    @transaction.atomic
    def suspend(*, pass_obj, actor=None, reason=""):
        """Transition: ACTIVE → SUSPENDED.

        A suspended pass is temporarily unusable but may be reactivated later
        (via :meth:`reactivate`) provided it is still within its validity
        window.
        """
        return PassService._apply_transition(
            pass_obj,
            Pass.Status.SUSPENDED,
            actor=actor,
            extra_notes=reason,
        )

    @staticmethod
    @transaction.atomic
    def reactivate(*, pass_obj, actor=None):
        """Transition: SUSPENDED → ACTIVE.

        Reactivation is only permitted if the pass is still within its validity
        window (a pass whose ``valid_until`` has passed cannot be revived — it
        must be reissued instead).
        """
        # A pass past its validity window cannot be revived; reissue instead.
        if pass_obj.valid_until is not None and pass_obj.valid_until <= timezone.now():
            raise ValidationError(
                "Cannot reactivate a pass that has passed its validity window."
            )
        return PassService._apply_transition(
            pass_obj,
            Pass.Status.ACTIVE,
            actor=actor,
        )

    @staticmethod
    @transaction.atomic
    def expire_expired_passes(*, society=None):
        """Bulk-transition all ACTIVE passes past their validity window to EXPIRED.

        If ``society`` is provided, only that society's passes are processed;
        otherwise all societies are processed. Returns the count of passes
        transitioned. Each transition is audited individually.
        """
        now = timezone.now()
        qs = Pass.objects.filter(
            status=Pass.Status.ACTIVE,
            is_active=True,
            valid_until__lt=now,
        )
        if society is not None:
            qs = qs.filter(society=society)

        # Materialize the list before mutating so the iterator is not invalidated.
        stale = list(qs)
        count = 0
        for pass_obj in stale:
            before = PassService._serialize(pass_obj)
            # Race-safe status update.
            Pass.objects.filter(pk=pass_obj.pk, status=Pass.Status.ACTIVE).update(
                status=Pass.Status.EXPIRED
            )
            pass_obj.status = Pass.Status.EXPIRED
            after = PassService._serialize(pass_obj)
            PassService._log_audit(
                society=pass_obj.society,
                action=GateOpsAuditLog.Action.STATE_TRANSITION,
                pass_obj=pass_obj,
                before=before,
                after=after,
                actor=None,
            )
            count += 1
        return count

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _apply_transition(pass_obj, new_status, *, actor=None, extra_notes=""):
        """Validate, apply, save, and audit a state transition.

        This is the shared spine of every transition: capture before → validate
        → mutate (race-safe via ``update()``) → refresh → capture after → audit.
        """
        before = PassService._serialize(pass_obj)
        PassService._validate_transition(pass_obj, new_status)

        # Race-safe status update: a targeted UPDATE avoids clobbering
        # concurrent usage_count increments from record_usage().
        Pass.objects.filter(pk=pass_obj.pk).update(status=new_status)
        pass_obj.status = new_status
        pass_obj.refresh_from_db()

        after = PassService._serialize(pass_obj)
        if extra_notes:
            after["reason"] = extra_notes

        PassService._log_audit(
            society=pass_obj.society,
            action=GateOpsAuditLog.Action.STATE_TRANSITION,
            pass_obj=pass_obj,
            before=before,
            after=after,
            actor=actor,
        )
        return pass_obj

    @staticmethod
    def _validate_transition(pass_obj, new_status):
        """Raise ``ValidationError`` if ``pass_obj.status → new_status`` is illegal."""
        allowed = PassService._TRANSITIONS.get(pass_obj.status, set())
        if new_status not in allowed:
            raise ValidationError(
                f"Illegal state transition: {pass_obj.status!r} → {new_status!r} "
                f"for Pass {pass_obj.pk}."
            )

    @staticmethod
    def _generate_code(pass_type, society):
        """Generate a credential code based on the pass type's validation method.

        - QR: a URL-safe token (``secrets.token_urlsafe(16)``).
        - OTP: a numeric string whose length comes from the society's
          ``GateOpsSocietyConfig.otp_length`` (default 6).
        - PIN: an uppercase alphanumeric string of length 6.
        - DIGITAL: a longer URL-safe token (``secrets.token_urlsafe(32)``).
        - NONE: an empty string (no credential presented).
        """
        method = pass_type.validation_method
        if method == PassType.ValidationMethod.QR:
            return secrets.token_urlsafe(16)
        if method == PassType.ValidationMethod.OTP:
            otp_length = PassService._get_otp_length(society)
            return "".join(
                secrets.choice(string.digits) for _ in range(otp_length)
            )
        if method == PassType.ValidationMethod.PIN:
            return "".join(
                secrets.choice(string.ascii_uppercase + string.digits)
                for _ in range(DEFAULT_PIN_LENGTH)
            )
        if method == PassType.ValidationMethod.DIGITAL:
            return secrets.token_urlsafe(32)
        # ValidationMethod.NONE — no credential code.
        return ""

    @staticmethod
    def _get_otp_length(society):
        """Return the OTP length for the society, falling back to the default.

        - No config row → ``DEFAULT_OTP_LENGTH`` (6).
        - Otherwise → ``config.otp_length``.
        """
        try:
            config = GateOpsSocietyConfig.objects.filter(society=society).first()
        except Exception:  # noqa: BLE001 — never block issuance on a config lookup.
            logger.exception(
                "Failed to load GateOpsSocietyConfig for society %s; "
                "defaulting OTP length to %s.",
                society,
                DEFAULT_OTP_LENGTH,
            )
            return DEFAULT_OTP_LENGTH
        if config is None:
            return DEFAULT_OTP_LENGTH
        return config.otp_length

    @staticmethod
    def _serialize(pass_obj):
        """Return a JSON-safe dict of the pass's key fields for audit logging."""
        def _dt(value):
            return value.isoformat() if value else None

        return {
            "id": str(pass_obj.pk),
            "code": pass_obj.code,
            "status": pass_obj.status,
            "usage_count": pass_obj.usage_count,
            "valid_from": _dt(pass_obj.valid_from),
            "valid_until": _dt(pass_obj.valid_until),
        }

    @staticmethod
    def _log_audit(*, society, action, pass_obj, before=None, after=None, actor=None):
        """Write an append-only GateOpsAuditLog entry for a pass operation.

        Wrapped so a logging failure never blocks a legitimate pass operation;
        the error is logged at ERROR level instead.
        """
        try:
            GateOpsAuditLog.log(
                society=society,
                action=action,
                entity_type="Pass",
                entity_id=str(pass_obj.pk),
                actor=actor,
                before_value=before,
                after_value=after,
            )
        except Exception:  # noqa: BLE001 — audit must not break the operation.
            logger.exception(
                "Failed to write Pass audit log for pass %s (action=%s)",
                pass_obj.pk,
                action,
            )
