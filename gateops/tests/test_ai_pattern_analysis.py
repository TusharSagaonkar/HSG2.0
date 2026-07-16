"""Tests for AIRecommendationService.analyze_visitor_patterns() (Phase 11 §3.2).

Covers: pattern detection, frequency score, typical days/time, avg duration,
is_frequent, upsert (create then update), empty data, batch processing,
risk level assignment, and audit logging.
"""

from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from core.test_base import SocietyTestCase
from core.test_factories import SocietyFactory
from gateops.models import (
    Gate,
    GateEvent,
    GateOpsAuditLog,
    Person,
    VisitorCategory,
    VisitorPattern,
)
from gateops.services.ai_recommendation_service import AIRecommendationService


class AnalyzeVisitorPatternsTest(SocietyTestCase):
    """Service-level tests for analyze_visitor_patterns()."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.visitor_cat = VisitorCategory.objects.get(
            society=cls.society, code="GUEST"
        )
        cls.gate = Gate.objects.get(society=cls.society, code="MAIN")

    def setUp(self):
        super().setUp()
        self.person = Person.objects.create(
            society=self.society, name="Frequent Visitor", phone="5555555501"
        )

    # --- helpers ----------------------------------------------------------

    def _make_completed_event(self, person=None, days_ago=0, duration_minutes=30,
                              arrived_hour=10, **overrides):
        """Create an EXITED GateEvent with valid timestamps.

        Uses QuerySet.update() to set ``created_at`` to the arrival time so
        that the service's ``created_at__gte`` window filter and span-day
        calculations work correctly (``created_at`` is ``auto_now_add``).
        """
        person = person or self.person
        now = timezone.now()
        arrived_at = now - timedelta(days=days_ago, hours=0)
        # Set the arrival hour precisely.
        arrived_at = arrived_at.replace(hour=arrived_hour, minute=0, second=0, microsecond=0)
        entered_at = arrived_at + timedelta(minutes=5)
        exited_at = entered_at + timedelta(minutes=duration_minutes)

        event = GateEvent.objects.create(
            society=self.society,
            gate=self.gate,
            person=person,
            visitor_category=self.visitor_cat,
            event_type=GateEvent.EventType.EXIT,
            status=GateEvent.Status.EXITED,
            direction=GateEvent.Direction.INBOUND,
            arrived_at=arrived_at,
            entered_at=entered_at,
            exited_at=exited_at,
            **overrides,
        )
        # Override auto_now_add so created_at reflects the actual visit time.
        GateEvent.objects.filter(pk=event.pk).update(created_at=arrived_at)
        event.refresh_from_db()
        return event

    def _make_auto_closed_event(self, person=None, days_ago=0, duration_minutes=600):
        """Create an AUTO_CLOSED GateEvent.

        Uses QuerySet.update() to set ``created_at`` to the arrival time so
        that the service's ``created_at__gte`` window filter works correctly.
        """
        person = person or self.person
        now = timezone.now()
        arrived_at = now - timedelta(days=days_ago)
        arrived_at = arrived_at.replace(hour=10, minute=0, second=0, microsecond=0)
        entered_at = arrived_at + timedelta(minutes=5)
        exited_at = entered_at + timedelta(minutes=duration_minutes)

        event = GateEvent.objects.create(
            society=self.society,
            gate=self.gate,
            person=person,
            visitor_category=self.visitor_cat,
            event_type=GateEvent.EventType.AUTO_CLOSE,
            status=GateEvent.Status.AUTO_CLOSED,
            direction=GateEvent.Direction.INBOUND,
            arrived_at=arrived_at,
            entered_at=entered_at,
            exited_at=exited_at,
        )
        # Override auto_now_add so created_at reflects the actual visit time.
        GateEvent.objects.filter(pk=event.pk).update(created_at=arrived_at)
        event.refresh_from_db()
        return event

    # --- basic pattern creation ------------------------------------------

    def test_creates_pattern_for_person_with_completed_events(self):
        self._make_completed_event(days_ago=1, duration_minutes=30)
        self._make_completed_event(days_ago=2, duration_minutes=45)

        result = AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        self.assertEqual(result["patterns_created"], 1)
        self.assertEqual(result["patterns_updated"], 0)
        self.assertEqual(result["errors"], 0)

        pattern = VisitorPattern.objects.get(
            society=self.society, person=self.person, is_active=True
        )
        self.assertEqual(pattern.visit_count, 2)
        self.assertIsNotNone(pattern.first_visit_at)
        self.assertIsNotNone(pattern.last_visit_at)

    def test_creates_pattern_with_visit_count(self):
        for i in range(3):
            self._make_completed_event(days_ago=i + 1)
        AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        pattern = VisitorPattern.objects.get(
            society=self.society, person=self.person
        )
        self.assertEqual(pattern.visit_count, 3)

    def test_creates_pattern_with_avg_duration(self):
        self._make_completed_event(days_ago=1, duration_minutes=30)
        self._make_completed_event(days_ago=2, duration_minutes=60)
        AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        pattern = VisitorPattern.objects.get(
            society=self.society, person=self.person
        )
        # Average of 30 and 60 = 45 minutes.
        self.assertEqual(pattern.avg_visit_duration_minutes, 45)

    def test_creates_pattern_with_typical_days(self):
        # Create events on the same weekday so it accounts for >= 20%.
        for i in range(5):
            self._make_completed_event(days_ago=i * 7 + 1)
        AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        pattern = VisitorPattern.objects.get(
            society=self.society, person=self.person
        )
        self.assertIsInstance(pattern.typical_visit_days, list)

    def test_creates_pattern_with_typical_time_window(self):
        # Need >= 3 events with arrived_at for a time window.
        for i in range(4):
            self._make_completed_event(days_ago=i + 1, arrived_hour=10)
        AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        pattern = VisitorPattern.objects.get(
            society=self.society, person=self.person
        )
        self.assertIsInstance(pattern.typical_time_window, dict)
        if pattern.typical_time_window:
            self.assertIn("start", pattern.typical_time_window)
            self.assertIn("end", pattern.typical_time_window)

    def test_typical_time_window_empty_for_fewer_than_three_events(self):
        self._make_completed_event(days_ago=1, arrived_hour=10)
        self._make_completed_event(days_ago=2, arrived_hour=10)
        AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        pattern = VisitorPattern.objects.get(
            society=self.society, person=self.person
        )
        self.assertEqual(pattern.typical_time_window, {})

    # --- frequency score & is_frequent -----------------------------------

    def test_frequency_score_in_valid_range(self):
        for i in range(5):
            self._make_completed_event(days_ago=i + 1)
        AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        pattern = VisitorPattern.objects.get(
            society=self.society, person=self.person
        )
        self.assertGreaterEqual(pattern.frequency_score, 0.0)
        self.assertLessEqual(pattern.frequency_score, 1.0)

    def test_is_frequent_true_for_repeated_visits(self):
        # 5+ visits spanning >= 7 days → is_frequent=True.
        for i in range(6):
            self._make_completed_event(days_ago=i * 2 + 1)
        AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        pattern = VisitorPattern.objects.get(
            society=self.society, person=self.person
        )
        self.assertTrue(pattern.is_frequent)

    def test_is_frequent_false_for_few_visits(self):
        self._make_completed_event(days_ago=1)
        AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        pattern = VisitorPattern.objects.get(
            society=self.society, person=self.person
        )
        self.assertFalse(pattern.is_frequent)

    # --- upsert (create then update) -------------------------------------

    def test_upsert_creates_then_updates(self):
        self._make_completed_event(days_ago=1, duration_minutes=30)
        result1 = AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        self.assertEqual(result1["patterns_created"], 1)
        self.assertEqual(result1["patterns_updated"], 0)

        # Add another event and re-run.
        self._make_completed_event(days_ago=2, duration_minutes=60)
        result2 = AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        self.assertEqual(result2["patterns_created"], 0)
        self.assertEqual(result2["patterns_updated"], 1)

        # Only one pattern row should exist.
        self.assertEqual(
            VisitorPattern.objects.filter(
                society=self.society, person=self.person, is_active=True
            ).count(),
            1,
        )
        pattern = VisitorPattern.objects.get(
            society=self.society, person=self.person
        )
        self.assertEqual(pattern.visit_count, 2)

    def test_upsert_updates_last_analyzed_at(self):
        self._make_completed_event(days_ago=1)
        AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        pattern = VisitorPattern.objects.get(
            society=self.society, person=self.person
        )
        first_analyzed = pattern.last_analyzed_at
        self.assertIsNotNone(first_analyzed)

        # Re-run after a tick.
        import time as _time
        _time.sleep(0.01)
        AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        pattern.refresh_from_db()
        self.assertGreaterEqual(pattern.last_analyzed_at, first_analyzed)

    # --- empty data -------------------------------------------------------

    def test_no_events_returns_zero_patterns(self):
        result = AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        self.assertEqual(result["patterns_created"], 0)
        self.assertEqual(result["patterns_updated"], 0)
        self.assertFalse(
            VisitorPattern.objects.filter(
                society=self.society, person=self.person
            ).exists()
        )

    def test_only_invited_events_not_analyzed(self):
        """Events in INVITED/ARRIVED/APPROVED/ENTERED status are not analyzed."""
        GateEvent.objects.create(
            society=self.society,
            gate=self.gate,
            person=self.person,
            visitor_category=self.visitor_cat,
            event_type=GateEvent.EventType.INVITATION,
            status=GateEvent.Status.INVITED,
            direction=GateEvent.Direction.INBOUND,
        )
        result = AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        self.assertEqual(result["patterns_created"], 0)

    # --- batch processing (all persons) ----------------------------------

    def test_batch_analysis_all_persons(self):
        person_a = Person.objects.create(
            society=self.society, name="Person A", phone="5555555502"
        )
        person_b = Person.objects.create(
            society=self.society, name="Person B", phone="5555555503"
        )
        self._make_completed_event(person=person_a, days_ago=1)
        self._make_completed_event(person=person_b, days_ago=1)

        result = AIRecommendationService.analyze_visitor_patterns(
            society=self.society
        )
        self.assertEqual(result["patterns_created"], 2)
        self.assertEqual(result["errors"], 0)

    def test_batch_analysis_society_scoped(self):
        """Patterns are only created for persons in the specified society."""
        other_society = SocietyFactory(name="Test Society Beta")
        other_cat = VisitorCategory.objects.get(society=other_society, code="GUEST")
        other_gate = Gate.objects.get(society=other_society, code="MAIN")
        other_person = Person.objects.create(
            society=other_society, name="Other Person", phone="5555555504"
        )
        now = timezone.now()
        arrived = now - timedelta(days=1)
        GateEvent.objects.create(
            society=other_society,
            gate=other_gate,
            person=other_person,
            visitor_category=other_cat,
            event_type=GateEvent.EventType.EXIT,
            status=GateEvent.Status.EXITED,
            direction=GateEvent.Direction.INBOUND,
            arrived_at=arrived,
            entered_at=arrived + timedelta(minutes=5),
            exited_at=arrived + timedelta(minutes=35),
        )

        result = AIRecommendationService.analyze_visitor_patterns(
            society=self.society
        )
        # No patterns for self.society (no events for self.person).
        self.assertEqual(result["patterns_created"], 0)
        # No pattern leaked from other_society.
        self.assertFalse(
            VisitorPattern.objects.filter(person=other_person).exists()
        )

    # --- risk level -------------------------------------------------------

    def test_pattern_has_valid_risk_level(self):
        self._make_completed_event(days_ago=1)
        AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        pattern = VisitorPattern.objects.get(
            society=self.society, person=self.person
        )
        self.assertIn(
            pattern.risk_level,
            [
                VisitorPattern.RiskLevel.LOW,
                VisitorPattern.RiskLevel.MEDIUM,
                VisitorPattern.RiskLevel.HIGH,
                VisitorPattern.RiskLevel.CRITICAL,
            ],
        )

    def test_pattern_risk_level_matches_risk_score(self):
        self._make_completed_event(days_ago=1)
        AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        pattern = VisitorPattern.objects.get(
            society=self.society, person=self.person
        )
        expected = VisitorPattern._risk_level_for_score(pattern.risk_score)
        self.assertEqual(pattern.risk_level, expected)

    def test_pattern_risk_score_in_valid_range(self):
        self._make_completed_event(days_ago=1)
        AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        pattern = VisitorPattern.objects.get(
            society=self.society, person=self.person
        )
        self.assertGreaterEqual(pattern.risk_score, 0.0)
        self.assertLessEqual(pattern.risk_score, 1.0)

    # --- auto_closed events included -------------------------------------

    def test_auto_closed_events_are_analyzed(self):
        self._make_auto_closed_event(days_ago=1, duration_minutes=600)
        result = AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        self.assertEqual(result["patterns_created"], 1)
        pattern = VisitorPattern.objects.get(
            society=self.society, person=self.person
        )
        self.assertEqual(pattern.visit_count, 1)

    # --- audit logging ----------------------------------------------------

    def test_creates_audit_log(self):
        self._make_completed_event(days_ago=1)
        AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person, actor=self.user
        )
        self.assertTrue(
            GateOpsAuditLog.objects.filter(
                society=self.society,
                entity_type="VisitorPattern",
                action=GateOpsAuditLog.Action.PATTERN_UPDATED,
            ).exists()
        )

    def test_audit_failure_does_not_block_analysis(self):
        self._make_completed_event(days_ago=1)
        with patch.object(
            GateOpsAuditLog,
            "log",
            side_effect=Exception("Audit DB down"),
        ):
            result = AIRecommendationService.analyze_visitor_patterns(
                society=self.society, person=self.person
            )
        # Pattern still created despite audit failure.
        self.assertEqual(result["patterns_created"], 1)
        self.assertTrue(
            VisitorPattern.objects.filter(
                society=self.society, person=self.person
            ).exists()
        )

    # --- get_visitor_pattern / list_visitor_patterns ---------------------

    def test_get_visitor_pattern_returns_active(self):
        self._make_completed_event(days_ago=1)
        AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        pattern = AIRecommendationService.get_visitor_pattern(
            society=self.society, person=self.person
        )
        self.assertIsNotNone(pattern)
        self.assertEqual(pattern.person, self.person)

    def test_get_visitor_pattern_returns_none_when_no_pattern(self):
        pattern = AIRecommendationService.get_visitor_pattern(
            society=self.society, person=self.person
        )
        self.assertIsNone(pattern)

    def test_list_visitor_patterns_returns_society_scoped(self):
        self._make_completed_event(days_ago=1)
        AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        patterns = AIRecommendationService.list_visitor_patterns(
            society=self.society
        )
        self.assertEqual(patterns.count(), 1)

    def test_list_visitor_patterns_excludes_inactive_by_default(self):
        self._make_completed_event(days_ago=1)
        AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        VisitorPattern.objects.filter(
            society=self.society, person=self.person
        ).update(is_active=False)
        patterns = AIRecommendationService.list_visitor_patterns(
            society=self.society
        )
        self.assertEqual(patterns.count(), 0)

    def test_list_visitor_patterns_include_inactive(self):
        self._make_completed_event(days_ago=1)
        AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        VisitorPattern.objects.filter(
            society=self.society, person=self.person
        ).update(is_active=False)
        patterns = AIRecommendationService.list_visitor_patterns(
            society=self.society, include_inactive=True
        )
        self.assertEqual(patterns.count(), 1)

    def test_list_visitor_patterns_filter_by_is_frequent(self):
        for i in range(6):
            self._make_completed_event(days_ago=i * 2 + 1)
        AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        frequent = AIRecommendationService.list_visitor_patterns(
            society=self.society, is_frequent=True
        )
        self.assertEqual(frequent.count(), 1)

    def test_list_visitor_patterns_filter_by_risk_level(self):
        self._make_completed_event(days_ago=1)
        AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person
        )
        pattern = VisitorPattern.objects.get(
            society=self.society, person=self.person
        )
        filtered = AIRecommendationService.list_visitor_patterns(
            society=self.society, risk_level=pattern.risk_level
        )
        self.assertEqual(filtered.count(), 1)

    # --- custom analysis window ------------------------------------------

    def test_custom_days_window(self):
        # Event 5 days ago; window of 3 days should exclude it.
        self._make_completed_event(days_ago=5)
        result = AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person, days=3
        )
        self.assertEqual(result["patterns_created"], 0)

    def test_custom_days_window_includes_recent(self):
        self._make_completed_event(days_ago=2)
        result = AIRecommendationService.analyze_visitor_patterns(
            society=self.society, person=self.person, days=5
        )
        self.assertEqual(result["patterns_created"], 1)
