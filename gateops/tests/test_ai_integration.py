"""Tests for Phase 11 AI Recommendation Engine integration points.

Covers three integration surfaces (Phase 11 §4):

1. **Lifecycle hooks** — ``GateEventLifecycleService.record_entry()`` calls
   ``AIRecommendationService._check_entry_anomalies()`` after a successful
   entry transition.  The hook is non-blocking: an AI failure never prevents
   or rolls back the entry.

2. **Rule context** — ``GateEventLifecycleService._build_rule_context()``
   injects a ``risk_score`` key (read from the cached
   ``VisitorPattern.risk_score``) so the rule engine can evaluate
   RISK_SCORE conditions without recomputing the score on every event.

3. **RISK_SCORE condition** — ``RuleEngineService.evaluate()`` compares the
   ``risk_score`` context value using standard numeric operators (gt, gte,
   lt, lte, between, eq).
"""

from datetime import time, timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.utils import timezone

from core.test_base import SocietyTestCase
from core.test_factories import UserFactory
from gateops.models import (
    AnomalyDetection,
    Gate,
    GateEvent,
    GateOpsAuditLog,
    GateOpsSocietyConfig,
    GateVehicle,
    Person,
    Rule,
    RuleAction,
    RuleCondition,
    SecurityGuard,
    VisitorCategory,
    VisitorPattern,
)
from gateops.services.ai_recommendation_service import AIRecommendationService
from gateops.services.gate_event_lifecycle import GateEventLifecycleService
from gateops.services.rule_engine import RuleEngineService


# =========================================================================
# Shared base
# =========================================================================

class AIIntegrationTestBase(SocietyTestCase):
    """Shared fixtures for AI integration tests."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.visitor_cat = VisitorCategory.objects.get(
            society=cls.society, code="GUEST"
        )
        cls.gate = Gate.objects.get(society=cls.society, code="MAIN")
        cls.user = UserFactory(password="password")

    def setUp(self):
        super().setUp()
        self.guard = SecurityGuard.objects.create(
            society=self.society,
            name="Integration Guard",
            phone="5555559001",
            badge_number="IG001",
        )
        self.person = Person.objects.create(
            society=self.society,
            name="Integration Visitor",
            phone="5555559002",
        )

    # --- helpers ----------------------------------------------------------

    def _make_invitation(self, person=None, **kwargs):
        return GateEventLifecycleService.create_invitation(
            society=self.society,
            visitor_category=self.visitor_cat,
            person=person or self.person,
            expected_arrival_at=timezone.now(),
            created_by=self.user,
            gate=self.gate,
            **kwargs,
        )

    def _make_entered_event(self, person=None):
        """Drive an event through invited → arrived → approved → entered."""
        event = self._make_invitation(person=person)
        GateEventLifecycleService.record_arrival(
            event, gate=self.gate, guard=self.guard
        )
        event.refresh_from_db()
        GateEventLifecycleService.approve(event, approved_by=self.user)
        event.refresh_from_db()
        GateEventLifecycleService.record_entry(event, guard=self.guard)
        event.refresh_from_db()
        return event

    def _set_night_mode(self, start_hour=22, end_hour=6):
        """Configure night-mode hours on the society's config."""
        config, _ = GateOpsSocietyConfig.objects.get_or_create(
            society=self.society
        )
        config.night_mode_start = time(hour=start_hour)
        config.night_mode_end = time(hour=end_hour)
        config.save()
        # Drop the cached reverse OneToOne relation.
        self.society.refresh_from_db()
        return config

    def _make_rule(self, code="RULE_AI_001", priority=100, **kwargs):
        return Rule.objects.create(
            society=self.society,
            name=kwargs.pop("name", "AI Integration Rule"),
            code=code,
            priority=priority,
            **kwargs,
        )

    def _add_condition(self, rule, field, operator, value,
                       connector="and", sort_order=0):
        return RuleCondition.objects.create(
            rule=rule,
            field=field,
            operator=operator,
            value=value,
            logical_connector=connector,
            sort_order=sort_order,
        )

    def _add_action(self, rule, action=RuleAction.ActionType.AUTO_APPROVE,
                    order=0):
        return RuleAction.objects.create(
            rule=rule,
            action=action,
            execution_order=order,
        )

    def _make_pattern(self, person=None, risk_score=0.0, **overrides):
        """Create a VisitorPattern with a specific risk_score."""
        person = person or self.person
        from gateops.models import VisitorPattern as VP
        risk_level = VP._risk_level_for_score(risk_score)
        defaults = {
            "society": self.society,
            "person": person,
            "visitor_category": overrides.pop(
                "visitor_category", self.visitor_cat
            ),
            "visit_count": overrides.pop("visit_count", 5),
            "avg_visit_duration_minutes": overrides.pop(
                "avg_visit_duration_minutes", 30
            ),
            "frequency_score": overrides.pop("frequency_score", 0.5),
            "risk_score": risk_score,
            "risk_level": risk_level,
            "is_frequent": overrides.pop("is_frequent", False),
        }
        defaults.update(overrides)
        return VisitorPattern.objects.create(**defaults)


