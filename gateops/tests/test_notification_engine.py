"""Test suite for gateops Phase 10 — Smart Notification Engine.

Test conventions:
- SocietyTestCase base class provides cls.society and cls.user (created once
  per class via SocietyFactory with django_get_or_create, avoiding repeated
  bootstrap signal cascades).
- Per-test mutable records (events, bundles, preferences) are created
  per-test or via helper methods.
- Seeded Gate and VisitorCategory records are fetched in setUpTestData().
- ``queue_email`` is mocked via ``@patch("gateops.services.notification_engine.queue_email")``
  so no real email infrastructure is exercised.

Covers:
- NotificationBundle model (clean, defaults, soft-delete, choices, M2M, indexes, ordering)
- NotificationEngineService (host resolution, preference resolution, dispatch,
  bundling, duplicate suppression, rule-action dispatch, query methods)
- GateEventLifecycleService integration (_notify hooks, rule-action dispatch,
  failure resilience)
"""
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.http import Http404
from django.test import TestCase
from django.utils import timezone

from core.test_base import SocietyTestCase
from core.test_factories import SocietyFactory, UserFactory
from gateops.models import (
    Gate,
    GateEvent,
    GateOpsAuditLog,
    NotificationBundle,
    NotificationPreference,
    Person,
    RuleAction,
    SecurityGuard,
    VisitorCategory,
)
from gateops.services.gate_event_lifecycle import GateEventLifecycleService
from gateops.services.notification_engine import NotificationEngineService
from housing.models import Member, Structure, Unit, UnitOccupancy, UnitOwnership


