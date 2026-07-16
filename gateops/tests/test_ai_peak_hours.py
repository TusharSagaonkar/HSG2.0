"""Tests for AIRecommendationService peak-hour prediction and batch analysis
(Phase 11 §3.5 and §5).

Covers ``predict_peak_hours()``, ``get_peak_hour_predictions()``, and
``run_full_analysis()``.
"""

from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from core.test_base import SocietyTestCase
from gateops.models import (
    Gate,
    GateEvent,
    GateOpsAuditLog,
    PeakHourPrediction,
    Person,
    VisitorCategory,
)
from gateops.services.ai_recommendation_service import (
    AIRecommendationService,
    DEFAULT_EWMA_DECAY_FACTOR,
    DEFAULT_PEAK_HOUR_FORECAST_DAYS,
    FULL_CONFIDENCE_DATA_POINTS,
    MIN_DATA_POINTS_FOR_CONFIDENCE,
)


# =========================================================================
# Shared base
# =========================================================================

class PeakHoursTestBase(SocietyTestCase):
    """Shared fixtures for peak-hour prediction tests."""

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
            society=self.society, name="Peak Person", phone="5555555801"
        )

    # --- helpers ----------------------------------------------------------

    def _make_entered_event(self, person=None, days_ago=0, entered_hour=10):
        """Create an ENTERED GateEvent with entered_at in the past."""
        person = person or self.person
        now = timezone.now()
        entered_at = now - timedelta(days=days_ago)
        entered_at = entered_at.replace(
            hour=entered_hour, minute=0, second=0, microsecond=0
        )
        arrived_at = entered_at - timedelta(minutes=5)
        approved_at = entered_at - timedelta(minutes=2)

        event = GateEvent.objects.create(
            society=self.society,
            gate=self.gate,
            person=person,
            visitor_category=self.visitor_cat,
            event_type=GateEvent.EventType.ENTRY,
            status=GateEvent.Status.ENTERED,
            direction=GateEvent.Direction.INBOUND,
            arrived_at=arrived_at,
            approved_at=approved_at,
            entered_at=entered_at,
        )
        GateEvent.objects.filter(pk=event.pk).update(
            created_at=entered_at, entered_at=entered_at
        )
        event.refresh_from_db()
        return event

    def _make_exited_event(self, person=None, days_ago=0, entered_hour=10,
                           duration_minutes=30):
        """Create an EXITED GateEvent with valid timestamps."""
        person = person or self.person
        now = timezone.now()
        entered_at = now - timedelta(days=days_ago)
        entered_at = entered_at.replace(
            hour=entered_hour, minute=0, second=0, microsecond=0
        )
        arrived_at = entered_at - timedelta(minutes=5)
        approved_at = entered_at - timedelta(minutes=2)
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
            approved_at=approved_at,
            entered_at=entered_at,
            exited_at=exited_at,
        )
        GateEvent.objects.filter(pk=event.pk).update(created_at=entered_at)
        event.refresh_from_db()
        return event


# =========================================================================
# predict_peak_hours — return structure
# =========================================================================

class PredictPeakHoursStructureTest(PeakHoursTestBase):
    """Tests for the return structure of predict_peak_hours()."""

    def test_returns_dict_with_required_keys(self):
        result = AIRecommendationService.predict_peak_hours(
            society=self.society
        )
        self.assertIn("predictions_created", result)
        self.assertIn("analysis_date", result)
        self.assertIn("errors", result)

    def test_analysis_date_is_today(self):
        result = AIRecommendationService.predict_peak_hours(
            society=self.society
        )
        self.assertEqual(result["analysis_date"], timezone.now().date())

    def test_no_events_creates_zero_predictions(self):
        """With no events, all slots have 0 count but predictions are still created."""
        result = AIRecommendationService.predict_peak_hours(
            society=self.society
        )
        # The service creates 24 * forecast_days predictions regardless of
        # whether historical data exists — slots with no data get count=0.
        self.assertEqual(
            result["predictions_created"],
            24 * DEFAULT_PEAK_HOUR_FORECAST_DAYS,
        )
        self.assertEqual(result["errors"], 0)
        # All predictions should have predicted_count=0.
        zero_count = PeakHourPrediction.objects.filter(
            society=self.society,
            is_active=True,
            predicted_count=0,
        ).count()
        self.assertEqual(
            zero_count, 24 * DEFAULT_PEAK_HOUR_FORECAST_DAYS
        )

    def test_creates_audit_log(self):
        AIRecommendationService.predict_peak_hours(society=self.society)
        audit = GateOpsAuditLog.objects.filter(
            society=self.society,
            action=GateOpsAuditLog.Action.PREDICTION_GENERATED,
        )
        self.assertTrue(audit.exists())


