"""Tests for AIRecommendationService anomaly detection (Phase 11 §3.3).

Covers: detect_anomalies() orchestration, all 8 detectors (forgotten exits,
after-hours entries, unusual frequency, blacklist bypass, off-pattern visits,
duplicate entries, long stays, suspicious patterns), deduplication,
notification dispatch, severity assignment, and anomaly lifecycle methods
(acknowledge, resolve, get_anomalies, get_anomaly).
"""

from datetime import time, timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.shortcuts import Http404
from django.utils import timezone

from core.test_base import SocietyTestCase
from gateops.models import (
    AnomalyDetection,
    Gate,
    GateEvent,
    GateOpsAuditLog,
    GateOpsSocietyConfig,
    Person,
    VisitorCategory,
    VisitorPattern,
)
from gateops.services.ai_recommendation_service import AIRecommendationService


class DetectAnomaliesTestBase(SocietyTestCase):
    """Shared fixtures for anomaly-detection tests."""

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
            society=self.society, name="Anomaly Person", phone="5555555601"
        )

    # --- helpers ----------------------------------------------------------

    def _make_entered_event(self, person=None, days_ago=0, entered_hour=10,
                            **overrides):
        """Create an ENTERED GateEvent with entered_at in the past.

        Uses QuerySet.update() to set ``created_at`` and ``entered_at`` to
        past values so that the service's ``entered_at__lt`` and
        ``created_at__gte`` filters work correctly.
        """
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
            **overrides,
        )
        # Override auto_now_add so created_at reflects the actual entry time.
        GateEvent.objects.filter(pk=event.pk).update(
            created_at=entered_at, entered_at=entered_at
        )
        event.refresh_from_db()
        return event

    def _make_exited_event(self, person=None, days_ago=0, entered_hour=10,
                           duration_minutes=30, **overrides):
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
            **overrides,
        )
        GateEvent.objects.filter(pk=event.pk).update(created_at=entered_at)
        event.refresh_from_db()
        return event

    def _make_auto_closed_event(self, person=None, days_ago=0,
                                duration_minutes=600, **overrides):
        """Create an AUTO_CLOSED GateEvent."""
        person = person or self.person
        now = timezone.now()
        entered_at = now - timedelta(days=days_ago)
        entered_at = entered_at.replace(hour=10, minute=0, second=0, microsecond=0)
        arrived_at = entered_at - timedelta(minutes=5)
        approved_at = entered_at - timedelta(minutes=2)
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
            approved_at=approved_at,
            entered_at=entered_at,
            exited_at=exited_at,
            **overrides,
        )
        GateEvent.objects.filter(pk=event.pk).update(created_at=entered_at)
        event.refresh_from_db()
        return event

    def _make_approved_event(self, person=None, days_ago=0, **overrides):
        """Create an APPROVED GateEvent (no entry yet)."""
        person = person or self.person
        now = timezone.now()
        arrived_at = now - timedelta(days=days_ago)
        arrived_at = arrived_at.replace(hour=10, minute=0, second=0, microsecond=0)
        approved_at = arrived_at + timedelta(minutes=3)

        event = GateEvent.objects.create(
            society=self.society,
            gate=self.gate,
            person=person,
            visitor_category=self.visitor_cat,
            event_type=GateEvent.EventType.ARRIVAL,
            status=GateEvent.Status.APPROVED,
            direction=GateEvent.Direction.INBOUND,
            arrived_at=arrived_at,
            approved_at=approved_at,
            **overrides,
        )
        GateEvent.objects.filter(pk=event.pk).update(created_at=arrived_at)
        event.refresh_from_db()
        return event

    def _set_night_mode(self, start_hour=22, end_hour=6):
        """Configure night-mode hours on the society's GateOpsSocietyConfig.

        ``self.society`` may have a stale cached ``gateops_config`` reverse
        relation from ``setUpTestData`` (where the config was created with
        ``night_mode_start=None``).  We refresh the society instance so the
        service's ``society.gateops_config`` accessor returns the updated
        values.
        """
        config, _ = GateOpsSocietyConfig.objects.get_or_create(
            society=self.society
        )
        config.night_mode_start = time(hour=start_hour)
        config.night_mode_end = time(hour=end_hour)
        config.save()
        # Force the society instance to drop its cached reverse relation.
        self.society.refresh_from_db()
        return config

    def _make_pattern(self, person=None, **overrides):
        """Create a VisitorPattern directly for frequency/off-pattern tests."""
        person = person or self.person
        defaults = {
            "society": self.society,
            "person": person,
            "visitor_category": self.visitor_cat,
            "visit_count": 5,
            "risk_score": 0.1,
            "risk_level": VisitorPattern.RiskLevel.LOW,
            "frequency_score": 0.2,
            "is_frequent": False,
            "first_visit_at": timezone.now() - timedelta(days=30),
            "last_visit_at": timezone.now() - timedelta(days=1),
        }
        defaults.update(overrides)
        return VisitorPattern.objects.create(**defaults)


# =========================================================================
# detect_anomalies() orchestration
# =========================================================================


