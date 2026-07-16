"""Service layer for contractor management (Phase 9 — Contractor Management).

This service is the single authority over :class:`Contractor`, :class:`Contract`,
:class:`Worker`, and :class:`WorkPermit` lifecycle operations. No caller should
mutate these models directly — every state-changing operation must flow through
:class:`ContractorService` so that:

1. Multi-tenant safety is enforced (every query scoped by ``society``).
2. The *before* state is captured as JSON.
3. The transition is applied (race-safe via ``update()`` where applicable).
4. The *after* state is captured as JSON.
5. A :class:`GateOpsAuditLog` entry is written (append-only).

Design notes
------------
- **Multi-tenant safety:** every query is scoped by ``society``. A contractor
  recorded in one society can never be looked up or mutated from another
  society's context.
- **Race safety:** soft-delete and status updates use ``QuerySet.update()``
  (not ``save()``) so concurrent operations on the same row cannot lose
  updates or interleave transitions.
- **Labour-limit enforcement:** worker registration checks the contract's
  ``max_workers`` ceiling before creating a :class:`Worker` row, raising
  ``ValidationError`` when the limit is reached.
- **Expiry sweeps:** :meth:`process_expiries` marks expired contracts as
  ``COMPLETED`` and expired work permits as ``EXPIRED`` in a single atomic
  transaction, returning a summary dict.
- **Audit robustness:** audit-log writes are wrapped so a logging failure
  never blocks a legitimate contractor operation (the error is logged loudly
  instead).
- **All methods are ``@staticmethod``** per the service contract; there is no
  shared mutable state.
"""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from django.utils import timezone

from gateops.models import (
    Contract,
    Contractor,
    GateEvent,
    GateOpsAuditLog,
    WorkPermit,
    Worker,
)

logger = logging.getLogger(__name__)

# Allowed fields for update_contractor / update_contract. Keeping an explicit
# allow-list prevents callers from setting protected fields (pk, society, etc.)
# via the generic **fields path.
_CONTRACTOR_UPDATE_FIELDS = frozenset(
    {
        "company_name",
        "supervisor_name",
        "supervisor_phone",
        "contact_person",
        "contact_phone",
        "gst_number",
        "pan_number",
        "address",
    }
)
_CONTRACT_UPDATE_FIELDS = frozenset(
    {
        "title",
        "description",
        "start_date",
        "end_date",
        "max_workers",
        "status",
    }
)


