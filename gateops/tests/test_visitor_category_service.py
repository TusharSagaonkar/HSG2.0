"""Phase 4 tests for the VisitorCategoryService reordering service.

Covers: move_up / move_down swaps, no-op at boundaries, sort_order compaction
(``reorder``), audit logging, active-only reordering, society scoping, the
``can_deactivate`` guard (Invariant #8), and the POST-only society-scoped
``setup_move_view`` frontend endpoint.

Convention: societies are created once per class in ``setUpTestData`` (society
creation triggers expensive bootstrap signals). Per-test mutations are reset in
``setUp`` so each test starts from a clean contiguous ``sort_order`` sequence.
"""

from django.test import TestCase
from django.urls import reverse

from gateops.models import Gate, GateEvent, GateOpsAuditLog, VisitorCategory
from gateops.services.visitor_category_service import VisitorCategoryService
from housing_accounting.selection import SESSION_SELECTED_SOCIETY_ID
from housing_accounting.users.tests.factories import UserFactory
from societies.services import create_society


class VisitorCategoryServiceTest(TestCase):
    """Service-level tests for VisitorCategoryService reordering.

    The society and seeded master data are created once per class via
    ``setUpTestData`` to avoid re-running the expensive gateops bootstrap
    signal on every test method. ``setUp`` resets ``sort_order`` to a clean
    contiguous sequence so each test starts from a known state.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Creating a Society triggers the gateops bootstrap signal, which
        # seeds 9 default visitor categories (GUEST, DELIVERY, ... UNKNOWN).
        cls.society = create_society(user=UserFactory(password="password"), name="Phase 4 Society")
        cls.user = cls.society.created_by
        # A second society for cross-society scoping checks (created once).
        cls.other_society = create_society(user=UserFactory(password="password"), name="Other Society")

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        # Reset sort_order to a clean contiguous 0..N-1 sequence so each test
        # starts from a known state (previous tests may have swapped values).
        self._reset_sort_orders(self.society)
        self.categories = list(
            VisitorCategory.objects.filter(society=self.society, is_active=True).order_by("sort_order", "name")
        )

    def _reset_sort_orders(self, society):
        cats = VisitorCategory.objects.filter(society=society, is_active=True).order_by("sort_order", "name")
        for idx, cat in enumerate(cats):
            if cat.sort_order != idx:
                VisitorCategory.objects.filter(pk=cat.pk).update(sort_order=idx)

    # --- move_up / move_down swaps ---------------------------------------

    def test_move_up_swaps_sort_order(self):
        cat = self.categories[1]  # sort_order == 1
        original_top = self.categories[0]  # sort_order == 0

        moved = VisitorCategoryService.move_up(cat, actor=self.user)

        self.assertTrue(moved)
        cat.refresh_from_db()
        original_top.refresh_from_db()
        self.assertEqual(cat.sort_order, 0)
        self.assertEqual(original_top.sort_order, 1)

    def test_move_down_swaps_sort_order(self):
        cat = self.categories[0]  # sort_order == 0
        next_cat = self.categories[1]  # sort_order == 1

        moved = VisitorCategoryService.move_down(cat, actor=self.user)

        self.assertTrue(moved)
        cat.refresh_from_db()
        next_cat.refresh_from_db()
        self.assertEqual(cat.sort_order, 1)
        self.assertEqual(next_cat.sort_order, 0)

    def test_move_up_at_top_is_noop(self):
        cat = self.categories[0]  # sort_order == 0
        original_sort_order = cat.sort_order

        moved = VisitorCategoryService.move_up(cat, actor=self.user)

        self.assertFalse(moved)
        cat.refresh_from_db()
        self.assertEqual(cat.sort_order, original_sort_order)

    def test_move_down_at_bottom_is_noop(self):
        cat = self.categories[-1]  # highest sort_order
        original_sort_order = cat.sort_order

        moved = VisitorCategoryService.move_down(cat, actor=self.user)

        self.assertFalse(moved)
        cat.refresh_from_db()
        self.assertEqual(cat.sort_order, original_sort_order)

    # --- reorder compaction ----------------------------------------------

    def test_reorder_compacts_gaps(self):
        # Manually set sort_order values with gaps (0, 5, 10, 15) on the
        # first four categories. Use update() to bypass clean(). The remaining
        # five categories keep their original contiguous sort_order values
        # (4..8), so after compaction the whole set becomes 0..8.
        cats = self.categories[:4]
        for idx, cat in enumerate(cats):
            VisitorCategory.objects.filter(pk=cat.pk).update(sort_order=idx * 5)

        changed = VisitorCategoryService.reorder(self.society, actor=self.user)

        refreshed = list(
            VisitorCategory.objects.filter(society=self.society, is_active=True).order_by("sort_order", "name")
        )
        # The full active set must be compacted to a contiguous 0..N-1 range.
        self.assertEqual([c.sort_order for c in refreshed], list(range(len(refreshed))))
        # At least the four we gapped must have changed.
        self.assertGreaterEqual(changed, 4)

    # --- audit logging ---------------------------------------------------

    def test_move_creates_audit_log(self):
        cat = self.categories[1]

        VisitorCategoryService.move_up(cat, actor=self.user)

        self.assertTrue(
            GateOpsAuditLog.objects.filter(
                society=self.society,
                entity_type="VisitorCategory",
                entity_id=str(cat.pk),
                action=GateOpsAuditLog.Action.UPDATE,
            ).exists()
        )

    # --- active-only + society scoping ----------------------------------

    def test_move_only_affects_active_categories(self):
        # Deactivate the 2nd category (index 1). move_up on the 3rd (index 2)
        # should skip the deactivated one and swap with the 1st (index 0).
        middle = self.categories[1]
        VisitorCategory.objects.filter(pk=middle.pk).update(is_active=False)
        below = self.categories[2]
        top = self.categories[0]
        # Ensure below has a higher sort_order than top.
        self.assertGreater(below.sort_order, top.sort_order)

        moved = VisitorCategoryService.move_up(below, actor=self.user)

        self.assertTrue(moved)
        below.refresh_from_db()
        top.refresh_from_db()
        # below should now hold top's original sort_order, and vice versa.
        self.assertEqual(below.sort_order, 0)
        self.assertEqual(top.sort_order, 2)

    def test_move_is_society_scoped(self):
        other_cats = list(
            VisitorCategory.objects.filter(society=self.other_society, is_active=True).order_by("sort_order", "name")
        )
        other_before = [c.sort_order for c in other_cats]

        # Move a category in society 1.
        cat = self.categories[1]
        VisitorCategoryService.move_up(cat, actor=self.user)

        # Society 2's categories must be untouched.
        other_after = [
            c.sort_order for c in VisitorCategory.objects.filter(society=self.other_society, is_active=True).order_by(
                "sort_order", "name"
            )
        ]
        self.assertEqual(other_after, other_before)

    # --- can_deactivate guard (Invariant #8) -----------------------------

    def test_can_deactivate_with_open_events(self):
        cat = self.categories[0]
        gate = Gate.objects.get(society=self.society, code="MAIN")
        # Create a GateEvent in the "invited" state referencing this category.
        GateEvent.objects.create(
            society=self.society,
            gate=gate,
            visitor_category=cat,
            event_type=GateEvent.EventType.INVITATION,
            status=GateEvent.Status.INVITED,
            direction=GateEvent.Direction.INBOUND,
        )

        self.assertFalse(VisitorCategoryService.can_deactivate(cat))

    def test_can_deactivate_without_open_events(self):
        cat = self.categories[0]
        # No GateEvent references this category.
        self.assertTrue(VisitorCategoryService.can_deactivate(cat))

    def test_can_deactivate_with_closed_event_only(self):
        cat = self.categories[0]
        gate = Gate.objects.get(society=self.society, code="MAIN")
        # A terminal-state event does NOT block deactivation.
        GateEvent.objects.create(
            society=self.society,
            gate=gate,
            visitor_category=cat,
            event_type=GateEvent.EventType.EXIT,
            status=GateEvent.Status.EXITED,
            direction=GateEvent.Direction.INBOUND,
        )

        self.assertTrue(VisitorCategoryService.can_deactivate(cat))


class VisitorCategoryMoveViewTest(TestCase):
    """Frontend tests for the POST-only, society-scoped setup_move_view.

    Societies are created once per class in ``setUpTestData``; ``setUp`` only
    logs in, selects the society, and resets ``sort_order`` to a clean state.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = UserFactory(password="password")
        cls.society = create_society(user=cls.user, name="Move View Society")
        # A second society for the cross-society 404 check (created once).
        cls.other_society = create_society(user=UserFactory(password="password"), name="Other Move Society")

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self._select_society(self.society)
        # Reset sort_order to a clean contiguous sequence.
        cats = VisitorCategory.objects.filter(society=self.society, is_active=True).order_by("sort_order", "name")
        for idx, cat in enumerate(cats):
            if cat.sort_order != idx:
                VisitorCategory.objects.filter(pk=cat.pk).update(sort_order=idx)
        self.categories = list(
            VisitorCategory.objects.filter(society=self.society, is_active=True).order_by("sort_order", "name")
        )

    def _select_society(self, society):
        session = self.client.session
        session[SESSION_SELECTED_SOCIETY_ID] = society.id
        session.save()

    def _move_url(self, cat, direction):
        return reverse(
            "gateops:setup-move",
            kwargs={"slug": "visitor-categories", "pk": cat.pk, "direction": direction},
        )

    def test_setup_move_up_view_post(self):
        cat = self.categories[1]
        top = self.categories[0]

        response = self.client.post(self._move_url(cat, "up"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("gateops:setup-section", kwargs={"slug": "visitor-categories"}))
        cat.refresh_from_db()
        top.refresh_from_db()
        self.assertEqual(cat.sort_order, 0)
        self.assertEqual(top.sort_order, 1)

    def test_setup_move_down_view_post(self):
        cat = self.categories[0]
        next_cat = self.categories[1]

        response = self.client.post(self._move_url(cat, "down"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("gateops:setup-section", kwargs={"slug": "visitor-categories"}))
        cat.refresh_from_db()
        next_cat.refresh_from_db()
        self.assertEqual(cat.sort_order, 1)
        self.assertEqual(next_cat.sort_order, 0)

    def test_setup_move_view_get_rejected(self):
        cat = self.categories[1]
        original_sort_order = cat.sort_order

        response = self.client.get(self._move_url(cat, "up"))

        self.assertEqual(response.status_code, 405)
        cat.refresh_from_db()
        self.assertEqual(cat.sort_order, original_sort_order)

    def test_setup_move_cross_society_404(self):
        # The category belongs to other_society; society A is selected.
        other_cat = VisitorCategory.objects.filter(society=self.other_society, is_active=True).first()
        self._select_society(self.society)

        response = self.client.post(self._move_url(other_cat, "up"))

        self.assertEqual(response.status_code, 404)
