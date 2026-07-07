"""Phase 3 tests for the GateEventLifecycleService state machine.

Covers: invitation creation, arrival, rule evaluation, approval/rejection,
entry, exit, auto-close, cancellation, invalid transitions, audit logging,
the blacklist invariant, person dedup, and ID-number encryption.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from gateops.models import (
    Gate,
    GateEvent,
    GateEventApproval,
    GateOpsAuditLog,
    Person,
    Rule,
    RuleAction,
    RuleCondition,
    SecurityGuard,
    VisitorCategory,
)
from gateops.services.gate_event_lifecycle import GateEventLifecycleService
from housing_accounting.users.tests.factories import UserFactory
from societies.models import Society


class GateEventLifecycleTest(TestCase):
    """Service-level tests for the GateEventLifecycleService state machine.

    The society and seeded master data are created once per class via
    ``setUpTestData`` to avoid re-running the expensive accounting + gateops
    bootstrap signal on every test method.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Creating a Society triggers the gateops bootstrap signal, which
        # seeds default categories, gates, roles, config, etc.
        cls.society = Society.objects.create(name="Lifecycle Society")
        cls.user = UserFactory(password="password")

        # Fetch seeded master data.
        cls.visitor_cat = VisitorCategory.objects.get(
            society=cls.society, code="DELIVERY"
        )
        cls.gate = Gate.objects.get(society=cls.society, code="MAIN")

    def setUp(self):
        super().setUp()
        # Per-test mutable record.
        self.guard = SecurityGuard.objects.create(
            society=self.society,
            name="Guard One",
            phone="1234567890",
            badge_number="G001",
        )

    # --- helpers ----------------------------------------------------------

    def _make_person(self, **kwargs):
        defaults = {
            "society": self.society,
            "name": "Test Visitor",
            "phone": "9876543210",
        }
        defaults.update(kwargs)
        return Person.objects.create(**defaults)

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

    def _make_rule(self, code="RULE_001", priority=100, **kwargs):
        return Rule.objects.create(
            society=self.society,
            name=kwargs.pop("name", "Test Rule"),
            code=code,
            priority=priority,
            **kwargs,
        )

    def _add_condition(self, rule, field, operator, value, connector="and", sort_order=0):
        return RuleCondition.objects.create(
            rule=rule,
            field=field,
            operator=operator,
            value=value,
            logical_connector=connector,
            sort_order=sort_order,
        )

    def _add_action(self, rule, action=RuleAction.ActionType.AUTO_APPROVE, order=0):
        return RuleAction.objects.create(
            rule=rule,
            action=action,
            execution_order=order,
        )

    def _make_entered_event(self, person=None):
        """Drive an event through invited → arrived → approved → entered."""
        event = self._make_invitation(person=person)
        GateEventLifecycleService.record_arrival(event, gate=self.gate, guard=self.guard)
        event.refresh_from_db()
        GateEventLifecycleService.approve(event, approved_by=self.user)
        event.refresh_from_db()
        GateEventLifecycleService.record_entry(event, guard=self.guard)
        event.refresh_from_db()
        return event

    # --- transitions ------------------------------------------------------

    def test_create_invitation_creates_event_with_invited_status(self):
        event = self._make_invitation()

        self.assertEqual(event.status, GateEvent.Status.INVITED)
        self.assertEqual(event.event_type, GateEvent.EventType.INVITATION)
        self.assertIsNotNone(event.expected_arrival_at)
        self.assertEqual(event.society, self.society)
        self.assertEqual(event.gate, self.gate)
        self.assertEqual(event.visitor_category, self.visitor_cat)
        self.assertEqual(event.created_by, self.user)

    def test_record_arrival_transitions_invited_to_arrived(self):
        event = self._make_invitation()

        GateEventLifecycleService.record_arrival(event, gate=self.gate, guard=self.guard)
        event.refresh_from_db()

        self.assertEqual(event.status, GateEvent.Status.ARRIVED)
        self.assertIsNotNone(event.arrived_at)
        self.assertEqual(event.gate, self.gate)
        self.assertEqual(event.guard, self.guard)

    def test_record_arrival_triggers_rule_evaluation(self):
        event = self._make_invitation()

        GateEventLifecycleService.record_arrival(event, gate=self.gate)
        event.refresh_from_db()

        # With no rules configured, the engine returns REQUIRE_APPROVAL and
        # creates a no-match RuleEvaluation row. The event stays in "arrived".
        self.assertIsNotNone(event.rule_evaluated)
        self.assertEqual(event.rule_action, RuleAction.ActionType.REQUIRE_APPROVAL)
        self.assertEqual(event.status, GateEvent.Status.ARRIVED)

    def test_approve_transitions_arrived_to_approved(self):
        event = self._make_invitation()
        GateEventLifecycleService.record_arrival(event, gate=self.gate)
        event.refresh_from_db()

        GateEventLifecycleService.approve(event, approved_by=self.user)
        event.refresh_from_db()

        self.assertEqual(event.status, GateEvent.Status.APPROVED)
        self.assertIsNotNone(event.approved_at)
        self.assertEqual(event.approved_by, self.user)

        approval = GateEventApproval.objects.get(gate_event=event)
        self.assertEqual(approval.decision, GateEventApproval.Decision.APPROVED)
        self.assertEqual(approval.decided_by, self.user)

    def test_reject_transitions_arrived_to_rejected(self):
        event = self._make_invitation()
        GateEventLifecycleService.record_arrival(event, gate=self.gate)
        event.refresh_from_db()

        GateEventLifecycleService.reject(event, decided_by=self.user, reason="No access")
        event.refresh_from_db()

        self.assertEqual(event.status, GateEvent.Status.REJECTED)
        self.assertEqual(event.event_type, GateEvent.EventType.REJECTED)

        approval = GateEventApproval.objects.get(gate_event=event)
        self.assertEqual(approval.decision, GateEventApproval.Decision.REJECTED)
        self.assertEqual(approval.decided_by, self.user)

    def test_record_entry_transitions_approved_to_entered(self):
        event = self._make_invitation()
        GateEventLifecycleService.record_arrival(event, gate=self.gate)
        event.refresh_from_db()
        GateEventLifecycleService.approve(event, approved_by=self.user)
        event.refresh_from_db()

        GateEventLifecycleService.record_entry(event, guard=self.guard)
        event.refresh_from_db()

        self.assertEqual(event.status, GateEvent.Status.ENTERED)
        self.assertIsNotNone(event.entered_at)
        self.assertIsNotNone(event.auto_close_at)
        # auto_close_at must be in the future (clean() enforces this).
        self.assertGreater(event.auto_close_at, timezone.now())

    def test_record_exit_transitions_entered_to_exited(self):
        event = self._make_entered_event()

        GateEventLifecycleService.record_exit(event, guard=self.guard)
        event.refresh_from_db()

        self.assertEqual(event.status, GateEvent.Status.EXITED)
        self.assertIsNotNone(event.exited_at)
        self.assertGreater(event.exited_at, event.entered_at)

    def test_auto_close_transitions_entered_to_auto_closed(self):
        event = self._make_entered_event()
        # Force auto_close_at into the past to simulate an overdue event.
        # Use .update() to bypass clean() which rejects past auto_close_at
        # while status is ENTERED.
        GateEvent.objects.filter(pk=event.pk).update(
            auto_close_at=timezone.now() - timedelta(hours=1)
        )
        event.refresh_from_db()

        GateEventLifecycleService.auto_close(event)
        event.refresh_from_db()

        self.assertEqual(event.status, GateEvent.Status.AUTO_CLOSED)
        self.assertEqual(event.event_type, GateEvent.EventType.AUTO_CLOSE)
        self.assertIsNotNone(event.exited_at)

    def test_cancel_transitions_invited_to_cancelled(self):
        event = self._make_invitation()

        GateEventLifecycleService.cancel(event, cancelled_by=self.user, reason="Changed plans")
        event.refresh_from_db()

        self.assertEqual(event.status, GateEvent.Status.CANCELLED)
        self.assertEqual(event.event_type, GateEvent.EventType.CANCELLED)

    def test_invalid_transition_raises_validation_error(self):
        # exited → entered is illegal.
        event = self._make_entered_event()
        GateEventLifecycleService.record_exit(event)
        event.refresh_from_db()
        with self.assertRaises(ValidationError):
            GateEventLifecycleService.record_entry(event)

        # rejected → approved is illegal (rejected is terminal).
        event2 = self._make_invitation(person=self._make_person(phone="5555555555"))
        GateEventLifecycleService.record_arrival(event2, gate=self.gate)
        event2.refresh_from_db()
        GateEventLifecycleService.reject(event2, decided_by=self.user)
        event2.refresh_from_db()
        with self.assertRaises(ValidationError):
            GateEventLifecycleService.approve(event2, approved_by=self.user)

    def test_every_transition_creates_audit_log(self):
        event = self._make_invitation()

        # create_invitation writes a CREATE audit log.
        self.assertTrue(
            GateOpsAuditLog.objects.filter(
                entity_type="GateEvent", entity_id=str(event.pk)
            ).exists()
        )

        GateEventLifecycleService.record_arrival(event, gate=self.gate)
        # record_arrival writes a STATE_TRANSITION audit log.
        self.assertGreaterEqual(
            GateOpsAuditLog.objects.filter(
                entity_type="GateEvent", entity_id=str(event.pk)
            ).count(),
            2,
        )

    def test_blacklisted_person_cannot_be_auto_approved(self):
        # Create a blacklisted person (clean() requires a blacklist_reason).
        person = self._make_person(
            name="Blacklisted Visitor",
            phone="1111111111",
            is_blacklisted=True,
            blacklist_reason="Banned for misconduct",
        )

        # Create a rule that would auto-approve any DELIVERY visitor.
        rule = self._make_rule(code="AUTO_DELIVERY")
        self._add_condition(
            rule,
            RuleCondition.ConditionField.VISITOR_TYPE,
            RuleCondition.Operator.EQ,
            "DELIVERY",
        )
        self._add_action(rule, RuleAction.ActionType.AUTO_APPROVE)

        event = self._make_invitation(person=person)
        GateEventLifecycleService.record_arrival(event, gate=self.gate)
        event.refresh_from_db()

        # The rule matched (AUTO_APPROVE) but the person is blacklisted, so the
        # event must NOT be approved — it stays in "arrived" with a pending
        # approval request.
        self.assertNotEqual(event.status, GateEvent.Status.APPROVED)
        self.assertEqual(event.status, GateEvent.Status.ARRIVED)
        self.assertTrue(
            GateEventApproval.objects.filter(
                gate_event=event,
                decision=GateEventApproval.Decision.PENDING,
            ).exists()
        )

    def test_person_dedup_by_phone(self):
        person1 = Person.objects.create(
            society=self.society, name="Alice", phone="1234567890"
        )

        # get_or_create returns the existing person (no duplicate).
        person2, created = Person.objects.get_or_create(
            society=self.society,
            phone="1234567890",
            defaults={"name": "Alice Duplicate"},
        )
        self.assertFalse(created)
        self.assertEqual(person1.pk, person2.pk)

        # Direct creation with the same active phone violates the unique
        # constraint.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Person.objects.create(
                    society=self.society, name="Bob", phone="1234567890"
                )

    def test_person_id_number_encryption(self):
        person = Person.objects.create(
            society=self.society, name="Encrypted Person", phone="2222222222"
        )
        person.id_number = "ABC123456"
        person.save()
        person.refresh_from_db()

        # The encrypted column must not contain the plaintext.
        self.assertNotEqual(person.id_number_encrypted, "ABC123456")
        self.assertTrue(person.id_number_encrypted)

        # The property transparently decrypts on read.
        self.assertEqual(person.id_number, "ABC123456")