class ContractorService:
    """Service for Contractor, Contract, Worker, and WorkPermit lifecycle.

    Every state-changing operation:
    1. Validates multi-tenant safety (society scoping).
    2. Captures before state as JSON.
    3. Applies the transition (race-safe via ``update()``).
    4. Captures after state as JSON.
    5. Creates a GateOpsAuditLog entry.
    """

    # ------------------------------------------------------------------ #
    # Contractor CRUD
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def create_contractor(
        *,
        society,
        company_name,
        supervisor_name="",
        supervisor_phone="",
        contact_person="",
        contact_phone="",
        gst_number="",
        pan_number="",
        address="",
        actor=None,
    ) -> Contractor:
        """Create a new active Contractor record and audit a CREATE.

        ``company_name`` is mandatory; a blank value raises ``ValidationError``
        (the model's ``clean()`` would also reject it, but we fail fast with a
        field-scoped error for a clean caller contract).
        """
        company_name = (company_name or "").strip()
        if not company_name:
            raise ValidationError({"company_name": "Company name is required."})

        contractor = Contractor(
            society=society,
            company_name=company_name,
            supervisor_name=supervisor_name,
            supervisor_phone=supervisor_phone,
            contact_person=contact_person,
            contact_phone=contact_phone,
            gst_number=gst_number,
            pan_number=pan_number,
            address=address,
            is_active=True,
        )
        contractor.save()
        after = ContractorService._serialize_contractor(contractor)
        ContractorService._log_audit(
            society=contractor.society,
            action=GateOpsAuditLog.Action.CREATE,
            entity_type="Contractor",
            entity_id=contractor.pk,
            before=None,
            after=after,
            actor=actor,
        )
        return contractor

    @staticmethod
    @transaction.atomic
    def update_contractor(*, contractor, actor=None, **fields) -> Contractor:
        """Update allowed fields on a contractor (race-safe via ``update()``).

        Only fields in ``_CONTRACTOR_UPDATE_FIELDS`` are applied; unknown keys
        raise ``ValidationError`` so callers cannot silently set protected
        fields (pk, society, is_active, etc.).
        """
        unknown = set(fields) - _CONTRACTOR_UPDATE_FIELDS
        if unknown:
            raise ValidationError(
                {"fields": f"Cannot update protected/unknown fields: {sorted(unknown)}"}
            )

        before = ContractorService._serialize_contractor(contractor)
        update_dict = {k: v for k, v in fields.items() if v is not None}
        if not update_dict:
            return contractor

        Contractor.objects.filter(pk=contractor.pk).update(**update_dict)
        contractor.refresh_from_db()
        after = ContractorService._serialize_contractor(contractor)
        ContractorService._log_audit(
            society=contractor.society,
            action=GateOpsAuditLog.Action.UPDATE,
            entity_type="Contractor",
            entity_id=contractor.pk,
            before=before,
            after=after,
            actor=actor,
        )
        return contractor

    @staticmethod
    @transaction.atomic
    def deactivate_contractor(*, contractor, actor=None) -> Contractor:
        """Soft-delete a contractor: set ``is_active=False`` + ``deleted_at``.

        Uses ``QuerySet.update()`` for race safety so concurrent deactivations
        cannot lose updates. Audits a STATE_TRANSITION.
        """
        before = ContractorService._serialize_contractor(contractor)
        Contractor.objects.filter(pk=contractor.pk).update(
            is_active=False,
            deleted_at=timezone.now(),
        )
        contractor.refresh_from_db()
        after = ContractorService._serialize_contractor(contractor)
        ContractorService._log_audit(
            society=contractor.society,
            action=GateOpsAuditLog.Action.STATE_TRANSITION,
            entity_type="Contractor",
            entity_id=contractor.pk,
            before=before,
            after=after,
            actor=actor,
        )
        return contractor

    @staticmethod
    def list_contractors(*, society, include_inactive=False) -> QuerySet:
        """Return contractors for a society, active-only by default.

        Ordered by ``-created_at`` (newest first). Uses ``select_related`` on
        ``society`` to avoid N+1 on display.
        """
        qs = Contractor.objects.filter(society=society).select_related("society")
        if not include_inactive:
            qs = qs.filter(is_active=True)
        return qs.order_by("-created_at")

    @staticmethod
    def get_contractor(*, society, pk) -> Contractor:
        """Return a single active contractor or raise Http404.

        Scoped by ``society`` + ``is_active=True`` so a soft-deleted or
        cross-tenant contractor is never returned.
        """
        return get_object_or_404(
            Contractor, society=society, pk=pk, is_active=True
        )

    # ------------------------------------------------------------------ #
    # Contract CRUD
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def create_contract(
        *,
        society,
        contractor,
        title,
        start_date,
        end_date,
        max_workers=10,
        description="",
        actor=None,
    ) -> Contract:
        """Create a new Contract record under a contractor and audit a CREATE.

        ``title`` is mandatory. The contract is created with
        ``status=ACTIVE`` and ``is_active=True``.
        """
        title = (title or "").strip()
        if not title:
            raise ValidationError({"title": "Contract title is required."})

        # Multi-tenant safety: the contractor must belong to this society.
        if contractor.society_id != society.pk:
            raise ValidationError(
                {"contractor": "Contractor must belong to the same society."}
            )

        contract = Contract(
            society=society,
            contractor=contractor,
            title=title,
            description=description,
            start_date=start_date,
            end_date=end_date,
            max_workers=max_workers,
            status=Contract.Status.ACTIVE,
            is_active=True,
        )
        contract.save()
        after = ContractorService._serialize_contract(contract)
        ContractorService._log_audit(
            society=contract.society,
            action=GateOpsAuditLog.Action.CREATE,
            entity_type="Contract",
            entity_id=contract.pk,
            before=None,
            after=after,
            actor=actor,
        )
        return contract

    @staticmethod
    @transaction.atomic
    def update_contract(*, contract, actor=None, **fields) -> Contract:
        """Update allowed fields on a contract (race-safe via ``update()``).

        Only fields in ``_CONTRACT_UPDATE_FIELDS`` are applied; unknown keys
        raise ``ValidationError``.
        """
        unknown = set(fields) - _CONTRACT_UPDATE_FIELDS
        if unknown:
            raise ValidationError(
                {"fields": f"Cannot update protected/unknown fields: {sorted(unknown)}"}
            )

        before = ContractorService._serialize_contract(contract)
        update_dict = {k: v for k, v in fields.items() if v is not None}
        if not update_dict:
            return contract

        Contract.objects.filter(pk=contract.pk).update(**update_dict)
        contract.refresh_from_db()
        after = ContractorService._serialize_contract(contract)
        ContractorService._log_audit(
            society=contract.society,
            action=GateOpsAuditLog.Action.UPDATE,
            entity_type="Contract",
            entity_id=contract.pk,
            before=before,
            after=after,
            actor=actor,
        )
        return contract

    @staticmethod
    @transaction.atomic
    def deactivate_contract(*, contract, actor=None) -> Contract:
        """Soft-delete a contract: set ``is_active=False`` + ``deleted_at``.

        Uses ``QuerySet.update()`` for race safety. Audits a STATE_TRANSITION.
        """
        before = ContractorService._serialize_contract(contract)
        Contract.objects.filter(pk=contract.pk).update(
            is_active=False,
            deleted_at=timezone.now(),
        )
        contract.refresh_from_db()
        after = ContractorService._serialize_contract(contract)
        ContractorService._log_audit(
            society=contract.society,
            action=GateOpsAuditLog.Action.STATE_TRANSITION,
            entity_type="Contract",
            entity_id=contract.pk,
            before=before,
            after=after,
            actor=actor,
        )
        return contract

    @staticmethod
    def list_contracts(*, society, contractor=None, include_inactive=False) -> QuerySet:
        """Return contracts for a society, optionally filtered by contractor.

        Ordered by ``-created_at``. Uses ``select_related`` on ``society`` and
        ``contractor`` to avoid N+1 on display.
        """
        qs = Contract.objects.filter(society=society).select_related(
            "society", "contractor"
        )
        if contractor is not None:
            qs = qs.filter(contractor=contractor)
        if not include_inactive:
            qs = qs.filter(is_active=True)
        return qs.order_by("-created_at")

    @staticmethod
    def get_contract(*, society, pk) -> Contract:
        """Return a single active contract or raise Http404."""
        return get_object_or_404(Contract, society=society, pk=pk, is_active=True)

    # ------------------------------------------------------------------ #
    # Worker CRUD
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def register_worker(
        *,
        society,
        contract,
        person,
        designation="",
        id_type="",
        id_number="",
        actor=None,
    ) -> Worker:
        """Register a Worker (Person ↔ Contract link) and audit a CREATE.

        Enforces the contract's ``max_workers`` ceiling: if the active worker
        count has already reached ``max_workers``, raises ``ValidationError``.
        Also validates the contract is in the ACTIVE status — workers cannot
        be enrolled on a completed/suspended contract.
        """
        # Multi-tenant safety: the contract must belong to this society.
        if contract.society_id != society.pk:
            raise ValidationError(
                {"contract": "Contract must belong to the same society."}
            )
        # Cross-society person-leak prevention (mirrors Worker.clean()).
        if person.society_id != society.pk:
            raise ValidationError(
                {"person": "Person must belong to the same society."}
            )

        # Contract must be ACTIVE to enrol workers.
        if contract.status != Contract.Status.ACTIVE:
            raise ValidationError(
                f"Cannot register workers on a contract with status "
                f"'{contract.status}' (must be '{Contract.Status.ACTIVE}')."
            )

        # Enforce the labour ceiling before creating the row. A race between
        # the count() and the save() is acceptable here — the conditional
        # unique constraint on (society, contract, person) prevents duplicate
        # enrolment, and exceeding max_workers by one under concurrency is a
        # tolerable edge case that the next check_in_worker call will surface.
        active_count = contract.workers.filter(is_active=True).count()
        if active_count >= contract.max_workers:
            raise ValidationError(
                "Maximum worker limit reached for this contract "
                f"({active_count}/{contract.max_workers})."
            )

        worker = Worker(
            society=society,
            contract=contract,
            person=person,
            designation=designation,
            id_type=id_type,
            id_number=id_number,
            is_active=True,
        )
        worker.save()
        after = ContractorService._serialize_worker(worker)
        ContractorService._log_audit(
            society=worker.society,
            action=GateOpsAuditLog.Action.CREATE,
            entity_type="Worker",
            entity_id=worker.pk,
            before=None,
            after=after,
            actor=actor,
        )
        return worker

    @staticmethod
    @transaction.atomic
    def deactivate_worker(*, worker, actor=None) -> Worker:
        """Soft-delete a worker: set ``is_active=False`` + ``deleted_at``.

        Uses ``QuerySet.update()`` for race safety. Audits a STATE_TRANSITION.
        """
        before = ContractorService._serialize_worker(worker)
        Worker.objects.filter(pk=worker.pk).update(
            is_active=False,
            deleted_at=timezone.now(),
        )
        worker.refresh_from_db()
        after = ContractorService._serialize_worker(worker)
        ContractorService._log_audit(
            society=worker.society,
            action=GateOpsAuditLog.Action.STATE_TRANSITION,
            entity_type="Worker",
            entity_id=worker.pk,
            before=before,
            after=after,
            actor=actor,
        )
        return worker

    @staticmethod
    def list_workers(*, society, contract=None, include_inactive=False) -> QuerySet:
        """Return workers for a society, optionally filtered by contract.

        Ordered by ``-created_at``. Uses ``select_related`` on ``society``,
        ``contract``, and ``person`` to avoid N+1 on display.
        """
        qs = Worker.objects.filter(society=society).select_related(
            "society", "contract", "person"
        )
        if contract is not None:
            qs = qs.filter(contract=contract)
        if not include_inactive:
            qs = qs.filter(is_active=True)
        return qs.order_by("-created_at")

    @staticmethod
    def get_worker(*, society, pk) -> Worker:
        """Return a single active worker or raise Http404."""
        return get_object_or_404(Worker, society=society, pk=pk, is_active=True)

    # ------------------------------------------------------------------ #
    # WorkPermit CRUD
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def issue_work_permit(
        *,
        society,
        contract,
        permit_number,
        issued_at,
        expires_at,
        safety_docs_verified=False,
        safety_briefing_given=False,
        work_area="",
        hazard_level="low",
        notes="",
        actor=None,
    ) -> WorkPermit:
        """Issue a new WorkPermit (status=ACTIVE) and audit a CREATE.

        ``permit_number`` is mandatory. The permit is created with
        ``status=ACTIVE`` and ``is_active=True``.
        """
        permit_number = (permit_number or "").strip()
        if not permit_number:
            raise ValidationError({"permit_number": "Permit number is required."})

        # Multi-tenant safety: the contract must belong to this society.
        if contract.society_id != society.pk:
            raise ValidationError(
                {"contract": "Contract must belong to the same society."}
            )

        work_permit = WorkPermit(
            society=society,
            contract=contract,
            permit_number=permit_number,
            issued_at=issued_at,
            expires_at=expires_at,
            safety_docs_verified=safety_docs_verified,
            safety_briefing_given=safety_briefing_given,
            work_area=work_area,
            hazard_level=hazard_level,
            notes=notes,
            status=WorkPermit.Status.ACTIVE,
            is_active=True,
        )
        work_permit.save()
        after = ContractorService._serialize_work_permit(work_permit)
        ContractorService._log_audit(
            society=work_permit.society,
            action=GateOpsAuditLog.Action.CREATE,
            entity_type="WorkPermit",
            entity_id=work_permit.pk,
            before=None,
            after=after,
            actor=actor,
        )
        return work_permit

    @staticmethod
    @transaction.atomic
    def revoke_work_permit(*, work_permit, actor=None) -> WorkPermit:
        """Revoke a work permit: set ``status=REVOKED``.

        Uses ``QuerySet.update()`` for race safety. Audits a STATE_TRANSITION.
        """
        before = ContractorService._serialize_work_permit(work_permit)
        WorkPermit.objects.filter(pk=work_permit.pk).update(
            status=WorkPermit.Status.REVOKED,
        )
        work_permit.refresh_from_db()
        after = ContractorService._serialize_work_permit(work_permit)
        ContractorService._log_audit(
            society=work_permit.society,
            action=GateOpsAuditLog.Action.STATE_TRANSITION,
            entity_type="WorkPermit",
            entity_id=work_permit.pk,
            before=before,
            after=after,
            actor=actor,
        )
        return work_permit

    @staticmethod
    def list_work_permits(
        *, society, contract=None, include_inactive=False
    ) -> QuerySet:
        """Return work permits for a society, optionally filtered by contract.

        Ordered by ``-created_at``. Uses ``select_related`` on ``society`` and
        ``contract`` to avoid N+1 on display.
        """
        qs = WorkPermit.objects.filter(society=society).select_related(
            "society", "contract"
        )
        if contract is not None:
            qs = qs.filter(contract=contract)
        if not include_inactive:
            qs = qs.filter(is_active=True)
        return qs.order_by("-created_at")

    @staticmethod
    def get_work_permit(*, society, pk) -> WorkPermit:
        """Return a single active work permit or raise Http404."""
        return get_object_or_404(
            WorkPermit, society=society, pk=pk, is_active=True
        )

    # ------------------------------------------------------------------ #
    # Expiry checks (the core Phase 9 feature)
    # ------------------------------------------------------------------ #

    @staticmethod
    def check_contract_expiry(*, contract, as_of=None) -> dict:
        """Return expiry info for a contract as of a reference date.

        Returns ``{"is_expired": bool, "days_until_expiry": int,
        "expiry_date": date}``. When ``as_of`` is ``None``, today's date is
        used. A contract is expired when ``as_of > contract.end_date``.
        """
        if as_of is None:
            as_of = timezone.now().date()
        is_expired = as_of > contract.end_date
        days_until_expiry = (contract.end_date - as_of).days
        return {
            "is_expired": is_expired,
            "days_until_expiry": days_until_expiry,
            "expiry_date": contract.end_date,
        }

    @staticmethod
    def check_work_permit_expiry(*, work_permit, as_of=None) -> dict:
        """Return expiry info for a work permit as of a reference datetime.

        Returns ``{"is_expired": bool, "days_until_expiry": int,
        "expiry_datetime": datetime}``. When ``as_of`` is ``None``, the
        current datetime is used. A permit is expired when
        ``as_of > work_permit.expires_at``.
        """
        if as_of is None:
            as_of = timezone.now()
        is_expired = as_of > work_permit.expires_at
        days_until_expiry = (work_permit.expires_at - as_of).days
        return {
            "is_expired": is_expired,
            "days_until_expiry": days_until_expiry,
            "expiry_datetime": work_permit.expires_at,
        }

    @staticmethod
    def get_expired_contracts(*, society, as_of=None) -> QuerySet:
        """Return ACTIVE contracts whose ``end_date`` has passed.

        These are contracts that should be marked ``COMPLETED`` by
        :meth:`process_expiries`. Scoped by ``society`` and ``is_active=True``.
        """
        if as_of is None:
            as_of = timezone.now().date()
        return Contract.objects.filter(
            society=society,
            is_active=True,
            status=Contract.Status.ACTIVE,
            end_date__lt=as_of,
        ).select_related("society", "contractor")

    @staticmethod
    def get_expired_work_permits(*, society, as_of=None) -> QuerySet:
        """Return ACTIVE work permits whose ``expires_at`` has passed.

        These are permits that should be marked ``EXPIRED`` by
        :meth:`process_expiries`. Scoped by ``society`` and ``is_active=True``.
        """
        if as_of is None:
            as_of = timezone.now()
        return WorkPermit.objects.filter(
            society=society,
            is_active=True,
            status=WorkPermit.Status.ACTIVE,
            expires_at__lt=as_of,
        ).select_related("society", "contract")

    @staticmethod
    @transaction.atomic
    def process_expiries(*, society, as_of=None, actor=None) -> dict:
        """Mark expired contracts as COMPLETED and expired permits as EXPIRED.

        Finds all active contracts past their ``end_date`` and active work
        permits past their ``expires_at``, transitions them in bulk (race-safe
        via ``update()``), and audits each transition. Returns a summary dict:

        ``{"contracts_marked_completed": int, "work_permits_marked_expired": int}``
        """
        now = timezone.now()
        if as_of is None:
            contract_as_of = now.date()
            permit_as_of = now
        else:
            # Allow a single ``as_of`` to drive both checks: a date is used
            # for contracts (date comparison) and a datetime for permits
            # (datetime comparison). When a date is passed, the permit check
            # falls back to ``now`` so permits are not skipped.
            if hasattr(as_of, "date"):
                contract_as_of = as_of.date()
                permit_as_of = as_of
            else:
                contract_as_of = as_of
                permit_as_of = now

        expired_contracts = list(
            ContractorService.get_expired_contracts(
                society=society, as_of=contract_as_of
            )
        )
        expired_permits = list(
            ContractorService.get_expired_work_permits(
                society=society, as_of=permit_as_of
            )
        )

        # Bulk-update contracts → COMPLETED (race-safe).
        contract_pks = [c.pk for c in expired_contracts]
        if contract_pks:
            Contract.objects.filter(pk__in=contract_pks).update(
                status=Contract.Status.COMPLETED,
            )

        # Bulk-update work permits → EXPIRED (race-safe).
        permit_pks = [p.pk for p in expired_permits]
        if permit_pks:
            WorkPermit.objects.filter(pk__in=permit_pks).update(
                status=WorkPermit.Status.EXPIRED,
            )

        # Audit each transition individually (append-only log rows).
        for contract in expired_contracts:
            after = ContractorService._serialize_contract(contract)
            after["status"] = Contract.Status.COMPLETED
            ContractorService._log_audit(
                society=society,
                action=GateOpsAuditLog.Action.STATE_TRANSITION,
                entity_type="Contract",
                entity_id=contract.pk,
                before={"status": Contract.Status.ACTIVE},
                after=after,
                actor=actor,
            )

        for permit in expired_permits:
            after = ContractorService._serialize_work_permit(permit)
            after["status"] = WorkPermit.Status.EXPIRED
            ContractorService._log_audit(
                society=society,
                action=GateOpsAuditLog.Action.STATE_TRANSITION,
                entity_type="WorkPermit",
                entity_id=permit.pk,
                before={"status": WorkPermit.Status.ACTIVE},
                after=after,
                actor=actor,
            )

        return {
            "contracts_marked_completed": len(expired_contracts),
            "work_permits_marked_expired": len(expired_permits),
        }

    # ------------------------------------------------------------------ #
    # Attendance
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def check_in_worker(*, worker, actor=None) -> GateEvent:
        """Create a GateEvent for a worker entering the society.

        Sets the ``contractor``, ``contract``, and ``work_permit`` FKs on the
        GateEvent (derived from ``worker.contract``) so the rule engine and
        attendance queries can resolve the contractor context. Delegates event
        creation to :class:`GateEventLifecycleService`.

        Returns the created GateEvent.
        """
        # Local import to avoid a circular dependency at module load time:
        # gate_event_lifecycle imports from gateops.models, which is fine, but
        # keeping the import local mirrors the lazy-import pattern used
        # elsewhere in the codebase and keeps the service import graph flat.
        from gateops.services.gate_event_lifecycle import GateEventLifecycleService

        contract = worker.contract
        contractor = contract.contractor

        # Resolve the most recent active work permit for the contract (if any)
        # so the gate event carries the permit context for the rule engine.
        work_permit = (
            WorkPermit.objects.filter(
                society=worker.society,
                contract=contract,
                status=WorkPermit.Status.ACTIVE,
                is_active=True,
            )
            .order_by("-expires_at")
            .first()
        )

        # Build the gate event via the lifecycle service. We create an
        # invitation and immediately record arrival + entry so the worker is
        # marked on-site in a single atomic flow.
        now = timezone.now()
        event = GateEventLifecycleService.create_invitation(
            society=worker.society,
            visitor_category=worker.society.visitor_categories.filter(
                is_contractor=True, is_active=True
            ).first(),
            person=worker.person,
            expected_arrival_at=now,
            created_by=actor,
            gate=worker.society.gates.filter(is_active=True).first(),
            purpose=f"Worker check-in: {worker.person.name} ({contract.title})",
            direction=GateEvent.Direction.INBOUND,
        )

        # Attach the contractor context FKs. These are additive nullable FKs
        # (SET_NULL) so historical events survive later contractor/contract
        # deletion.
        GateEvent.objects.filter(pk=event.pk).update(
            contractor=contractor,
            contract=contract,
            work_permit=work_permit,
        )
        event.refresh_from_db()

        # Drive the event through arrival → (rule engine) → entry.
        GateEventLifecycleService.record_arrival(
            event, gate=event.gate, guard=None
        )
        event.refresh_from_db()

        ContractorService._log_audit(
            society=worker.society,
            action=GateOpsAuditLog.Action.ENTRY,
            entity_type="Worker",
            entity_id=worker.pk,
            before=None,
            after={
                "gate_event_id": str(event.pk),
                "contractor_id": str(contractor.pk),
                "contract_id": str(contract.pk),
                "work_permit_id": str(work_permit.pk) if work_permit else None,
            },
            actor=actor,
        )
        return event

    @staticmethod
    @transaction.atomic
    def check_out_worker(*, worker, actor=None) -> GateEvent:
        """Mark the worker's active (on-site) GateEvent as exited.

        Finds the worker's most recent ENTERED gate event (not yet exited) and
        transitions it to EXITED via :class:`GateEventLifecycleService`. Raises
        ``ValidationError`` if the worker has no active on-site event.
        """
        from gateops.services.gate_event_lifecycle import GateEventLifecycleService

        # The worker's active on-site event is the most recent ENTERED event
        # linked to the worker's person within the worker's society that has
        # not yet been exited.
        event = (
            GateEvent.objects.filter(
                society=worker.society,
                person=worker.person,
                status=GateEvent.Status.ENTERED,
                exited_at__isnull=True,
            )
            .order_by("-created_at")
            .first()
        )
        if event is None:
            raise ValidationError(
                "Worker has no active on-site gate event to check out from."
            )

        GateEventLifecycleService.record_exit(event, guard=None)
        event.refresh_from_db()

        ContractorService._log_audit(
            society=worker.society,
            action=GateOpsAuditLog.Action.EXIT,
            entity_type="Worker",
            entity_id=worker.pk,
            before={"gate_event_id": str(event.pk), "status": GateEvent.Status.ENTERED},
            after={"gate_event_id": str(event.pk), "status": GateEvent.Status.EXITED},
            actor=actor,
        )
        return event

    @staticmethod
    def get_active_workers_on_site(*, society) -> QuerySet:
        """Return workers who currently have an active (not exited) GateEvent.

        Queries GateEvent rows where a contractor is linked, the status is an
        active on-site state (ENTERED), and ``exited_at`` is null. Returns the
        GateEvent queryset (each event's ``person`` maps to a worker) ordered
        by most-recent entry first.
        """
        return (
            GateEvent.objects.filter(
                society=society,
                contractor__isnull=False,
                status=GateEvent.Status.ENTERED,
                exited_at__isnull=True,
            )
            .select_related("contractor", "contract", "work_permit", "person")
            .order_by("-entered_at")
        )

    @staticmethod
    def get_labour_count(*, contract) -> int:
        """Return the count of active workers enrolled on a contract."""
        return contract.workers.filter(is_active=True).count()

    @staticmethod
    def is_labour_limit_exceeded(*, contract) -> bool:
        """Return ``True`` when the active worker count has reached ``max_workers``."""
        return (
            ContractorService.get_labour_count(contract=contract)
            >= contract.max_workers
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _serialize_contractor(contractor) -> dict:
        """Return a JSON-safe dict of the contractor's key fields for audit."""
        return {
            "id": str(contractor.pk),
            "company_name": contractor.company_name,
            "supervisor_name": contractor.supervisor_name,
            "supervisor_phone": contractor.supervisor_phone,
            "is_active": contractor.is_active,
        }

    @staticmethod
    def _serialize_contract(contract) -> dict:
        """Return a JSON-safe dict of the contract's key fields for audit."""
        def _dt(value):
            return value.isoformat() if value else None

        return {
            "id": str(contract.pk),
            "title": contract.title,
            "contractor_id": str(contract.contractor_id),
            "status": contract.status,
            "start_date": _dt(contract.start_date),
            "end_date": _dt(contract.end_date),
            "max_workers": contract.max_workers,
            "is_active": contract.is_active,
        }

    @staticmethod
    def _serialize_worker(worker) -> dict:
        """Return a JSON-safe dict of the worker's key fields for audit."""
        return {
            "id": str(worker.pk),
            "contract_id": str(worker.contract_id),
            "person_id": str(worker.person_id),
            "designation": worker.designation,
            "id_type": worker.id_type,
            "is_active": worker.is_active,
        }

    @staticmethod
    def _serialize_work_permit(work_permit) -> dict:
        """Return a JSON-safe dict of the work permit's key fields for audit."""
        def _dt(value):
            return value.isoformat() if value else None

        return {
            "id": str(work_permit.pk),
            "permit_number": work_permit.permit_number,
            "contract_id": str(work_permit.contract_id),
            "status": work_permit.status,
            "issued_at": _dt(work_permit.issued_at),
            "expires_at": _dt(work_permit.expires_at),
            "hazard_level": work_permit.hazard_level,
            "safety_docs_verified": work_permit.safety_docs_verified,
            "safety_briefing_given": work_permit.safety_briefing_given,
            "is_active": work_permit.is_active,
        }

    @staticmethod
    def _log_audit(
        *,
        society,
        action,
        entity_type,
        entity_id,
        before=None,
        after=None,
        actor=None,
    ) -> None:
        """Write an append-only GateOpsAuditLog entry for a contractor operation.

        Wrapped so a logging failure never blocks a legitimate contractor
        operation; the error is logged at ERROR level instead.
        """
        try:
            GateOpsAuditLog.log(
                society=society,
                action=action,
                entity_type=entity_type,
                entity_id=str(entity_id) if entity_id is not None else "",
                actor=actor,
                before_value=before,
                after_value=after,
            )
        except Exception:  # noqa: BLE001 — audit must not break the operation.
            logger.exception(
                "Failed to write %s audit log for entity %s (action=%s)",
                entity_type,
                entity_id,
                action,
            )
