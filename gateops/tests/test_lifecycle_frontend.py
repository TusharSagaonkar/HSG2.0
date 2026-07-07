"""Phase 3 frontend tests for the GateEvent lifecycle views.

Covers: event list, currently-inside, event create (GET/POST), event detail,
cross-society isolation, POST-only approve/reject/exit endpoints, and the
missing-society guard.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from gateops.models import Gate, GateEvent, Person, VisitorCategory
from gateops.services.gate_event_lifecycle import GateEventLifecycleService
from housing_accounting.selection import SESSION_SELECTED_SOCIETY_ID
from housing_accounting.users.tests.factories import UserFactory
from societies.services import create_society


class GateEventFrontendTest(TestCase):
    """Frontend view tests for the GateEvent lifecycle console."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = UserFactory(password="password")

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    # --- helpers ----------------------------------------------------------

    def _create_accessible_society(self, name):
        return create_society(user=self.user, name=name)

    def _select_society(self, society):
        session = self.client.session
        session[SESSION_SELECTED_SOCIETY_ID] = society.id
        session.save()

    def _seed_entered_event(self, society, *, phone="9000000000", name="Inside Visitor"):
        """Create a fully-entered GateEvent for the given society."""
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
        return event

    def _post_event(self, society, **overrides):
        """POST a new walk-in arrival via the event-create form."""
        visitor_cat = VisitorCategory.objects.get(society=society, code="DELIVERY")
        gate = Gate.objects.get(society=society, code="MAIN")
        data = {
            "person_phone": "8000000000",
            "person_name": "Walk In",
            "visitor_category": visitor_cat.pk,
            "gate": gate.pk,
            "direction": GateEvent.Direction.INBOUND,
            "purpose": "Delivery",
            "photo_url": "",
            "id_verified": "on",
            "notes": "",
        }
        data.update(overrides)
        return self.client.post(reverse("gateops:event-create"), data)

    # --- tests ------------------------------------------------------------

    def test_event_list_returns_200_and_shows_events(self):
        society = self._create_accessible_society("Event List Society")
        self._select_society(society)
        event = self._seed_entered_event(society)

        response = self.client.get(reverse("gateops:event-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gate Events")
        self.assertContains(response, event.person.name)

    def test_currently_inside_returns_200(self):
        society = self._create_accessible_society("Inside Society")
        self._select_society(society)
        self._seed_entered_event(society, name="Inside Person")

        response = self.client.get(reverse("gateops:currently-inside"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Currently Inside")

    def test_event_create_get_renders_form(self):
        society = self._create_accessible_society("Create Get Society")
        self._select_society(society)

        response = self.client.get(reverse("gateops:event-create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "New Gate Event")
        self.assertContains(response, "Record Arrival")

    def test_event_create_post_creates_event_and_person(self):
        society = self._create_accessible_society("Create Post Society")
        self._select_society(society)

        response = self._post_event(society, person_phone="7000000000", person_name="New Visitor")

        event = GateEvent.objects.get(society=society)
        self.assertEqual(event.status, GateEvent.Status.ARRIVED)
        self.assertEqual(event.person.phone, "7000000000")
        self.assertEqual(event.person.name, "New Visitor")
        # Redirects to the event detail page.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("gateops:event-detail", kwargs={"uuid": event.event_uuid}))

    def test_event_detail_returns_200(self):
        society = self._create_accessible_society("Detail Society")
        self._select_society(society)
        event = self._seed_entered_event(society)

        response = self.client.get(reverse("gateops:event-detail", kwargs={"uuid": event.event_uuid}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Event Summary")
        self.assertContains(response, str(event.event_uuid))

    def test_event_detail_cross_society_returns_404(self):
        society_a = self._create_accessible_society("Society A")
        society_b = self._create_accessible_society("Society B")
        event_a = self._seed_entered_event(society_a)
        self._select_society(society_b)

        response = self.client.get(reverse("gateops:event-detail", kwargs={"uuid": event_a.event_uuid}))

        self.assertEqual(response.status_code, 404)

    def test_event_approve_get_returns_405(self):
        society = self._create_accessible_society("Approve 405 Society")
        self._select_society(society)
        # Create an event in "arrived" state (no rules → pending approval).
        visitor_cat = VisitorCategory.objects.get(society=society, code="DELIVERY")
        gate = Gate.objects.get(society=society, code="MAIN")
        person = Person.objects.create(society=society, name="Pending", phone="6000000000")
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

        response = self.client.get(reverse("gateops:event-approve", kwargs={"uuid": event.event_uuid}))

        self.assertEqual(response.status_code, 405)

    def test_event_approve_post_transitions_to_approved(self):
        society = self._create_accessible_society("Approve Post Society")
        self._select_society(society)
        visitor_cat = VisitorCategory.objects.get(society=society, code="DELIVERY")
        gate = Gate.objects.get(society=society, code="MAIN")
        person = Person.objects.create(society=society, name="Approve Me", phone="5000000000")
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

        response = self.client.post(reverse("gateops:event-approve", kwargs={"uuid": event.event_uuid}))
        event.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(event.status, GateEvent.Status.APPROVED)

    def test_event_reject_get_returns_405(self):
        society = self._create_accessible_society("Reject 405 Society")
        self._select_society(society)
        visitor_cat = VisitorCategory.objects.get(society=society, code="DELIVERY")
        gate = Gate.objects.get(society=society, code="MAIN")
        person = Person.objects.create(society=society, name="Reject Me", phone="4000000000")
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

        response = self.client.get(reverse("gateops:event-reject", kwargs={"uuid": event.event_uuid}))

        self.assertEqual(response.status_code, 405)

    def test_event_exit_get_returns_405(self):
        society = self._create_accessible_society("Exit 405 Society")
        self._select_society(society)
        event = self._seed_entered_event(society)

        response = self.client.get(reverse("gateops:event-exit", kwargs={"uuid": event.event_uuid}))

        self.assertEqual(response.status_code, 405)

    def test_event_exit_post_transitions_to_exited(self):
        society = self._create_accessible_society("Exit Post Society")
        self._select_society(society)
        event = self._seed_entered_event(society)

        response = self.client.post(reverse("gateops:event-exit", kwargs={"uuid": event.event_uuid}))
        event.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(event.status, GateEvent.Status.EXITED)

    def test_currently_inside_shows_entered_events(self):
        society = self._create_accessible_society("Inside Show Society")
        self._select_society(society)
        event = self._seed_entered_event(society, name="Inside Visible")

        response = self.client.get(reverse("gateops:currently-inside"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Inside Visible")

    def test_currently_inside_does_not_show_exited_events(self):
        society = self._create_accessible_society("Inside Hide Society")
        self._select_society(society)
        event = self._seed_entered_event(society, name="Exited Hidden")
        GateEventLifecycleService.record_exit(event)
        event.refresh_from_db()

        response = self.client.get(reverse("gateops:currently-inside"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Exited Hidden")

    def test_missing_society_returns_404_with_message(self):
        response = self.client.get(reverse("gateops:event-list"))

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "No society selected", status_code=404)
        self.assertContains(response, "Select a society to use Gate Operations.", status_code=404)
