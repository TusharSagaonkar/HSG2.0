"""Phase 6 integration tests: GateVehicle <-> GateEventLifecycleService.

These tests cover the integration gaps fixed in Phase 6:

1. ``create_invitation()`` and ``record_arrival()`` accept an optional
   ``gate_vehicle`` kwarg (instance or ID) that is validated for society scope
   via the private ``_resolve_gate_vehicle()`` helper.
2. ``_build_rule_context()`` now exposes 7 new gate-vehicle keys so rules can
   react to watchlisted / repeat visitor vehicles.

The tests follow the conventions established in ``test_lifecycle.py`` and
``test_vehicle_service.py``: the society is created once per class (triggering
the gateops bootstrap signal that seeds categories, gates, etc.) and per-test
mutable records are created in ``setUp``.
"""
from django.utils import timezone

from core.test_base import SocietyTestCase
from core.test_factories import SocietyFactory
from gateops.models import (
    Gate,
    GateEvent,
    GateVehicle,
    Person,
    VehicleCategory,
    VisitorCategory,
)
from gateops.services.gate_event_lifecycle import GateEventLifecycleService


class GateVehicleLifecycleIntegrationTest(SocietyTestCase):
    """Integration tests linking ``GateVehicle`` to the event lifecycle service.

    The society and seeded master data are created once per class via
    ``setUpTestData`` to avoid re-running the expensive gateops bootstrap
    signal on every test method.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Fetch seeded master data (bootstrap signal seeds these).
        cls.visitor_cat = VisitorCategory.objects.get(
            society=cls.society, code="DELIVERY"
        )
        cls.gate = Gate.objects.get(society=cls.society, code="MAIN")
        cls.vehicle_cat = VehicleCategory.objects.get(
            society=cls.society, code="VISITOR"
        )
        # Second society for cross-society tests (triggers bootstrap once).
        cls.other_society = SocietyFactory(name="Lifecycle Integration Beta")
        cls.other_vehicle_cat = VehicleCategory.objects.get(
            society=cls.other_society, code="VISITOR"
        )

    def setUp(self):
        super().setUp()
        self.person = Person.objects.create(
            society=self.society, name="Integration Driver", phone="+919999999999"
        )
        self.gate_vehicle = GateVehicle.objects.create(
            society=self.society,
            person=self.person,
            vehicle_number="INT123",
            vehicle_category=self.vehicle_cat,
            last_seen_at=timezone.now(),
        )
        # A watchlisted + repeat vehicle for rule-context assertions.
        self.flagged_vehicle = GateVehicle.objects.create(
            society=self.society,
            person=self.person,
            vehicle_number="FLAG001",
            vehicle_category=self.vehicle_cat,
            is_watchlisted=True,
            watchlist_reason="Suspicious activity",
            is_repeat=True,
            last_seen_at=timezone.now(),
        )
        # A vehicle belonging to the *other* society.
        self.other_person = Person.objects.create(
            society=self.other_society, name="Other Driver", phone="+917777777777"
        )
        self.other_gate_vehicle = GateVehicle.objects.create(
            society=self.other_society,
            person=self.other_person,
            vehicle_number="OTHER001",
            vehicle_category=self.other_vehicle_cat,
            last_seen_at=timezone.now(),
        )

    # --- helpers ---------------------------------------------------------

    def _make_invitation(self, **kwargs):
        """Create an invitation using the lifecycle service."""
        return GateEventLifecycleService.create_invitation(
            society=self.society,
            visitor_category=self.visitor_cat,
            person=self.person,
            expected_arrival_at=timezone.now(),
            created_by=self.user,
            gate=self.gate,
            **kwargs,
        )

    # ------------------------------------------------------------------ #
    # create_invitation() with gate_vehicle
    # ------------------------------------------------------------------ #

    def test_create_invitation_with_gate_vehicle_instance_sets_fk(self):
        """A valid ``GateVehicle`` instance is linked to the new event."""
        event = self._make_invitation(gate_vehicle=self.gate_vehicle)

        event.refresh_from_db()
        self.assertEqual(event.gate_vehicle, self.gate_vehicle)
        self.assertEqual(event.gate_vehicle_id, self.gate_vehicle.pk)

    def test_create_invitation_with_gate_vehicle_id_resolves(self):
        """A ``gate_vehicle`` passed as a primary key (int) is resolved."""
        event = self._make_invitation(gate_vehicle=self.gate_vehicle.pk)

        event.refresh_from_db()
        self.assertEqual(event.gate_vehicle, self.gate_vehicle)
        self.assertEqual(event.gate_vehicle_id, self.gate_vehicle.pk)

    def test_create_invitation_with_cross_society_gate_vehicle_raises(self):
        """A ``gate_vehicle`` from a different society raises ``ValueError``."""
        with self.assertRaises(ValueError):
            self._make_invitation(gate_vehicle=self.other_gate_vehicle)

    def test_create_invitation_with_cross_society_gate_vehicle_id_raises(self):
        """A cross-society ``gate_vehicle`` ID also raises ``ValueError``."""
        with self.assertRaises(ValueError):
            self._make_invitation(gate_vehicle=self.other_gate_vehicle.pk)

    def test_create_invitation_with_nonexistent_gate_vehicle_id_raises(self):
        """A non-existent ``gate_vehicle`` ID raises ``ValueError``."""
        # Use an ID that is extremely unlikely to exist.
        nonexistent_id = 9_999_999
        with self.assertRaises(ValueError):
            self._make_invitation(gate_vehicle=nonexistent_id)

    def test_create_invitation_without_gate_vehicle_leaves_fk_null(self):
        """Omitting ``gate_vehicle`` (default ``None``) leaves the FK null.

        This is a regression guard: existing callers that do not pass
        ``gate_vehicle`` must be unaffected.
        """
        event = self._make_invitation()

        event.refresh_from_db()
        self.assertIsNone(event.gate_vehicle)
        self.assertIsNone(event.gate_vehicle_id)

    # ------------------------------------------------------------------ #
    # record_arrival() with gate_vehicle
    # ------------------------------------------------------------------ #

    def test_record_arrival_with_gate_vehicle_sets_fk(self):
        """A valid ``gate_vehicle`` is linked to the event at arrival time."""
        event = self._make_invitation()

        GateEventLifecycleService.record_arrival(
            event, gate=self.gate, gate_vehicle=self.gate_vehicle
        )
        event.refresh_from_db()

        self.assertEqual(event.gate_vehicle, self.gate_vehicle)
        self.assertEqual(event.status, GateEvent.Status.ARRIVED)

    def test_record_arrival_without_gate_vehicle_preserves_existing_fk(self):
        """Passing ``gate_vehicle=None`` does NOT clobber an existing link.

        A vehicle linked at invitation time must survive a subsequent arrival
        that omits the ``gate_vehicle`` kwarg.
        """
        event = self._make_invitation(gate_vehicle=self.gate_vehicle)
        self.assertEqual(event.gate_vehicle, self.gate_vehicle)

        GateEventLifecycleService.record_arrival(
            event, gate=self.gate, gate_vehicle=None
        )
        event.refresh_from_db()

        self.assertEqual(event.gate_vehicle, self.gate_vehicle)

    def test_record_arrival_with_cross_society_gate_vehicle_raises(self):
        """A cross-society ``gate_vehicle`` at arrival raises ``ValueError``."""
        event = self._make_invitation()

        with self.assertRaises(ValueError):
            GateEventLifecycleService.record_arrival(
                event, gate=self.gate, gate_vehicle=self.other_gate_vehicle
            )

    # ------------------------------------------------------------------ #
    # _build_rule_context() with gate_vehicle
    # ------------------------------------------------------------------ #

    def test_rule_context_contains_all_gate_vehicle_keys_when_set(self):
        """All 7 new keys are present with correct values when a vehicle is linked."""
        event = self._make_invitation(gate_vehicle=self.flagged_vehicle)

        context = GateEventLifecycleService._build_rule_context(event)

        # The 7 Phase-6 keys.
        self.assertEqual(context["gate_vehicle"], self.flagged_vehicle)
        self.assertEqual(context["gate_vehicle_id"], self.flagged_vehicle.pk)
        self.assertEqual(context["gate_vehicle_number"], "FLAG001")
        self.assertEqual(
            context["gate_vehicle_category"], self.flagged_vehicle.vehicle_category_id
        )
        self.assertEqual(
            context["gate_vehicle_category_name"],
            self.flagged_vehicle.vehicle_category.name,
        )
        self.assertTrue(context["gate_vehicle_is_watchlisted"])
        self.assertTrue(context["gate_vehicle_is_repeat"])

    def test_rule_context_defaults_when_no_gate_vehicle(self):
        """Gate-vehicle keys safely default to None/False when no vehicle is linked."""
        event = self._make_invitation()

        context = GateEventLifecycleService._build_rule_context(event)

        self.assertIsNone(context["gate_vehicle"])
        self.assertIsNone(context["gate_vehicle_id"])
        self.assertIsNone(context["gate_vehicle_number"])
        self.assertFalse(context["gate_vehicle_is_watchlisted"])
        self.assertFalse(context["gate_vehicle_is_repeat"])

    def test_rule_context_preserves_existing_keys(self):
        """The pre-Phase-6 context keys are still present and unchanged.

        This is a regression guard: adding the 7 new keys must not displace or
        alter the existing keys consumed by the rule engine.
        """
        event = self._make_invitation(gate_vehicle=self.gate_vehicle)

        context = GateEventLifecycleService._build_rule_context(event)

        # A representative subset of the pre-Phase-6 keys.
        self.assertEqual(context["society"], self.society)
        self.assertEqual(context["society_id"], self.society.pk)
        self.assertEqual(context["direction"], event.direction)
        self.assertEqual(context["gate"], self.gate.pk)
        self.assertEqual(context["gate_id"], self.gate.pk)
        self.assertEqual(context["visitor_category"], self.visitor_cat.code)
        self.assertEqual(context["visitor_category_id"], self.visitor_cat.pk)
        self.assertEqual(context["gate_event_id"], event.pk)
        self.assertIn("is_blacklisted", context)
        self.assertIn("is_vip", context)
        self.assertIn("person", context)