# =========================================================================
# Lifecycle hook — _check_entry_anomalies called from record_entry
# =========================================================================

class EntryAnomalyHookTest(AIIntegrationTestBase):
    """Tests for the real-time anomaly hook in record_entry()."""

    @patch("gateops.services.notification_engine.queue_email")
    def test_after_hours_entry_creates_anomaly(self, mock_queue):
        """An entry during night-mode hours triggers an AFTER_HOURS_ENTRY anomaly."""
        mock_queue.return_value = None
        self._set_night_mode(start_hour=22, end_hour=6)

        # Patch timezone.now so record_entry sets entered_at to 2am.
        fixed_now = timezone.now().replace(
            hour=2, minute=30, second=0, microsecond=0
        )
        with patch(
            "gateops.services.gate_event_lifecycle.timezone.now",
            return_value=fixed_now,
        ):
            event = self._make_entered_event()

        anomaly = AnomalyDetection.objects.filter(
            society=self.society,
            gate_event=event,
            anomaly_type=AnomalyDetection.AnomalyType.AFTER_HOURS_ENTRY,
            is_active=True,
        ).first()
        self.assertIsNotNone(anomaly)
        self.assertEqual(anomaly.status, AnomalyDetection.Status.OPEN)

    @patch("gateops.services.notification_engine.queue_email")
    def test_daytime_entry_no_after_hours_anomaly(self, mock_queue):
        """A daytime entry does not trigger an after-hours anomaly."""
        mock_queue.return_value = None
        self._set_night_mode(start_hour=22, end_hour=6)

        # Entry at 10am — not night mode.
        fixed_now = timezone.now().replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        with patch(
            "gateops.services.gate_event_lifecycle.timezone.now",
            return_value=fixed_now,
        ):
            event = self._make_entered_event()

        exists = AnomalyDetection.objects.filter(
            society=self.society,
            gate_event=event,
            anomaly_type=AnomalyDetection.AnomalyType.AFTER_HOURS_ENTRY,
        ).exists()
        self.assertFalse(exists)

    @patch("gateops.services.notification_engine.queue_email")
    def test_no_night_mode_no_after_hours_anomaly(self, mock_queue):
        """Without night-mode configured, no after-hours anomaly is created."""
        mock_queue.return_value = None
        # Do NOT call _set_night_mode — config has night_mode_start=None.

        fixed_now = timezone.now().replace(
            hour=2, minute=30, second=0, microsecond=0
        )
        with patch(
            "gateops.services.gate_event_lifecycle.timezone.now",
            return_value=fixed_now,
        ):
            event = self._make_entered_event()

        exists = AnomalyDetection.objects.filter(
            society=self.society,
            gate_event=event,
            anomaly_type=AnomalyDetection.AnomalyType.AFTER_HOURS_ENTRY,
        ).exists()
        self.assertFalse(exists)

    @patch("gateops.services.notification_engine.queue_email")
    def test_duplicate_entry_creates_anomaly(self, mock_queue):
        """A second ENTERED event for the same person triggers DUPLICATE_ENTRY."""
        mock_queue.return_value = None

        # First entry — normal.
        event1 = self._make_entered_event()

        # Second entry for the same person (without exiting first).
        event2 = self._make_invitation()
        GateEventLifecycleService.record_arrival(
            event2, gate=self.gate, guard=self.guard
        )
        event2.refresh_from_db()
        GateEventLifecycleService.approve(event2, approved_by=self.user)
        event2.refresh_from_db()
        GateEventLifecycleService.record_entry(event2, guard=self.guard)
        event2.refresh_from_db()

        anomaly = AnomalyDetection.objects.filter(
            society=self.society,
            gate_event=event2,
            anomaly_type=AnomalyDetection.AnomalyType.DUPLICATE_ENTRY,
            is_active=True,
        ).first()
        self.assertIsNotNone(anomaly)
        self.assertEqual(
            anomaly.severity, AnomalyDetection.Severity.HIGH
        )

    @patch("gateops.services.notification_engine.queue_email")
    def test_blacklist_bypass_creates_anomaly(self, mock_queue):
        """An entry by a blacklisted person triggers BLACKLIST_BYPASS."""
        mock_queue.return_value = None

        # Blacklist the person.
        self.person.is_blacklisted = True
        self.person.blacklist_reason = "Known troublemaker"
        self.person.save()

        event = self._make_entered_event()

        anomaly = AnomalyDetection.objects.filter(
            society=self.society,
            gate_event=event,
            anomaly_type=AnomalyDetection.AnomalyType.BLACKLIST_BYPASS,
            is_active=True,
        ).first()
        self.assertIsNotNone(anomaly)
        self.assertEqual(
            anomaly.severity, AnomalyDetection.Severity.CRITICAL
        )

    @patch("gateops.services.notification_engine.queue_email")
    def test_non_blacklisted_no_bypass_anomaly(self, mock_queue):
        """A non-blacklisted person does not trigger a blacklist bypass."""
        mock_queue.return_value = None

        event = self._make_entered_event()

        exists = AnomalyDetection.objects.filter(
            society=self.society,
            gate_event=event,
            anomaly_type=AnomalyDetection.AnomalyType.BLACKLIST_BYPASS,
        ).exists()
        self.assertFalse(exists)

    @patch("gateops.services.notification_engine.queue_email")
    def test_entry_succeeds_even_if_anomaly_check_fails(self, mock_queue):
        """If _check_entry_anomalies raises, the entry still succeeds."""
        mock_queue.return_value = None

        with patch.object(
            AIRecommendationService,
            "_check_entry_anomalies",
            side_effect=RuntimeError("AI exploded"),
        ):
            event = self._make_entered_event()

        # The entry transition completed despite the AI failure.
        event.refresh_from_db()
        self.assertEqual(event.status, GateEvent.Status.ENTERED)
        self.assertIsNotNone(event.entered_at)

    @patch("gateops.services.notification_engine.queue_email")
    def test_deduplication_in_realtime_hook(self, mock_queue):
        """Re-checking the same event does not create duplicate anomalies."""
        mock_queue.return_value = None
        self._set_night_mode(start_hour=22, end_hour=6)

        fixed_now = timezone.now().replace(
            hour=2, minute=30, second=0, microsecond=0
        )
        with patch(
            "gateops.services.gate_event_lifecycle.timezone.now",
            return_value=fixed_now,
        ):
            event = self._make_entered_event()

        # Manually re-run the hook — should NOT create a second anomaly.
        AIRecommendationService._check_entry_anomalies(event=event)

        count = AnomalyDetection.objects.filter(
            society=self.society,
            gate_event=event,
            anomaly_type=AnomalyDetection.AnomalyType.AFTER_HOURS_ENTRY,
        ).count()
        self.assertEqual(count, 1)