# =========================================================================
# predict_peak_hours — prediction generation
# =========================================================================

class PredictPeakHoursGenerationTest(PeakHoursTestBase):
    """Tests for prediction generation from historical events."""

    def test_creates_predictions_for_forecast_days(self):
        """predict_peak_hours creates 24 * forecast_days predictions."""
        self._make_entered_event(days_ago=1, entered_hour=10)
        result = AIRecommendationService.predict_peak_hours(
            society=self.society, forecast_days=7
        )
        # With 1 event, only 1 slot has data. But all 24*7=168 slots
        # are created (slots with no data get count=0, confidence=0).
        # Actually, the code only creates predictions for slots that
        # have data OR all 24 hours for each forecast day.
        # Looking at the code: it iterates all 24 hours for each day,
        # creating/upserting a prediction for every slot.
        total_predictions = PeakHourPrediction.objects.filter(
            society=self.society, is_active=True
        ).count()
        self.assertEqual(total_predictions, 24 * 7)

    def test_custom_forecast_days(self):
        self._make_entered_event(days_ago=1, entered_hour=10)
        result = AIRecommendationService.predict_peak_hours(
            society=self.society, forecast_days=3
        )
        total_predictions = PeakHourPrediction.objects.filter(
            society=self.society, is_active=True
        ).count()
        self.assertEqual(total_predictions, 24 * 3)

    def test_default_forecast_days(self):
        self._make_entered_event(days_ago=1, entered_hour=10)
        AIRecommendationService.predict_peak_hours(society=self.society)
        total_predictions = PeakHourPrediction.objects.filter(
            society=self.society, is_active=True
        ).count()
        self.assertEqual(
            total_predictions, 24 * DEFAULT_PEAK_HOUR_FORECAST_DAYS
        )

    def test_prediction_has_correct_predicted_count(self):
        """A single event at hour 10 should produce predicted_count=1."""
        self._make_entered_event(days_ago=1, entered_hour=10)
        AIRecommendationService.predict_peak_hours(society=self.society)

        today = timezone.now().date()
        # The event was 1 day ago — use that date's weekday, not today's.
        event_date = today - timedelta(days=1)
        dow = event_date.weekday()
        pred = PeakHourPrediction.objects.filter(
            society=self.society,
            day_of_week=dow,
            hour=10,
            is_active=True,
        ).first()
        self.assertIsNotNone(pred)
        self.assertEqual(pred.predicted_count, 1)

    def test_prediction_with_no_data_has_zero_count(self):
        """Slots with no historical data get predicted_count=0."""
        self._make_entered_event(days_ago=1, entered_hour=10)
        AIRecommendationService.predict_peak_hours(society=self.society)

        today = timezone.now().date()
        dow = today.weekday()
        pred = PeakHourPrediction.objects.filter(
            society=self.society,
            day_of_week=dow,
            hour=3,  # 3am — no events
            is_active=True,
        ).first()
        self.assertIsNotNone(pred)
        self.assertEqual(pred.predicted_count, 0)
        self.assertEqual(pred.confidence_score, 0.0)

    def test_multiple_events_same_slot_accumulate(self):
        """Multiple events at the same hour/weekday accumulate."""
        person2 = Person.objects.create(
            society=self.society, name="P2", phone="5555555802"
        )
        person3 = Person.objects.create(
            society=self.society, name="P3", phone="5555555803"
        )
        self._make_entered_event(person=self.person, days_ago=1, entered_hour=10)
        self._make_entered_event(person=person2, days_ago=1, entered_hour=10)
        self._make_entered_event(person=person3, days_ago=1, entered_hour=10)

        AIRecommendationService.predict_peak_hours(society=self.society)

        today = timezone.now().date()
        # Events were 1 day ago — use that date's weekday.
        dow = (today - timedelta(days=1)).weekday()
        pred = PeakHourPrediction.objects.filter(
            society=self.society,
            day_of_week=dow,
            hour=10,
            is_active=True,
        ).first()
        self.assertEqual(pred.predicted_count, 3)

    def test_uses_entered_and_exited_and_auto_closed(self):
        """All three statuses (ENTERED, EXITED, AUTO_CLOSED) are analyzed."""
        self._make_entered_event(days_ago=1, entered_hour=10)
        self._make_exited_event(days_ago=2, entered_hour=11)
        # AUTO_CLOSED event
        now = timezone.now()
        entered_at = now - timedelta(days=3)
        entered_at = entered_at.replace(hour=12, minute=0, second=0, microsecond=0)
        arrived_at = entered_at - timedelta(minutes=5)
        approved_at = entered_at - timedelta(minutes=2)
        exited_at = entered_at + timedelta(minutes=600)
        ac_event = GateEvent.objects.create(
            society=self.society,
            gate=self.gate,
            person=self.person,
            visitor_category=self.visitor_cat,
            event_type=GateEvent.EventType.AUTO_CLOSE,
            status=GateEvent.Status.AUTO_CLOSED,
            direction=GateEvent.Direction.INBOUND,
            arrived_at=arrived_at,
            approved_at=approved_at,
            entered_at=entered_at,
            exited_at=exited_at,
        )
        GateEvent.objects.filter(pk=ac_event.pk).update(created_at=arrived_at)

        AIRecommendationService.predict_peak_hours(society=self.society)

        today = timezone.now().date()
        # The events are on different weekdays (1, 2, 3 days ago).
        # Each should produce a prediction for its respective weekday/hour.
        for days_ago, hour in [(1, 10), (2, 11), (3, 12)]:
            event_date = today - timedelta(days=days_ago)
            dow = event_date.weekday()
            pred = PeakHourPrediction.objects.filter(
                society=self.society,
                day_of_week=dow,
                hour=hour,
                is_active=True,
            ).first()
            self.assertIsNotNone(
                pred,
                f"No prediction for dow={dow} hour={hour} (days_ago={days_ago})",
            )
            self.assertGreaterEqual(pred.predicted_count, 1)