# ---------------------------------------------------------------------------
# Model tests (1-18)
# ---------------------------------------------------------------------------
class NotificationBundleModelTest(SocietyTestCase):
    """Model-level tests for NotificationBundle (clean, defaults, soft-delete)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other_society = SocietyFactory(name="Notif Society Beta")
        cls.visitor_cat = VisitorCategory.objects.get(
            society=cls.society, code="GUEST"
        )
        cls.other_cat = VisitorCategory.objects.get(
            society=cls.other_society, code="GUEST"
        )
        cls.gate = Gate.objects.get(society=cls.society, code="MAIN")

    # --- helpers ---------------------------------------------------------

    def _make_person(self, **overrides):
        defaults = {
            "society": self.society,
            "name": "Test Visitor",
            "phone": uuid.uuid4().hex[:10],
        }
        defaults.update(overrides)
        return Person.objects.create(**defaults)

    def _make_bundle(self, **overrides):
        defaults = {
            "society": self.society,
            "visitor_category": self.visitor_cat,
            "trigger": NotificationPreference.Trigger.ARRIVAL,
            "channel": NotificationPreference.Channel.EMAIL,
            "status": NotificationBundle.Status.PENDING,
        }
        defaults.update(overrides)
        return NotificationBundle.objects.create(**defaults)

    # --- creation & defaults (1-5) ---------------------------------------

    def test_creation_with_all_required_fields(self):
        bundle = self._make_bundle()
        self.assertEqual(bundle.society, self.society)
        self.assertEqual(bundle.visitor_category, self.visitor_cat)
        self.assertEqual(bundle.trigger, NotificationPreference.Trigger.ARRIVAL)
        self.assertEqual(bundle.channel, NotificationPreference.Channel.EMAIL)
        self.assertEqual(bundle.status, NotificationBundle.Status.PENDING)
        self.assertTrue(bundle.is_active)
        self.assertIsNone(bundle.deleted_at)
        self.assertIsNotNone(bundle.created_at)
        self.assertIsNotNone(bundle.updated_at)

    def test_default_status_is_pending(self):
        bundle = NotificationBundle(
            society=self.society,
            visitor_category=self.visitor_cat,
            trigger=NotificationPreference.Trigger.ARRIVAL,
            channel=NotificationPreference.Channel.EMAIL,
        )
        bundle.save()
        self.assertEqual(bundle.status, NotificationBundle.Status.PENDING)

    def test_default_bundle_window_minutes_is_zero(self):
        bundle = self._make_bundle()
        self.assertEqual(bundle.bundle_window_minutes, 0)

    def test_default_is_active_is_true(self):
        bundle = self._make_bundle()
        self.assertTrue(bundle.is_active)

    def test_default_dispatched_at_is_none(self):
        bundle = self._make_bundle()
        self.assertIsNone(bundle.dispatched_at)

    # --- __str__ (6) -----------------------------------------------------

    def test_str_representation(self):
        bundle = self._make_bundle(status=NotificationBundle.Status.SENT)
        expected = f"Bundle {bundle.pk} — {self.visitor_cat.code} — Sent"
        self.assertEqual(str(bundle), expected)

    # --- cross-society guard (7-8) ---------------------------------------

    def test_clean_rejects_cross_society_visitor_category(self):
        bundle = NotificationBundle(
            society=self.society,
            visitor_category=self.other_cat,
            trigger=NotificationPreference.Trigger.ARRIVAL,
            channel=NotificationPreference.Channel.EMAIL,
        )
        with self.assertRaises(ValidationError):
            bundle.clean()

    def test_clean_accepts_same_society_visitor_category(self):
        bundle = NotificationBundle(
            society=self.society,
            visitor_category=self.visitor_cat,
            trigger=NotificationPreference.Trigger.ARRIVAL,
            channel=NotificationPreference.Channel.EMAIL,
        )
        # Should not raise.
        bundle.clean()

    # --- soft-delete (9-10) ----------------------------------------------

    def test_soft_delete_sets_is_active_false_and_deleted_at(self):
        bundle = self._make_bundle()
        bundle.is_active = False
        bundle.deleted_at = timezone.now()
        bundle.save()
        bundle.refresh_from_db()
        self.assertFalse(bundle.is_active)
        self.assertIsNotNone(bundle.deleted_at)

    def test_soft_deleted_bundle_remains_in_db(self):
        bundle = self._make_bundle()
        bundle.is_active = False
        bundle.deleted_at = timezone.now()
        bundle.save()
        self.assertTrue(
            NotificationBundle.objects.filter(pk=bundle.pk).exists()
        )

    # --- status choices (11-13) ------------------------------------------

    def test_status_choices_contain_pending_sent_skipped(self):
        choices = {c[0] for c in NotificationBundle.Status.choices}
        self.assertIn("pending", choices)
        self.assertIn("sent", choices)
        self.assertIn("skipped", choices)

    def test_channel_uses_notification_preference_channel_choices(self):
        # The channel field reuses NotificationPreference.Channel.choices.
        bundle = self._make_bundle(channel=NotificationPreference.Channel.SMS)
        self.assertEqual(bundle.channel, NotificationPreference.Channel.SMS)

    def test_trigger_uses_notification_preference_trigger_choices(self):
        bundle = self._make_bundle(trigger=NotificationPreference.Trigger.ENTRY)
        self.assertEqual(bundle.trigger, NotificationPreference.Trigger.ENTRY)

    # --- M2M (14) --------------------------------------------------------

    def test_gate_events_m2m_can_link_multiple_events(self):
        bundle = self._make_bundle()
        person = self._make_person()
        event1 = GateEvent.objects.create(
            society=self.society,
            gate=self.gate,
            person=person,
            visitor_category=self.visitor_cat,
            event_type=GateEvent.EventType.ARRIVAL,
            status=GateEvent.Status.ARRIVED,
        )
        event2 = GateEvent.objects.create(
            society=self.society,
            gate=self.gate,
            person=person,
            visitor_category=self.visitor_cat,
            event_type=GateEvent.EventType.ARRIVAL,
            status=GateEvent.Status.ARRIVED,
        )
        bundle.gate_events.add(event1, event2)
        self.assertEqual(bundle.gate_events.count(), 2)
        self.assertIn(event1, bundle.gate_events.all())
        self.assertIn(event2, bundle.gate_events.all())

    # --- indexes (15-17) -------------------------------------------------

    def test_has_society_is_active_index(self):
        index_names = [
            idx.name for idx in NotificationBundle._meta.indexes
        ]
        self.assertIn("notifbundle_soc_active_idx", index_names)

    def test_has_society_host_unit_is_active_index(self):
        index_names = [
            idx.name for idx in NotificationBundle._meta.indexes
        ]
        self.assertIn("nb_soc_unit_active_idx", index_names)

    def test_has_society_status_is_active_index(self):
        index_names = [
            idx.name for idx in NotificationBundle._meta.indexes
        ]
        self.assertIn("notifbundle_soc_status_idx", index_names)

    # --- ordering (18) ---------------------------------------------------

    def test_ordering_is_by_created_at_descending(self):
        self.assertEqual(
            NotificationBundle._meta.ordering, ["-created_at"]
        )


# ---------------------------------------------------------------------------
# Service tests (19-58)
# ---------------------------------------------------------------------------
class NotificationEngineServiceTest(SocietyTestCase):
    """Service-level tests for NotificationEngineService.

    Covers host resolution, preference resolution, dispatch_for_event,
    bundling, duplicate suppression, rule-action dispatch, and query methods.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other_society = SocietyFactory(name="Notif Engine Beta")
        cls.visitor_cat = VisitorCategory.objects.get(
            society=cls.society, code="GUEST"
        )
        cls.delivery_cat = VisitorCategory.objects.get(
            society=cls.society, code="DELIVERY"
        )
        cls.other_cat = VisitorCategory.objects.get(
            society=cls.other_society, code="GUEST"
        )
        cls.gate = Gate.objects.get(society=cls.society, code="MAIN")

        # Create a structure + unit for host resolution tests.
        cls.structure = Structure.objects.create(
            society=cls.society,
            structure_type=Structure.StructureType.BUILDING,
            name="Building A",
        )
        cls.unit = Unit.objects.create(
            structure=cls.structure,
            unit_type=Unit.UnitType.FLAT,
            identifier="101",
        )

        # Create a host user + member + occupancy for resolve_host tests.
        cls.host_user = UserFactory(email="host@example.com", name="Host User")
        cls.host_member = Member.objects.create(
            society=cls.society,
            unit=cls.unit,
            user=cls.host_user,
            full_name="Host User",
            email="host@example.com",
            phone="9876543210",
            role=Member.MemberRole.OWNER,
            status=Member.MemberStatus.ACTIVE,
        )
        cls.occupancy = UnitOccupancy.objects.create(
            unit=cls.unit,
            occupant=cls.host_user,
            occupancy_type=UnitOccupancy.OccupancyType.OWNER,
            start_date=timezone.now().date(),
        )

    def setUp(self):
        super().setUp()
        self.guard = SecurityGuard.objects.create(
            society=self.society,
            name="Guard One",
            phone="1234567890",
            badge_number="G001",
        )

    # --- helpers ---------------------------------------------------------

    def _make_person(self, **overrides):
        defaults = {
            "society": self.society,
            "name": "Test Visitor",
            "phone": uuid.uuid4().hex[:10],
        }
        defaults.update(overrides)
        return Person.objects.create(**defaults)

    def _make_event(self, **overrides):
        """Create a GateEvent with host_unit set and status=ARRIVED."""
        defaults = {
            "society": self.society,
            "gate": self.gate,
            "person": self._make_person(),
            "visitor_category": self.visitor_cat,
            "event_type": GateEvent.EventType.ARRIVAL,
            "status": GateEvent.Status.ARRIVED,
            "host_unit": self.unit,
        }
        defaults.update(overrides)
        return GateEvent.objects.create(**defaults)

    def _make_preference(self, **overrides):
        """Create or update a NotificationPreference for the visitor_cat."""
        # Delete existing seeded preferences for this cat to avoid unique
        # constraint collisions on (society, visitor_category, channel).
        NotificationPreference.objects.filter(
            society=self.society, visitor_category=self.visitor_cat
        ).delete()
        defaults = {
            "society": self.society,
            "visitor_category": self.visitor_cat,
            "channel": NotificationPreference.Channel.EMAIL,
            "trigger": NotificationPreference.Trigger.ARRIVAL,
            "is_silent": False,
            "bundle_window_minutes": 0,
        }
        defaults.update(overrides)
        return NotificationPreference.objects.create(**defaults)

    def _make_bundle(self, **overrides):
        defaults = {
            "society": self.society,
            "visitor_category": self.visitor_cat,
            "trigger": NotificationPreference.Trigger.ARRIVAL,
            "channel": NotificationPreference.Channel.EMAIL,
            "status": NotificationBundle.Status.PENDING,
        }
        defaults.update(overrides)
        return NotificationBundle.objects.create(**defaults)

    # --- Host Resolution (19-25) -----------------------------------------

    def test_resolve_host_returns_none_when_no_host_unit(self):
        event = self._make_event(host_unit=None)
        result = NotificationEngineService.resolve_host(event=event)
        self.assertIsNone(result)

    def test_resolve_host_via_unit_occupancy(self):
        event = self._make_event()
        result = NotificationEngineService.resolve_host(event=event)
        self.assertIsNotNone(result)
        self.assertEqual(result["user"], self.host_user)
        self.assertEqual(result["email"], "host@example.com")
        self.assertEqual(result["unit"], self.unit)

    def test_resolve_host_falls_back_to_ownership_when_no_occupancy(self):
        # Remove the occupancy so ownership is used.
        UnitOccupancy.objects.filter(unit=self.unit).delete()
        UnitOwnership.objects.create(
            unit=self.unit,
            owner=self.host_user,
            role=UnitOwnership.OwnershipRole.PRIMARY,
            start_date=timezone.now().date(),
        )
        event = self._make_event()
        result = NotificationEngineService.resolve_host(event=event)
        self.assertIsNotNone(result)
        self.assertEqual(result["user"], self.host_user)

    def test_resolve_host_falls_back_to_member_when_no_occupancy_or_ownership(self):
        UnitOccupancy.objects.filter(unit=self.unit).delete()
        event = self._make_event()
        result = NotificationEngineService.resolve_host(event=event)
        self.assertIsNotNone(result)
        # Member fallback returns the member's user.
        self.assertEqual(result["user"], self.host_user)
        self.assertEqual(result["name"], "Host User")

    def test_resolve_host_skips_vacant_occupancy(self):
        UnitOccupancy.objects.filter(unit=self.unit).delete()
        UnitOccupancy.objects.create(
            unit=self.unit,
            occupancy_type=UnitOccupancy.OccupancyType.VACANT,
            start_date=timezone.now().date(),
        )
        # With only a VACANT occupancy, should fall through to ownership/member.
        event = self._make_event()
        result = NotificationEngineService.resolve_host(event=event)
        # Member exists, so result should not be None.
        self.assertIsNotNone(result)

    def test_resolve_host_returns_none_when_no_resolution_possible(self):
        # Create a unit with no occupancy, ownership, or member.
        empty_unit = Unit.objects.create(
            structure=self.structure,
            unit_type=Unit.UnitType.FLAT,
            identifier="999",
        )
        event = self._make_event(host_unit=empty_unit)
        result = NotificationEngineService.resolve_host(event=event)
        self.assertIsNone(result)

    def test_resolve_host_returns_dict_with_required_keys(self):
        event = self._make_event()
        result = NotificationEngineService.resolve_host(event=event)
        self.assertIn("user", result)
        self.assertIn("email", result)
        self.assertIn("phone", result)
        self.assertIn("name", result)
        self.assertIn("unit", result)

    # --- Preference Resolution (26-28) -----------------------------------

    def test_get_preferences_returns_active_for_society_and_category(self):
        self._make_preference()
        qs = NotificationEngineService.get_preferences(
            society=self.society, visitor_category=self.visitor_cat
        )
        self.assertTrue(qs.exists())
        for pref in qs:
            self.assertTrue(pref.is_active)
            self.assertEqual(pref.society, self.society)
            self.assertEqual(pref.visitor_category, self.visitor_cat)

    def test_get_preferences_excludes_inactive(self):
        pref = self._make_preference()
        pref.is_active = False
        pref.save()
        qs = NotificationEngineService.get_preferences(
            society=self.society, visitor_category=self.visitor_cat
        )
        self.assertFalse(qs.exists())

    def test_get_preference_for_trigger_returns_matching_preference(self):
        self._make_preference(trigger=NotificationPreference.Trigger.ENTRY)
        result = NotificationEngineService.get_preference_for_trigger(
            society=self.society,
            visitor_category=self.visitor_cat,
            trigger=NotificationPreference.Trigger.ENTRY,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.trigger, NotificationPreference.Trigger.ENTRY)

    def test_get_preference_for_trigger_returns_none_when_no_match(self):
        NotificationPreference.objects.filter(
            society=self.society, visitor_category=self.visitor_cat
        ).delete()
        result = NotificationEngineService.get_preference_for_trigger(
            society=self.society,
            visitor_category=self.visitor_cat,
            trigger=NotificationPreference.Trigger.EXIT,
        )
        self.assertIsNone(result)

    # --- dispatch_for_event (29-38) --------------------------------------

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispatch_for_event_returns_none_when_no_preference(self, mock_queue):
        NotificationPreference.objects.filter(
            society=self.society, visitor_category=self.visitor_cat
        ).delete()
        event = self._make_event()
        result = NotificationEngineService.dispatch_for_event(
            event=event, trigger=NotificationPreference.Trigger.ARRIVAL
        )
        self.assertIsNone(result)
        mock_queue.assert_not_called()

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispatch_for_event_returns_none_when_channel_is_none(self, mock_queue):
        self._make_preference(channel=NotificationPreference.Channel.NONE)
        event = self._make_event()
        result = NotificationEngineService.dispatch_for_event(
            event=event, trigger=NotificationPreference.Trigger.ARRIVAL
        )
        self.assertIsNone(result)
        mock_queue.assert_not_called()

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispatch_for_event_returns_none_when_trigger_is_never(self, mock_queue):
        self._make_preference(trigger=NotificationPreference.Trigger.NEVER)
        event = self._make_event()
        result = NotificationEngineService.dispatch_for_event(
            event=event, trigger=NotificationPreference.Trigger.ARRIVAL
        )
        self.assertIsNone(result)
        mock_queue.assert_not_called()

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispatch_for_event_creates_skipped_bundle_when_silent(self, mock_queue):
        self._make_preference(is_silent=True)
        event = self._make_event()
        result = NotificationEngineService.dispatch_for_event(
            event=event, trigger=NotificationPreference.Trigger.ARRIVAL
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.status, NotificationBundle.Status.SKIPPED)
        mock_queue.assert_not_called()

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispatch_for_event_creates_skipped_bundle_when_no_host(self, mock_queue):
        self._make_preference()
        # Event with no host_unit and no resolution possible.
        empty_unit = Unit.objects.create(
            structure=self.structure,
            unit_type=Unit.UnitType.FLAT,
            identifier="998",
        )
        event = self._make_event(host_unit=empty_unit)
        result = NotificationEngineService.dispatch_for_event(
            event=event, trigger=NotificationPreference.Trigger.ARRIVAL
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.status, NotificationBundle.Status.SKIPPED)
        mock_queue.assert_not_called()

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispatch_for_event_dispatches_email_immediately_when_no_bundling(
        self, mock_queue
    ):
        mock_queue.return_value = None
        self._make_preference(
            channel=NotificationPreference.Channel.EMAIL,
            bundle_window_minutes=0,
        )
        event = self._make_event()
        result = NotificationEngineService.dispatch_for_event(
            event=event, trigger=NotificationPreference.Trigger.ARRIVAL
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.status, NotificationBundle.Status.SENT)
        self.assertIsNotNone(result.dispatched_at)
        mock_queue.assert_called_once()

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispatch_for_event_leaves_pending_when_bundling_enabled(self, mock_queue):
        self._make_preference(
            channel=NotificationPreference.Channel.EMAIL,
            bundle_window_minutes=30,
        )
        event = self._make_event()
        result = NotificationEngineService.dispatch_for_event(
            event=event, trigger=NotificationPreference.Trigger.ARRIVAL
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.status, NotificationBundle.Status.PENDING)
        self.assertIsNone(result.dispatched_at)
        # queue_email should NOT be called during dispatch — deferred to flush.
        mock_queue.assert_not_called()

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispatch_for_event_creates_audit_log(self, mock_queue):
        mock_queue.return_value = None
        self._make_preference()
        event = self._make_event()
        NotificationEngineService.dispatch_for_event(
            event=event, trigger=NotificationPreference.Trigger.ARRIVAL
        )
        audit_exists = GateOpsAuditLog.objects.filter(
            society=self.society,
            entity_type="NotificationBundle",
        ).exists()
        self.assertTrue(audit_exists)

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispatch_for_event_never_raises_on_exception(self, mock_queue):
        self._make_preference()
        event = self._make_event()
        # Force queue_email to raise — dispatch_for_event must swallow it.
        mock_queue.side_effect = RuntimeError("SMTP down")
        result = NotificationEngineService.dispatch_for_event(
            event=event, trigger=NotificationPreference.Trigger.ARRIVAL
        )
        # Should return None, not raise.
        self.assertIsNone(result)

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispatch_for_event_non_email_channel_leaves_pending(self, mock_queue):
        self._make_preference(channel=NotificationPreference.Channel.PUSH)
        event = self._make_event()
        result = NotificationEngineService.dispatch_for_event(
            event=event, trigger=NotificationPreference.Trigger.ARRIVAL
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.status, NotificationBundle.Status.PENDING)
        mock_queue.assert_not_called()

    # --- Bundling (39-43) ------------------------------------------------

    @patch("gateops.services.notification_engine.queue_email")
    def test_find_or_create_bundle_creates_new_when_no_existing(self, mock_queue):
        pref = self._make_preference(bundle_window_minutes=30)
        event = self._make_event()
        host = NotificationEngineService.resolve_host(event=event)
        bundle = NotificationEngineService._find_or_create_bundle(
            event=event, preference=pref, trigger=pref.trigger, host=host
        )
        self.assertEqual(bundle.status, NotificationBundle.Status.PENDING)
        self.assertEqual(bundle.gate_events.count(), 1)
        self.assertIn(event, bundle.gate_events.all())

    @patch("gateops.services.notification_engine.queue_email")
    def test_find_or_create_bundle_reuses_existing_within_window(self, mock_queue):
        pref = self._make_preference(bundle_window_minutes=30)
        event1 = self._make_event()
        host = NotificationEngineService.resolve_host(event=event1)
        bundle1 = NotificationEngineService._find_or_create_bundle(
            event=event1, preference=pref, trigger=pref.trigger, host=host
        )
        # Second event for the same unit + trigger + channel should reuse.
        event2 = self._make_event()
        bundle2 = NotificationEngineService._find_or_create_bundle(
            event=event2, preference=pref, trigger=pref.trigger, host=host
        )
        self.assertEqual(bundle1.pk, bundle2.pk)
        self.assertEqual(bundle2.gate_events.count(), 2)

    @patch("gateops.services.notification_engine.queue_email")
    def test_find_or_create_bundle_always_new_when_window_zero(self, mock_queue):
        pref = self._make_preference(bundle_window_minutes=0)
        event1 = self._make_event()
        host = NotificationEngineService.resolve_host(event=event1)
        bundle1 = NotificationEngineService._find_or_create_bundle(
            event=event1, preference=pref, trigger=pref.trigger, host=host
        )
        event2 = self._make_event()
        bundle2 = NotificationEngineService._find_or_create_bundle(
            event=event2, preference=pref, trigger=pref.trigger, host=host
        )
        self.assertNotEqual(bundle1.pk, bundle2.pk)

    @patch("gateops.services.notification_engine.queue_email")
    def test_flush_bundle_dispatches_pending_email_bundle(self, mock_queue):
        mock_queue.return_value = None
        pref = self._make_preference(bundle_window_minutes=30)
        event = self._make_event()
        host = NotificationEngineService.resolve_host(event=event)
        bundle = NotificationEngineService._find_or_create_bundle(
            event=event, preference=pref, trigger=pref.trigger, host=host
        )
        self.assertEqual(bundle.status, NotificationBundle.Status.PENDING)
        flushed = NotificationEngineService.flush_bundle(bundle=bundle)
        self.assertEqual(flushed.status, NotificationBundle.Status.SENT)
        self.assertIsNotNone(flushed.dispatched_at)
        mock_queue.assert_called_once()

    @patch("gateops.services.notification_engine.queue_email")
    def test_flush_bundle_skips_non_pending(self, mock_queue):
        bundle = self._make_bundle(status=NotificationBundle.Status.SENT)
        result = NotificationEngineService.flush_bundle(bundle=bundle)
        self.assertEqual(result.status, NotificationBundle.Status.SENT)
        mock_queue.assert_not_called()

    @patch("gateops.services.notification_engine.queue_email")
    def test_flush_bundle_marks_skipped_when_no_events(self, mock_queue):
        bundle = self._make_bundle(status=NotificationBundle.Status.PENDING)
        # No gate_events linked.
        result = NotificationEngineService.flush_bundle(bundle=bundle)
        self.assertEqual(result.status, NotificationBundle.Status.SKIPPED)
        mock_queue.assert_not_called()

    @patch("gateops.services.notification_engine.queue_email")
    def test_flush_pending_bundles_returns_count(self, mock_queue):
        mock_queue.return_value = None
        # Use window=0 so flush_pending_bundles considers the bundle
        # immediately ready (window == 0 → dispatch now).
        pref = self._make_preference(bundle_window_minutes=0)
        event = self._make_event()
        host = NotificationEngineService.resolve_host(event=event)
        NotificationEngineService._find_or_create_bundle(
            event=event, preference=pref, trigger=pref.trigger, host=host
        )
        count = NotificationEngineService.flush_pending_bundles(
            society=self.society
        )
        self.assertEqual(count, 1)

    @patch("gateops.services.notification_engine.queue_email")
    def test_flush_pending_bundles_society_scoped(self, mock_queue):
        # Create a PENDING bundle in the other society.
        other_bundle = NotificationBundle.objects.create(
            society=self.other_society,
            visitor_category=self.other_cat,
            trigger=NotificationPreference.Trigger.ARRIVAL,
            channel=NotificationPreference.Channel.PUSH,
            bundle_window_minutes=0,
            status=NotificationBundle.Status.PENDING,
        )
        count = NotificationEngineService.flush_pending_bundles(
            society=self.society
        )
        self.assertEqual(count, 0)
        # Other society's bundle should remain PENDING.
        other_bundle.refresh_from_db()
        self.assertEqual(other_bundle.status, NotificationBundle.Status.PENDING)

    # --- Duplicate Suppression (44-46) -----------------------------------

    @patch("gateops.services.notification_engine.queue_email")
    def test_is_duplicate_returns_false_when_no_person(self, mock_queue):
        event = self._make_event(person=None)
        result = NotificationEngineService._is_duplicate_notification(
            event=event, trigger=NotificationPreference.Trigger.ARRIVAL
        )
        self.assertFalse(result)

    @patch("gateops.services.notification_engine.queue_email")
    def test_is_duplicate_returns_false_when_no_prior_bundle(self, mock_queue):
        person = self._make_person()
        event = self._make_event(person=person)
        result = NotificationEngineService._is_duplicate_notification(
            event=event, trigger=NotificationPreference.Trigger.ARRIVAL
        )
        self.assertFalse(result)

    @patch("gateops.services.notification_engine.queue_email")
    def test_is_duplicate_returns_true_when_prior_bundle_exists(self, mock_queue):
        person = self._make_person()
        event = self._make_event(person=person)
        # Create a prior SENT bundle linked to the same person via gate_events.
        prior_bundle = self._make_bundle(status=NotificationBundle.Status.SENT)
        prior_bundle.gate_events.add(event)
        result = NotificationEngineService._is_duplicate_notification(
            event=event, trigger=NotificationPreference.Trigger.ARRIVAL
        )
        self.assertTrue(result)

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispatch_suppresses_duplicate_within_window(self, mock_queue):
        self._make_preference()
        person = self._make_person()
        event = self._make_event(person=person)
        # First dispatch creates a bundle.
        NotificationEngineService.dispatch_for_event(
            event=event, trigger=NotificationPreference.Trigger.ARRIVAL
        )
        # Second event with the same person should be suppressed.
        event2 = self._make_event(person=person)
        result = NotificationEngineService.dispatch_for_event(
            event=event2, trigger=NotificationPreference.Trigger.ARRIVAL
        )
        self.assertIsNone(result)

    # --- Rule Action Dispatch (47-52) ------------------------------------

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispatch_for_rule_action_send_notification_with_channels(
        self, mock_queue
    ):
        mock_queue.return_value = None
        event = self._make_event()
        result = NotificationEngineService.dispatch_for_rule_action(
            event=event,
            action=RuleAction.ActionType.SEND_NOTIFICATION,
            parameters={"notify_channels": ["email"]},
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.channel, NotificationPreference.Channel.EMAIL)
        mock_queue.assert_called_once()

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispatch_for_rule_action_send_notification_falls_back_to_preference(
        self, mock_queue
    ):
        mock_queue.return_value = None
        self._make_preference()
        event = self._make_event()
        result = NotificationEngineService.dispatch_for_rule_action(
            event=event,
            action=RuleAction.ActionType.SEND_NOTIFICATION,
            parameters={},
        )
        self.assertIsNotNone(result)
        mock_queue.assert_called_once()

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispatch_for_rule_action_notify_security_creates_sms_bundle(
        self, mock_queue
    ):
        event = self._make_event()
        result = NotificationEngineService.dispatch_for_rule_action(
            event=event,
            action=RuleAction.ActionType.NOTIFY_SECURITY,
            parameters={},
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.channel, NotificationPreference.Channel.SMS)
        self.assertEqual(result.status, NotificationBundle.Status.PENDING)
        mock_queue.assert_not_called()

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispatch_for_rule_action_escalate_creates_push_bundle(self, mock_queue):
        event = self._make_event()
        result = NotificationEngineService.dispatch_for_rule_action(
            event=event,
            action=RuleAction.ActionType.ESCALATE,
            parameters={"escalate_to": "supervisor"},
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.channel, NotificationPreference.Channel.PUSH)
        self.assertEqual(result.status, NotificationBundle.Status.PENDING)

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispatch_for_rule_action_unknown_action_returns_none(self, mock_queue):
        event = self._make_event()
        result = NotificationEngineService.dispatch_for_rule_action(
            event=event,
            action="unknown_action",
            parameters={},
        )
        self.assertIsNone(result)

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispatch_for_rule_action_never_raises_on_exception(self, mock_queue):
        event = self._make_event()
        mock_queue.side_effect = RuntimeError("Boom")
        result = NotificationEngineService.dispatch_for_rule_action(
            event=event,
            action=RuleAction.ActionType.SEND_NOTIFICATION,
            parameters={"notify_channels": ["email"]},
        )
        self.assertIsNone(result)

    # --- Query Methods (53-58) -------------------------------------------

    def test_list_bundles_returns_society_scoped(self):
        self._make_bundle()
        NotificationBundle.objects.create(
            society=self.other_society,
            visitor_category=self.other_cat,
            trigger=NotificationPreference.Trigger.ARRIVAL,
            channel=NotificationPreference.Channel.EMAIL,
        )
        qs = NotificationEngineService.list_bundles(society=self.society)
        for bundle in qs:
            self.assertEqual(bundle.society, self.society)

    def test_list_bundles_filters_by_status(self):
        self._make_bundle(status=NotificationBundle.Status.PENDING)
        self._make_bundle(status=NotificationBundle.Status.SENT)
        qs = NotificationEngineService.list_bundles(
            society=self.society, status=NotificationBundle.Status.SENT
        )
        for bundle in qs:
            self.assertEqual(bundle.status, NotificationBundle.Status.SENT)

    def test_list_bundles_excludes_inactive_by_default(self):
        bundle = self._make_bundle()
        bundle.is_active = False
        bundle.save()
        qs = NotificationEngineService.list_bundles(society=self.society)
        self.assertNotIn(bundle, qs)

    def test_list_bundles_include_inactive_returns_all(self):
        bundle = self._make_bundle()
        bundle.is_active = False
        bundle.save()
        qs = NotificationEngineService.list_bundles(
            society=self.society, include_inactive=True
        )
        self.assertIn(bundle, qs)

    def test_get_bundle_returns_bundle(self):
        bundle = self._make_bundle()
        result = NotificationEngineService.get_bundle(
            society=self.society, pk=bundle.pk
        )
        self.assertEqual(result.pk, bundle.pk)

    def test_get_bundle_404_for_other_society(self):
        other_bundle = NotificationBundle.objects.create(
            society=self.other_society,
            visitor_category=self.other_cat,
            trigger=NotificationPreference.Trigger.ARRIVAL,
            channel=NotificationPreference.Channel.EMAIL,
        )
        with self.assertRaises(Http404):
            NotificationEngineService.get_bundle(
                society=self.society, pk=other_bundle.pk
            )

    def test_get_bundle_404_for_inactive(self):
        bundle = self._make_bundle()
        bundle.is_active = False
        bundle.save()
        with self.assertRaises(Http404):
            NotificationEngineService.get_bundle(
                society=self.society, pk=bundle.pk
            )

    def test_get_pending_bundle_count_returns_correct_count(self):
        self._make_bundle(status=NotificationBundle.Status.PENDING)
        self._make_bundle(status=NotificationBundle.Status.PENDING)
        self._make_bundle(status=NotificationBundle.Status.SENT)
        count = NotificationEngineService.get_pending_bundle_count(
            society=self.society
        )
        self.assertEqual(count, 2)

    def test_get_pending_bundle_count_excludes_inactive(self):
        bundle = self._make_bundle(status=NotificationBundle.Status.PENDING)
        bundle.is_active = False
        bundle.save()
        count = NotificationEngineService.get_pending_bundle_count(
            society=self.society
        )
        self.assertEqual(count, 0)

    def test_get_pending_bundle_count_society_scoped(self):
        self._make_bundle(status=NotificationBundle.Status.PENDING)
        NotificationBundle.objects.create(
            society=self.other_society,
            visitor_category=self.other_cat,
            trigger=NotificationPreference.Trigger.ARRIVAL,
            channel=NotificationPreference.Channel.EMAIL,
            status=NotificationBundle.Status.PENDING,
        )
        count = NotificationEngineService.get_pending_bundle_count(
            society=self.society
        )
        self.assertEqual(count, 1)

    # --- Template Selection (59-62) --------------------------------------

    def test_select_template_arrival(self):
        event = self._make_event(status=GateEvent.Status.ARRIVED)
        template = NotificationEngineService._select_template(
            event=event, trigger=NotificationPreference.Trigger.ARRIVAL
        )
        # ARRIVED status → approval_request template.
        self.assertEqual(template, "gateops.approval_request")

    def test_select_template_entry(self):
        event = self._make_event(status=GateEvent.Status.ENTERED)
        template = NotificationEngineService._select_template(
            event=event, trigger=NotificationPreference.Trigger.ENTRY
        )
        self.assertEqual(template, "gateops.visitor_entry")

    def test_select_template_exit(self):
        event = self._make_event(status=GateEvent.Status.EXITED)
        template = NotificationEngineService._select_template(
            event=event, trigger=NotificationPreference.Trigger.EXIT
        )
        self.assertEqual(template, "gateops.visitor_exit")

    def test_select_template_auto_close(self):
        event = self._make_event(
            status=GateEvent.Status.AUTO_CLOSED,
            event_type=GateEvent.EventType.AUTO_CLOSE,
        )
        template = NotificationEngineService._select_template(
            event=event, trigger=NotificationPreference.Trigger.EXIT
        )
        self.assertEqual(template, "gateops.auto_close")

    def test_select_template_override(self):
        event = self._make_event()
        template = NotificationEngineService._select_template(
            event=event,
            trigger=NotificationPreference.Trigger.ARRIVAL,
            template_name="custom.template",
        )
        self.assertEqual(template, "custom.template")

    # --- Trigger Inference (63-65) ---------------------------------------

    def test_infer_trigger_arrived(self):
        event = self._make_event(status=GateEvent.Status.ARRIVED)
        trigger = NotificationEngineService._infer_trigger_from_event(event=event)
        self.assertEqual(trigger, NotificationPreference.Trigger.ARRIVAL)

    def test_infer_trigger_entered(self):
        event = self._make_event(status=GateEvent.Status.ENTERED)
        trigger = NotificationEngineService._infer_trigger_from_event(event=event)
        self.assertEqual(trigger, NotificationPreference.Trigger.ENTRY)

    def test_infer_trigger_exited(self):
        event = self._make_event(status=GateEvent.Status.EXITED)
        trigger = NotificationEngineService._infer_trigger_from_event(event=event)
        self.assertEqual(trigger, NotificationPreference.Trigger.EXIT)

    def test_infer_trigger_auto_closed(self):
        event = self._make_event(status=GateEvent.Status.AUTO_CLOSED)
        trigger = NotificationEngineService._infer_trigger_from_event(event=event)
        self.assertEqual(trigger, NotificationPreference.Trigger.EXIT)

    # --- Email Context (66) ----------------------------------------------

    def test_build_email_context_contains_required_keys(self):
        person = self._make_person(name="Alice", phone="1234567890")
        event = self._make_event(person=person, purpose="Delivery")
        host = NotificationEngineService.resolve_host(event=event)
        ctx = NotificationEngineService._build_email_context(
            event=event, host=host
        )
        self.assertEqual(ctx["visitor_name"], "Alice")
        self.assertEqual(ctx["visitor_phone"], "1234567890")
        self.assertEqual(ctx["purpose"], "Delivery")
        self.assertIn("gate_name", ctx)
        self.assertIn("host_name", ctx)
        self.assertIn("society_name", ctx)


