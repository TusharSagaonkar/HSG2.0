"""CRUD + reordering service for ``VisitorCategory`` (Phase 4).

The ``VisitorCategory`` model already supports CRUD via the generic setup
views. This service adds atomic reordering (move up/down) and ``sort_order``
compaction, plus the deactivation guard (Invariant #8: a category referenced
by an open :class:`GateEvent` cannot be deactivated).

Design notes
------------
- **Race safety:** every reordering method is wrapped in
  ``@transaction.atomic`` and uses ``select_for_update()`` so concurrent
  reorders cannot interleave or lose updates.
- **Society scoping:** all queries are filtered by ``society`` so a reorder in
  one tenant never touches another tenant's ``sort_order`` values.
- **Active-only reordering:** ``move_up`` / ``move_down`` / ``reorder`` only
  consider ``is_active=True`` categories. Deactivated rows keep their
  ``sort_order`` and are skipped during swaps.
- **Audit robustness:** audit-log writes are wrapped so a logging failure
  never blocks a legitimate reorder (the error is logged loudly instead),
  matching the ``GateEventLifecycleService`` philosophy.
- **All methods are ``@staticmethod``** per the service contract; there is no
  shared mutable state.
"""

from __future__ import annotations

import logging

from django.db import transaction

from gateops.models import GateOpsAuditLog, VisitorCategory

logger = logging.getLogger(__name__)

# GateEvent statuses that count as "open" for the deactivation guard
# (Invariant #8). A category referenced by an event in any of these states
# cannot be deactivated because the event still needs the category for
# reporting/approval flows.
_OPEN_EVENT_STATUSES = (
    "invited",
    "arrived",
    "approved",
    "entered",
)


class VisitorCategoryService:
    """CRUD + reordering service for VisitorCategory (Phase 4).

    The model already supports CRUD via the generic setup views. This service
    adds atomic reordering (move up/down) and ``sort_order`` compaction.
    """

    # ------------------------------------------------------------------ #
    # Public reordering API
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def move_up(category, actor=None):
        """Swap ``sort_order`` with the previous active category.

        Looks for the active category in the same society with the next-lower
        ``sort_order`` and swaps the two values atomically. If the category is
        already at the top (no active category above it) this is a no-op.

        Returns ``True`` if a swap occurred, ``False`` otherwise.
        """
        prev = (
            VisitorCategory.objects.select_for_update()
            .filter(
                society=category.society,
                is_active=True,
                sort_order__lt=category.sort_order,
            )
            .order_by("-sort_order")
            .first()
        )
        if prev is None:
            return False
        VisitorCategoryService._swap_and_audit(category, prev, actor=actor)
        return True

    @staticmethod
    @transaction.atomic
    def move_down(category, actor=None):
        """Swap ``sort_order`` with the next active category.

        Looks for the active category in the same society with the next-higher
        ``sort_order`` and swaps the two values atomically. If the category is
        already at the bottom (no active category below it) this is a no-op.

        Returns ``True`` if a swap occurred, ``False`` otherwise.
        """
        nxt = (
            VisitorCategory.objects.select_for_update()
            .filter(
                society=category.society,
                is_active=True,
                sort_order__gt=category.sort_order,
            )
            .order_by("sort_order")
            .first()
        )
        if nxt is None:
            return False
        VisitorCategoryService._swap_and_audit(category, nxt, actor=actor)
        return True

    @staticmethod
    @transaction.atomic
    def reorder(society, actor=None):
        """Compact ``sort_order`` to ``0, 1, 2, ...`` for all active categories.

        Useful after deletions or manual edits that leave gaps in the
        ``sort_order`` sequence. Only active categories are reindexed;
        deactivated rows are left untouched.

        Returns the number of categories whose ``sort_order`` changed.
        """
        cats = list(
            VisitorCategory.objects.select_for_update()
            .filter(society=society, is_active=True)
            .order_by("sort_order", "name")
        )
        changed = 0
        for idx, cat in enumerate(cats):
            if cat.sort_order != idx:
                before = {"sort_order": cat.sort_order}
                VisitorCategory.objects.filter(pk=cat.pk).update(sort_order=idx)
                cat.sort_order = idx
                after = {"sort_order": idx}
                VisitorCategoryService._audit(cat, actor, before, after)
                changed += 1
        return changed

    @staticmethod
    def can_deactivate(category):
        """Return ``True`` if a VisitorCategory can be deactivated.

        A category referenced by an open :class:`GateEvent` (one whose status
        is ``invited``, ``arrived``, ``approved``, or ``entered``) cannot be
        deactivated — Invariant #8 from the design spec.
        """
        from gateops.models import GateEvent

        return not GateEvent.objects.filter(
            visitor_category=category,
            status__in=_OPEN_EVENT_STATUSES,
        ).exists()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _swap_and_audit(a, b, actor=None):
        """Atomically swap ``sort_order`` between two categories and audit.

        Uses ``update()`` on each row (rather than ``save()``) to avoid
        triggering ``clean()`` and to issue a targeted UPDATE that does not
        race with concurrent reorders inside the surrounding
        ``select_for_update`` transaction.
        """
        before_a = {"sort_order": a.sort_order}
        before_b = {"sort_order": b.sort_order}
        # Swap using update() to avoid race conditions and clean() re-runs.
        VisitorCategory.objects.filter(pk=a.pk).update(sort_order=before_b["sort_order"])
        VisitorCategory.objects.filter(pk=b.pk).update(sort_order=before_a["sort_order"])
        # Keep in-memory objects in sync for callers.
        a.sort_order, b.sort_order = before_b["sort_order"], before_a["sort_order"]
        # Audit both changes.
        VisitorCategoryService._audit(a, actor, before_a, {"sort_order": a.sort_order})
        VisitorCategoryService._audit(b, actor, before_b, {"sort_order": b.sort_order})

    @staticmethod
    def _audit(category, actor, before, after):
        """Write an append-only GateOpsAuditLog entry for a reorder change.

        Wrapped so a logging failure never blocks a legitimate reorder; the
        error is logged at ERROR level instead, matching the
        ``GateEventLifecycleService`` audit-robustness contract.
        """
        try:
            GateOpsAuditLog.log(
                society=category.society,
                action=GateOpsAuditLog.Action.UPDATE,
                entity_type="VisitorCategory",
                entity_id=str(category.pk),
                actor=actor,
                before_value=before,
                after_value=after,
            )
        except Exception:  # noqa: BLE001 — audit must not break the reorder.
            logger.exception(
                "Failed to write GateOpsAuditLog for VisitorCategory %s reorder",
                category.pk,
            )