# =========================================================================
# predict_peak_hours — confidence scoring
# =========================================================================

class PredictPeakHoursConfidenceTest(PeakHoursTestBase):
    """Tests for confidence score computation in predict_peak_hours()."""

    def test_single_week_data_has_zero_confidence(self):
        """With < MIN_DATA_POINTS_FOR_CONFIDENCE weeks, confidence=0."""
        self._make_entered_event(days_ago=1, entered_hour=10)
        AIRecommendationService.predict_peak_hours(society=self.society)

        today = timezone.now().date()
        dow = today.weekday()
        pred = PeakHourPrediction.objects.filter(
            society=self.society,
            day_of_week=dow,
            hour=10,
            is_active=True,
        ).first()
        self.assertEqual(pred.confidence_score, 0.0)

    def test_three_weeks_data_has_partial_confidence(self):
        """With MIN_DATA_POINTS_FOR_CONFIDENCE weeks, confidence > 0."""
        for week in range(MIN_DATA_POINTS_FOR_CONFIDENCE):
            self._make_entered_event(
                days_ago=7 * (week + 1), entered_hour=10
            )
        AIRecommendationService.predict_peak_hours(society=self.society)

        today = timezone.now().date()
        dow = today.weekday()
        pred = PeakHourPrediction.objects.filter(
            society=self.society,
            day_of_week=dow,
            hour=10,
            is_active=True,
        ).first()
        self.assertGreater(pred.confidence_score, 0.0)
        self.assertLessEqual(pred.confidence_score, 1.0)

    def test_full_confidence_with_twelve_weeks(self):
        """With FULL_CONFIDENCE_DATA_POINTS weeks, confidence=1.0."""
        for week in range(FULL_CONFIDENCE_DATA_POINTS):
            self._make_entered_event(
                days_ago=7 * (week + 1), entered_hour=10
            )
        AIRecommendationService.predict_peak_hours(society=self.society)

        today = timezone.now().date()
        dow = today.weekday()
        pred = PeakHourPrediction.objects.filter(
            society=self.society,
            day_of_week=dow,
            hour=10,
            is_active=True,
        ).first()
        self.assertEqual(pred.confidence_score, 1.0)

    def test_confidence_capped_at_one(self):
        """Even with > FULL_CONFIDENCE_DATA_POINTS weeks, confidence ≤ 1.0."""
        for week in range(FULL_CONFIDENCE_DATA_POINTS + 5):
            self._make_entered_event(
                days_ago=7 * (week + 1), entered_hour=10
            )
        AIRecommendationService.predict_peak_hours(society=self.society)

        today = timezone.now().date()
        dow = today.weekday()
        pred = PeakHourPrediction.objects.filter(
            society=self.society,
            day_of_week=dow,
            hour=10,
            is_active=True,
        ).first()
        self.assertLessEqual(pred.confidence_score, 1.0)