# ---------------------------------------------------------------------------
# Integration / View tests (67-80)
# ---------------------------------------------------------------------------
class NotificationEngineViewTest(SocietyTestCase):
    """Integration tests for notification hooks in GateEventLifecycleService.

    Verifies that lifecycle transitions trigger notification dispatch, that
    rule-action dispatch is wired correctly, and that notification failures
    never block gate operations.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.visitor_cat = VisitorCategory.objects.get(
            society=cls.society, code="GUEST"
        )
        cls.gate = Gate.objects.get(society=cls.society, code="MAIN")

        # Structure + unit + host for notifications.
        cls.structure = Structure.objects.create(
            society=cls.society,
            structure_type=Structure.StructureType.BUILDING,
            name="Bldg B",
        )
        cls.unit = Unit.objects.create(
            structure=cls.structure,
            unit_type=Unit.UnitType.FLAT,
            identifier="201",
        )
        cls.host_user = UserFactory(email="host2@example.com", name="Host Two")
        Member.objects.create(
            society=cls.society,
            unit=cls.unit,
            user=cls.host_user,
            full_name="Host Two",
            email="host2@example.com",
            phone="9876500000",
            role=Member.MemberRole.OWNER,
            status=Member.MemberStatus.ACTIVE,
        )
        UnitOccupancy.objects.create(
            unit=cls.unit,
            occupant=cls.host_user,
            occupancy_type=UnitOccupancy.OccupancyType.OWNER,
            start_date=timezone.now().date(),
        )

    def setUp(self):
        super().setUp()
        self.guard = SecurityGuard.objects.create(
            society=self.society,
            name="Guard Two",
            phone="1234567890",
            badge_number="G002",
        )

    # --- helpers ---------------------------------------------------------

    def _make_person(self, **overrides):
        defaults = {
            "society": self.society,
            "name": "Integration Visitor",
            "phone": uuid.uuid4().hex[:10],
        }
        defaults.update(overrides)
        return Person.objects.create(**defaults)

    def _make_preference(self, **overrides):
        NotificationPreference.objects.filter(
            society=self.society, visitor_category=self.visitor_cat
        ).delete()
        defaults = {
            "society": self.society,
            "visitor_category": self.visitor_cat,
            "channel": NotificationPreference.Channel.EMAIL,
            "trigger": NotificationPreference.Trigger.ARRIVAL,
            "is_silent": False,
            "bundle_window_minutes": 0,
        }
        defaults.update(overrides)
        return NotificationPreference.objects.create(**defaults)

    def _make_invitation(self, person=None, **kwargs):
        return GateEventLifecycleService.create_invitation(
            society=self.society,
            visitor_category=self.visitor_cat,
            person=person or self._make_person(),
            expected_arrival_at=timezone.now(),
            created_by=self.user,
            gate=self.gate,
            **kwargs,
        )

    # --- _notify hooks (67-72) -------------------------------------------

    @patch("gateops.services.notification_engine.queue_email")
    def test_record_arrival_triggers_arrival_notification(self, mock_queue):
        self._make_preference(trigger=NotificationPreference.Trigger.ARRIVAL)
        event = self._make_invitation()
        event.host_unit = self.unit
        event.save()
        GateEventLifecycleService.record_arrival(
            event, gate=self.gate, guard=self.guard
        )
        event.refresh_from_db()
        # A notification bundle should have been created.
        bundles = NotificationBundle.objects.filter(
            society=self.society, trigger=NotificationPreference.Trigger.ARRIVAL
        )
        self.assertTrue(bundles.exists())

    @patch("gateops.services.notification_engine.queue_email")
    def test_approve_triggers_arrival_notification(self, mock_queue):
        self._make_preference(trigger=NotificationPreference.Trigger.ARRIVAL)
        event = self._make_invitation()
        event.host_unit = self.unit
        event.save()
        GateEventLifecycleService.record_arrival(event, gate=self.gate)
        event.refresh_from_db()
        # Clear bundles from arrival to isolate the approve notification.
        NotificationBundle.objects.all().delete()
        GateEventLifecycleService.approve(event, approved_by=self.user)
        bundles = NotificationBundle.objects.filter(
            society=self.society, trigger=NotificationPreference.Trigger.ARRIVAL
        )
        self.assertTrue(bundles.exists())

    @patch("gateops.services.notification_engine.queue_email")
    def test_record_entry_triggers_entry_notification(self, mock_queue):
        self._make_preference(trigger=NotificationPreference.Trigger.ENTRY)
        event = self._make_invitation()
        event.host_unit = self.unit
        event.save()
        GateEventLifecycleService.record_arrival(event, gate=self.gate)
        event.refresh_from_db()
        GateEventLifecycleService.approve(event, approved_by=self.user)
        event.refresh_from_db()
        NotificationBundle.objects.all().delete()
        GateEventLifecycleService.record_entry(event, guard=self.guard)
        bundles = NotificationBundle.objects.filter(
            society=self.society, trigger=NotificationPreference.Trigger.ENTRY
        )
        self.assertTrue(bundles.exists())

    @patch("gateops.services.notification_engine.queue_email")
    def test_record_exit_triggers_exit_notification(self, mock_queue):
        self._make_preference(trigger=NotificationPreference.Trigger.EXIT)
        event = self._make_invitation()
        event.host_unit = self.unit
        event.save()
        GateEventLifecycleService.record_arrival(event, gate=self.gate)
        event.refresh_from_db()
        GateEventLifecycleService.approve(event, approved_by=self.user)
        event.refresh_from_db()
        GateEventLifecycleService.record_entry(event, guard=self.guard)
        event.refresh_from_db()
        NotificationBundle.objects.all().delete()
        GateEventLifecycleService.record_exit(event, guard=self.guard)
        bundles = NotificationBundle.objects.filter(
            society=self.society, trigger=NotificationPreference.Trigger.EXIT
        )
        self.assertTrue(bundles.exists())

    @patch("gateops.services.notification_engine.queue_email")
    def test_auto_close_triggers_exit_notification(self, mock_queue):
        self._make_preference(trigger=NotificationPreference.Trigger.EXIT)
        event = self._make_invitation()
        event.host_unit = self.unit
        event.save()
        GateEventLifecycleService.record_arrival(event, gate=self.gate)
        event.refresh_from_db()
        GateEventLifecycleService.approve(event, approved_by=self.user)
        event.refresh_from_db()
        GateEventLifecycleService.record_entry(event, guard=self.guard)
        event.refresh_from_db()
        NotificationBundle.objects.all().delete()
        GateEventLifecycleService.auto_close(event)
        bundles = NotificationBundle.objects.filter(
            society=self.society, trigger=NotificationPreference.Trigger.EXIT
        )
        self.assertTrue(bundles.exists())

    @patch("gateops.services.notification_engine.queue_email")
    def test_notification_failure_does_not_block_arrival(self, mock_queue):
        self._make_preference()
        event = self._make_invitation()
        event.host_unit = self.unit
        event.save()
        # Force the notification engine to fail.
        mock_queue.side_effect = RuntimeError("Email server down")
        # record_arrival must succeed despite the notification failure.
        GateEventLifecycleService.record_arrival(
            event, gate=self.gate, guard=self.guard
        )
        event.refresh_from_db()
        self.assertEqual(event.status, GateEvent.Status.ARRIVED)

    # --- Rule action integration (73-75) ---------------------------------

    @patch("gateops.services.notification_engine.queue_email")
    def test_rule_action_send_notification_dispatches_bundle(self, mock_queue):
        mock_queue.return_value = None
        event = self._make_invitation()
        event.host_unit = self.unit
        event.save()
        GateEventLifecycleService.record_arrival(event, gate=self.gate)
        event.refresh_from_db()
        # Manually invoke rule-action dispatch (as evaluate_rules would).
        result = NotificationEngineService.dispatch_for_rule_action(
            event=event,
            action=RuleAction.ActionType.SEND_NOTIFICATION,
            parameters={"notify_channels": ["email"]},
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.channel, NotificationPreference.Channel.EMAIL)
        mock_queue.assert_called_once()

    @patch("gateops.services.notification_engine.queue_email")
    def test_rule_action_notify_security_creates_bundle(self, mock_queue):
        event = self._make_invitation()
        event.host_unit = self.unit
        event.save()
        GateEventLifecycleService.record_arrival(event, gate=self.gate)
        event.refresh_from_db()
        result = NotificationEngineService.dispatch_for_rule_action(
            event=event,
            action=RuleAction.ActionType.NOTIFY_SECURITY,
            parameters={},
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.channel, NotificationPreference.Channel.SMS)
        mock_queue.assert_not_called()

    @patch("gateops.services.notification_engine.queue_email")
    def test_rule_action_escalate_creates_bundle(self, mock_queue):
        event = self._make_invitation()
        event.host_unit = self.unit
        event.save()
        GateEventLifecycleService.record_arrival(event, gate=self.gate)
        event.refresh_from_db()
        result = NotificationEngineService.dispatch_for_rule_action(
            event=event,
            action=RuleAction.ActionType.ESCALATE,
            parameters={"escalate_to": "supervisor"},
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.channel, NotificationPreference.Channel.PUSH)

    # --- Failure resilience (76-78) --------------------------------------

    @patch("gateops.services.notification_engine.queue_email")
    def test_dispatch_for_event_swallows_exception(self, mock_queue):
        self._make_preference()
        event = self._make_invitation()
        event.host_unit = self.unit
        event.save()
        GateEventLifecycleService.record_arrival(event, gate=self.gate)
        event.refresh_from_db()
        mock_queue.side_effect = RuntimeError("Fail")
        # Direct call — should return None, not raise.
        result = NotificationEngineService.dispatch_for_event(
            event=event, trigger=NotificationPreference.Trigger.ARRIVAL
        )
        self.assertIsNone(result)

    @patch("gateops.services.notification_engine.queue_email")
    def test_lifecycle_proceeds_when_no_preference_configured(self, mock_queue):
        # Delete all preferences for this category.
        NotificationPreference.objects.filter(
            society=self.society, visitor_category=self.visitor_cat
        ).delete()
        event = self._make_invitation()
        event.host_unit = self.unit
        event.save()
        # Arrival should succeed with no notification.
        GateEventLifecycleService.record_arrival(
            event, gate=self.gate, guard=self.guard
        )
        event.refresh_from_db()
        self.assertEqual(event.status, GateEvent.Status.ARRIVED)
        mock_queue.assert_not_called()

    @patch("gateops.services.notification_engine.queue_email")
    def test_lifecycle_proceeds_when_no_host_unit(self, mock_queue):
        self._make_preference()
        event = self._make_invitation()
        # No host_unit set — notification should be skipped, not crash.
        GateEventLifecycleService.record_arrival(
            event, gate=self.gate, guard=self.guard
        )
        event.refresh_from_db()
        self.assertEqual(event.status, GateEvent.Status.ARRIVED)
        # A SKIPPED bundle may be created, but no email dispatched.
        # (queue_email is not called because host resolution returns None.)

    # --- Audit logging integration (79-80) -------------------------------

    @patch("gateops.services.notification_engine.queue_email")
    def test_notification_dispatch_creates_audit_log(self, mock_queue):
        mock_queue.return_value = None
        self._make_preference()
        event = self._make_invitation()
        event.host_unit = self.unit
        event.save()
        GateEventLifecycleService.record_arrival(
            event, gate=self.gate, guard=self.guard
        )
        audit = GateOpsAuditLog.objects.filter(
            society=self.society,
            entity_type="NotificationBundle",
        )
        self.assertTrue(audit.exists())

    @patch("gateops.services.notification_engine.queue_email")
    def test_audit_log_failure_does_not_block_notification(self, mock_queue):
        mock_queue.return_value = None
        self._make_preference()
        event = self._make_invitation()
        event.host_unit = self.unit
        event.save()
        with patch.object(
            GateOpsAuditLog,
            "log",
            side_effect=RuntimeError("Audit DB down"),
        ):
            # dispatch_for_event should still return a bundle despite audit
            # failure (audit is wrapped in try/except).
            result = NotificationEngineService.dispatch_for_event(
                event=event, trigger=NotificationPreference.Trigger.ARRIVAL
            )
        self.assertIsNotNone(result)