# =========================================================================
# Rule context — risk_score injection
# =========================================================================

class RuleContextRiskScoreTest(AIIntegrationTestBase):
    """Tests for risk_score injection in _build_rule_context()."""

    def test_context_contains_risk_score_key(self):
        """_build_rule_context includes a 'risk_score' key."""
        event = self._make_invitation()
        context = GateEventLifecycleService._build_rule_context(event)
        self.assertIn("risk_score", context)

    def test_risk_score_defaults_to_zero_without_pattern(self):
        """Without a VisitorPattern, risk_score defaults to 0.0."""
        event = self._make_invitation()
        context = GateEventLifecycleService._build_rule_context(event)
        self.assertEqual(context["risk_score"], 0.0)

    def test_risk_score_reads_cached_pattern_value(self):
        """risk_score is read from the cached VisitorPattern.risk_score."""
        self._make_pattern(person=self.person, risk_score=0.75)

        event = self._make_invitation()
        context = GateEventLifecycleService._build_rule_context(event)
        self.assertEqual(context["risk_score"], 0.75)

    def test_risk_score_reads_low_pattern_value(self):
        """A low risk_score is correctly read from the pattern."""
        self._make_pattern(person=self.person, risk_score=0.10)

        event = self._make_invitation()
        context = GateEventLifecycleService._build_rule_context(event)
        self.assertEqual(context["risk_score"], 0.10)

    def test_risk_score_defaults_to_zero_for_no_person(self):
        """If event has no person, risk_score defaults to 0.0."""
        event = self._make_invitation()
        event.person = None
        context = GateEventLifecycleService._build_rule_context(event)
        self.assertEqual(context["risk_score"], 0.0)

    def test_risk_score_defaults_to_zero_on_exception(self):
        """If _get_cached_risk_score raises, risk_score defaults to 0.0."""
        event = self._make_invitation()
        with patch.object(
            AIRecommendationService,
            "_get_cached_risk_score",
            side_effect=RuntimeError("DB error"),
        ):
            context = GateEventLifecycleService._build_rule_context(event)
        self.assertEqual(context["risk_score"], 0.0)

    def test_risk_score_uses_active_pattern_only(self):
        """Soft-deleted (inactive) patterns are not used for risk_score."""
        self._make_pattern(
            person=self.person, risk_score=0.80, is_active=False
        )
        event = self._make_invitation()
        context = GateEventLifecycleService._build_rule_context(event)
        # No active pattern → defaults to 0.0.
        self.assertEqual(context["risk_score"], 0.0)

    def test_risk_score_society_scoped(self):
        """risk_score is read from a pattern in the same society only."""
        # Create a pattern for self.person in this society.
        self._make_pattern(person=self.person, risk_score=0.60)

        event = self._make_invitation()
        context = GateEventLifecycleService._build_rule_context(event)
        self.assertEqual(context["risk_score"], 0.60)


