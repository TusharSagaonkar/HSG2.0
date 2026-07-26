"""Financial year setup service for the Society Creation & Accounting Migration
Wizard (Steps 4 & 5 — Accounting Start Year and Financial Year Creation).

This service creates a :class:`FinancialYear` for the society being onboarded.
The ``FinancialYear.save()`` method auto-creates monthly
:class:`AccountingPeriod` records, so this service only needs to create the
FinancialYear itself.

Design notes
------------
- **All methods are ``@staticmethod``** per the service contract established
  in ``gateops/services/contractor_service.py``.
- Supports three financial year patterns: ``APRIL_MARCH``, ``JAN_DEC``, and
  ``JUL_JUN``.
- The FY label format is ``"YYYY-YY"`` (e.g. ``"2026-27"``), where the first
  part is the calendar year in which the FY starts.
- Audit logging is via :class:`MigrationAuditLog`.
- The created FinancialYear is set as the active (open) financial year for
  the society.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounting.models import FinancialYear
from onboarding.models import MigrationAuditLog, OnboardingWizard

logger = logging.getLogger(__name__)

# Financial year pattern constants.
FY_PATTERN_APRIL_MARCH = "APRIL_MARCH"
FY_PATTERN_JAN_DEC = "JAN_DEC"
FY_PATTERN_JUL_JUN = "JUL_JUN"

VALID_FY_PATTERNS = {FY_PATTERN_APRIL_MARCH, FY_PATTERN_JAN_DEC, FY_PATTERN_JUL_JUN}

# Maps each FY pattern to the (start_month, start_day, end_month, end_day)
# relative to the start calendar year. The end date is in the *next* calendar
# year for APRIL_MARCH and JUL_JUN, and the *same* year for JAN_DEC.
_FY_PATTERN_MONTHS: dict[str, tuple[int, int, int, int, bool]] = {
    # (start_month, start_day, end_month, end_day, end_in_next_year)
    FY_PATTERN_APRIL_MARCH: (4, 1, 3, 31, True),
    FY_PATTERN_JAN_DEC: (1, 1, 12, 31, False),
    FY_PATTERN_JUL_JUN: (7, 1, 6, 30, True),
}


class FinancialYearSetupService:
    """Service for creating the financial year during onboarding (Steps 4 & 5).

    Handles parsing the FY label (e.g. ``"2026-27"``) into start/end dates
    based on the society's financial year pattern, and creating the
    :class:`FinancialYear` record (which auto-creates monthly periods).
    """

    @staticmethod
    @transaction.atomic
    def create_financial_year(wizard, start_year, user=None) -> FinancialYear:
        """Create a :class:`FinancialYear` for the wizard's society.

        The ``start_year`` is a label like ``"2026-27"``. It is parsed to
        derive the start and end dates based on the society's financial year
        pattern (April-March by default).

        The :class:`FinancialYear.save()` method auto-creates 12 monthly
        :class:`AccountingPeriod` records. This FinancialYear is set as the
        active (open) financial year.

        Parameters
        ----------
        wizard : OnboardingWizard
            The wizard whose society the FY is being created for. The
            society must already be linked (``wizard.society`` must not be
            None).
        start_year : str
            The FY label, e.g. ``"2026-27"``.
        user : User, optional
            The user performing the action (for audit logging).

        Returns
        -------
        FinancialYear
            The created (or existing) FinancialYear instance.

        Raises
        ------
        ValidationError
            If the wizard has no society linked, or if ``start_year``
            cannot be parsed.
        """
        if wizard.society is None:
            raise ValidationError(
                "Cannot create a financial year before the society is created. "
                "Ensure Step 1 (Society Details) is completed first."
            )

        fy_pattern = FinancialYearSetupService.get_financial_year_pattern(wizard.society)
        start_date, end_date = FinancialYearSetupService.derive_fy_dates(
            fy_label=start_year, fy_pattern=fy_pattern
        )

        society = wizard.society
        fy_name = f"FY {start_year}"

        # Use get_or_create for idempotency — if the FY already exists
        # (e.g. re-running the step after going back), return it.
        fy, created = FinancialYear.objects.get_or_create(
            society=society,
            start_date=start_date,
            end_date=end_date,
            defaults={
                "name": fy_name,
                "is_open": True,
            },
        )

        # Ensure it's open (in case it was previously closed).
        if not fy.is_open:
            fy.is_open = True
            fy.save(update_fields=["is_open"])

        # Store the accounting start year on the wizard for reference.
        before = {
            "wizard_data": dict(wizard.wizard_data) if wizard.wizard_data else {},
        }
        data = dict(wizard.wizard_data) if wizard.wizard_data else {}
        data["accounting_start_year"] = start_year
        data["fy_pattern"] = fy_pattern
        data["financial_year_id"] = str(fy.pk)
        wizard.wizard_data = data
        wizard.save(update_fields=["wizard_data"])
        wizard.refresh_from_db()

        FinancialYearSetupService._log_audit(
            wizard=wizard,
            action="CREATE_FINANCIAL_YEAR",
            user=user,
            before_state=before,
            after_state={
                "wizard_data": wizard.wizard_data,
                "financial_year": {
                    "id": str(fy.pk),
                    "name": fy.name,
                    "start_date": fy.start_date.isoformat(),
                    "end_date": fy.end_date.isoformat(),
                    "is_open": fy.is_open,
                },
            },
            details={
                "fy_label": start_year,
                "fy_pattern": fy_pattern,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "created": created,
            },
        )
        return fy

    @staticmethod
    def get_financial_year_pattern(society) -> str:
        """Return the FY pattern for the society.

        Looks up the pattern from the society's wizard_data (stored during
        Step 1) or defaults to ``APRIL_MARCH``.

        Parameters
        ----------
        society : Society
            The society to get the FY pattern for.

        Returns
        -------
        str
            One of ``APRIL_MARCH``, ``JAN_DEC``, ``JUL_JUN``.
        """
        # The FY pattern is stored on the wizard's wizard_data during Step 1.
        # We look it up via the society's onboarding wizards.
        wizard = (
            OnboardingWizard.objects.unscoped()
            .filter(society=society)
            .order_by("-started_at")
            .first()
        )
        if wizard and wizard.wizard_data:
            pattern = wizard.wizard_data.get("fy_pattern")
            if pattern and pattern in VALID_FY_PATTERNS:
                return pattern
        return FY_PATTERN_APRIL_MARCH

    @staticmethod
    def derive_fy_dates(fy_label, fy_pattern=FY_PATTERN_APRIL_MARCH) -> tuple[date, date]:
        """Parse a FY label into ``(start_date, end_date)``.

        Parameters
        ----------
        fy_label : str
            The FY label, e.g. ``"2026-27"``. The first part is the calendar
            year in which the FY starts.
        fy_pattern : str
            One of ``APRIL_MARCH``, ``JAN_DEC``, ``JUL_JUN``.

        Returns
        -------
        tuple[date, date]
            The ``(start_date, end_date)`` for the financial year.

        Raises
        ------
        ValidationError
            If the label cannot be parsed or the pattern is invalid.
        """
        if fy_pattern not in VALID_FY_PATTERNS:
            raise ValidationError(
                f"Invalid FY pattern '{fy_pattern}'. "
                f"Must be one of {sorted(VALID_FY_PATTERNS)}."
            )

        start_year = FinancialYearSetupService._parse_start_year(fy_label)
        start_month, start_day, end_month, end_day, end_in_next_year = _FY_PATTERN_MONTHS[fy_pattern]

        start_date = date(start_year, start_month, start_day)
        end_year = start_year + 1 if end_in_next_year else start_year
        end_date = date(end_year, end_month, end_day)

        return start_date, end_date

    @staticmethod
    def get_fy_options(fy_pattern=FY_PATTERN_APRIL_MARCH, reference_year=None) -> list[str]:
        """Generate the current FY label followed by the previous ten years.

        Parameters
        ----------
        fy_pattern : str
            One of ``APRIL_MARCH``, ``JAN_DEC``, ``JUL_JUN``.
        reference_year : int, optional
            The current FY's start year. When omitted, it is derived from
            today's date and the configured financial-year start month.

        Returns
        -------
        list[str]
            Eleven labels in descending order, e.g. ``["2026-27", ...,
            "2016-17"]``.
        """
        if fy_pattern not in VALID_FY_PATTERNS:
            raise ValidationError(
                f"Invalid FY pattern '{fy_pattern}'. "
                f"Must be one of {sorted(VALID_FY_PATTERNS)}."
            )

        if reference_year is None:
            today = timezone.localdate()
            start_month = _FY_PATTERN_MONTHS[fy_pattern][0]
            reference_year = today.year if today.month >= start_month else today.year - 1

        labels = []
        for offset in range(11):
            start_year = reference_year - offset
            end_year_short = str(start_year + 1)[-2:]
            labels.append(f"{start_year}-{end_year_short}")
        return labels

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_start_year(fy_label) -> int:
        """Parse the start calendar year from a FY label like ``"2026-27"``.

        Handles both ``"2026-27"`` and ``"2026"`` formats.

        Raises
        ------
        ValidationError
            If the label cannot be parsed.
        """
        if not fy_label or not isinstance(fy_label, str):
            raise ValidationError(
                f"Invalid FY label '{fy_label}'. Expected format 'YYYY-YY' (e.g. '2026-27')."
            )
        label = fy_label.strip()
        # Split on '-' and take the first part as the start year.
        parts = label.split("-")
        try:
            start_year = int(parts[0])
        except (ValueError, IndexError):
            raise ValidationError(
                f"Invalid FY label '{fy_label}'. "
                "Expected format 'YYYY-YY' (e.g. '2026-27')."
            )
        if start_year < 1900 or start_year > 2100:
            raise ValidationError(
                f"FY start year {start_year} is out of range (1900–2100)."
            )
        return start_year

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
