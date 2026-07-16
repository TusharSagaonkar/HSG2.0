"""Society setup service for the Society Creation & Accounting Migration Wizard
(Steps 1, 6, 7, 8 — Society Details, Structure, Unit Configuration, Member
Assignment).

This service wraps existing society-creation patterns from
``societies/services.py`` (``create_society``) and
``housing/management/commands/seed_deepsagar.py`` (structure/unit/member
creation), adapting them for the wizard flow.

Design notes
------------
- **All methods are ``@staticmethod``** per the service contract established
  in ``gateops/services/contractor_service.py``.
- **Tenant context:** When the wizard creates a new society (Step 1), the
  ``_current_tenant`` contextvar is set so that subsequent queries
  (structures, units, members) are automatically scoped to the new society.
- **Idempotency:** Structure, Unit, and Member creation uses
  ``get_or_create`` so re-running a step (after going back) does not create
  duplicates.
- **Extended society fields:** The :class:`Society` model only has ``name``,
  ``registration_number``, ``address``, ``created_by``, and ``created_at``.
  Extra fields from the wizard (city, state, PAN, GST, email, phone, FY
  pattern, etc.) are stored in ``wizard.wizard_data`` for later use.
- **Member lifecycle:** After creating a Member, ``sync_member_unit_lifecycle``
  is called to create :class:`UnitOwnership` and :class:`UnitOccupancy`
  records (pattern from ``MemberCreateView``).
- Audit logging is via :class:`MigrationAuditLog`.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from housing.services.membership_lifecycle import sync_member_unit_lifecycle
from members.models import Member, Structure, Unit
from onboarding.models import MigrationAuditLog, OnboardingWizard
from societies.managers import _current_tenant
from societies.models import Society
from societies.services import create_society

logger = logging.getLogger(__name__)

# Fields from the wizard's society_data that map directly to the Society model.
# The Society model only has: name, registration_number, address, created_by.
_SOCIETY_MODEL_FIELDS = {"name", "registration_number", "address"}

# Extra fields from society_data that don't exist on the Society model.
# These are stored in wizard.wizard_data for later reference.
_SOCIETY_EXTRA_FIELDS = {
    "registration_date", "society_type", "city", "state", "country",
    "pin_code", "pan", "gst_number", "tan", "email", "phone",
    "timezone", "currency", "financial_year_pattern",
}

# Maps usage_type values from the wizard to Unit.UnitType choices.
_USAGE_TYPE_TO_UNIT_TYPE: dict[str, str] = {
    "RESIDENTIAL": Unit.UnitType.FLAT,
    "COMMERCIAL": Unit.UnitType.OFFICE,
    "SHOP": Unit.UnitType.SHOP,
    "OFFICE": Unit.UnitType.OFFICE,
    # Direct UnitType values (in case the caller passes them directly).
    "FLAT": Unit.UnitType.FLAT,
    "OTHER": Unit.UnitType.OTHER,
}

# Maps member_type values from the wizard to Member.MemberRole choices.
_MEMBER_TYPE_TO_ROLE: dict[str, str] = {
    "OWNER": Member.MemberRole.OWNER,
    "TENANT": Member.MemberRole.TENANT,
    "NOMINEE": Member.MemberRole.NOMINEE,
    # Direct MemberRole values.
    "ASSOCIATE": Member.MemberRole.OWNER,  # Associates map to OWNER role.
}


class SocietySetupService:
    """Service for creating the society, structures, units, and members.

    Handles Steps 1, 6, 7, and 8 of the wizard:
        * Step 1 — :meth:`create_society`
        * Step 6 — :meth:`create_structure`
        * Step 7 — :meth:`create_units`
        * Step 8 — :meth:`assign_members`
    """

    # ------------------------------------------------------------------ #
    # Step 1: Society creation
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def create_society(wizard, society_data, user=None) -> Society:
        """Create a :class:`Society` and link it to the wizard.

        Uses the ``create_society`` service from ``societies.services`` to
        atomically create the Society + Membership (owner). Extended fields
        (city, state, PAN, GST, etc.) that don't exist on the Society model
        are stored in ``wizard.wizard_data``.

        After creation, the ``_current_tenant`` contextvar is set so
        subsequent queries are scoped to the new society.

        Parameters
        ----------
        wizard : OnboardingWizard
            The wizard to link the society to.
        society_data : dict
            Dictionary with keys: ``name``, ``registration_number``,
            ``registration_date``, ``society_type``, ``address``, ``city``,
            ``state``, ``country``, ``pin_code``, ``pan``, ``gst_number``,
            ``tan``, ``email``, ``phone``, ``timezone``, ``currency``,
            ``financial_year_pattern``.
        user : User, optional
            The user creating the society. Defaults to ``wizard.created_by``.

        Returns
        -------
        Society
            The created society instance.

        Raises
        ------
        ValidationError
            If ``society_data`` is missing required fields (``name``).
        """
        if not society_data or not isinstance(society_data, dict):
            raise ValidationError("society_data must be a non-empty dict.")

        name = (society_data.get("name") or "").strip()
        if not name:
            raise ValidationError({"name": "Society name is required."})

        actor = user or wizard.created_by

        # Extract the fields that map to the Society model.
        registration_number = (society_data.get("registration_number") or "").strip()
        address = (society_data.get("address") or "").strip()

        # Create the Society + Membership via the existing service.
        society = create_society(
            user=actor,
            name=name,
            registration_number=registration_number,
            address=address,
        )

        # Link the society to the wizard.
        wizard.society = society

        # Store extra fields in wizard_data (they don't exist on the Society
        # model but are needed later, e.g. FY pattern for Step 5).
        extra_data = {}
        for key in _SOCIETY_EXTRA_FIELDS:
            value = society_data.get(key)
            if value is not None and value != "":
                extra_data[key] = value
        # Also store the full society_data snapshot for reference.
        extra_data["society_details"] = {
            k: v for k, v in society_data.items() if v is not None and v != ""
        }

        data = dict(wizard.wizard_data) if wizard.wizard_data else {}
        data.update(extra_data)
        wizard.wizard_data = data
        wizard.save(update_fields=["society", "wizard_data"])
        wizard.refresh_from_db()

        # Set the tenant contextvar so subsequent queries are scoped.
        _current_tenant.set(society)

        SocietySetupService._log_audit(
            wizard=wizard,
            action="CREATE_SOCIETY",
            user=actor,
            after_state={
                "society_id": str(society.pk),
                "name": society.name,
                "registration_number": society.registration_number,
            },
            details={
                "society_data": extra_data,
            },
        )
        return society

    # ------------------------------------------------------------------ #
    # Step 6: Structure creation
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def create_structure(wizard, structures_data, user=None) -> list[Structure]:
        """Create :class:`Structure` records (Building, Wing, Floor) for the
        wizard's society.

        The ``structures_data`` is a list of dicts. Each dict represents a
        structure node with:
            * ``building_name`` (str) — the building name.
            * ``wing_name`` (str, optional) — the wing name within the building.
            * ``floor_number`` (int, optional) — the floor number.
            * ``structure_type`` (str, optional) — override the structure type.
            * ``display_order`` (int, optional) — display ordering.

        Structures are created hierarchically: Building → Wing (optional) →
        Floor (optional). Parent-child relationships are established
        automatically.

        Parameters
        ----------
        wizard : OnboardingWizard
            The wizard whose society the structures belong to.
        structures_data : list[dict]
            List of structure definition dicts.
        user : User, optional
            The user performing the action.

        Returns
        -------
        list[Structure]
            The created (or existing) Structure instances.

        Raises
        ------
        ValidationError
            If the wizard has no society linked, or if ``structures_data``
            is empty.
        """
        society = SocietySetupService._get_society(wizard)

        if not structures_data:
            raise ValidationError("structures_data must be a non-empty list.")

        actor = user or wizard.created_by
        created_structures: list[Structure] = []

        # Cache building/wing structures by name for parent lookups.
        building_cache: dict[str, Structure] = {}
        wing_cache: dict[str, Structure] = {}  # key: "building|wing"

        for idx, node in enumerate(structures_data, start=1):
            if not isinstance(node, dict):
                continue

            building_name = (node.get("building_name") or "").strip()
            if not building_name:
                continue

            # 1. Create or get the Building.
            building = building_cache.get(building_name)
            if building is None:
                building, _ = Structure.objects.get_or_create(
                    society=society,
                    parent=None,
                    structure_type=Structure.StructureType.BUILDING,
                    name=building_name,
                    defaults={"display_order": idx},
                )
                building_cache[building_name] = building
            created_structures.append(building)

            # 2. Create or get the Wing (optional).
            wing_name = (node.get("wing_name") or "").strip()
            wing = None
            if wing_name:
                wing_key = f"{building_name}|{wing_name}"
                wing = wing_cache.get(wing_key)
                if wing is None:
                    wing, _ = Structure.objects.get_or_create(
                        society=society,
                        parent=building,
                        structure_type=Structure.StructureType.WING,
                        name=wing_name,
                        defaults={"display_order": idx},
                    )
                    wing_cache[wing_key] = wing
                created_structures.append(wing)

            # 3. Create or get the Floor (optional).
            floor_number = node.get("floor_number")
            if floor_number is not None:
                floor_name = str(floor_number)
                parent = wing or building
                floor, _ = Structure.objects.get_or_create(
                    society=society,
                    parent=parent,
                    structure_type=Structure.StructureType.FLOOR,
                    name=floor_name,
                    defaults={"display_order": idx},
                )
                created_structures.append(floor)

        SocietySetupService._log_audit(
            wizard=wizard,
            action="CREATE_STRUCTURES",
            user=actor,
            after_state={
                "structure_count": len(created_structures),
                "buildings": list(building_cache.keys()),
            },
            details={
                "structures_data": structures_data,
            },
        )
        return created_structures

    # ------------------------------------------------------------------ #
    # Step 7: Unit creation
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def create_units(wizard, units_data, user=None) -> list[Unit]:
        """Create :class:`Unit` records for the wizard's society.

        The ``units_data`` is a list of dicts. Each dict represents a unit
        with:
            * ``flat_number`` (str) — the unit identifier (e.g. "101", "A1-101").
            * ``area`` (Decimal/float/str, optional) — area in sqft.
            * ``usage_type`` (str, optional) — ``RESIDENTIAL``, ``COMMERCIAL``,
              ``SHOP``, ``OFFICE``. Defaults to ``RESIDENTIAL`` (FLAT).
            * ``parking_allocation`` (str, optional) — stored in wizard_data.
            * ``maintenance_calc_method`` (str, optional) — stored in wizard_data.
            * ``building`` (str) — the building name (to resolve the structure).
            * ``wing`` (str, optional) — the wing name.
            * ``floor`` (str/int, optional) — the floor number/name.

        Units are linked to the deepest available structure (Floor > Wing >
        Building) for the given building/wing/floor combination.

        Parameters
        ----------
        wizard : OnboardingWizard
            The wizard whose society the units belong to.
        units_data : list[dict]
            List of unit definition dicts.
        user : User, optional
            The user performing the action.

        Returns
        -------
        list[Unit]
            The created (or existing) Unit instances.

        Raises
        ------
        ValidationError
            If the wizard has no society linked, or if a unit's building
            structure cannot be found.
        """
        society = SocietySetupService._get_society(wizard)

        if not units_data:
            raise ValidationError("units_data must be a non-empty list.")

        actor = user or wizard.created_by
        created_units: list[Unit] = []
        extra_unit_data: list[dict[str, Any]] = []

        for idx, unit_data in enumerate(units_data, start=1):
            if not isinstance(unit_data, dict):
                continue

            flat_number = (unit_data.get("flat_number") or "").strip()
            if not flat_number:
                continue

            # Resolve the structure for this unit.
            structure = SocietySetupService._resolve_structure(
                society=society,
                building_name=unit_data.get("building"),
                wing_name=unit_data.get("wing"),
                floor=unit_data.get("floor"),
            )

            # Map usage_type to UnitType.
            usage_type = (unit_data.get("usage_type") or "RESIDENTIAL").upper()
            unit_type = _USAGE_TYPE_TO_UNIT_TYPE.get(usage_type, Unit.UnitType.FLAT)

            # Parse area.
            area = SocietySetupService._parse_decimal(unit_data.get("area"))

            # Create or get the unit.
            unit, _ = Unit.objects.get_or_create(
                structure=structure,
                identifier=flat_number,
                defaults={
                    "unit_type": unit_type,
                    "area_sqft": area,
                    "is_active": True,
                },
            )
            created_units.append(unit)

            # Collect extra fields for wizard_data storage.
            extra = {
                "flat_number": flat_number,
                "building": unit_data.get("building"),
                "wing": unit_data.get("wing"),
                "floor": unit_data.get("floor"),
            }
            if unit_data.get("parking_allocation"):
                extra["parking_allocation"] = unit_data["parking_allocation"]
            if unit_data.get("maintenance_calc_method"):
                extra["maintenance_calc_method"] = unit_data["maintenance_calc_method"]
            extra_unit_data.append(extra)

        # Store extra unit metadata in wizard_data.
        data = dict(wizard.wizard_data) if wizard.wizard_data else {}
        data["units_metadata"] = extra_unit_data
        wizard.wizard_data = data
        wizard.save(update_fields=["wizard_data"])
        wizard.refresh_from_db()

        SocietySetupService._log_audit(
            wizard=wizard,
            action="CREATE_UNITS",
            user=actor,
            after_state={
                "unit_count": len(created_units),
            },
            details={
                "units_data": units_data,
            },
        )
        return created_units

    # ------------------------------------------------------------------ #
    # Step 8: Member assignment
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def assign_members(wizard, members_data, user=None) -> list[Member]:
        """Create :class:`Member` records and sync ownership/occupancy.

        The ``members_data`` is a list of dicts. Each dict represents a member
        with:
            * ``member_name`` (str) — the member's full name.
            * ``member_type`` (str) — ``OWNER``, ``ASSOCIATE``, ``TENANT``,
              ``NOMINEE``.
            * ``unit_identifier`` (str) — the unit's identifier (flat number).
            * ``email`` (str, optional) — the member's email.
            * ``phone`` (str, optional) — the member's phone.
            * ``occupation_status`` (str, optional) — stored in wizard_data.
            * ``start_date`` (date, optional) — tenancy/ownership start.

        After creating each Member, ``sync_member_unit_lifecycle`` is called
        to create :class:`UnitOwnership` and :class:`UnitOccupancy` records
        (pattern from ``MemberCreateView`` and ``seed_deepsagar``).

        Parameters
        ----------
        wizard : OnboardingWizard
            The wizard whose society the members belong to.
        members_data : list[dict]
            List of member definition dicts.
        user : User, optional
            The user performing the action.

        Returns
        -------
        list[Member]
            The created (or existing) Member instances.

        Raises
        ------
        ValidationError
            If the wizard has no society linked, or if a member's unit
            cannot be found.
        """
        society = SocietySetupService._get_society(wizard)

        if not members_data:
            raise ValidationError("members_data must be a non-empty list.")

        actor = user or wizard.created_by
        created_members: list[Member] = []
        extra_member_data: list[dict[str, Any]] = []

        for idx, member_data in enumerate(members_data, start=1):
            if not isinstance(member_data, dict):
                continue

            member_name = (member_data.get("member_name") or "").strip()
            if not member_name:
                continue

            # Resolve the unit by identifier.
            unit_identifier = (member_data.get("unit_identifier") or "").strip()
            if not unit_identifier:
                continue

            unit = SocietySetupService._resolve_unit(society, unit_identifier)

            # Map member_type to MemberRole.
            member_type = (member_data.get("member_type") or "OWNER").upper()
            role = _MEMBER_TYPE_TO_ROLE.get(member_type, Member.MemberRole.OWNER)

            email = (member_data.get("email") or "").strip()
            phone = (member_data.get("phone") or "").strip()

            # Parse start_date (defaults to today).
            start_date = SocietySetupService._parse_date(
                member_data.get("start_date"),
                default=timezone.localdate(),
            )

            # Create or get the member.
            member, _ = Member.objects.get_or_create(
                society=society,
                unit=unit,
                full_name=member_name,
                role=role,
                defaults={
                    "email": email,
                    "phone": phone,
                    "status": Member.MemberStatus.ACTIVE,
                    "start_date": start_date,
                    "join_date": start_date,
                },
            )

            # Sync ownership and occupancy (creates UnitOwnership +
            # UnitOccupancy). This is idempotent and safe to call multiple
            # times.
            try:
                sync_member_unit_lifecycle(member)
            except Exception:
                # Lifecycle sync failures (e.g. user provisioning issues)
                # should not block member creation. Log and continue.
                logger.exception(
                    "Failed to sync member unit lifecycle for member %s (unit %s)",
                    member.pk,
                    unit.pk,
                )

            created_members.append(member)

            # Collect extra fields for wizard_data storage.
            extra = {
                "member_name": member_name,
                "member_type": member_type,
                "unit_identifier": unit_identifier,
                "email": email,
                "phone": phone,
            }
            if member_data.get("occupation_status"):
                extra["occupation_status"] = member_data["occupation_status"]
            extra_member_data.append(extra)

        # Store extra member metadata in wizard_data.
        data = dict(wizard.wizard_data) if wizard.wizard_data else {}
        data["members_metadata"] = extra_member_data
        wizard.wizard_data = data
        wizard.save(update_fields=["wizard_data"])
        wizard.refresh_from_db()

        SocietySetupService._log_audit(
            wizard=wizard,
            action="ASSIGN_MEMBERS",
            user=actor,
            after_state={
                "member_count": len(created_members),
            },
            details={
                "members_data": members_data,
            },
        )
        return created_members

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_society(wizard) -> Society:
        """Return the wizard's society or raise ``ValidationError``."""
        if wizard.society is None:
            raise ValidationError(
                "Society has not been created yet. Complete Step 1 (Society "
                "Details) before proceeding."
            )
        # Ensure the tenant contextvar is set for scoped queries.
        _current_tenant.set(wizard.society)
        return wizard.society

    @staticmethod
    def _resolve_structure(
        *,
        society: Society,
        building_name: str | None,
        wing_name: str | None,
        floor: str | int | None,
    ) -> Structure:
        """Resolve the deepest structure for the given building/wing/floor.

        Falls back to Building if Wing/Floor are not specified or not found.

        Raises
        ------
        ValidationError
            If the building structure cannot be found.
        """
        building_name = (building_name or "").strip()
        if not building_name:
            raise ValidationError("Building name is required to resolve a structure.")

        # Find the building.
        building = Structure.objects.filter(
            society=society,
            parent__isnull=True,
            structure_type=Structure.StructureType.BUILDING,
            name=building_name,
        ).first()
        if building is None:
            raise ValidationError(
                f"Building '{building_name}' not found. Create structures "
                "(Step 6) before creating units."
            )

        # Try to find a wing under the building.
        parent = building
        wing_name = (wing_name or "").strip()
        if wing_name:
            wing = Structure.objects.filter(
                society=society,
                parent=building,
                structure_type=Structure.StructureType.WING,
                name=wing_name,
            ).first()
            if wing is not None:
                parent = wing

        # Try to find a floor under the wing/building.
        if floor is not None:
            floor_name = str(floor)
            floor_obj = Structure.objects.filter(
                society=society,
                parent=parent,
                structure_type=Structure.StructureType.FLOOR,
                name=floor_name,
            ).first()
            if floor_obj is not None:
                parent = floor_obj

        return parent

    @staticmethod
    def _resolve_unit(society: Society, unit_identifier: str) -> Unit:
        """Resolve a :class:`Unit` by its identifier within the society.

        Raises
        ------
        ValidationError
            If the unit cannot be found.
        """
        unit = (
            Unit.objects.select_related("structure")
            .filter(
                structure__society=society,
                identifier=unit_identifier,
            )
            .first()
        )
        if unit is None:
            raise ValidationError(
                f"Unit '{unit_identifier}' not found in society '{society.name}'. "
                "Create units (Step 7) before assigning members."
            )
        return unit

    @staticmethod
    def _parse_decimal(value) -> Decimal | None:
        """Parse a value into a :class:`Decimal`, returning ``None`` on failure."""
        if value is None or value == "":
            return None
        try:
            return Decimal(str(value))
        except Exception:
            return None

    @staticmethod
    def _parse_date(value, default=None):
        """Parse a value into a ``date``, returning ``default`` on failure.

        Accepts ``date`` objects, ISO date strings (``"YYYY-MM-DD"``), and
        ``datetime`` objects.
        """
        if value is None:
            return default
        if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
            # Already a date or datetime — extract the date part.
            return getattr(value, "date", lambda: value)()
        if isinstance(value, str):
            try:
                from datetime import datetime

                return datetime.fromisoformat(value).date()
            except ValueError:
                return default
        return default

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