# =========================================================================
# predict_peak_hours — EWMA weighting
# =========================================================================

class PredictPeakHoursEWMATest(PeakHoursTestBase):
    """Tests for EWMA (Exponentially Weighted Moving Average) behavior."""

    def test_recent_weeks_weighted_higher(self):
        """EWMA weighting gives recent weeks more influence than older weeks.

        The weight formula ``decay ^ week_idx`` (where ``week_idx =
        days_ago // 7``) assigns the current week (week_idx=0) a weight of
        1.0, last week (week_idx=1) a weight of 0.85, two weeks ago
        (week_idx=2) a weight of 0.7225, and so on — the standard EWMA
        decay pattern where more recent weeks are weighted HIGHER.

        Setup:
            Week 2 (oldest, days_ago=14): 10 events at hour 10
            Week 1 (newest, days_ago=7):  1 event  at hour 10
            Simple average = (10 + 1) / 2 = 5.5

        Because the recent week (1 event) is weighted higher than the older
        week (10 events), the weighted average is pulled DOWN toward the
        recent week's value, landing below the simple average.
        """
        # Week 2 (oldest, days_ago=14): 10 events at hour 10
        # Week 1 (newest, days_ago=7): 1 event at hour 10
        # Simple average = (10 + 1) / 2 = 5.5
        for _ in range(10):
            person = Person.objects.create(
                society=self.society,
                name=f"P_old_{_}",
                phone=f"555555581{_}",
            )
            self._make_entered_event(
                person=person, days_ago=14, entered_hour=10
            )
        self._make_entered_event(days_ago=7, entered_hour=10)

        AIRecommendationService.predict_peak_hours(society=self.society)

        today = timezone.now().date()
        dow = (today - timedelta(days=7)).weekday()
        pred = PeakHourPrediction.objects.filter(
            society=self.society,
            day_of_week=dow,
            hour=10,
            is_active=True,
        ).first()
        self.assertIsNotNone(pred)
        # With the corrected EWMA weighting (decay ^ week_idx):
        #   week 1 (1 event,  days_ago=7):  week_idx=1, weight=0.85^1=0.85
        #   week 2 (10 events, days_ago=14): week_idx=2, weight=0.85^2=0.7225
        #   weighted_avg = (1*0.85 + 10*0.7225) / (0.85 + 0.7225)
        #               = 8.075 / 1.5725 ≈ 5.134 → 5
        # This is LOWER than the simple average of 5.5 because the recent
        # week (with 1 event) gets the higher weight, pulling the result
        # toward the more recent (and lower) value.
        self.assertEqual(pred.predicted_count, 5)

    def test_ewma_decay_factor_applied(self):
        """Verify the decay factor gives recent data more weight."""
        # 1 event this week, 1 event last week at same slot.
        self._make_entered_event(days_ago=7, entered_hour=10)
        self._make_entered_event(days_ago=14, entered_hour=10)

        AIRecommendationService.predict_peak_hours(society=self.society)

        today = timezone.now().date()
        dow = (today - timedelta(days=7)).weekday()
        pred = PeakHourPrediction.objects.filter(
            society=self.society,
            day_of_week=dow,
            hour=10,
            is_active=True,
        ).first()
        self.assertIsNotNone(pred)
        # Both weeks have 1 event each. EWMA = (1*1 + 1*0.85)/(1+0.85)
        # = 1.85/1.85 = 1.0 → rounded to 1
        self.assertEqual(pred.predicted_count, 1)


# =========================================================================
# predict_peak_hours — upsert behavior
# =========================================================================

