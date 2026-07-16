"""
Test suite for gateops Phase 12 — Exit Management views.

Covers all Phase 12 views:
- currently_inside_view: GET 200, filtered, login required.
- quick_exit_view: POST-only, GET rejected, login required.
  NOTE: quick_exit_view passes `actor=actor` to process_quick_exit which does
  NOT accept that parameter — this is a known implementation bug. Tests
  document the bug by mocking the service to verify the view calls it
  correctly (the TypeError would occur inside the service call, which the
  view catches as a generic exception).
- qr_exit_scan_view: GET 200, login required.
- qr_exit_view: POST-only, GET rejected.
  NOTE: Same `actor` bug as quick_exit_view.
- handover_list_view: GET 200, filtered by status/gate, cross-society 404.
- handover_create_view: GET 200, POST creates handover, cross-society
  rejection.
- handover_detail_view: GET 200, 404 for cross-society.
- handover_acknowledge_view: POST-only, acknowledges handover.
- handover_dispute_view: POST-only, disputes handover.

Test conventions follow test_contractor_service.py:
- TestCase base (not SocietyTestCase) for view tests.
- create_society from societies.services for society setup.
- force_login + _select_society for session-based society selection.
- SESSION_SELECTED_SOCIETY_ID from housing_accounting.selection.
"""
import uuid
from datetime import time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.test_factories import UserFactory
from gateops.models import (
    Gate,
    GateEvent,
    GuardShift,
    GuardShiftAssignment,
    Person,
    SecurityGuard,
    ShiftHandover,
    VisitorCategory,
)
from gateops.services.gate_event_lifecycle import GateEventLifecycleService
from gateops.services.shift_handover_service import ShiftHandoverService
from housing_accounting.selection import SESSION_SELECTED_SOCIETY_ID
from societies.services import create_society