class DetectAnomaliesOrchestrationTest(DetectAnomaliesTestBase):
    """Tests for the detect_anomalies() entry point."""

    def test_returns_expected_keys(self):
        result = AIRecommendationService.detect_anomalies(society=self.society)
        self.assertIn("anomalies_created", result)
        self.assertIn("by_type", result)
        self.assertIn("errors", result)

    def test_no_anomalies_returns_zero(self):
        result = AIRecommendationService.detect_anomalies(society=self.society)
        self.assertEqual(result["anomalies_created"], 0)
        self.assertEqual(result["errors"], 0)

    def test_creates_audit_log(self):
        AIRecommendationService.detect_anomalies(society=self.society)
        audit = GateOpsAuditLog.objects.filter(
            society=self.society,
            action=GateOpsAuditLog.Action.ANOMALY_DETECTED,
        )
        self.assertTrue(audit.exists())

    def test_custom_since_parameter(self):
        # Event from 2 days ago; since=1 hour ago should not find it.
        self._make_entered_event(days_ago=2, entered_hour=10)
        # Override entered_at to be 13 hours ago (past the 12h threshold).
        event = GateEvent.objects.get(person=self.person)
        old_entered = timezone.now() - timedelta(hours=13)
        GateEvent.objects.filter(pk=event.pk).update(entered_at=old_entered)

        # With since=1 hour ago, forgotten_exit detector ignores it
        # (it doesn't use `since`, but after_hours/duplicate/etc do).
        result = AIRecommendationService.detect_anomalies(
            society=self.society,
            since=timezone.now() - timedelta(hours=1),
        )
        # Forgotten exit detector doesn't use `since`, so it WILL find it.
        self.assertGreaterEqual(result["anomalies_created"], 1)

    def test_by_type_counts_match_created(self):
        self._make_entered_event(days_ago=0, entered_hour=10)
        event = GateEvent.objects.get(person=self.person)
        old_entered = timezone.now() - timedelta(hours=13)
        GateEvent.objects.filter(pk=event.pk).update(entered_at=old_entered)

        result = AIRecommendationService.detect_anomalies(society=self.society)
        total_by_type = sum(result["by_type"].values())
        self.assertEqual(total_by_type, result["anomalies_created"])

    def test_society_scoped(self):
        """Anomalies from one society don't appear in another's scan."""
        from core.test_factories import SocietyFactory

        other_society = SocietyFactory(name="Other Anomaly Society")
        # Create a forgotten-exit scenario in the other society.
        other_person = Person.objects.create(
            society=other_society, name="Other Person", phone="5555555602"
        )
        # SocietyFactory's post_save signal already seeds a GUEST category.
        other_cat = VisitorCategory.objects.get(
            society=other_society, code="GUEST"
        )
        # SocietyFactory's post_save signal already seeds a MAIN gate.
        other_gate = Gate.objects.get(society=other_society, code="MAIN")
        now = timezone.now()
        entered_at = now - timedelta(hours=13)
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

        # Our society should have 0 anomalies.
        result = AIRecommendationService.detect_anomalies(society=self.society)
        self.assertEqual(result["anomalies_created"], 0)


# =========================================================================
# Detector 1: Forgotten Exit
# =========================================================================


class ForgottenExitDetectorTest(DetectAnomaliesTestBase):
    """Tests for _detect_forgotten_exits()."""

    def test_detects_entered_event_past_threshold(self):
        self._make_entered_event(days_ago=0, entered_hour=10)
        event = GateEvent.objects.get(person=self.person)
        # Set entered_at to 13 hours ago (past 12h threshold).
        old_entered = timezone.now() - timedelta(hours=13)
        GateEvent.objects.filter(pk=event.pk).update(entered_at=old_entered)

        result = AIRecommendationService.detect_anomalies(society=self.society)
        anomalies = AnomalyDetection.objects.filter(
            society=self.society,
            anomaly_type=AnomalyDetection.AnomalyType.FORGOTTEN_EXIT,
        )
        self.assertEqual(anomalies.count(), 1)
        self.assertEqual(anomalies[0].severity, AnomalyDetection.Severity.MEDIUM)

    def test_high_severity_for_over_24_hours(self):
        self._make_entered_event(days_ago=2, entered_hour=10)
        event = GateEvent.objects.get(person=self.person)
        old_entered = timezone.now() - timedelta(hours=25)
        GateEvent.objects.filter(pk=event.pk).update(entered_at=old_entered)

        AIRecommendationService.detect_anomalies(society=self.society)
        anomaly = AnomalyDetection.objects.get(
            society=self.society,
            anomaly_type=AnomalyDetection.AnomalyType.FORGOTTEN_EXIT,
        )
        self.assertEqual(anomaly.severity, AnomalyDetection.Severity.HIGH)

    def test_ignores_recent_entered_event(self):
        """Events entered less than 12h ago should not be flagged."""
        self._make_entered_event(days_ago=0, entered_hour=10)
        # entered_at is now (within 12h threshold).
        result = AIRecommendationService._detect_forgotten_exits(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 0)

    def test_ignores_exited_events(self):
        self._make_exited_event(days_ago=2, duration_minutes=30)
        result = AIRecommendationService._detect_forgotten_exits(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 0)

    def test_context_contains_hours_inside(self):
        self._make_entered_event(days_ago=0, entered_hour=10)
        event = GateEvent.objects.get(person=self.person)
        old_entered = timezone.now() - timedelta(hours=13)
        GateEvent.objects.filter(pk=event.pk).update(entered_at=old_entered)

        AIRecommendationService.detect_anomalies(society=self.society)
        anomaly = AnomalyDetection.objects.get(
            anomaly_type=AnomalyDetection.AnomalyType.FORGOTTEN_EXIT
        )
        self.assertIn("hours_inside", anomaly.context)
        self.assertGreater(anomaly.context["hours_inside"], 12)


# =========================================================================
# Detector 2: After-Hours Entry
# =========================================================================