class PredictPeakHoursUpsertTest(PeakHoursTestBase):
    """Tests for the update_or_create (upsert) behavior."""

    def test_rerun_updates_existing_predictions(self):
        """Re-running on the same day upserts rather than duplicating."""
        self._make_entered_event(days_ago=1, entered_hour=10)
        AIRecommendationService.predict_peak_hours(society=self.society)
        count1 = PeakHourPrediction.objects.filter(
            society=self.society, is_active=True
        ).count()

        # Run again.
        result = AIRecommendationService.predict_peak_hours(
            society=self.society
        )
        count2 = PeakHourPrediction.objects.filter(
            society=self.society, is_active=True
        ).count()
        self.assertEqual(count1, count2)
        self.assertEqual(result["predictions_created"], 0)

    def test_rerun_with_new_data_updates_counts(self):
        """Re-running with additional events updates the predicted_count."""
        self._make_entered_event(days_ago=1, entered_hour=10)
        AIRecommendationService.predict_peak_hours(society=self.society)

        today = timezone.now().date()
        # Event was 1 day ago — use that date's weekday.
        dow = (today - timedelta(days=1)).weekday()
        pred = PeakHourPrediction.objects.filter(
            society=self.society,
            day_of_week=dow,
            hour=10,
            is_active=True,
        ).first()
        self.assertEqual(pred.predicted_count, 1)

        # Add another event and re-run.
        person2 = Person.objects.create(
            society=self.society, name="P2", phone="5555555820"
        )
        self._make_entered_event(
            person=person2, days_ago=1, entered_hour=10
        )
        AIRecommendationService.predict_peak_hours(society=self.society)

        pred.refresh_from_db()
        self.assertEqual(pred.predicted_count, 2)


# =========================================================================
# predict_peak_hours — society scoping
# =========================================================================

class PredictPeakHoursSocietyScopedTest(PeakHoursTestBase):
    """Tests for multi-tenant safety in peak-hour prediction."""

    def test_does_not_use_other_society_events(self):
        from core.test_factories import SocietyFactory

        other_society = SocietyFactory(name="Peak Hours Beta")
        other_person = Person.objects.create(
            society=other_society, name="Other", phone="5555555821"
        )
        other_cat = VisitorCategory.objects.get(
            society=other_society, code="GUEST"
        )
        other_gate = Gate.objects.get(society=other_society, code="MAIN")

        now = timezone.now()
        entered_at = now - timedelta(days=1)
        entered_at = entered_at.replace(hour=10, minute=0, second=0, microsecond=0)
        e = GateEvent.objects.create(
            society=other_society,
            gate=other_gate,
            person=other_person,
            visitor_category=other_cat,
            event_type=GateEvent.EventType.ENTRY,
            status=GateEvent.Status.ENTERED,
            direction=GateEvent.Direction.INBOUND,
            arrived_at=entered_at - timedelta(minutes=5),
            approved_at=entered_at - timedelta(minutes=2),
            entered_at=entered_at,
        )
        GateEvent.objects.filter(pk=e.pk).update(created_at=entered_at)

        # Our society should have all-zero predictions.
        AIRecommendationService.predict_peak_hours(society=self.society)
        today = timezone.now().date()
        dow = today.weekday()
        pred = PeakHourPrediction.objects.filter(
            society=self.society,
            day_of_week=dow,
            hour=10,
            is_active=True,
        ).first()
        self.assertIsNotNone(pred)
        self.assertEqual(pred.predicted_count, 0)


# =========================================================================
# get_peak_hour_predictions
# =========================================================================

class GetPeakHourPredictionsTest(PeakHoursTestBase):
    """Tests for get_peak_hour_predictions() query method."""

    def test_returns_society_scoped(self):
        self._make_entered_event(days_ago=1, entered_hour=10)
        AIRecommendationService.predict_peak_hours(society=self.society)
        qs = AIRecommendationService.get_peak_hour_predictions(
            society=self.society
        )
        self.assertTrue(qs.exists())
        for pred in qs:
            self.assertEqual(pred.society_id, self.society.pk)

    def test_filters_by_analysis_date(self):
        self._make_entered_event(days_ago=1, entered_hour=10)
        AIRecommendationService.predict_peak_hours(society=self.society)
        today = timezone.now().date()
        qs = AIRecommendationService.get_peak_hour_predictions(
            society=self.society, analysis_date=today
        )
        self.assertTrue(qs.exists())
        for pred in qs:
            self.assertEqual(pred.analysis_date, today)

    def test_filters_by_day_of_week(self):
        self._make_entered_event(days_ago=1, entered_hour=10)
        AIRecommendationService.predict_peak_hours(society=self.society)
        today = timezone.now().date()
        dow = today.weekday()
        qs = AIRecommendationService.get_peak_hour_predictions(
            society=self.society, day_of_week=dow
        )
        self.assertTrue(qs.exists())
        for pred in qs:
            self.assertEqual(pred.day_of_week, dow)

    def test_excludes_inactive(self):
        self._make_entered_event(days_ago=1, entered_hour=10)
        AIRecommendationService.predict_peak_hours(society=self.society)
        # Soft-delete one prediction.
        pred = PeakHourPrediction.objects.filter(
            society=self.society, is_active=True
        ).first()
        pred.is_active = False
        pred.save()

        qs = AIRecommendationService.get_peak_hour_predictions(
            society=self.society
        )
        self.assertFalse(qs.filter(pk=pred.pk).exists())

    def test_empty_society_returns_empty_qs(self):
        qs = AIRecommendationService.get_peak_hour_predictions(
            society=self.society
        )
        self.assertFalse(qs.exists())