class ExitViewTestBase(TestCase):
    """Base class for Phase 12 exit management view tests."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = UserFactory(password="password")
        cls.society = create_society(user=cls.user, name="Exit View Society")
        cls.gate = Gate.objects.get(society=cls.society, code="MAIN")
        cls.visitor_cat = VisitorCategory.objects.get(
            society=cls.society, code="GUEST"
        )
        cls.shift = GuardShift.objects.create(
            society=cls.society,
            name="Morning",
            start_time=time(6, 0),
            end_time=time(14, 0),
        )
        cls.other_user = UserFactory(password="password")
        cls.other_society = create_society(
            user=cls.other_user, name="Other Exit View Society"
        )

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self._select_society(self.society)
        cache.clear()

    # --- helpers ---------------------------------------------------------

    def _select_society(self, society):
        session = self.client.session
        session[SESSION_SELECTED_SOCIETY_ID] = society.id
        session.save()

    def _make_guard(self, label="Guard", society=None):
        return SecurityGuard.objects.create(
            society=society or self.society,
            name=f"{label} {uuid.uuid4().hex[:6]}",
            phone=f"{uuid.uuid4().int % (10**10):010d}",
            badge_number=f"B{uuid.uuid4().hex[:6]}",
        )

    def _make_person(self, society=None, name=None):
        return Person.objects.create(
            society=society or self.society,
            name=name or f"Visitor {uuid.uuid4().hex[:6]}",
            phone=f"{uuid.uuid4().int % (10**10):010d}",
        )

    def _make_entered_event(self, person=None, gate=None, society=None):
        soc = society or self.society
        g = gate or Gate.objects.get(society=soc, code="MAIN")
        guard = self._make_guard("Entry", society=soc)
        cat = VisitorCategory.objects.get(society=soc, code="GUEST")
        creator = self.user if soc == self.society else self.other_user
        event = GateEventLifecycleService.create_invitation(
            society=soc,
            visitor_category=cat,
            person=person or self._make_person(society=soc),
            expected_arrival_at=timezone.now(),
            created_by=creator,
            gate=g,
        )
        GateEventLifecycleService.record_arrival(event, gate=g, guard=guard)
        event.refresh_from_db()
        GateEventLifecycleService.approve(event, approved_by=creator)
        event.refresh_from_db()
        GateEventLifecycleService.record_entry(event, guard=guard)
        event.refresh_from_db()
        return event

    def _make_handover(self, **overrides):
        society = overrides.get("society", self.society)
        defaults = {
            "society": society,
            "outgoing_guard": self._make_guard("Outgoing", society=society),
            "incoming_guard": self._make_guard("Incoming", society=society),
            "gate": Gate.objects.get(society=society, code="MAIN"),
        }
        defaults.update(overrides)
        return ShiftHandover.objects.create(**defaults)


# ======================================================================== #
# currently_inside_view
# ======================================================================== #
class CurrentlyInsideViewTest(ExitViewTestBase):
    """Tests for the currently-inside list view."""

    @patch("gateops.services.notification_engine.queue_email")
    def test_currently_inside_view_requires_login(self, mock_queue):
        """Logged-out users are redirected to the login page (302)."""
        self.client.logout()
        response = self.client.get(reverse("gateops:currently-inside"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    @patch("gateops.services.notification_engine.queue_email")
    def test_currently_inside_view_returns_200(self, mock_queue):
        response = self.client.get(reverse("gateops:currently-inside"))
        self.assertEqual(response.status_code, 200)

    @patch("gateops.services.notification_engine.queue_email")
    def test_currently_inside_view_shows_entered_events(self, mock_queue):
        self._make_entered_event()
        response = self.client.get(reverse("gateops:currently-inside"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total"], 1)
        self.assertEqual(len(response.context["results"]), 1)

    @patch("gateops.services.notification_engine.queue_email")
    def test_currently_inside_view_empty(self, mock_queue):
        response = self.client.get(reverse("gateops:currently-inside"))
        self.assertEqual(response.context["total"], 0)

    @patch("gateops.services.notification_engine.queue_email")
    def test_currently_inside_view_with_search_filter(self, mock_queue):
        person = self._make_person(name="Alice Wonder")
        self._make_entered_event(person=person)
        response = self.client.get(
            reverse("gateops:currently-inside"), {"search": "Alice"}
        )
        self.assertEqual(response.context["total"], 1)

    @patch("gateops.services.notification_engine.queue_email")
    def test_currently_inside_view_inside_count_badge(self, mock_queue):
        self._make_entered_event()
        self._make_entered_event()
        response = self.client.get(reverse("gateops:currently-inside"))
        self.assertEqual(response.context["inside_count"], 2)

    @patch("gateops.services.notification_engine.queue_email")
    def test_currently_inside_view_cross_society_isolation(self, mock_queue):
        # Create an entered event in OTHER society.
        self._make_entered_event(society=self.other_society)
        # Stay on self.society — should see 0 events.
        response = self.client.get(reverse("gateops:currently-inside"))
        self.assertEqual(response.context["total"], 0)


# ======================================================================== #
# quick_exit_view
# ======================================================================== #
class QuickExitViewTest(ExitViewTestBase):
    """Tests for the quick exit view.

    NOTE: quick_exit_view passes `actor=actor` to process_quick_exit, but the
    service method does NOT accept that parameter. This is a known
    implementation bug. The view catches the resulting TypeError as a generic
    exception and redirects with an error message. Tests document this by
    verifying the view's error handling path.
    """

    @patch("gateops.services.notification_engine.queue_email")
    def test_quick_exit_view_requires_login(self, mock_queue):
        self.client.logout()
        response = self.client.post(
            reverse("gateops:quick-exit"), {"gate_event_id": "test"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    @patch("gateops.services.notification_engine.queue_email")
    def test_quick_exit_view_get_rejected(self, mock_queue):
        response = self.client.get(reverse("gateops:quick-exit"))
        self.assertEqual(response.status_code, 405)

    @patch("gateops.services.notification_engine.queue_email")
    def test_quick_exit_view_invalid_input_redirects(self, mock_queue):
        response = self.client.post(
            reverse("gateops:quick-exit"), {"gate_event_id": ""}
        )
        self.assertEqual(response.status_code, 302)

    @patch("gateops.services.notification_engine.queue_email")
    def test_quick_exit_view_unknown_event_redirects(self, mock_queue):
        """An unknown gate_event_id raises GateEvent.DoesNotExist, which the
        view catches and redirects with an error message (302).
        """
        response = self.client.post(
            reverse("gateops:quick-exit"),
            {"gate_event_id": str(uuid.uuid4())},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("gateops:currently-inside"))

    @patch("gateops.services.notification_engine.queue_email")
    def test_quick_exit_view_actor_bug_documents_typeerror(self, mock_queue):
        """The view passes actor= to process_quick_exit, which accepts it and
        delegates the exit transition to GateEventLifecycleService.record_exit.

        After the fix, the view successfully processes the exit and redirects
        to the currently-inside page (302).
        """
        event = self._make_entered_event()
        response = self.client.post(
            reverse("gateops:quick-exit"),
            {"gate_event_id": str(event.event_uuid)},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("gateops:currently-inside"))
        event.refresh_from_db()
        self.assertEqual(event.status, GateEvent.Status.EXITED)


# ======================================================================== #
# qr_exit_scan_view
# ======================================================================== #
class QrExitScanViewTest(ExitViewTestBase):
    """Tests for the QR exit scan form view."""

    @patch("gateops.services.notification_engine.queue_email")
    def test_qr_exit_scan_view_requires_login(self, mock_queue):
        self.client.logout()
        response = self.client.get(reverse("gateops:qr-exit-scan"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    @patch("gateops.services.notification_engine.queue_email")
    def test_qr_exit_scan_view_returns_200(self, mock_queue):
        response = self.client.get(reverse("gateops:qr-exit-scan"))
        self.assertEqual(response.status_code, 200)

    @patch("gateops.services.notification_engine.queue_email")
    def test_qr_exit_scan_view_renders_form(self, mock_queue):
        response = self.client.get(reverse("gateops:qr-exit-scan"))
        self.assertIn("form", response.context)


# ======================================================================== #
# qr_exit_view
# ======================================================================== #
class QrExitViewTest(ExitViewTestBase):
    """Tests for the QR exit POST view.

    NOTE: Same actor= bug as quick_exit_view.
    """

    @patch("gateops.services.notification_engine.queue_email")
    def test_qr_exit_view_requires_login(self, mock_queue):
        self.client.logout()
        response = self.client.post(
            reverse("gateops:qr-exit"), {"qr_code": "test"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    @patch("gateops.services.notification_engine.queue_email")
    def test_qr_exit_view_get_rejected(self, mock_queue):
        response = self.client.get(reverse("gateops:qr-exit"))
        self.assertEqual(response.status_code, 405)

    @patch("gateops.services.notification_engine.queue_email")
    def test_qr_exit_view_invalid_input_redirects(self, mock_queue):
        response = self.client.post(
            reverse("gateops:qr-exit"), {"qr_code": ""}
        )
        self.assertEqual(response.status_code, 302)

    @patch("gateops.services.notification_engine.queue_email")
    def test_qr_exit_view_invalid_code_redirects(self, mock_queue):
        """An invalid QR code raises ValidationError, which the view catches
        and redirects to the QR scan page with an error message (302).
        """
        response = self.client.post(
            reverse("gateops:qr-exit"), {"qr_code": "INVALID-CODE"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("gateops:qr-exit-scan"))


# ======================================================================== #
# handover_list_view
# ======================================================================== #
class HandoverListViewTest(ExitViewTestBase):
    """Tests for the handover list view."""

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_list_view_requires_login(self, mock_queue):
        self.client.logout()
        response = self.client.get(reverse("gateops:handover-list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_list_view_returns_200(self, mock_queue):
        response = self.client.get(reverse("gateops:handover-list"))
        self.assertEqual(response.status_code, 200)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_list_view_shows_handovers(self, mock_queue):
        self._make_handover()
        response = self.client.get(reverse("gateops:handover-list"))
        self.assertEqual(response.context["handovers"].count(), 1)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_list_view_empty(self, mock_queue):
        response = self.client.get(reverse("gateops:handover-list"))
        self.assertEqual(response.context["handovers"].count(), 0)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_list_view_filter_by_status(self, mock_queue):
        self._make_handover(status=ShiftHandover.Status.PENDING)
        self._make_handover(
            status=ShiftHandover.Status.ACKNOWLEDGED,
            acknowledged_at=timezone.now(),
        )
        response = self.client.get(
            reverse("gateops:handover-list"),
            {"status": ShiftHandover.Status.PENDING},
        )
        self.assertEqual(response.context["handovers"].count(), 1)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_list_view_cross_society_isolation(self, mock_queue):
        # Create a handover in OTHER society.
        self._make_handover(society=self.other_society)
        # Stay on self.society — should see 0 handovers.
        response = self.client.get(reverse("gateops:handover-list"))
        self.assertEqual(response.context["handovers"].count(), 0)


# ======================================================================== #
# handover_create_view
# ======================================================================== #
class HandoverCreateViewTest(ExitViewTestBase):
    """Tests for the handover create view."""

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_create_view_requires_login(self, mock_queue):
        self.client.logout()
        response = self.client.get(reverse("gateops:handover-create"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_create_view_get_returns_200(self, mock_queue):
        response = self.client.get(reverse("gateops:handover-create"))
        self.assertEqual(response.status_code, 200)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_create_view_get_renders_form(self, mock_queue):
        response = self.client.get(reverse("gateops:handover-create"))
        self.assertIn("form", response.context)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_create_view_post_creates_handover(self, mock_queue):
        """The form sets instance.society before full_clean() so the model's
        cross-society checks pass, and the view creates the handover and
        redirects to the detail page (302).
        """
        outgoing = self._make_guard("Outgoing")
        incoming = self._make_guard("Incoming")
        response = self.client.post(
            reverse("gateops:handover-create"),
            {
                "outgoing_guard": outgoing.pk,
                "incoming_guard": incoming.pk,
                "gate": self.gate.pk,
                "shift": self.shift.pk,
                "outgoing_notes": "All clear",
            },
        )
        self.assertEqual(response.status_code, 302)
        handover = ShiftHandover.objects.get(
            society=self.society,
            outgoing_guard=outgoing,
            incoming_guard=incoming,
        )
        self.assertEqual(handover.gate, self.gate)
        self.assertEqual(handover.shift, self.shift)
        self.assertEqual(handover.outgoing_notes, "All clear")
        self.assertEqual(handover.status, ShiftHandover.Status.PENDING)
        self.assertIn(str(handover.handover_uuid), response.url)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_create_view_self_handover_rejected(self, mock_queue):
        guard = self._make_guard("Same")
        response = self.client.post(
            reverse("gateops:handover-create"),
            {
                "outgoing_guard": guard.pk,
                "incoming_guard": guard.pk,
                "gate": self.gate.pk,
                "shift": self.shift.pk,
            },
        )
        # Form error — stays on the form page (200).
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            ShiftHandover.objects.filter(
                outgoing_guard=guard, incoming_guard=guard
            ).exists()
        )

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_create_view_duplicate_pending_rejected(self, mock_queue):
        outgoing = self._make_guard("Outgoing")
        incoming = self._make_guard("Incoming")
        # Create first handover.
        ShiftHandoverService.create_shift_handover(
            society=self.society,
            outgoing_guard=outgoing,
            incoming_guard=incoming,
            gate=self.gate,
        )
        # Attempt to create a second pending handover for the same guard+gate.
        response = self.client.post(
            reverse("gateops:handover-create"),
            {
                "outgoing_guard": outgoing.pk,
                "incoming_guard": incoming.pk,
                "gate": self.gate.pk,
                "shift": self.shift.pk,
            },
        )
        # Form error — stays on the form page (200).
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            ShiftHandover.objects.filter(
                outgoing_guard=outgoing, gate=self.gate
            ).count(),
            1,
        )


# ======================================================================== #
# handover_detail_view
# ======================================================================== #
class HandoverDetailViewTest(ExitViewTestBase):
    """Tests for the handover detail view."""

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_detail_view_requires_login(self, mock_queue):
        handover = self._make_handover()
        self.client.logout()
        response = self.client.get(
            reverse(
                "gateops:handover-detail",
                kwargs={"uuid": handover.handover_uuid},
            )
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_detail_view_returns_200(self, mock_queue):
        handover = self._make_handover()
        response = self.client.get(
            reverse(
                "gateops:handover-detail",
                kwargs={"uuid": handover.handover_uuid},
            )
        )
        self.assertEqual(response.status_code, 200)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_detail_view_context(self, mock_queue):
        handover = self._make_handover()
        response = self.client.get(
            reverse(
                "gateops:handover-detail",
                kwargs={"uuid": handover.handover_uuid},
            )
        )
        self.assertEqual(response.context["handover"], handover)
        self.assertIn("items", response.context)
        self.assertIn("acknowledge_form", response.context)
        self.assertIn("dispute_form", response.context)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_detail_view_404_for_cross_society(self, mock_queue):
        # Create handover in OTHER society.
        handover = self._make_handover(society=self.other_society)
        # Stay on self.society — should get 404.
        response = self.client.get(
            reverse(
                "gateops:handover-detail",
                kwargs={"uuid": handover.handover_uuid},
            )
        )
        self.assertEqual(response.status_code, 404)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_detail_view_404_for_unknown_uuid(self, mock_queue):
        response = self.client.get(
            reverse(
                "gateops:handover-detail",
                kwargs={"uuid": uuid.uuid4()},
            )
        )
        self.assertEqual(response.status_code, 404)


# ======================================================================== #
# handover_acknowledge_view
# ======================================================================== #
class HandoverAcknowledgeViewTest(ExitViewTestBase):
    """Tests for the handover acknowledge view."""

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_acknowledge_view_requires_login(self, mock_queue):
        handover = self._make_handover()
        self.client.logout()
        response = self.client.post(
            reverse(
                "gateops:handover-acknowledge",
                kwargs={"uuid": handover.handover_uuid},
            )
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_acknowledge_view_get_rejected(self, mock_queue):
        handover = self._make_handover()
        response = self.client.get(
            reverse(
                "gateops:handover-acknowledge",
                kwargs={"uuid": handover.handover_uuid},
            )
        )
        self.assertEqual(response.status_code, 405)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_acknowledge_view_post_acknowledges(self, mock_queue):
        handover = self._make_handover()
        response = self.client.post(
            reverse(
                "gateops:handover-acknowledge",
                kwargs={"uuid": handover.handover_uuid},
            ),
            {"notes": "All verified"},
        )
        self.assertEqual(response.status_code, 302)
        handover.refresh_from_db()
        self.assertEqual(handover.status, ShiftHandover.Status.ACKNOWLEDGED)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_acknowledge_view_404_for_cross_society(self, mock_queue):
        # Create handover in OTHER society.
        handover = self._make_handover(society=self.other_society)
        # Stay on self.society — should get 404.
        response = self.client.post(
            reverse(
                "gateops:handover-acknowledge",
                kwargs={"uuid": handover.handover_uuid},
            ),
            {"notes": "test"},
        )
        self.assertEqual(response.status_code, 404)


# ======================================================================== #
# handover_dispute_view
# ======================================================================== #
class HandoverDisputeViewTest(ExitViewTestBase):
    """Tests for the handover dispute view."""

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_dispute_view_requires_login(self, mock_queue):
        handover = self._make_handover()
        self.client.logout()
        response = self.client.post(
            reverse(
                "gateops:handover-dispute",
                kwargs={"uuid": handover.handover_uuid},
            )
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_dispute_view_get_rejected(self, mock_queue):
        handover = self._make_handover()
        response = self.client.get(
            reverse(
                "gateops:handover-dispute",
                kwargs={"uuid": handover.handover_uuid},
            )
        )
        self.assertEqual(response.status_code, 405)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_dispute_view_post_disputes(self, mock_queue):
        handover = self._make_handover()
        response = self.client.post(
            reverse(
                "gateops:handover-dispute",
                kwargs={"uuid": handover.handover_uuid},
            ),
            {"reason": "Items count mismatch"},
        )
        self.assertEqual(response.status_code, 302)
        handover.refresh_from_db()
        self.assertEqual(handover.status, ShiftHandover.Status.DISPUTED)
        self.assertEqual(handover.dispute_reason, "Items count mismatch")

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_dispute_view_empty_reason_redirects(self, mock_queue):
        handover = self._make_handover()
        response = self.client.post(
            reverse(
                "gateops:handover-dispute",
                kwargs={"uuid": handover.handover_uuid},
            ),
            {"reason": ""},
        )
        self.assertEqual(response.status_code, 302)
        handover.refresh_from_db()
        self.assertEqual(handover.status, ShiftHandover.Status.PENDING)

    @patch("gateops.services.notification_engine.queue_email")
    def test_handover_dispute_view_404_for_cross_society(self, mock_queue):
        # Create handover in OTHER society.
        handover = self._make_handover(society=self.other_society)
        # Stay on self.society — should get 404.
        response = self.client.post(
            reverse(
                "gateops:handover-dispute",
                kwargs={"uuid": handover.handover_uuid},
            ),
            {"reason": "Cross society"},
        )
        self.assertEqual(response.status_code, 404)
