"""Phase 3 tests for the ``gateops_auto_close`` management command.

Covers: closing overdue events, dry-run mode, society filtering, and the
graceful no-op when there is nothing to close.
"""

from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from gateops.models import Gate, GateEvent, Person, VisitorCategory
from gateops.services.gate_event_lifecycle import GateEventLifecycleService
from housing_accounting.users.tests.factories import UserFactory
from societies.services import create_society


class AutoCloseCommandTest(TestCase):
    """Tests for the ``gateops_auto_close`` management command.

    The society and seeded master data are created once per class via
    ``setUpTestData`` to avoid re-running the expensive accounting + gateops
    bootstrap signal on every test method.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = UserFactory(password="password")
        cls.society = create_society(user=cls.user, name="Auto Close Society")
        cls.visitor_cat = VisitorCategory.objects.get(society=cls.society, code="DELIVERY")
        cls.gate = Gate.objects.get(society=cls.society, code="MAIN")

    # --- helpers ----------------------------------------------------------

    def _make_entered_event(self, society=None, *, phone="9111111111", name="Overdue Visitor"):
        """Create a fully-entered event and force auto_close_at into the past."""
        society = society or self.society
        visitor_cat = VisitorCategory.objects.get(society=society, code="DELIVERY")
        gate = Gate.objects.get(society=society, code="MAIN")
        person = Person.objects.create(society=society, name=name, phone=phone)
        event = GateEventLifecycleService.create_invitation(
            society=society,
            visitor_category=visitor_cat,
            person=person,
            expected_arrival_at=timezone.now(),
            created_by=self.user,
            gate=gate,
        )
        GateEventLifecycleService.record_arrival(event, gate=gate)
        event.refresh_from_db()
        GateEventLifecycleService.approve(event, approved_by=self.user)
        event.refresh_from_db()
        GateEventLifecycleService.record_entry(event)
        event.refresh_from_db()
        # Force auto_close_at into the past to simulate an overdue event.
        # Use .update() to bypass clean() which rejects past auto_close_at
        # while status is ENTERED.
        GateEvent.objects.filter(pk=event.pk).update(
            auto_close_at=timezone.now() - timedelta(hours=1)
        )
        event.refresh_from_db()
        return event

    # --- tests ------------------------------------------------------------

    def test_command_closes_overdue_events(self):
        event = self._make_entered_event()

        call_command("gateops_auto_close")
        event.refresh_from_db()

        self.assertEqual(event.status, GateEvent.Status.AUTO_CLOSED)
        self.assertEqual(event.event_type, GateEvent.EventType.AUTO_CLOSE)

    def test_dry_run_does_not_close_events(self):
        event = self._make_entered_event()

        call_command("gateops_auto_close", dry_run=True)
        event.refresh_from_db()

        # Dry run must not change the event status.
        self.assertEqual(event.status, GateEvent.Status.ENTERED)

    def test_society_filter_only_closes_matching_society(self):
        other_society = create_society(user=self.user, name="Other Society")
        event_self = self._make_entered_event(society=self.society, phone="9111111111")
        event_other = self._make_entered_event(society=other_society, phone="9222222222")

        call_command("gateops_auto_close", society=self.society.name)
        event_self.refresh_from_db()
        event_other.refresh_from_db()

        self.assertEqual(event_self.status, GateEvent.Status.AUTO_CLOSED)
        self.assertEqual(event_other.status, GateEvent.Status.ENTERED)

    def test_no_events_to_close_is_graceful(self):
        # No overdue events exist — the command should not raise.
        from io import StringIO

        out = StringIO()
        call_command("gateops_auto_close", stdout=out)

        self.assertIn("No events to auto-close.", out.getvalue())