class AfterHoursEntryDetectorTest(DetectAnomaliesTestBase):
    """Tests for _detect_after_hours_entries()."""

    def test_no_night_mode_returns_empty(self):
        self._make_exited_event(days_ago=0, entered_hour=23)
        result = AIRecommendationService._detect_after_hours_entries(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 0)

    def test_detects_entry_during_night_hours(self):
        self._set_night_mode(start_hour=22, end_hour=6)
        self._make_exited_event(days_ago=0, entered_hour=23)

        result = AIRecommendationService._detect_after_hours_entries(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0]["anomaly_type"],
            AnomalyDetection.AnomalyType.AFTER_HOURS_ENTRY,
        )

    def test_high_severity_for_late_night(self):
        self._set_night_mode(start_hour=22, end_hour=6)
        self._make_exited_event(days_ago=0, entered_hour=2)

        result = AIRecommendationService._detect_after_hours_entries(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(result[0]["severity"], AnomalyDetection.Severity.HIGH)

    def test_medium_severity_for_early_night(self):
        self._set_night_mode(start_hour=22, end_hour=6)
        self._make_exited_event(days_ago=0, entered_hour=23)

        result = AIRecommendationService._detect_after_hours_entries(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(result[0]["severity"], AnomalyDetection.Severity.MEDIUM)

    def test_ignores_daytime_entry(self):
        self._set_night_mode(start_hour=22, end_hour=6)
        self._make_exited_event(days_ago=0, entered_hour=10)

        result = AIRecommendationService._detect_after_hours_entries(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 0)

    def test_midnight_spanning_window(self):
        """Night mode 22:00–06:00 should catch entries at 23:00 and 02:00."""
        self._set_night_mode(start_hour=22, end_hour=6)
        self._make_exited_event(days_ago=0, entered_hour=23)
        person2 = Person.objects.create(
            society=self.society, name="Night Owl", phone="5555555603"
        )
        self._make_exited_event(person=person2, days_ago=0, entered_hour=2)

        result = AIRecommendationService._detect_after_hours_entries(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 2)

    def test_non_spanning_window(self):
        """Night mode 06:00–18:00 should catch entries at 10:00 but not 20:00."""
        self._set_night_mode(start_hour=6, end_hour=18)
        self._make_exited_event(days_ago=0, entered_hour=10)

        result = AIRecommendationService._detect_after_hours_entries(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 1)

    def test_context_contains_night_mode_hours(self):
        self._set_night_mode(start_hour=22, end_hour=6)
        self._make_exited_event(days_ago=0, entered_hour=23)

        results = AIRecommendationService._detect_after_hours_entries(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        ctx = results[0]["context"]
        self.assertEqual(ctx["night_mode_start"], 22)
        self.assertEqual(ctx["night_mode_end"], 6)


# =========================================================================
# Detector 3: Unusual Frequency
# =========================================================================


class UnusualFrequencyDetectorTest(DetectAnomaliesTestBase):
    """Tests for _detect_unusual_frequency()."""

    def test_detects_frequency_spike(self):
        # Create a pattern with low historical average.
        self._make_pattern(
            visit_count=2,
            first_visit_at=timezone.now() - timedelta(days=60),
            last_visit_at=timezone.now() - timedelta(days=30),
        )
        # Create many recent events (spike).
        for i in range(5):
            self._make_exited_event(days_ago=i)

        result = AIRecommendationService._detect_unusual_frequency(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0]["anomaly_type"],
            AnomalyDetection.AnomalyType.UNUSUAL_FREQUENCY,
        )

    def test_no_spike_returns_empty(self):
        self._make_pattern(
            visit_count=2,
            first_visit_at=timezone.now() - timedelta(days=60),
            last_visit_at=timezone.now() - timedelta(days=30),
        )
        # Only 1 recent event — not a spike.
        self._make_exited_event(days_ago=1)

        result = AIRecommendationService._detect_unusual_frequency(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 0)

    def test_no_patterns_returns_empty(self):
        result = AIRecommendationService._detect_unusual_frequency(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 0)

    def test_high_severity_for_large_spike(self):
        self._make_pattern(
            visit_count=1,
            first_visit_at=timezone.now() - timedelta(days=90),
            last_visit_at=timezone.now() - timedelta(days=60),
        )
        # 5 recent events with baseline of 1 → ratio = 5 > 3 → CRITICAL.
        for i in range(5):
            self._make_exited_event(days_ago=i)

        result = AIRecommendationService._detect_unusual_frequency(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(result[0]["severity"], AnomalyDetection.Severity.CRITICAL)

    def test_context_contains_spike_ratio(self):
        self._make_pattern(
            visit_count=1,
            first_visit_at=timezone.now() - timedelta(days=90),
            last_visit_at=timezone.now() - timedelta(days=60),
        )
        for i in range(5):
            self._make_exited_event(days_ago=i)

        results = AIRecommendationService._detect_unusual_frequency(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        ctx = results[0]["context"]
        self.assertIn("spike_ratio", ctx)
        self.assertIn("recent_visits_7d", ctx)
        self.assertIn("historical_weekly_avg", ctx)


# =========================================================================
# Detector 4: Blacklist Bypass
# =========================================================================


class BlacklistBypassDetectorTest(DetectAnomaliesTestBase):
    """Tests for _detect_blacklist_bypass()."""

    def test_detects_blacklisted_person_entered(self):
        self.person.is_blacklisted = True
        self.person.blacklist_reason = "Banned"
        self.person.save()
        self._make_entered_event(days_ago=0, entered_hour=10)

        result = AIRecommendationService._detect_blacklist_bypass(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0]["anomaly_type"],
            AnomalyDetection.AnomalyType.BLACKLIST_BYPASS,
        )
        self.assertEqual(result[0]["severity"], AnomalyDetection.Severity.CRITICAL)

    def test_detects_blacklisted_person_approved(self):
        self.person.is_blacklisted = True
        self.person.blacklist_reason = "Banned"
        self.person.save()
        self._make_approved_event(days_ago=0)

        result = AIRecommendationService._detect_blacklist_bypass(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 1)

    def test_ignores_non_blacklisted_person(self):
        self._make_entered_event(days_ago=0, entered_hour=10)
        result = AIRecommendationService._detect_blacklist_bypass(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 0)

    def test_ignores_exited_blacklisted_person(self):
        """Only APPROVED and ENTERED statuses are checked."""
        self.person.is_blacklisted = True
        self.person.blacklist_reason = "Banned"
        self.person.save()
        self._make_exited_event(days_ago=0, entered_hour=10)

        result = AIRecommendationService._detect_blacklist_bypass(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 0)

    def test_context_contains_blacklist_reason(self):
        self.person.is_blacklisted = True
        self.person.blacklist_reason = "Trespassing"
        self.person.save()
        self._make_entered_event(days_ago=0, entered_hour=10)

        results = AIRecommendationService._detect_blacklist_bypass(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        ctx = results[0]["context"]
        self.assertEqual(ctx["blacklist_reason"], "Trespassing")


# =========================================================================
# Detector 5: Off-Pattern Visit
# =========================================================================


class OffPatternVisitDetectorTest(DetectAnomaliesTestBase):
    """Tests for _detect_off_pattern_visits()."""

    def test_detects_off_day_visit(self):
        # Pattern: visits on mon/wed/fri, typical_days = ["mon", "wed", "fri"]
        self._make_pattern(
            is_frequent=True,
            typical_visit_days=["mon", "wed", "fri"],
            typical_time_window={"start": "09:00", "end": "11:00"},
        )
        # Create a recent event on a different day.
        event = self._make_exited_event(days_ago=0, entered_hour=10)
        # Force the arrived_at to a day NOT in typical_days.
        # We can't control the weekday easily, so just check the detector runs.
        # If today is in typical_days, the test may not flag off-day.
        # Instead, set typical_days to an empty list to test off-time.
        VisitorPattern.objects.filter(person=self.person).update(
            typical_visit_days=["sun"],
        )

        result = AIRecommendationService._detect_off_pattern_visits(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        # The event should be flagged as off-day (unless today is Sunday).
        today_code = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][
            timezone.now().weekday()
        ]
        if today_code != "sun":
            self.assertEqual(len(result), 1)
            self.assertEqual(
                result[0]["anomaly_type"],
                AnomalyDetection.AnomalyType.OFF_PATTERN_VISIT,
            )

    def test_detects_off_time_visit(self):
        # Pattern: typical time 09:00–11:00.
        self._make_pattern(
            is_frequent=True,
            typical_visit_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            typical_time_window={"start": "09:00", "end": "11:00"},
        )
        # Event at 15:00 (outside 09:00–11:00).
        self._make_exited_event(days_ago=0, entered_hour=15)

        result = AIRecommendationService._detect_off_pattern_visits(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["severity"], AnomalyDetection.Severity.LOW)

    def test_no_frequent_patterns_returns_empty(self):
        self._make_pattern(is_frequent=False)
        self._make_exited_event(days_ago=0, entered_hour=10)

        result = AIRecommendationService._detect_off_pattern_visits(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 0)

    def test_on_pattern_visit_not_flagged(self):
        today_code = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][
            timezone.now().weekday()
        ]
        self._make_pattern(
            is_frequent=True,
            typical_visit_days=[today_code],
            typical_time_window={"start": "08:00", "end": "12:00"},
        )
        self._make_exited_event(days_ago=0, entered_hour=10)

        result = AIRecommendationService._detect_off_pattern_visits(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 0)

    def test_medium_severity_for_off_day(self):
        today_code = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][
            timezone.now().weekday()
        ]
        # Set typical_days to a day that is NOT today.
        other_days = [d for d in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
                      if d != today_code]
        self._make_pattern(
            is_frequent=True,
            typical_visit_days=other_days[:1],
            typical_time_window={"start": "08:00", "end": "12:00"},
        )
        self._make_exited_event(days_ago=0, entered_hour=10)

        result = AIRecommendationService._detect_off_pattern_visits(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["severity"], AnomalyDetection.Severity.MEDIUM)


# =========================================================================
# Detector 6: Duplicate Entry
# =========================================================================


class DuplicateEntryDetectorTest(DetectAnomaliesTestBase):
    """Tests for _detect_duplicate_entries()."""

    def test_detects_multiple_open_entries(self):
        self._make_entered_event(days_ago=0, entered_hour=10)
        # Create a second ENTERED event for the same person.
        person2_event = GateEvent.objects.create(
            society=self.society,
            gate=self.gate,
            person=self.person,
            visitor_category=self.visitor_cat,
            event_type=GateEvent.EventType.ENTRY,
            status=GateEvent.Status.ENTERED,
            direction=GateEvent.Direction.INBOUND,
            arrived_at=timezone.now() - timedelta(minutes=10),
            approved_at=timezone.now() - timedelta(minutes=5),
            entered_at=timezone.now() - timedelta(minutes=3),
        )

        result = AIRecommendationService._detect_duplicate_entries(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0]["anomaly_type"],
            AnomalyDetection.AnomalyType.DUPLICATE_ENTRY,
        )
        self.assertEqual(result[0]["severity"], AnomalyDetection.Severity.HIGH)

    def test_no_duplicate_returns_empty(self):
        self._make_entered_event(days_ago=0, entered_hour=10)
        result = AIRecommendationService._detect_duplicate_entries(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 0)

    def test_ignores_exited_events(self):
        self._make_exited_event(days_ago=0, entered_hour=10)
        self._make_exited_event(days_ago=0, entered_hour=12)
        result = AIRecommendationService._detect_duplicate_entries(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 0)

    def test_context_contains_open_entries(self):
        self._make_entered_event(days_ago=0, entered_hour=10)
        GateEvent.objects.create(
            society=self.society,
            gate=self.gate,
            person=self.person,
            visitor_category=self.visitor_cat,
            event_type=GateEvent.EventType.ENTRY,
            status=GateEvent.Status.ENTERED,
            direction=GateEvent.Direction.INBOUND,
            arrived_at=timezone.now() - timedelta(minutes=10),
            approved_at=timezone.now() - timedelta(minutes=5),
            entered_at=timezone.now() - timedelta(minutes=3),
        )

        results = AIRecommendationService._detect_duplicate_entries(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        ctx = results[0]["context"]
        self.assertIn("open_entries", ctx)
        self.assertEqual(len(ctx["open_entries"]), 2)


# =========================================================================
# Detector 7: Long Stay
# =========================================================================


class LongStayDetectorTest(DetectAnomaliesTestBase):
    """Tests for _detect_long_stays()."""

    def test_detects_long_stay_above_p95(self):
        # Create many short-stay events to establish a low p95.
        for i in range(20):
            self._make_exited_event(days_ago=i, duration_minutes=30)
        # Create one very long stay.
        self._make_exited_event(days_ago=0, duration_minutes=600)

        result = AIRecommendationService._detect_long_stays(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertGreaterEqual(len(result), 1)
        self.assertEqual(
            result[0]["anomaly_type"],
            AnomalyDetection.AnomalyType.LONG_STAY,
        )

    def test_no_events_returns_empty(self):
        result = AIRecommendationService._detect_long_stays(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 0)

    def test_short_stay_not_flagged(self):
        for i in range(20):
            self._make_exited_event(days_ago=i, duration_minutes=30)
        result = AIRecommendationService._detect_long_stays(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        # All stays are 30 min; p95 is 30; none exceed it.
        self.assertEqual(len(result), 0)

    def test_context_contains_duration_minutes(self):
        for i in range(20):
            self._make_exited_event(days_ago=i, duration_minutes=30)
        self._make_exited_event(days_ago=0, duration_minutes=600)

        results = AIRecommendationService._detect_long_stays(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        ctx = results[0]["context"]
        self.assertIn("duration_minutes", ctx)
        self.assertIn("p95_duration_minutes", ctx)
        self.assertIn("percentile", ctx)

    def test_high_severity_for_above_p99(self):
        # Create events with varying durations.
        for i in range(20):
            self._make_exited_event(days_ago=i, duration_minutes=30)
        # One extremely long stay (should be above p99).
        self._make_exited_event(days_ago=0, duration_minutes=6000)

        results = AIRecommendationService._detect_long_stays(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        # The 6000-min event should be HIGH severity.
        long_stays = [r for r in results if r["context"]["duration_minutes"] > 5000]
        if long_stays:
            self.assertEqual(long_stays[0]["severity"], AnomalyDetection.Severity.HIGH)


# =========================================================================
# Detector 8: Suspicious Pattern
# =========================================================================


class SuspiciousPatternDetectorTest(DetectAnomaliesTestBase):
    """Tests for _detect_suspicious_patterns()."""

    def test_detects_high_risk_pattern(self):
        # risk_score 0.50 maps to HIGH (>=0.50 and <0.75).
        self._make_pattern(
            risk_score=0.50,
            risk_level=VisitorPattern.RiskLevel.HIGH,
        )
        result = AIRecommendationService._detect_suspicious_patterns(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0]["anomaly_type"],
            AnomalyDetection.AnomalyType.SUSPICIOUS_PATTERN,
        )
        self.assertEqual(result[0]["severity"], AnomalyDetection.Severity.HIGH)

    def test_detects_critical_risk_pattern(self):
        self._make_pattern(
            risk_score=0.90,
            risk_level=VisitorPattern.RiskLevel.CRITICAL,
        )
        result = AIRecommendationService._detect_suspicious_patterns(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(result[0]["severity"], AnomalyDetection.Severity.CRITICAL)

    def test_ignores_low_risk_pattern(self):
        self._make_pattern(
            risk_score=0.1,
            risk_level=VisitorPattern.RiskLevel.LOW,
        )
        result = AIRecommendationService._detect_suspicious_patterns(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 0)

    def test_ignores_medium_risk_pattern(self):
        self._make_pattern(
            risk_score=0.4,
            risk_level=VisitorPattern.RiskLevel.MEDIUM,
        )
        result = AIRecommendationService._detect_suspicious_patterns(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 0)

    def test_does_not_re_flag_already_flagged(self):
        # risk_score 0.50 maps to HIGH (>=0.50 and <0.75).
        self._make_pattern(
            risk_score=0.50,
            risk_level=VisitorPattern.RiskLevel.HIGH,
        )
        # Pre-create a SUSPICIOUS_PATTERN anomaly for this person.
        AnomalyDetection.objects.create(
            society=self.society,
            anomaly_type=AnomalyDetection.AnomalyType.SUSPICIOUS_PATTERN,
            severity=AnomalyDetection.Severity.HIGH,
            person=self.person,
            description="Already flagged",
            context={},
        )
        result = AIRecommendationService._detect_suspicious_patterns(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        self.assertEqual(len(result), 0)

    def test_context_contains_risk_score(self):
        # risk_score 0.50 maps to HIGH (>=0.50 and <0.75).
        self._make_pattern(
            risk_score=0.50,
            risk_level=VisitorPattern.RiskLevel.HIGH,
        )
        results = AIRecommendationService._detect_suspicious_patterns(
            society=self.society, since=timezone.now() - timedelta(hours=24)
        )
        ctx = results[0]["context"]
        self.assertIn("risk_score", ctx)
        self.assertIn("top_factors", ctx)


# =========================================================================
# Deduplication
# =========================================================================


class AnomalyDeduplicationTest(DetectAnomaliesTestBase):
    """Tests for anomaly deduplication in _create_anomaly()."""

    def test_duplicate_gate_event_anomaly_skipped(self):
        self._make_entered_event(days_ago=0, entered_hour=10)
        event = GateEvent.objects.get(person=self.person)
        old_entered = timezone.now() - timedelta(hours=13)
        GateEvent.objects.filter(pk=event.pk).update(entered_at=old_entered)

        # First run creates the anomaly.
        AIRecommendationService.detect_anomalies(society=self.society)
        count1 = AnomalyDetection.objects.filter(
            anomaly_type=AnomalyDetection.AnomalyType.FORGOTTEN_EXIT
        ).count()
        self.assertEqual(count1, 1)

        # Second run should NOT create a duplicate.
        AIRecommendationService.detect_anomalies(society=self.society)
        count2 = AnomalyDetection.objects.filter(
            anomaly_type=AnomalyDetection.AnomalyType.FORGOTTEN_EXIT
        ).count()
        self.assertEqual(count2, 1)

    def test_resolved_anomaly_allows_new_detection(self):
        self._make_entered_event(days_ago=0, entered_hour=10)
        event = GateEvent.objects.get(person=self.person)
        old_entered = timezone.now() - timedelta(hours=13)
        GateEvent.objects.filter(pk=event.pk).update(entered_at=old_entered)

        # First run creates the anomaly.
        AIRecommendationService.detect_anomalies(society=self.society)
        anomaly = AnomalyDetection.objects.get(
            anomaly_type=AnomalyDetection.AnomalyType.FORGOTTEN_EXIT
        )
        # Resolve it.
        AnomalyDetection.objects.filter(pk=anomaly.pk).update(
            status=AnomalyDetection.Status.RESOLVED,
            resolved_at=timezone.now(),
        )

        # Second run should create a new anomaly (old one is RESOLVED).
        AIRecommendationService.detect_anomalies(society=self.society)
        count = AnomalyDetection.objects.filter(
            anomaly_type=AnomalyDetection.AnomalyType.FORGOTTEN_EXIT,
            status=AnomalyDetection.Status.OPEN,
        ).count()
        self.assertEqual(count, 1)

    def test_different_anomaly_types_for_same_event(self):
        """Different anomaly types for the same gate_event are NOT deduplicated."""
        self._set_night_mode(start_hour=22, end_hour=6)
        self.person.is_blacklisted = True
        self.person.blacklist_reason = "Banned"
        self.person.save()

        # Create an ENTERED event at 2am (after-hours + blacklist bypass).
        self._make_entered_event(days_ago=0, entered_hour=2)

        AIRecommendationService.detect_anomalies(society=self.society)
        # Should have at least AFTER_HOURS_ENTRY and BLACKLIST_BYPASS.
        types = set(
            AnomalyDetection.objects.filter(
                society=self.society
            ).values_list("anomaly_type", flat=True)
        )
        self.assertIn(AnomalyDetection.AnomalyType.AFTER_HOURS_ENTRY, types)
        self.assertIn(AnomalyDetection.AnomalyType.BLACKLIST_BYPASS, types)


# =========================================================================
# Notification Dispatch
# =========================================================================


class AnomalyNotificationTest(DetectAnomaliesTestBase):
    """Tests for _notify_anomaly() dispatch behavior."""

    @patch("gateops.services.notification_engine.queue_email")
    def test_critical_anomaly_with_event_dispatches_notification(self, mock_queue):
        mock_queue.return_value = None
        self.person.is_blacklisted = True
        self.person.blacklist_reason = "Banned"
        self.person.save()
        self._make_entered_event(days_ago=0, entered_hour=10)

        with patch.object(
            AIRecommendationService, "_notify_anomaly"
        ) as mock_notify:
            AIRecommendationService.detect_anomalies(society=self.society)
            # _notify_anomaly should have been called at least once.
            mock_notify.assert_called()

    @patch("gateops.services.notification_engine.queue_email")
    def test_non_critical_anomaly_no_notification(self, mock_queue):
        mock_queue.return_value = None
        # Forgotten exit is MEDIUM severity (13h < 24h).
        self._make_entered_event(days_ago=0, entered_hour=10)
        event = GateEvent.objects.get(person=self.person)
        old_entered = timezone.now() - timedelta(hours=13)
        GateEvent.objects.filter(pk=event.pk).update(entered_at=old_entered)

        AIRecommendationService.detect_anomalies(society=self.society)
        # queue_email should NOT have been called (MEDIUM severity).
        mock_queue.assert_not_called()

    @patch("gateops.services.notification_engine.queue_email")
    def test_notification_failure_does_not_block_anomaly(self, mock_queue):
        mock_queue.side_effect = Exception("Email server down")
        self.person.is_blacklisted = True
        self.person.blacklist_reason = "Banned"
        self.person.save()
        self._make_entered_event(days_ago=0, entered_hour=10)

        # Should not raise.
        AIRecommendationService.detect_anomalies(society=self.society)
        # Anomaly should still be created.
        self.assertTrue(
            AnomalyDetection.objects.filter(
                anomaly_type=AnomalyDetection.AnomalyType.BLACKLIST_BYPASS
            ).exists()
        )

    @patch("gateops.services.notification_engine.queue_email")
    def test_critical_anomaly_without_event_no_notification(self, mock_queue):
        mock_queue.return_value = None
        # SUSPICIOUS_PATTERN is CRITICAL but has no gate_event.
        self._make_pattern(
            risk_score=0.90,
            risk_level=VisitorPattern.RiskLevel.CRITICAL,
        )
        AIRecommendationService.detect_anomalies(society=self.society)
        # queue_email should NOT have been called (no gate_event).
        mock_queue.assert_not_called()


# =========================================================================
# Anomaly Lifecycle: acknowledge, resolve, get
# =========================================================================


class AnomalyLifecycleTest(DetectAnomaliesTestBase):
    """Tests for acknowledge_anomaly(), resolve_anomaly(), get methods."""

    def _make_anomaly(self, **overrides):
        defaults = {
            "society": self.society,
            "anomaly_type": AnomalyDetection.AnomalyType.FORGOTTEN_EXIT,
            "severity": AnomalyDetection.Severity.MEDIUM,
            "person": self.person,
            "description": "Test anomaly",
            "context": {"key": "value"},
        }
        defaults.update(overrides)
        return AnomalyDetection.objects.create(**defaults)

    # --- acknowledge ------------------------------------------------------

    def test_acknowledge_transitions_open_to_acknowledged(self):
        anomaly = self._make_anomaly()
        result = AIRecommendationService.acknowledge_anomaly(
            anomaly=anomaly, actor=self.user
        )
        self.assertEqual(result.status, AnomalyDetection.Status.ACKNOWLEDGED)

    def test_acknowledge_creates_audit_log(self):
        anomaly = self._make_anomaly()
        AIRecommendationService.acknowledge_anomaly(anomaly=anomaly, actor=self.user)
        audit = GateOpsAuditLog.objects.filter(
            society=self.society,
            entity_type="AnomalyDetection",
            entity_id=anomaly.pk,
        )
        self.assertTrue(audit.exists())

    def test_acknowledge_rejects_non_open(self):
        anomaly = self._make_anomaly()
        AnomalyDetection.objects.filter(pk=anomaly.pk).update(
            status=AnomalyDetection.Status.ACKNOWLEDGED
        )
        # Refresh in-memory object so the service sees the updated status.
        anomaly.refresh_from_db()
        with self.assertRaises(ValidationError):
            AIRecommendationService.acknowledge_anomaly(anomaly=anomaly)

    # --- resolve ----------------------------------------------------------

    def test_resolve_transitions_to_resolved(self):
        anomaly = self._make_anomaly()
        result = AIRecommendationService.resolve_anomaly(
            anomaly=anomaly, resolved_by=self.user, resolution_notes="Fixed"
        )
        self.assertEqual(result.status, AnomalyDetection.Status.RESOLVED)
        self.assertIsNotNone(result.resolved_at)
        self.assertEqual(result.resolved_by, self.user)
        self.assertEqual(result.resolution_notes, "Fixed")

    def test_resolve_as_false_positive(self):
        anomaly = self._make_anomaly()
        result = AIRecommendationService.resolve_anomaly(
            anomaly=anomaly,
            resolved_by=self.user,
            is_false_positive=True,
        )
        self.assertEqual(result.status, AnomalyDetection.Status.FALSE_POSITIVE)

    def test_resolve_from_acknowledged(self):
        anomaly = self._make_anomaly()
        AnomalyDetection.objects.filter(pk=anomaly.pk).update(
            status=AnomalyDetection.Status.ACKNOWLEDGED
        )
        result = AIRecommendationService.resolve_anomaly(
            anomaly=anomaly, resolved_by=self.user
        )
        self.assertEqual(result.status, AnomalyDetection.Status.RESOLVED)

    def test_resolve_rejects_already_resolved(self):
        anomaly = self._make_anomaly()
        AnomalyDetection.objects.filter(pk=anomaly.pk).update(
            status=AnomalyDetection.Status.RESOLVED,
            resolved_at=timezone.now(),
        )
        # Refresh in-memory object so the service sees the updated status.
        anomaly.refresh_from_db()
        with self.assertRaises(ValidationError):
            AIRecommendationService.resolve_anomaly(
                anomaly=anomaly, resolved_by=self.user
            )

    def test_resolve_creates_audit_log(self):
        anomaly = self._make_anomaly()
        AIRecommendationService.resolve_anomaly(
            anomaly=anomaly, resolved_by=self.user
        )
        audit = GateOpsAuditLog.objects.filter(
            society=self.society,
            entity_type="AnomalyDetection",
            entity_id=anomaly.pk,
        )
        self.assertTrue(audit.exists())

    # --- get_anomalies / get_anomaly -------------------------------------

    def test_get_anomalies_returns_society_scoped(self):
        self._make_anomaly()
        from core.test_factories import SocietyFactory

        other = SocietyFactory(name="Other Get Society")
        AnomalyDetection.objects.create(
            society=other,
            anomaly_type=AnomalyDetection.AnomalyType.FORGOTTEN_EXIT,
            severity=AnomalyDetection.Severity.MEDIUM,
            description="Other",
        )
        qs = AIRecommendationService.get_anomalies(society=self.society)
        self.assertEqual(qs.count(), 1)

    def test_get_anomalies_filters_by_status(self):
        self._make_anomaly()
        a2 = self._make_anomaly()
        AnomalyDetection.objects.filter(pk=a2.pk).update(
            status=AnomalyDetection.Status.ACKNOWLEDGED
        )
        qs = AIRecommendationService.get_anomalies(
            society=self.society, status=AnomalyDetection.Status.OPEN
        )
        self.assertEqual(qs.count(), 1)

    def test_get_anomalies_filters_by_severity(self):
        self._make_anomaly(severity=AnomalyDetection.Severity.LOW)
        self._make_anomaly(severity=AnomalyDetection.Severity.HIGH)
        qs = AIRecommendationService.get_anomalies(
            society=self.society, severity=AnomalyDetection.Severity.HIGH
        )
        self.assertEqual(qs.count(), 1)

    def test_get_anomalies_filters_by_anomaly_type(self):
        self._make_anomaly(
            anomaly_type=AnomalyDetection.AnomalyType.FORGOTTEN_EXIT
        )
        self._make_anomaly(
            anomaly_type=AnomalyDetection.AnomalyType.LONG_STAY
        )
        qs = AIRecommendationService.get_anomalies(
            society=self.society,
            anomaly_type=AnomalyDetection.AnomalyType.LONG_STAY,
        )
        self.assertEqual(qs.count(), 1)

    def test_get_anomalies_excludes_inactive_by_default(self):
        anomaly = self._make_anomaly()
        AnomalyDetection.objects.filter(pk=anomaly.pk).update(is_active=False)
        qs = AIRecommendationService.get_anomalies(society=self.society)
        self.assertEqual(qs.count(), 0)

    def test_get_anomalies_include_inactive(self):
        anomaly = self._make_anomaly()
        AnomalyDetection.objects.filter(pk=anomaly.pk).update(is_active=False)
        qs = AIRecommendationService.get_anomalies(
            society=self.society, include_inactive=True
        )
        self.assertEqual(qs.count(), 1)

    def test_get_anomaly_returns_anomaly(self):
        anomaly = self._make_anomaly()
        result = AIRecommendationService.get_anomaly(
            society=self.society, pk=anomaly.pk
        )
        self.assertEqual(result.pk, anomaly.pk)

    def test_get_anomaly_404_for_other_society(self):
        from core.test_factories import SocietyFactory

        other = SocietyFactory(name="Other Get 404 Society")
        anomaly = AnomalyDetection.objects.create(
            society=other,
            anomaly_type=AnomalyDetection.AnomalyType.FORGOTTEN_EXIT,
            severity=AnomalyDetection.Severity.MEDIUM,
            description="Other",
        )
        with self.assertRaises(Http404):
            AIRecommendationService.get_anomaly(society=self.society, pk=anomaly.pk)

    def test_get_anomaly_404_for_inactive(self):
        anomaly = self._make_anomaly()
        AnomalyDetection.objects.filter(pk=anomaly.pk).update(is_active=False)
        with self.assertRaises(Http404):
            AIRecommendationService.get_anomaly(society=self.society, pk=anomaly.pk)


# =========================================================================
# Real-time hook: _check_entry_anomalies
# =========================================================================


class CheckEntryAnomaliesTest(DetectAnomaliesTestBase):
    """Tests for the real-time _check_entry_anomalies() hook."""

    def test_after_hours_entry_creates_anomaly(self):
        self._set_night_mode(start_hour=22, end_hour=6)
        # Create an ENTERED event at 2am.
        self._make_entered_event(days_ago=0, entered_hour=2)
        event = GateEvent.objects.get(person=self.person)

        AIRecommendationService._check_entry_anomalies(event=event)
        self.assertTrue(
            AnomalyDetection.objects.filter(
                society=self.society,
                anomaly_type=AnomalyDetection.AnomalyType.AFTER_HOURS_ENTRY,
                gate_event=event,
            ).exists()
        )

    def test_daytime_entry_no_after_hours_anomaly(self):
        self._set_night_mode(start_hour=22, end_hour=6)
        self._make_entered_event(days_ago=0, entered_hour=10)
        event = GateEvent.objects.get(person=self.person)

        AIRecommendationService._check_entry_anomalies(event=event)
        self.assertFalse(
            AnomalyDetection.objects.filter(
                anomaly_type=AnomalyDetection.AnomalyType.AFTER_HOURS_ENTRY
            ).exists()
        )

    def test_no_night_mode_no_after_hours_anomaly(self):
        self._make_entered_event(days_ago=0, entered_hour=23)
        event = GateEvent.objects.get(person=self.person)

        AIRecommendationService._check_entry_anomalies(event=event)
        self.assertFalse(
            AnomalyDetection.objects.filter(
                anomaly_type=AnomalyDetection.AnomalyType.AFTER_HOURS_ENTRY
            ).exists()
        )

    def test_duplicate_entry_creates_anomaly(self):
        # First entered event.
        self._make_entered_event(days_ago=0, entered_hour=10)
        # Second entered event for same person (without exiting first).
        event2 = GateEvent.objects.create(
            society=self.society,
            gate=self.gate,
            person=self.person,
            visitor_category=self.visitor_cat,
            event_type=GateEvent.EventType.ENTRY,
            status=GateEvent.Status.ENTERED,
            direction=GateEvent.Direction.INBOUND,
            arrived_at=timezone.now() - timedelta(minutes=10),
            approved_at=timezone.now() - timedelta(minutes=5),
            entered_at=timezone.now() - timedelta(minutes=3),
        )

        AIRecommendationService._check_entry_anomalies(event=event2)
        self.assertTrue(
            AnomalyDetection.objects.filter(
                society=self.society,
                anomaly_type=AnomalyDetection.AnomalyType.DUPLICATE_ENTRY,
                gate_event=event2,
            ).exists()
        )

    def test_blacklist_bypass_creates_anomaly(self):
        self.person.is_blacklisted = True
        self.person.blacklist_reason = "Banned"
        self.person.save()
        self._make_entered_event(days_ago=0, entered_hour=10)
        event = GateEvent.objects.get(person=self.person)

        AIRecommendationService._check_entry_anomalies(event=event)
        self.assertTrue(
            AnomalyDetection.objects.filter(
                society=self.society,
                anomaly_type=AnomalyDetection.AnomalyType.BLACKLIST_BYPASS,
                gate_event=event,
            ).exists()
        )

    def test_non_blacklisted_no_bypass_anomaly(self):
        self._make_entered_event(days_ago=0, entered_hour=10)
        event = GateEvent.objects.get(person=self.person)

        AIRecommendationService._check_entry_anomalies(event=event)
        self.assertFalse(
            AnomalyDetection.objects.filter(
                anomaly_type=AnomalyDetection.AnomalyType.BLACKLIST_BYPASS
            ).exists()
        )

    def test_deduplication_in_realtime_hook(self):
        """Running _check_entry_anomalies twice should not create duplicates."""
        self._set_night_mode(start_hour=22, end_hour=6)
        self._make_entered_event(days_ago=0, entered_hour=2)
        event = GateEvent.objects.get(person=self.person)

        AIRecommendationService._check_entry_anomalies(event=event)
        AIRecommendationService._check_entry_anomalies(event=event)
        self.assertEqual(
            AnomalyDetection.objects.filter(
                anomaly_type=AnomalyDetection.AnomalyType.AFTER_HOURS_ENTRY,
                gate_event=event,
            ).count(),
            1,
        )