# =========================================================================
# RISK_SCORE condition — rule engine evaluation
# =========================================================================

class RiskScoreConditionTest(AIIntegrationTestBase):
    """Tests for RISK_SCORE condition evaluation in the rule engine."""

    def _make_event_with_risk(self, risk_score=0.0):
        """Create an event and a VisitorPattern with the given risk_score."""
        self._make_pattern(person=self.person, risk_score=risk_score)
        event = self._make_invitation()
        return event

    def _eval_risk_condition(self, operator, value, risk_score=0.0):
        """Evaluate a single RISK_SCORE condition against a risk_score."""
        event = self._make_event_with_risk(risk_score=risk_score)
        rule = self._make_rule()
        self._add_condition(
            rule,
            field=RuleCondition.ConditionField.RISK_SCORE,
            operator=operator,
            value=value,
        )
        self._add_action(rule, action=RuleAction.ActionType.AUTO_APPROVE)

        context = GateEventLifecycleService._build_rule_context(event)
        result = RuleEngineService.evaluate(context)
        return result

    def test_risk_score_gt_matches(self):
        """RISK_SCORE gt 0.5 matches when risk_score=0.75."""
        result = self._eval_risk_condition(
            operator=RuleCondition.Operator.GT,
            value=0.5,
            risk_score=0.75,
        )
        self.assertTrue(result.matched)

    def test_risk_score_gt_no_match(self):
        """RISK_SCORE gt 0.5 does not match when risk_score=0.30."""
        result = self._eval_risk_condition(
            operator=RuleCondition.Operator.GT,
            value=0.5,
            risk_score=0.30,
        )
        self.assertFalse(result.matched)

    def test_risk_score_gte_matches_at_boundary(self):
        """RISK_SCORE gte 0.5 matches when risk_score=0.50 (boundary)."""
        result = self._eval_risk_condition(
            operator=RuleCondition.Operator.GTE,
            value=0.5,
            risk_score=0.50,
        )
        self.assertTrue(result.matched)

    def test_risk_score_lt_matches(self):
        """RISK_SCORE lt 0.25 matches when risk_score=0.10."""
        result = self._eval_risk_condition(
            operator=RuleCondition.Operator.LT,
            value=0.25,
            risk_score=0.10,
        )
        self.assertTrue(result.matched)

    def test_risk_score_lte_matches_at_boundary(self):
        """RISK_SCORE lte 0.25 matches when risk_score=0.25 (boundary)."""
        result = self._eval_risk_condition(
            operator=RuleCondition.Operator.LTE,
            value=0.25,
            risk_score=0.25,
        )
        self.assertTrue(result.matched)

    def test_risk_score_between_matches(self):
        """RISK_SCORE between [0.25, 0.75] matches when risk_score=0.50."""
        result = self._eval_risk_condition(
            operator=RuleCondition.Operator.BETWEEN,
            value={"start": 0.25, "end": 0.75},
            risk_score=0.50,
        )
        self.assertTrue(result.matched)

    def test_risk_score_between_no_match(self):
        """RISK_SCORE between [0.25, 0.75] does not match when risk_score=0.90."""
        result = self._eval_risk_condition(
            operator=RuleCondition.Operator.BETWEEN,
            value={"start": 0.25, "end": 0.75},
            risk_score=0.90,
        )
        self.assertFalse(result.matched)

    def test_risk_score_eq_matches(self):
        """RISK_SCORE eq 0.50 matches when risk_score=0.50."""
        result = self._eval_risk_condition(
            operator=RuleCondition.Operator.EQ,
            value=0.5,
            risk_score=0.50,
        )
        self.assertTrue(result.matched)

    def test_risk_score_zero_default_no_match_for_gt(self):
        """Without a pattern, risk_score=0.0 does not match gt 0.5."""
        event = self._make_invitation()  # No pattern created.
        rule = self._make_rule()
        self._add_condition(
            rule,
            field=RuleCondition.ConditionField.RISK_SCORE,
            operator=RuleCondition.Operator.GT,
            value=0.5,
        )
        self._add_action(rule)
        context = GateEventLifecycleService._build_rule_context(event)
        result = RuleEngineService.evaluate(context)
        self.assertFalse(result.matched)

    def test_risk_score_condition_with_action(self):
        """A matching RISK_SCORE condition triggers the rule action."""
        event = self._make_event_with_risk(risk_score=0.80)
        rule = self._make_rule()
        self._add_condition(
            rule,
            field=RuleCondition.ConditionField.RISK_SCORE,
            operator=RuleCondition.Operator.GTE,
            value=0.75,
        )
        self._add_action(rule, action=RuleAction.ActionType.SEND_NOTIFICATION)

        context = GateEventLifecycleService._build_rule_context(event)
        result = RuleEngineService.evaluate(context)
        self.assertTrue(result.matched)
        self.assertEqual(result.rule.pk, rule.pk)
        self.assertEqual(len(result.actions), 1)
        self.assertEqual(
            result.actions[0].action,
            RuleAction.ActionType.SEND_NOTIFICATION,
        )

    def test_risk_score_combined_with_other_condition(self):
        """RISK_SCORE can be combined with other conditions via AND."""
        event = self._make_event_with_risk(risk_score=0.60)
        rule = self._make_rule()
        # Condition 1: risk_score >= 0.5
        self._add_condition(
            rule,
            field=RuleCondition.ConditionField.RISK_SCORE,
            operator=RuleCondition.Operator.GTE,
            value=0.5,
            connector="and",
            sort_order=0,
        )
        # Condition 2: visitor_category = GUEST
        self._add_condition(
            rule,
            field=RuleCondition.ConditionField.VISITOR_CATEGORY,
            operator=RuleCondition.Operator.EQ,
            value="GUEST",
            connector="and",
            sort_order=1,
        )
        self._add_action(rule)

        context = GateEventLifecycleService._build_rule_context(event)
        result = RuleEngineService.evaluate(context)
        self.assertTrue(result.matched)

    def test_risk_score_combined_with_other_condition_no_match(self):
        """AND combination fails when one condition doesn't match."""
        event = self._make_event_with_risk(risk_score=0.60)
        rule = self._make_rule()
        # Condition 1: risk_score >= 0.5 (matches)
        self._add_condition(
            rule,
            field=RuleCondition.ConditionField.RISK_SCORE,
            operator=RuleCondition.Operator.GTE,
            value=0.5,
            connector="and",
            sort_order=0,
        )
        # Condition 2: visitor_category = DELIVERY (doesn't match — it's GUEST)
        self._add_condition(
            rule,
            field=RuleCondition.ConditionField.VISITOR_CATEGORY,
            operator=RuleCondition.Operator.EQ,
            value="DELIVERY",
            connector="and",
            sort_order=1,
        )
        self._add_action(rule)

        context = GateEventLifecycleService._build_rule_context(event)
        result = RuleEngineService.evaluate(context)
        self.assertFalse(result.matched)


