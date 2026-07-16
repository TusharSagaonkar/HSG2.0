"""Wizard lifecycle management service for the Society Creation & Accounting
Migration Wizard.

This service is the single authority over :class:`OnboardingWizard` state
transitions: creation, step navigation (advance / go-back), resume, abandon,
and completion. No caller should mutate wizard state directly — every
state-changing operation must flow through :class:`WizardService` so that:

1. The transition is applied atomically (``@transaction.atomic``).
2. A :class:`WizardStepLog` entry is written for step navigation.
3. A :class:`MigrationAuditLog` entry is written for lifecycle events.

Design notes
------------
- **All methods are ``@staticmethod``** per the service contract established
  in ``gateops/services/contractor_service.py``; there is no shared mutable
  state.
- **Audit robustness:** audit-log writes are wrapped so a logging failure
  never blocks a legitimate wizard operation (the error is logged loudly
  instead).
- **Step navigation rules:**
    * ``advance_step`` increments ``current_step`` by 1 and logs a
      ``COMPLETED`` step log.
    * ``go_to_step`` allows backward navigation (review/correction) or
      re-entering the immediate next step. It logs a ``STARTED`` step log.
- **Branch handling at Step 9:** when ``society_type == NEW``, the wizard
  jumps from Step 9 directly to Step 28 (Society Ready), skipping the
  migration steps 10–27 which only apply to EXISTING societies.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from onboarding.models import (
    MigrationAuditLog,
    OnboardingWizard,
    WizardStepLog,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Step constants
# --------------------------------------------------------------------------- #

STEP_SOCIETY_DETAILS = 1
STEP_SOCIETY_TYPE = 2
STEP_MODULE_SELECTION = 3
STEP_ACCOUNTING_START_YEAR = 4
STEP_FINANCIAL_YEAR_CREATION = 5
STEP_SOCIETY_STRUCTURE = 6
STEP_UNIT_CONFIGURATION = 7
STEP_MEMBER_ASSIGNMENT = 8
STEP_ACCOUNTING_SETUP = 9
STEP_CHART_OF_ACCOUNTS = 10
STEP_IMPORT_TEMPLATES = 11
STEP_STAGING_AREA = 12
STEP_IMPORT_VALIDATION = 13
STEP_DELETE_REUPLOAD = 14
STEP_OPENING_TRIAL_BALANCE = 15
STEP_MEMBER_OUTSTANDING = 16
STEP_VENDOR_OUTSTANDING = 17
STEP_BANK_OPENING = 18
STEP_CASH_OPENING = 19
STEP_FUNDS = 20
STEP_FIXED_ASSETS = 21
STEP_LOANS = 22
STEP_RECONCILIATION_DASHBOARD = 23
STEP_MIGRATION_VALIDATION_CHECKLIST = 24
STEP_FINAL_APPROVAL = 25
STEP_CREATE_OPENING_JOURNAL = 26
STEP_LOCK_MIGRATION = 27
STEP_SOCIETY_READY = 28

STEP_NAMES = {
    1: "Society Details",
    2: "Society Type",
    3: "Module Selection",
    4: "Accounting Start Year",
    5: "Financial Year Creation",
    6: "Society Structure",
    7: "Unit Configuration",
    8: "Member Assignment",
    9: "Accounting Setup",
    10: "Chart of Accounts",
    11: "Import Templates",
    12: "Staging Area",
    13: "Import Validation",
    14: "Delete & Re-upload",
    15: "Opening Trial Balance",
    16: "Member Outstanding",
    17: "Vendor Outstanding",
    18: "Bank Opening Balances",
    19: "Cash Opening Balance",
    20: "Funds",
    21: "Fixed Assets",
    22: "Loans",
    23: "Reconciliation Dashboard",
    24: "Migration Validation Checklist",
    25: "Final Approval",
    26: "Create Opening Journal",
    27: "Lock Migration",
    28: "Society Ready",
}

# --------------------------------------------------------------------------- #
# Module constants
# --------------------------------------------------------------------------- #

CORE_MODULES = ["accounting", "billing", "members", "administration"]
OPTIONAL_MODULES = [
    "parking", "gateops", "complaints", "facility_booking",
    "staff", "vendors", "inventory", "assets", "shares",
    "reconciliation", "amc", "notice_board", "documents",
    "email", "sms", "whatsapp", "analytics", "ai_assistant",
]
ALL_MODULES = CORE_MODULES + OPTIONAL_MODULES

# The maximum step number (used for validation).
MAX_STEP = STEP_SOCIETY_READY


class WizardService:
    """Service for :class:`OnboardingWizard` lifecycle management.

    Every state-changing operation:
    1. Validates the transition is legal.
    2. Applies the transition atomically.
    3. Writes a :class:`WizardStepLog` (for step navigation) and/or a
       :class:`MigrationAuditLog` (for lifecycle events).
    """

    # ------------------------------------------------------------------ #
    # Creation & retrieval
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def create_wizard(user, society_type=None) -> OnboardingWizard:
        """Create a new :class:`OnboardingWizard` with status=IN_PROGRESS.

        Parameters
        ----------
        user : User
            The user initiating the wizard. Stored as ``created_by``.
        society_type : str, optional
            One of ``SocietyType`` choice values (``NEW`` or ``EXISTING``).
            May be set later via :meth:`set_society_type`.

        Returns
        -------
        OnboardingWizard
            The newly created wizard instance.
        """
        if society_type is not None:
            society_type = society_type.upper()
            valid = {OnboardingWizard.SocietyType.NEW, OnboardingWizard.SocietyType.EXISTING}
            if society_type not in valid:
                raise ValidationError(
                    {"society_type": f"Invalid society_type '{society_type}'. Must be one of {sorted(valid)}."}
                )

        wizard = OnboardingWizard.objects.create(
            current_step=STEP_SOCIETY_DETAILS,
            status=OnboardingWizard.Status.IN_PROGRESS,
            society_type=society_type or "",
            created_by=user,
        )

        WizardService._log_audit(
            wizard=wizard,
            action="CREATE",
            user=user,
            after_state=WizardService._serialize_wizard(wizard),
        )
        return wizard

    @staticmethod
    def get_wizard(wizard_id) -> OnboardingWizard:
        """Return the wizard with the given ID or raise ``DoesNotExist``.

        Uses ``.unscoped()`` so the wizard can be retrieved even before a
        society is associated (the :class:`TenantManager` would otherwise
        filter it out when no tenant contextvar is set).
        """
        return OnboardingWizard.objects.unscoped().get(pk=wizard_id)

    @staticmethod
    def get_wizard_state(wizard) -> dict[str, Any]:
        """Return a dict summarising the wizard's current state.

        Used by views to render the current step and the progress bar.

        Returns
        -------
        dict
            Keys: ``current_step``, ``society_type``, ``status``,
            ``selected_modules``, ``completed_steps``, ``is_finalized``,
            ``society_id``.
        """
        completed_steps = list(
            wizard.step_logs.filter(
                status=WizardStepLog.Status.COMPLETED,
            ).values_list("step_number", flat=True)
        )

        return {
            "current_step": wizard.current_step,
            "society_type": wizard.society_type,
            "status": wizard.status,
            "selected_modules": wizard.selected_modules,
            "completed_steps": completed_steps,
            "is_finalized": wizard.is_finalized,
            "society_id": wizard.society_id,
        }

    # ------------------------------------------------------------------ #
    # Step navigation
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def advance_step(wizard, step_data=None, user=None) -> OnboardingWizard:
        """Advance the wizard to the next step.

        Logs the current step as ``COMPLETED`` via :class:`WizardStepLog`,
        merges ``step_data`` into ``wizard_data``, then increments
        ``current_step``.

        Branch handling: if the current step is :data:`STEP_ACCOUNTING_SETUP`
        (9) and ``society_type == NEW``, the wizard jumps directly to
        :data:`STEP_SOCIETY_READY` (28), skipping the migration steps.

        Parameters
        ----------
        wizard : OnboardingWizard
            The wizard to advance.
        step_data : dict, optional
            Data captured during the current step. Merged into
            ``wizard.wizard_data`` under the step's name key.
        user : User, optional
            The user performing the action (for audit logging).

        Returns
        -------
        OnboardingWizard
            The updated wizard instance.
        """
        if wizard.status != OnboardingWizard.Status.IN_PROGRESS:
            raise ValidationError(
                f"Cannot advance a wizard with status '{wizard.status}'. "
                "Only IN_PROGRESS wizards can be advanced."
            )

        current = wizard.current_step
        step_name = STEP_NAMES.get(current, f"Step {current}")

        # Merge step_data into wizard_data under the step name key.
        if step_data:
            data = dict(wizard.wizard_data) if wizard.wizard_data else {}
            data[step_name] = step_data
            wizard.wizard_data = data

        # Log the step completion (append-only).
        WizardService._log_step(
            wizard=wizard,
            step_number=current,
            step_name=step_name,
            status=WizardStepLog.Status.COMPLETED,
            user=user,
            data_snapshot=step_data or {},
        )

        # Determine the next step (with branch handling at Step 9).
        next_step = WizardService._compute_next_step(wizard, current)

        before = WizardService._serialize_wizard(wizard)
        wizard.current_step = next_step
        wizard.save(update_fields=["current_step", "wizard_data"])
        wizard.refresh_from_db()

        WizardService._log_audit(
            wizard=wizard,
            action="ADVANCE_STEP",
            user=user,
            before_state=before,
            after_state=WizardService._serialize_wizard(wizard),
            details={"from_step": current, "to_step": next_step},
        )
        return wizard

    @staticmethod
    @transaction.atomic
    def go_to_step(wizard, step_number, user=None) -> OnboardingWizard:
        """Navigate to a specific step (backward or re-enter immediate next).

        Allowed when:
            * ``step_number < wizard.current_step`` (go back to review), or
            * ``step_number == wizard.current_step + 1`` (re-enter the next
              step without completing the current one — useful for
              corrections).

        Logs a ``STARTED`` step log for the target step.

        Parameters
        ----------
        wizard : OnboardingWizard
            The wizard to navigate.
        step_number : int
            The target step number (1–28).
        user : User, optional
            The user performing the action.

        Returns
        -------
        OnboardingWizard
            The updated wizard instance.
        """
        if not (1 <= step_number <= MAX_STEP):
            raise ValidationError(
                {"step_number": f"Step number must be between 1 and {MAX_STEP}."}
            )

        if wizard.status != OnboardingWizard.Status.IN_PROGRESS:
            raise ValidationError(
                f"Cannot navigate a wizard with status '{wizard.status}'."
            )

        current = wizard.current_step
        if step_number != current and step_number != current + 1 and step_number >= current:
            raise ValidationError(
                {"step_number": (
                    f"Cannot go to step {step_number} from step {current}. "
                    "Only backward navigation (step < current) or re-entering "
                    "the immediate next step (step == current + 1) is allowed."
                )}
            )

        step_name = STEP_NAMES.get(step_number, f"Step {step_number}")
        WizardService._log_step(
            wizard=wizard,
            step_number=step_number,
            step_name=step_name,
            status=WizardStepLog.Status.STARTED,
            user=user,
            data_snapshot={},
        )

        before = WizardService._serialize_wizard(wizard)
        wizard.current_step = step_number
        wizard.save(update_fields=["current_step"])
        wizard.refresh_from_db()

        WizardService._log_audit(
            wizard=wizard,
            action="GO_TO_STEP",
            user=user,
            before_state=before,
            after_state=WizardService._serialize_wizard(wizard),
            details={"from_step": current, "to_step": step_number},
        )
        return wizard

    # ------------------------------------------------------------------ #
    # Lifecycle transitions
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def resume_wizard(wizard, user=None) -> OnboardingWizard:
        """Resume an abandoned wizard.

        Increments ``resumed_count`` and sets ``status`` back to
        ``IN_PROGRESS`` if it was ``ABANDONED``.

        Parameters
        ----------
        wizard : OnboardingWizard
            The wizard to resume.
        user : User, optional
            The user resuming the wizard.

        Returns
        -------
        OnboardingWizard
            The updated wizard instance.
        """
        before = WizardService._serialize_wizard(wizard)

        wizard.resumed_count = (wizard.resumed_count or 0) + 1
        if wizard.status == OnboardingWizard.Status.ABANDONED:
            wizard.status = OnboardingWizard.Status.IN_PROGRESS

        wizard.save(update_fields=["resumed_count", "status"])
        wizard.refresh_from_db()

        WizardService._log_audit(
            wizard=wizard,
            action="RESUME",
            user=user,
            before_state=before,
            after_state=WizardService._serialize_wizard(wizard),
            details={"resumed_count": wizard.resumed_count},
        )
        return wizard

    @staticmethod
    @transaction.atomic
    def abandon_wizard(wizard, user=None) -> OnboardingWizard:
        """Abandon the wizard (set status to ``ABANDONED``).

        Parameters
        ----------
        wizard : OnboardingWizard
            The wizard to abandon.
        user : User, optional
            The user abandoning the wizard.

        Returns
        -------
        OnboardingWizard
            The updated wizard instance.
        """
        before = WizardService._serialize_wizard(wizard)

        wizard.status = OnboardingWizard.Status.ABANDONED
        wizard.save(update_fields=["status"])
        wizard.refresh_from_db()

        WizardService._log_audit(
            wizard=wizard,
            action="ABANDON",
            user=user,
            before_state=before,
            after_state=WizardService._serialize_wizard(wizard),
        )
        return wizard

    @staticmethod
    @transaction.atomic
    def complete_wizard(wizard, user=None) -> OnboardingWizard:
        """Mark the wizard as completed and finalized.

        Sets ``status=COMPLETED``, ``completed_at=now()``, and
        ``is_finalized=True``.

        Parameters
        ----------
        wizard : OnboardingWizard
            The wizard to complete.
        user : User, optional
            The user completing the wizard.

        Returns
        -------
        OnboardingWizard
            The updated wizard instance.
        """
        before = WizardService._serialize_wizard(wizard)

        wizard.status = OnboardingWizard.Status.COMPLETED
        wizard.completed_at = timezone.now()
        wizard.is_finalized = True
        wizard.save(update_fields=["status", "completed_at", "is_finalized"])
        wizard.refresh_from_db()

        WizardService._log_audit(
            wizard=wizard,
            action="COMPLETE",
            user=user,
            before_state=before,
            after_state=WizardService._serialize_wizard(wizard),
        )
        return wizard

    # ------------------------------------------------------------------ #
    # Data & configuration setters
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def update_wizard_data(wizard, key, value) -> OnboardingWizard:
        """Update a single key in ``wizard.wizard_data`` and save.

        Parameters
        ----------
        wizard : OnboardingWizard
            The wizard to update.
        key : str
            The wizard_data key to set.
        value : Any
            The value to store (must be JSON-serialisable).

        Returns
        -------
        OnboardingWizard
            The updated wizard instance.
        """
        data = dict(wizard.wizard_data) if wizard.wizard_data else {}
        data[key] = value
        wizard.wizard_data = data
        wizard.save(update_fields=["wizard_data"])
        wizard.refresh_from_db()
        return wizard

    @staticmethod
    @transaction.atomic
    def set_society_type(wizard, society_type, user=None) -> OnboardingWizard:
        """Set ``society_type`` on the wizard and log via :class:`MigrationAuditLog`.

        Parameters
        ----------
        wizard : OnboardingWizard
            The wizard to update.
        society_type : str
            One of ``SocietyType`` choice values (``NEW`` or ``EXISTING``).
        user : User, optional
            The user performing the action.

        Returns
        -------
        OnboardingWizard
            The updated wizard instance.
        """
        society_type = (society_type or "").upper()
        valid = {OnboardingWizard.SocietyType.NEW, OnboardingWizard.SocietyType.EXISTING}
        if society_type not in valid:
            raise ValidationError(
                {"society_type": f"Invalid society_type '{society_type}'. Must be one of {sorted(valid)}."}
            )

        before = WizardService._serialize_wizard(wizard)
        wizard.society_type = society_type
        wizard.save(update_fields=["society_type"])
        wizard.refresh_from_db()

        WizardService._log_audit(
            wizard=wizard,
            action="SET_SOCIETY_TYPE",
            user=user,
            before_state=before,
            after_state=WizardService._serialize_wizard(wizard),
            details={"society_type": society_type},
        )
        return wizard

    @staticmethod
    @transaction.atomic
    def set_selected_modules(wizard, modules_list, user=None) -> OnboardingWizard:
        """Set ``selected_modules`` on the wizard and log via :class:`MigrationAuditLog`.

        Core modules are always included regardless of what the caller passes.

        Parameters
        ----------
        wizard : OnboardingWizard
            The wizard to update.
        modules_list : list[str]
            The list of selected module keys (optional modules).
        user : User, optional
            The user performing the action.

        Returns
        -------
        OnboardingWizard
            The updated wizard instance.
        """
        # Always include core modules; de-duplicate while preserving order.
        seen = set(CORE_MODULES)
        modules = list(CORE_MODULES)
        for mod in modules_list or []:
            mod = mod.strip().lower()
            if mod and mod not in seen and mod in ALL_MODULES:
                seen.add(mod)
                modules.append(mod)

        before = WizardService._serialize_wizard(wizard)
        wizard.selected_modules = modules
        wizard.save(update_fields=["selected_modules"])
        wizard.refresh_from_db()

        WizardService._log_audit(
            wizard=wizard,
            action="SET_SELECTED_MODULES",
            user=user,
            before_state=before,
            after_state=WizardService._serialize_wizard(wizard),
            details={"selected_modules": modules},
        )
        return wizard

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _compute_next_step(wizard, current_step) -> int:
        """Compute the next step number, applying branch logic at Step 9.

        For ``NEW`` societies, the migration steps (10–27) are skipped —
        the wizard jumps from Step 9 (Accounting Setup) directly to Step 28
        (Society Ready).
        """
        if (
            current_step == STEP_ACCOUNTING_SETUP
            and wizard.society_type == OnboardingWizard.SocietyType.NEW
        ):
            return STEP_SOCIETY_READY

        next_step = current_step + 1
        if next_step > MAX_STEP:
            return MAX_STEP
        return next_step

    @staticmethod
    def _log_step(
        *,
        wizard,
        step_number,
        step_name,
        status,
        user,
        data_snapshot,
    ) -> WizardStepLog | None:
        """Create a :class:`WizardStepLog` entry (append-only).

        Wrapped so a logging failure never blocks a legitimate wizard
        operation; the error is logged at ERROR level instead.

        Note: ``WizardStepLog`` has a ``unique_together`` on
        ``(wizard, step_number)``. If a step log already exists for this
        step number (e.g. re-completion after going back), we skip creating
        a duplicate rather than raising.
        """
        try:
            # Skip if a log already exists for this step (idempotent).
            if WizardStepLog.objects.filter(
                wizard=wizard, step_number=step_number
            ).exists():
                logger.debug(
                    "Step log already exists for wizard %s step %s; skipping.",
                    wizard.pk,
                    step_number,
                )
                return None

            log = WizardStepLog(
                wizard=wizard,
                step_number=step_number,
                step_name=step_name,
                status=status,
                data_snapshot=data_snapshot or {},
                completed_by=user,
                completed_at=timezone.now() if status == WizardStepLog.Status.COMPLETED else None,
            )
            log.save()
            return log
        except Exception:  # noqa: BLE001 — logging must not break the operation.
            logger.exception(
                "Failed to write WizardStepLog for wizard %s step %s (status=%s)",
                wizard.pk,
                step_number,
                status,
            )
            return None

    @staticmethod
    def _log_audit(
        *,
        wizard,
        action,
        user,
        details=None,
        before_state=None,
        after_state=None,
    ) -> None:
        """Create a :class:`MigrationAuditLog` entry (append-only).

        Wrapped so a logging failure never blocks a legitimate wizard
        operation; the error is logged at ERROR level instead.
        """
        try:
            log = MigrationAuditLog(
                wizard=wizard,
                society=wizard.society,
                action=action,
                actor=user,
                details=details or {},
                before_state=before_state or {},
                after_state=after_state or {},
            )
            log.save()
        except Exception:  # noqa: BLE001 — audit must not break the operation.
            logger.exception(
                "Failed to write MigrationAuditLog for wizard %s (action=%s)",
                wizard.pk,
                action,
            )

    @staticmethod
    def _serialize_wizard(wizard) -> dict[str, Any]:
        """Return a JSON-safe dict of the wizard's key fields for audit."""
        return {
            "id": str(wizard.pk),
            "current_step": wizard.current_step,
            "society_type": wizard.society_type,
            "status": wizard.status,
            "selected_modules": wizard.selected_modules,
            "is_finalized": wizard.is_finalized,
            "resumed_count": wizard.resumed_count,
            "society_id": str(wizard.society_id) if wizard.society_id else None,
        }
