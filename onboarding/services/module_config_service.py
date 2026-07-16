"""Module configuration service for the Society Creation & Accounting Migration
Wizard (Step 3 — Module Selection).

This service manages which optional modules are enabled for a society being
onboarded. Core modules are always enabled and cannot be deselected.

Design notes
------------
- **All methods are ``@staticmethod``** per the service contract established
  in ``gateops/services/contractor_service.py``.
- Core modules are always included in the enabled set, regardless of what the
  caller passes.
- Module keys are stored in ``wizard.selected_modules`` (a JSONField list).
- Audit logging is via :class:`MigrationAuditLog`.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from onboarding.models import MigrationAuditLog, OnboardingWizard
from onboarding.services.wizard_service import (
    ALL_MODULES,
    CORE_MODULES,
    OPTIONAL_MODULES,
)

logger = logging.getLogger(__name__)

# Human-readable display names for each module key.
MODULE_DISPLAY_NAMES: dict[str, str] = {
    # Core modules
    "accounting": "Accounting",
    "billing": "Billing",
    "members": "Members",
    "administration": "Administration",
    # Optional modules
    "parking": "Parking Management",
    "gateops": "Gate Operations",
    "complaints": "Complaints",
    "facility_booking": "Facility Booking",
    "staff": "Staff Management",
    "vendors": "Vendor Management",
    "inventory": "Inventory",
    "assets": "Asset Register",
    "shares": "Share Certificate Management",
    "reconciliation": "Bank Reconciliation",
    "amc": "AMC Contracts",
    "notice_board": "Notice Board",
    "documents": "Document Management",
    "email": "Email Notifications",
    "sms": "SMS Notifications",
    "whatsapp": "WhatsApp Notifications",
    "analytics": "Analytics",
    "ai_assistant": "AI Assistant",
}


class ModuleConfigurationService:
    """Service for managing module enablement during onboarding (Step 3).

    Core modules are always enabled. Optional modules are selected by the
    user during Step 3 of the wizard.
    """

    @staticmethod
    @transaction.atomic
    def configure_modules(wizard, selected_modules, user=None) -> list[str]:
        """Store the selected modules on the wizard.

        Core modules are always included. Unknown module keys are silently
        ignored (only valid keys from :data:`ALL_MODULES` are stored).

        Parameters
        ----------
        wizard : OnboardingWizard
            The wizard to update.
        selected_modules : list[str]
            The list of selected optional module keys.
        user : User, optional
            The user performing the action (for audit logging).

        Returns
        -------
        list[str]
            The full list of enabled modules (core + selected optional).
        """
        enabled = ModuleConfigurationService._merge_modules(selected_modules)

        before = {
            "selected_modules": wizard.selected_modules,
        }
        wizard.selected_modules = enabled
        wizard.save(update_fields=["selected_modules"])
        wizard.refresh_from_db()

        ModuleConfigurationService._log_audit(
            wizard=wizard,
            action="CONFIGURE_MODULES",
            user=user,
            before_state=before,
            after_state={"selected_modules": wizard.selected_modules},
            details={"enabled_modules": enabled},
        )
        return enabled

    @staticmethod
    def get_enabled_modules(wizard) -> list[str]:
        """Return the list of enabled modules (core + selected optional).

        If the wizard has no ``selected_modules`` set yet, returns only the
        core modules.

        Parameters
        ----------
        wizard : OnboardingWizard
            The wizard to query.

        Returns
        -------
        list[str]
            The list of enabled module keys.
        """
        stored = wizard.selected_modules or []
        # Ensure core modules are always present (defensive — they should
        # already be there if configure_modules was used).
        return ModuleConfigurationService._merge_modules(stored)

    @staticmethod
    def get_module_display_names() -> dict[str, str]:
        """Return a dict mapping module keys to human-readable names.

        Returns
        -------
        dict[str, str]
            Mapping of all module keys to display names.
        """
        return dict(MODULE_DISPLAY_NAMES)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _merge_modules(selected_modules) -> list[str]:
        """Merge core modules with the selected optional modules.

        De-duplicates while preserving order: core modules first, then
        selected optional modules in the order they were provided.
        """
        seen: set[str] = set(CORE_MODULES)
        modules = list(CORE_MODULES)
        for mod in selected_modules or []:
            mod = (mod or "").strip().lower()
            if mod and mod not in seen and mod in ALL_MODULES:
                seen.add(mod)
                modules.append(mod)
        return modules

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

        Wrapped so a logging failure never blocks a legitimate operation.
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