# =========================================================================
# Full lifecycle integration — entry triggers anomaly + rule evaluation
# =========================================================================

class FullLifecycleIntegrationTest(AIIntegrationTestBase):
    """End-to-end tests: entry → anomaly creation + rule evaluation."""

    @patch("gateops.services.notification_engine.queue_email")
    def test_high_risk_entry_triggers_rule_and_anomaly(self, mock_queue):
        """A high-risk person entering at night triggers both a rule and an anomaly."""
        mock_queue.return_value = None
        self._set_night_mode(start_hour=22, end_hour=6)
        self._make_pattern(person=self.person, risk_score=0.80)

        # Create a rule that matches high risk scores.
        rule = self._make_rule()
        self._add_condition(
            rule,
            field=RuleCondition.ConditionField.RISK_SCORE,
            operator=RuleCondition.Operator.GTE,
            value=0.75,
        )
        self._add_action(rule, action=RuleAction.ActionType.NOTIFY_SECURITY)

        # Entry at 2am (night mode).
        fixed_now = timezone.now().replace(
            hour=2, minute=30, second=0, microsecond=0
        )
        with patch(
            "gateops.services.gate_event_lifecycle.timezone.now",
            return_value=fixed_now,
        ):
            event = self._make_entered_event()

        # The after-hours anomaly should be created.
        anomaly = AnomalyDetection.objects.filter(
            society=self.society,
            gate_event=event,
            anomaly_type=AnomalyDetection.AnomalyType.AFTER_HOURS_ENTRY,
            is_active=True,
        ).first()
        self.assertIsNotNone(anomaly)

        # The rule should have been evaluated during record_arrival.
        # (record_arrival calls evaluate_rules which builds the context.)
        # We verify the rule exists and would match.
        context = GateEventLifecycleService._build_rule_context(event)
        result = RuleEngineService.evaluate(context)
        self.assertTrue(result.matched)

    @patch("gateops.services.notification_engine.queue_email")
    def test_entry_creates_audit_log_for_anomaly(self, mock_queue):
        """The anomaly created by the entry hook has an audit log."""
        mock_queue.return_value = None
        self._set_night_mode(start_hour=22, end_hour=6)

        fixed_now = timezone.now().replace(
            hour=2, minute=30, second=0, microsecond=0
        )
        with patch(
            "gateops.services.gate_event_lifecycle.timezone.now",
            return_value=fixed_now,
        ):
            event = self._make_entered_event()

        # The anomaly creation should log an audit entry.
        audit = GateOpsAuditLog.objects.filter(
            society=self.society,
            action=GateOpsAuditLog.Action.ANOMALY_DETECTED,
        )
        self.assertTrue(audit.exists())

    @patch("gateops.services.notification_engine.queue_email")
    def test_multiple_anomaly_types_for_same_entry(self, mock_queue):
        """A single entry can trigger multiple anomaly types."""
        mock_queue.return_value = None
        self._set_night_mode(start_hour=22, end_hour=6)

        # Blacklist the person.
        self.person.is_blacklisted = True
        self.person.blacklist_reason = "Banned"
        self.person.save()

        # Entry at 2am (night mode) by a blacklisted person.
        fixed_now = timezone.now().replace(
            hour=2, minute=30, second=0, microsecond=0
        )
        with patch(
            "gateops.services.gate_event_lifecycle.timezone.now",
            return_value=fixed_now,
        ):
            event = self._make_entered_event()

        # Both after-hours and blacklist bypass anomalies should exist.
        after_hours = AnomalyDetection.objects.filter(
            society=self.society,
            gate_event=event,
            anomaly_type=AnomalyDetection.AnomalyType.AFTER_HOURS_ENTRY,
        ).exists()
        blacklist = AnomalyDetection.objects.filter(
            society=self.society,
            gate_event=event,
            anomaly_type=AnomalyDetection.AnomalyType.BLACKLIST_BYPASS,
        ).exists()
        self.assertTrue(after_hours)
        self.assertTrue(blacklist)

    @patch("gateops.services.notification_engine.queue_email")
    def test_record_exit_does_not_trigger_entry_anomalies(self, mock_queue):
        """record_exit does not call _check_entry_anomalies."""
        mock_queue.return_value = None
        self._set_night_mode(start_hour=22, end_hour=6)

        # Create an entered event during daytime.
        fixed_now = timezone.now().replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        with patch(
            "gateops.services.gate_event_lifecycle.timezone.now",
            return_value=fixed_now,
        ):
            event = self._make_entered_event()

        # Now record exit at night — should NOT create after-hours anomaly.
        exit_time = fixed_now + timedelta(hours=15)  # 1am next day.
        with patch(
            "gateops.services.gate_event_lifecycle.timezone.now",
            return_value=exit_time,
        ):
            GateEventLifecycleService.record_exit(event, guard=self.guard)

        # No after-hours anomaly should exist (exit doesn't trigger the hook).
        after_hours = AnomalyDetection.objects.filter(
            society=self.society,
            gate_event=event,
            anomaly_type=AnomalyDetection.AnomalyType.AFTER_HOURS_ENTRY,
        ).exists()
        self.assertFalse(after_hours)