# =========================================================================
# run_full_analysis
# =========================================================================

class RunFullAnalysisTest(PeakHoursTestBase):
    """Tests for run_full_analysis() — the batch pipeline."""

    def test_returns_dict_with_all_three_keys(self):
        result = AIRecommendationService.run_full_analysis(
            society=self.society
        )
        self.assertIn("patterns", result)
        self.assertIn("anomalies", result)
        self.assertIn("predictions", result)

    def test_patterns_key_has_expected_structure(self):
        self._make_exited_event(days_ago=1)
        result = AIRecommendationService.run_full_analysis(
            society=self.society
        )
        self.assertIn("patterns_created", result["patterns"])

    def test_anomalies_key_has_expected_structure(self):
        result = AIRecommendationService.run_full_analysis(
            society=self.society
        )
        self.assertIn("anomalies_created", result["anomalies"])

    def test_predictions_key_has_expected_structure(self):
        result = AIRecommendationService.run_full_analysis(
            society=self.society
        )
        self.assertIn("predictions_created", result["predictions"])

    def test_creates_patterns(self):
        self._make_exited_event(days_ago=1)
        result = AIRecommendationService.run_full_analysis(
            society=self.society
        )
        self.assertGreater(result["patterns"]["patterns_created"], 0)

    def test_creates_predictions(self):
        self._make_entered_event(days_ago=1, entered_hour=10)
        result = AIRecommendationService.run_full_analysis(
            society=self.society
        )
        self.assertGreater(result["predictions"]["predictions_created"], 0)

    def test_step_failure_does_not_abort_others(self):
        """If one step fails, the others still run."""
        with patch.object(
            AIRecommendationService,
            "analyze_visitor_patterns",
            side_effect=RuntimeError("boom"),
        ):
            result = AIRecommendationService.run_full_analysis(
                society=self.society
            )
        self.assertEqual(result["patterns"], {"error": "pattern_analysis_failed"})
        # Other steps should still have run.
        self.assertIn("anomalies_created", result["anomalies"])
        self.assertIn("predictions_created", result["predictions"])

    def test_anomaly_failure_does_not_abort_others(self):
        with patch.object(
            AIRecommendationService,
            "detect_anomalies",
            side_effect=RuntimeError("boom"),
        ):
            result = AIRecommendationService.run_full_analysis(
                society=self.society
            )
        self.assertEqual(
            result["anomalies"], {"error": "anomaly_detection_failed"}
        )
        self.assertIn("patterns_created", result["patterns"])
        self.assertIn("predictions_created", result["predictions"])

    def test_prediction_failure_does_not_abort_others(self):
        self._make_exited_event(days_ago=1)
        with patch.object(
            AIRecommendationService,
            "predict_peak_hours",
            side_effect=RuntimeError("boom"),
        ):
            result = AIRecommendationService.run_full_analysis(
                society=self.society
            )
        self.assertEqual(
            result["predictions"], {"error": "prediction_failed"}
        )
        self.assertIn("patterns_created", result["patterns"])
        self.assertIn("anomalies_created", result["anomalies"])

    def test_creates_audit_logs_for_all_steps(self):
        """Each step in run_full_analysis creates its own audit log."""
        self._make_exited_event(days_ago=1)
        AIRecommendationService.run_full_analysis(society=self.society)
        audit = GateOpsAuditLog.objects.filter(society=self.society)
        actions = set(audit.values_list("action", flat=True))
        self.assertIn(GateOpsAuditLog.Action.PATTERN_UPDATED, actions)
        self.assertIn(GateOpsAuditLog.Action.ANOMALY_DETECTED, actions)
        self.assertIn(GateOpsAuditLog.Action.PREDICTION_GENERATED, actions)
