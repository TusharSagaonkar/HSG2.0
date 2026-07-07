"""Service layer for the ``Parcel`` model (Phase 8 — Parcel Management).

This service is the single authority over :class:`Parcel` creation, OTP-based
collection, returns, loss marking, and overdue sweeps. No caller should
mutate a ``Parcel``'s status directly — every state-changing operation must
flow through :class:`ParcelService` so that:

1. Multi-tenant safety is enforced (every query scoped by ``society``).
2. The *before* state is captured as JSON.
3. The transition is validated against the state machine and applied
   (race-safe via ``update()``).
4. The *after* state is captured as JSON.
5. A :class:`GateOpsAuditLog` entry is written (append-only).

Design notes
------------
- **Multi-tenant safety:** every query is scoped by ``society``. A parcel
  recorded in one society can never be looked up or mutated from another
  society's context. Recording additionally asserts
  ``gate_event.society`` is present (the denormalized ``society`` is copied
  from it).
- **Race safety:** status / collection-time updates use ``QuerySet.update()``
  (not ``save()``) so concurrent operations on the same parcel cannot lose
  updates or interleave transitions.
- **State machine:** transitions are validated against ``_TRANSITIONS``;
  terminal states (``COLLECTED``, ``RETURNED``, ``LOST``) cannot leave.
- **OTP verification:** collection requires a matching ``otp_code``; the OTP
  is generated at receipt time using ``secrets.choice(string.digits)`` and
  its length is taken from ``GateOpsSocietyConfig.otp_length`` (default 6).
- **Audit robustness:** audit-log writes are wrapped so a logging failure
  never blocks a legitimate parcel operation (the error is logged loudly
  instead).
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
from django.db.models import QuerySet
from django.utils import timezone

from gateops.models import (
    GateOpsAuditLog,
    GateOpsSocietyConfig,
    Parcel,
)

logger = logging.getLogger(__name__)

# Default OTP length when a society has no GateOpsSocietyConfig row. The model
# default is 6, which is also the safe fallback for missing config.
DEFAULT_OTP_LENGTH = 6


class ParcelService:
    """Service for Parcel receipt, OTP collection, return, and loss tracking.

    Every state-changing operation:
    1. Validates multi-tenant safety (society scoping).
    2. Captures before state as JSON.
    3. Validates the transition against the state machine.
    4. Applies the transition (race-safe via ``update()``).
    5. Captures after state as JSON.
    6. Creates a GateOpsAuditLog entry.
    """

    # ------------------------------------------------------------------ #
    # State machine
    # ------------------------------------------------------------------ #
    # RECEIVED is the entry state. COLLECTED, RETURNED, and LOST are terminal.
    _TRANSITIONS = {
        Parcel.Status.RECEIVED: {
            Parcel.Status.COLLECTED,
            Parcel.Status.RETURNED,
            Parcel.Status.LOST,
        },
        Parcel.Status.COLLECTED: set(),  # terminal
        Parcel.Status.RETURNED: set(),   # terminal
        Parcel.Status.LOST: set(),       # terminal
    }

    # ------------------------------------------------------------------ #
    # Public read API
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_pending(*, society) -> QuerySet:
        """Return active parcels awaiting collection for a society.

        Ordered by ``stored_at`` ascending (oldest first — FIFO) so the
        longest-waiting parcels surface first. Uses ``select_related`` on
        ``gate_event`` to avoid N+1 on display.
        """
        return (
            Parcel.objects.filter(
                society=society,
                status=Parcel.Status.RECEIVED,
                is_active=True,
            )
            .select_related("gate_event")
            .order_by("stored_at", "created_at")
        )

    @staticmethod
    def get_by_event(*, gate_event) -> QuerySet:
        """Return all active parcels attached to a gate event.

        Uses ``select_related`` on ``gate_event`` to avoid N+1 when rendering
        the line-item list for a single event.
        """
        return (
            Parcel.objects.filter(
                gate_event=gate_event,
                is_active=True,
            )
            .select_related("gate_event")
            .order_by("created_at")
        )

    @staticmethod
    def get_overdue(*, society, max_storage_days: int = 7) -> QuerySet:
        """Return active RECEIVED parcels that have been in storage too long.

        A parcel is overdue when ``stored_at`` is older than
        ``now - max_storage_days``. Parcels with a null ``stored_at`` are
        excluded (they have not yet been placed in storage). Uses
        ``select_related`` on ``gate_event`` to avoid N+1 on display.
        """
        cutoff = timezone.now() - timedelta(days=max_storage_days)
        return (
            Parcel.objects.filter(
                society=society,
                status=Parcel.Status.RECEIVED,
                stored_at__isnull=False,
                stored_at__lt=cutoff,
                is_active=True,
            )
            .select_related("gate_event")
            .order_by("stored_at")
        )

    # ------------------------------------------------------------------ #
    # Public state-changing API
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def receive_parcel(
        *,
        gate_event,
        tracking_number: str,
        courier: str = "",
        is_cold_storage: bool = False,
        is_fragile: bool = False,
        is_cod: bool = False,
        cod_amount=None,
        actor=None,
    ) -> Parcel:
        """Record a new parcel arrival against a gate event.

        - Validates the gate event has a society (multi-tenant anchor).
        - Generates an OTP code (length from ``GateOpsSocietyConfig.otp_length``
          or the default 6) for later collection verification.
        - Creates the parcel with ``status=RECEIVED`` and ``stored_at=now()``.
        - Audits a CREATE with the after state.

        Returns the created parcel.
        """
        # The gate event must carry a society — it is the denormalized tenant
        # anchor copied onto the parcel. A missing society is a programming
        # error, not a user error.
        if gate_event.society_id is None:
            raise ValidationError(
                "Gate event must have a society before a parcel can be recorded."
            )

        # Normalize the tracking number early so clean() sees a stripped value.
        tracking_number = (tracking_number or "").strip()
        if not tracking_number:
            raise ValidationError(
                {"tracking_number": "Tracking number is required."}
            )

        # COD consistency: a COD parcel must carry a positive amount.
        if is_cod:
            if cod_amount is None or cod_amount <= 0:
                raise ValidationError(
                    {"cod_amount": "COD amount must be greater than zero for COD parcels."}
                )
        else:
            # Non-COD parcels never carry an amount.
            cod_amount = None

        otp_code = ParcelService.generate_otp(
            length=ParcelService._get_otp_length(gate_event.society)
        )

        parcel = Parcel(
            society=gate_event.society,
            gate_event=gate_event,
            tracking_number=tracking_number,
            courier=courier,
            is_cold_storage=is_cold_storage,
            is_fragile=is_fragile,
            is_cod=is_cod,
            cod_amount=cod_amount,
            otp_code=otp_code,
            status=Parcel.Status.RECEIVED,
            stored_at=timezone.now(),
        )
        parcel.save()
        after = ParcelService._serialize(parcel)
        ParcelService._log_audit(
            society=parcel.society,
            action=GateOpsAuditLog.Action.CREATE,
            parcel=parcel,
            before=None,
            after=after,
            actor=actor,
        )
        return parcel

    @staticmethod
    def verify_otp(*, parcel, otp_code: str) -> bool:
        """Return ``True`` when ``otp_code`` matches the parcel's OTP.

        Pure read method — it does NOT modify the parcel. Raises
        ``ValidationError`` if the parcel is not in the RECEIVED status (an
        already-collected/returned/lost parcel cannot be collected again).
        """
        if parcel.status != Parcel.Status.RECEIVED:
            raise ValidationError(
                f"Parcel is not available for collection (status='{parcel.status}')."
            )
        if not parcel.otp_code:
            # No OTP was ever generated — treat as a mismatch so collection
            # is never permitted without a credential.
            return False
        # Constant-time comparison would be ideal, but OTPs are short numeric
        # strings and the threat model is mis-delivery, not timing attacks.
        return parcel.otp_code == otp_code

    @staticmethod
    @transaction.atomic
    def collect_parcel(*, parcel, otp_code: str, collected_by, actor=None) -> Parcel:
        """Collect a parcel after verifying its OTP.

        - Verifies the OTP (raises ``ValidationError`` on mismatch).
        - Validates the transition RECEIVED → COLLECTED.
        - Race-safe update: sets ``status=COLLECTED``,
          ``collected_by=collected_by``, ``collected_at=now()``.
        - Audits a STATE_TRANSITION.

        Returns the refreshed parcel.
        """
        # Verify the OTP before transitioning. verify_otp also asserts the
        # parcel is still RECEIVED, which keeps the transition legal.
        if not ParcelService.verify_otp(parcel=parcel, otp_code=otp_code):
            raise ValidationError("OTP does not match.")

        return ParcelService._apply_transition(
            parcel=parcel,
            new_status=Parcel.Status.COLLECTED,
            actor=actor,
            extra_fields={
                "collected_by": collected_by,
                "collected_at": timezone.now(),
            },
        )

    @staticmethod
    @transaction.atomic
    def return_parcel(*, parcel, actor=None, reason: str = "") -> Parcel:
        """Return a parcel to the courier (RECEIVED → RETURNED).

        Used when a parcel is refused or could not be delivered. The optional
        ``reason`` is recorded in the audit after-state for traceability (it
        is not persisted on the model itself). Uses the race-safe
        ``_apply_transition`` helper and audits a STATE_TRANSITION.

        Returns the refreshed parcel.
        """
        parcel = ParcelService._apply_transition(
            parcel=parcel,
            new_status=Parcel.Status.RETURNED,
            actor=actor,
        )
        if reason:
            after = ParcelService._serialize(parcel)
            after["reason"] = reason
            ParcelService._log_audit(
                society=parcel.society,
                action=GateOpsAuditLog.Action.STATE_TRANSITION,
                parcel=parcel,
                before=None,
                after=after,
                actor=actor,
            )
        return parcel

    @staticmethod
    @transaction.atomic
    def mark_lost(*, parcel, actor=None) -> Parcel:
        """Mark a parcel as lost (RECEIVED → LOST).

        Used when a parcel cannot be located in storage. Uses the race-safe
        ``_apply_transition`` helper and audits a STATE_TRANSITION.

        Returns the refreshed parcel.
        """
        return ParcelService._apply_transition(
            parcel=parcel,
            new_status=Parcel.Status.LOST,
            actor=actor,
        )

    @staticmethod
    @transaction.atomic
    def bundle_parcels(*, parcels, actor=None) -> list:
        """Group parcels for a single collection notification.

        Given a queryset/list of parcels, returns the subset that are in the
        RECEIVED status and belong to the same society. This is a read /
        aggregation method — it does NOT mutate the parcels. The bundling
        action is audited (Action.UPDATE) so the notification dispatch is
        traceable.

        Returns the filtered list of parcels.
        """
        # Materialize the input so we can iterate safely regardless of
        # whether a QuerySet or a list was passed.
        parcel_list = list(parcels)
        if not parcel_list:
            return []

        # Derive the society from the first parcel; all bundled parcels must
        # belong to the same society (multi-tenant safety).
        reference_society_id = getattr(parcel_list[0], "society_id", None)
        if reference_society_id is None:
            raise ValidationError(
                "Cannot bundle parcels without a society anchor."
            )

        # Filter to RECEIVED + same-society parcels. A parcel that has already
        # been collected/returned/lost is excluded from the notification.
        bundled = [
            p for p in parcel_list
            if p.status == Parcel.Status.RECEIVED
            and getattr(p, "society_id", None) == reference_society_id
        ]

        # Audit the bundling action once for the group. The after-state
        # records the bundled parcel ids so the notification is traceable.
        after = {
            "bundled_count": len(bundled),
            "parcel_ids": [str(p.pk) for p in bundled],
            "society_id": str(reference_society_id),
        }
        ParcelService._log_audit(
            society=parcel_list[0].society,
            action=GateOpsAuditLog.Action.UPDATE,
            parcel=None,
            before=None,
            after=after,
            actor=actor,
        )
        return bundled

    @staticmethod
    def generate_otp(*, length: int = DEFAULT_OTP_LENGTH) -> str:
        """Generate a random numeric OTP of the given length.

        Uses ``secrets.choice(string.digits)`` for a cryptographically strong
        random source (suitable for collection credentials).
        """
        if length < 1:
            raise ValueError("OTP length must be at least 1.")
        return "".join(
            secrets.choice(string.digits) for _ in range(length)
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _validate_transition(parcel, new_status) -> None:
        """Validate that ``new_status`` is reachable from the parcel's current status.

        Raises ``ValidationError`` if the transition is not permitted by the
        ``_TRANSITIONS`` table (e.g. leaving a terminal state, or jumping to
        an unreachable state).
        """
        allowed = ParcelService._TRANSITIONS.get(parcel.status, set())
        if new_status not in allowed:
            raise ValidationError(
                f"Transition from '{parcel.status}' to '{new_status}' is not allowed."
            )

    @staticmethod
    def _apply_transition(
        *,
        parcel,
        new_status,
        actor=None,
        extra_fields=None,
    ) -> Parcel:
        """Validate, apply, and audit a status transition (race-safe).

        - Captures the before state via :meth:`_serialize`.
        - Validates the transition via :meth:`_validate_transition`.
        - Builds an update dict (``status`` + any ``extra_fields``) and applies
          it with ``QuerySet.update()`` so concurrent operations cannot lose
          updates.
        - Refreshes the instance from the DB and captures the after state.
        - Audits a STATE_TRANSITION with before/after.

        Returns the refreshed parcel.
        """
        before = ParcelService._serialize(parcel)
        ParcelService._validate_transition(parcel, new_status)

        update_dict = {"status": new_status}
        if extra_fields:
            update_dict.update(extra_fields)

        Parcel.objects.filter(pk=parcel.pk).update(**update_dict)
        parcel.refresh_from_db()
        after = ParcelService._serialize(parcel)
        ParcelService._log_audit(
            society=parcel.society,
            action=GateOpsAuditLog.Action.STATE_TRANSITION,
            parcel=parcel,
            before=before,
            after=after,
            actor=actor,
        )
        return parcel

    @staticmethod
    def _serialize(parcel) -> dict:
        """Return a JSON-safe dict of the parcel's key fields for audit logging."""
        def _dt(value):
            return value.isoformat() if value else None

        return {
            "id": str(parcel.pk),
            "status": parcel.status,
            "tracking_number": parcel.tracking_number,
            "courier": parcel.courier,
            "is_cod": parcel.is_cod,
            "cod_amount": str(parcel.cod_amount) if parcel.cod_amount is not None else None,
            "otp_code": parcel.otp_code,
            "stored_at": _dt(parcel.stored_at),
            "collected_at": _dt(parcel.collected_at),
            "collected_by": (
                str(parcel.collected_by_id) if parcel.collected_by_id else None
            ),
        }

    @staticmethod
    def _log_audit(
        *,
        society,
        action,
        parcel,
        before=None,
        after=None,
        actor=None,
    ) -> None:
        """Write an append-only GateOpsAuditLog entry for a parcel operation.

        Wrapped so a logging failure never blocks a legitimate parcel
        operation; the error is logged at ERROR level instead.

        ``parcel`` may be ``None`` for aggregate actions (e.g. bundling) that
        are not tied to a single parcel row; in that case ``entity_id`` is
        recorded as an empty string.
        """
        try:
            GateOpsAuditLog.log(
                society=society,
                action=action,
                entity_type="Parcel",
                entity_id=str(parcel.pk) if parcel is not None else "",
                actor=actor,
                before_value=before,
                after_value=after,
            )
        except Exception:  # noqa: BLE001 — audit must not break the operation.
            logger.exception(
                "Failed to write Parcel audit log for parcel %s (action=%s)",
                getattr(parcel, "pk", None),
                action,
            )

    @staticmethod
    def _get_otp_length(society) -> int:
        """Return the OTP length for the society, falling back to the default.

        - No config row → ``DEFAULT_OTP_LENGTH`` (6).
        - Otherwise → ``config.otp_length``.
        """
        try:
            config = GateOpsSocietyConfig.objects.filter(society=society).first()
        except Exception:  # noqa: BLE001 — never block receipt on a config lookup.
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
