"""Test suite for gateops Phase 13 — Analytics.

Test conventions (matching ``test_vehicle_service.py``):
- ``SocietyTestCase`` base class provides ``cls.society`` and ``cls.user``
  (created once per class via ``SocietyFactory`` with django_get_or_create,
  triggering the gateops bootstrap signal that seeds gates, visitor
  categories, vehicle categories, and ``GateOpsRole`` records).
- Per-test mutable records (persons, gate events, rule evaluations, etc.)
  are created in ``setUp()``.
- View tests follow the ``VehicleViewTest`` pattern: societies are created
  once per class via ``create_society`` (which grants an active OWNER
  membership), ``setUp`` logs in and selects the society via the session.

Covers:
- ``AnalyticsService`` (live visitors, peak hours, guard performance,
  custom report, rule-violation stats, anomaly stats, visitor trends,
  snapshot generation / get-or-create).
- Analytics views (dashboard, live-visitors AJAX, peak hours, guard
  performance, custom report, CSV export) — permission enforcement and
  society scoping.

All assertions are written against the ACTUAL implementation in
``gateops/services/analytics_service.py`` and ``gateops/views.py``.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.test_base import SocietyTestCase
from core.test_factories import SocietyFactory, UserFactory
from gateops.models import (
    AnomalyDetection,
    AnalyticsSnapshot,
    Gate,
    GateEvent,
    GateOpsRole,
    PeakHourPrediction,
    Person,
    Rule,
    RuleEvaluation,
    SecurityGuard,
    VisitorCategory,
    VisitorPattern,
)
from gateops.services.analytics_service import AnalyticsService
from housing_accounting.selection import SESSION_SELECTED_SOCIETY_ID
from societies.services import create_society


class AnalyticsServiceTest(SocietyTestCase):
    """Service-level tests for ``AnalyticsService``.

    The society and seeded master data are created once per class via
    ``setUpTestData`` to avoid re-running the expensive gateops bootstrap
    signal on every test method.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Fetch seeded master data (bootstrap signal seeds these).
        cls.gate = Gate.objects.get(society=cls.society, code="MAIN")
        cls.visitor_cat = VisitorCategory.objects.get(society=cls.society, code="GUEST")
        cls.delivery_cat = VisitorCategory.objects.get(
            society=cls.society, code="DELIVERY"
        )
        # Second society for cross-society tests (triggers bootstrap once).
        cls.other_society = SocietyFactory(name="Analytics Isolation Beta")

    def setUp(self):
        super().setUp()
        self.person = self._make_person()
        self.now = timezone.now()

    # --- helpers ---------------------------------------------------------

    def _make_person(self, **overrides):
        defaults = {
            "society": self.society,
            "name": "Analytics Visitor",
            "phone": "+919999999999",
        }
        defaults.update(overrides)
        return Person.objects.create(**defaults)

    def _make_gate_event(self, **overrides):
        """Create a GateEvent with sensible analytics-friendly defaults.

        Defaults to an ENTERED event (counts as a live visitor and as an
        entry in peak-hours / trends).  ``GateEvent.save()`` runs ``clean()``
        which validates status/timestamp consistency, so ENTERED requires
        ``entered_at`` to be set.
        """
        defaults = {
            "society": self.society,
            "gate": self.gate,
            "person": self.person,
            "visitor_category": self.visitor_cat,
            "event_type": GateEvent.EventType.ENTRY,
            "status": GateEvent.Status.ENTERED,
            "direction": GateEvent.Direction.INBOUND,
            "entered_at": self.now,
        }
        defaults.update(overrides)
        return GateEvent.objects.create(**defaults)

    def _make_rule_evaluation(self, **overrides):
        defaults = {
            "society": self.society,
            "action_taken": RuleEvaluation.ActionTaken.AUTO_APPROVE,
            "execution_time_ms": 10,
        }
        defaults.update(overrides)
        return RuleEvaluation.objects.create(**defaults)

    def _make_anomaly(self, **overrides):
        defaults = {
            "society": self.society,
            "anomaly_type": AnomalyDetection.AnomalyType.FORGOTTEN_EXIT,
            "severity": AnomalyDetection.Severity.MEDIUM,
            "status": AnomalyDetection.Status.OPEN,
            "description": "Test anomaly",
        }
        defaults.update(overrides)
        return AnomalyDetection.objects.create(**defaults)

    # ------------------------------------------------------------------ #
    # 1. Live visitors
    # ------------------------------------------------------------------ #

    def test_get_live_visitors_count(self):
        """ENTERED events are counted as live visitors."""
        self._make_gate_event()
        self._make_gate_event(
            person=self._make_person(phone="+918888888881", name="Visitor 2")
        )
        result = AnalyticsService.get_live_visitors(society=self.society)

        self.assertEqual(result["count"], 2)
        self.assertEqual(len(result["visitors"]), 2)

    def test_get_live_visitors_by_category(self):
        """``by_category`` breaks down live visitors by visitor category name."""
        self._make_gate_event(visitor_category=self.visitor_cat)
        self._make_gate_event(
            visitor_category=self.delivery_cat,
            person=self._make_person(phone="+918888888882", name="Delivery 1"),
        )
        result = AnalyticsService.get_live_visitors(society=self.society)

        self.assertEqual(result["by_category"][self.visitor_cat.name], 1)
        self.assertEqual(result["by_category"][self.delivery_cat.name], 1)

    def test_get_live_visitors_by_gate(self):
        """``by_gate`` breaks down live visitors by gate name."""
        self._make_gate_event(gate=self.gate)
        result = AnalyticsService.get_live_visitors(society=self.society)

        self.assertEqual(result["by_gate"][self.gate.name], 1)

    def test_get_live_visitors_cross_society_isolation(self):
        """Society A cannot see Society B's live visitors."""
        self._make_gate_event()
        # Create a visitor in the other society.
        other_gate = Gate.objects.get(society=self.other_society, code="MAIN")
        other_cat = VisitorCategory.objects.get(
            society=self.other_society, code="GUEST"
        )
        other_person = Person.objects.create(
            society=self.other_society, name="Other", phone="+917777777777"
        )
        GateEvent.objects.create(
            society=self.other_society,
            gate=other_gate,
            person=other_person,
            visitor_category=other_cat,
            event_type=GateEvent.EventType.ENTRY,
            status=GateEvent.Status.ENTERED,
            direction=GateEvent.Direction.INBOUND,
            entered_at=self.now,
        )

        result = AnalyticsService.get_live_visitors(society=self.society)
        self.assertEqual(result["count"], 1)
        # The other society's visitor must not appear.
        names = [v["person_name"] for v in result["visitors"]]
        self.assertIn("Analytics Visitor", names)
        self.assertNotIn("Other", names)

    def test_get_live_visitors_empty(self):
        """No visitors returns count=0 and empty dicts/lists."""
        result = AnalyticsService.get_live_visitors(society=self.society)

        self.assertEqual(result["count"], 0)
        self.assertEqual(result["by_category"], {})
        self.assertEqual(result["by_gate"], {})
        self.assertEqual(result["visitors"], [])

    # ------------------------------------------------------------------ #
    # 2. Peak hours
    # ------------------------------------------------------------------ #

    def test_get_peak_hours_hourly_distribution(self):
        """Hourly distribution reflects the hour of ``entered_at``."""
        # One event at 09:00, two events at 14:00.
        t1 = self.now.replace(hour=9, minute=0, second=0, microsecond=0)
        t2 = self.now.replace(hour=14, minute=0, second=0, microsecond=0)
        self._make_gate_event(entered_at=t1)
        self._make_gate_event(
            entered_at=t2,
            person=self._make_person(phone="+918888888883", name="V2"),
        )
        self._make_gate_event(
            entered_at=t2,
            person=self._make_person(phone="+918888888884", name="V3"),
        )

        today = timezone.localdate()
        result = AnalyticsService.get_peak_hours(
            society=self.society, date_from=today, date_to=today
        )

        hourly = result["actual"]["hourly"]
        self.assertEqual(hourly["9"], 1)
        self.assertEqual(hourly["14"], 2)
        # Peak hour is 14 with count 2.
        self.assertEqual(result["peak_hour"], 14)
        self.assertEqual(result["peak_hour_count"], 2)
        self.assertEqual(result["total_entries"], 3)

    def test_get_peak_hours_with_predictions(self):
        """PeakHourPrediction data is overlaid in the ``predicted`` dict."""
        today = timezone.localdate()
        # Create a prediction for today's weekday at hour 10.
        PeakHourPrediction.objects.create(
            society=self.society,
            day_of_week=today.weekday(),
            hour=10,
            predicted_count=25,
            confidence_score=0.8,
            analysis_date=today,
            is_active=True,
        )

        result = AnalyticsService.get_peak_hours(
            society=self.society, date_from=today, date_to=today
        )

        # Predicted is averaged across the number of distinct days in range
        # (1 day here), so predicted[10] == 25.0.
        self.assertEqual(result["predicted"]["10"], 25.0)
        # Other hours remain 0.
        self.assertEqual(result["predicted"]["0"], 0)

    def test_get_peak_hours_date_range_filter(self):
        """Events outside the date range are excluded."""
        yesterday = timezone.localdate() - timedelta(days=1)
        today = timezone.localdate()
        # Event yesterday (outside today's range).
        self._make_gate_event(
            entered_at=timezone.make_aware(
                timezone.datetime.combine(yesterday, timezone.datetime.min.time())
            )
        )
        # Event today.
        self._make_gate_event(
            entered_at=self.now,
            person=self._make_person(phone="+918888888885", name="Today V"),
        )

        result = AnalyticsService.get_peak_hours(
            society=self.society, date_from=today, date_to=today
        )

        self.assertEqual(result["total_entries"], 1)

    # ------------------------------------------------------------------ #
    # 3. Guard performance
    # ------------------------------------------------------------------ #

    def test_get_guard_performance_metrics(self):
        """Per-guard metrics reflect entries processed by the guard."""
        guard = SecurityGuard.objects.create(
            society=self.society, name="Guard One", phone="9999999999"
        )
        self._make_gate_event(guard=guard)

        today = timezone.localdate()
        result = AnalyticsService.get_guard_performance(
            society=self.society, date_from=today, date_to=today
        )

        self.assertEqual(len(result["guards"]), 1)
        g = result["guards"][0]
        self.assertEqual(g["guard_name"], "Guard One")
        self.assertEqual(g["entries_processed"], 1)
        self.assertEqual(g["approvals_given"], 1)  # ENTERED counts as approval
        # Totals aggregate the single guard.
        self.assertEqual(result["totals"]["entries_processed"], 1)

    def test_get_guard_performance_avg_processing_time(self):
        """Average processing time is derived from RuleEvaluation."""
        guard = SecurityGuard.objects.create(
            society=self.society, name="Guard Two", phone="8888888888"
        )
        event = self._make_gate_event(guard=guard)
        self._make_rule_evaluation(
            gate_event=event, execution_time_ms=100
        )
        self._make_rule_evaluation(
            gate_event=event, execution_time_ms=200
        )

        today = timezone.localdate()
        result = AnalyticsService.get_guard_performance(
            society=self.society, date_from=today, date_to=today
        )

        g = result["guards"][0]
        # Avg of 100 and 200 == 150.0
        self.assertEqual(g["avg_processing_time_ms"], 150.0)

    def test_get_guard_performance_specific_guard(self):
        """Passing ``guard`` filters metrics to a single guard."""
        guard_a = SecurityGuard.objects.create(
            society=self.society, name="Guard A", phone="7777777771"
        )
        guard_b = SecurityGuard.objects.create(
            society=self.society, name="Guard B", phone="7777777772"
        )
        self._make_gate_event(guard=guard_a)
        self._make_gate_event(
            guard=guard_b,
            person=self._make_person(phone="+918888888886", name="B Visitor"),
        )

        today = timezone.localdate()
        result = AnalyticsService.get_guard_performance(
            society=self.society, guard=guard_a, date_from=today, date_to=today
        )

        self.assertEqual(len(result["guards"]), 1)
        self.assertEqual(result["guards"][0]["guard_name"], "Guard A")
        self.assertEqual(result["guards"][0]["entries_processed"], 1)

    # ------------------------------------------------------------------ #
    # 4. Custom report
    # ------------------------------------------------------------------ #

    def test_get_custom_report_grouping(self):
        """Custom report with ``group_by`` returns a grouped series."""
        self._make_gate_event(gate=self.gate)
        self._make_gate_event(
            person=self._make_person(phone="+918888888887", name="V2")
        )

        today = timezone.localdate()
        result = AnalyticsService.get_custom_report(
            society=self.society,
            metrics=["total_events"],
            date_from=today,
            date_to=today,
            group_by="gate",
        )

        self.assertEqual(result["metrics"]["total_events"], 2)
        self.assertEqual(result["group_by"], "gate")
        # Grouped is a list of {gate__name, count} dicts.
        self.assertTrue(len(result["grouped"]) >= 1)
        gate_group = next(
            g for g in result["grouped"] if g["gate__name"] == self.gate.name
        )
        self.assertEqual(gate_group["count"], 2)

    # ------------------------------------------------------------------ #
    # 5. Rule violation stats
    # ------------------------------------------------------------------ #

    def test_get_rule_violation_stats(self):
        """Violation counts are aggregated by action, rule, and gate."""
        rule = Rule.objects.create(
            society=self.society,
            name="Test Rule",
            code="TEST_RULE_1",
            priority=10,
        )
        event = self._make_gate_event()
        # A violation action.
        self._make_rule_evaluation(
            gate_event=event,
            rule=rule,
            action_taken=RuleEvaluation.ActionTaken.REJECT,
            execution_time_ms=50,
        )
        # A clean auto-approve (not a violation).
        self._make_rule_evaluation(
            gate_event=event,
            rule=rule,
            action_taken=RuleEvaluation.ActionTaken.AUTO_APPROVE,
            execution_time_ms=5,
        )

        today = timezone.localdate()
        result = AnalyticsService.get_rule_violation_stats(
            society=self.society, date_from=today, date_to=today
        )

        self.assertEqual(result["total_evaluations"], 2)
        self.assertEqual(result["by_action"]["reject"], 1)
        self.assertEqual(result["by_action"]["auto_approve"], 1)
        # Top violated rules includes the REJECT rule.
        self.assertTrue(len(result["top_violated_rules"]) >= 1)
        top = result["top_violated_rules"][0]
        self.assertEqual(top["rule_name"], "Test Rule")
        self.assertEqual(top["violation_count"], 1)
        # by_gate counts violations linked to a gate event with a gate.
        self.assertEqual(result["by_gate"][self.gate.name], 1)

    # ------------------------------------------------------------------ #
    # 6. Anomaly stats
    # ------------------------------------------------------------------ #

    def test_get_anomaly_stats(self):
        """Anomaly counts are broken down by type, severity, and status."""
        self._make_anomaly(
            anomaly_type=AnomalyDetection.AnomalyType.FORGOTTEN_EXIT,
            severity=AnomalyDetection.Severity.HIGH,
            status=AnomalyDetection.Status.OPEN,
        )
        self._make_anomaly(
            anomaly_type=AnomalyDetection.AnomalyType.AFTER_HOURS_ENTRY,
            severity=AnomalyDetection.Severity.LOW,
            status=AnomalyDetection.Status.OPEN,
        )

        today = timezone.localdate()
        result = AnalyticsService.get_anomaly_stats(
            society=self.society, date_from=today, date_to=today
        )

        self.assertEqual(result["total_anomalies"], 2)
        self.assertEqual(
            result["by_type"][AnomalyDetection.AnomalyType.FORGOTTEN_EXIT], 1
        )
        self.assertEqual(
            result["by_type"][AnomalyDetection.AnomalyType.AFTER_HOURS_ENTRY], 1
        )
        self.assertEqual(result["by_severity"]["high"], 1)
        self.assertEqual(result["by_severity"]["low"], 1)
        self.assertEqual(result["by_status"]["open"], 2)

    def test_get_anomaly_stats_resolution_rate(self):
        """Resolution rate = (resolved + false_positive) / total * 100."""
        self._make_anomaly(status=AnomalyDetection.Status.OPEN)
        self._make_anomaly(
            status=AnomalyDetection.Status.RESOLVED,
            resolved_at=timezone.now(),
        )
        self._make_anomaly(
            status=AnomalyDetection.Status.FALSE_POSITIVE,
            resolved_at=timezone.now(),
        )

        today = timezone.localdate()
        result = AnalyticsService.get_anomaly_stats(
            society=self.society, date_from=today, date_to=today
        )

        # 2 of 3 resolved/false_positive => 66.7%
        self.assertEqual(result["total_anomalies"], 3)
        self.assertAlmostEqual(result["resolution_rate"], 66.7, places=1)

    # ------------------------------------------------------------------ #
    # 7. Visitor trends
    # ------------------------------------------------------------------ #

    def test_get_visitor_trends_daily(self):
        """Daily granularity returns one data point per day in range."""
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        # Event today (ENTERED counts as an entry).
        self._make_gate_event(entered_at=self.now)
        # Event yesterday.
        self._make_gate_event(
            entered_at=timezone.make_aware(
                timezone.datetime.combine(yesterday, timezone.datetime.min.time())
            ) + timedelta(hours=2),
            person=self._make_person(phone="+918888888888", name="Yesterday V"),
        )

        result = AnalyticsService.get_visitor_trends(
            society=self.society,
            date_from=yesterday,
            date_to=today,
            granularity="daily",
        )

        self.assertEqual(result["granularity"], "daily")
        self.assertEqual(len(result["data_points"]), 2)
        # Totals sum across all data points.
        self.assertEqual(result["totals"]["entries"], 2)
        # Each data point has the expected keys.
        dp = result["data_points"][0]
        self.assertIn("date", dp)
        self.assertIn("entries", dp)
        self.assertIn("exits", dp)
        self.assertIn("auto_closed", dp)
        self.assertIn("rejected", dp)

    # ------------------------------------------------------------------ #
    # 8. Snapshot generation
    # ------------------------------------------------------------------ #

    def test_generate_snapshot_creates_record(self):
        """``generate_snapshot`` creates an AnalyticsSnapshot row."""
        today = timezone.localdate()
        snapshot = AnalyticsService.generate_snapshot(
            society=self.society, date=today, actor=self.user
        )

        self.assertIsNotNone(snapshot.id)
        self.assertEqual(snapshot.society, self.society)
        self.assertEqual(snapshot.date, today)
        self.assertEqual(snapshot.snapshot_type, AnalyticsSnapshot.SnapshotType.DAILY)
        self.assertTrue(snapshot.is_active)
        self.assertIsInstance(snapshot.metrics, dict)
        # Metrics should contain the expected keys.
        self.assertIn("total_entries", snapshot.metrics)
        self.assertIn("hourly_distribution", snapshot.metrics)

    def test_generate_snapshot_soft_deletes_old(self):
        """Re-generating a snapshot soft-deletes the previous active one."""
        today = timezone.localdate()
        first = AnalyticsService.generate_snapshot(
            society=self.society, date=today
        )
        self.assertTrue(first.is_active)

        second = AnalyticsService.generate_snapshot(
            society=self.society, date=today
        )

        first.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertIsNotNone(first.deleted_at)
        self.assertTrue(second.is_active)
        # Only one active snapshot for this tuple.
        active_count = AnalyticsSnapshot.objects.filter(
            society=self.society,
            date=today,
            snapshot_type=AnalyticsSnapshot.SnapshotType.DAILY,
            is_active=True,
        ).count()
        self.assertEqual(active_count, 1)

    def test_get_or_create_snapshot_idempotent(self):
        """Duplicate ``get_or_create_snapshot`` returns the existing row."""
        today = timezone.localdate()
        first = AnalyticsService.get_or_create_snapshot(
            society=self.society, date=today
        )
        second = AnalyticsService.get_or_create_snapshot(
            society=self.society, date=today
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(AnalyticsSnapshot.objects.filter(society=self.society).count(), 1)

    def test_get_or_create_snapshot_creates_new(self):
        """``get_or_create_snapshot`` creates a snapshot when none exists."""
        today = timezone.localdate()
        self.assertEqual(
            AnalyticsSnapshot.objects.filter(society=self.society).count(), 0
        )

        snapshot = AnalyticsService.get_or_create_snapshot(
            society=self.society, date=today
        )

        self.assertIsNotNone(snapshot.id)
        self.assertTrue(snapshot.is_active)
        self.assertEqual(
            AnalyticsSnapshot.objects.filter(society=self.society).count(), 1
        )


class AnalyticsViewTest(TestCase):
    """Frontend tests for the Phase 13 analytics views.

    Societies are created once per class in ``setUpTestData``; ``setUp`` logs
    in and selects the society so every view resolves the correct tenant.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = UserFactory(password="password")
        # create_society grants the user an active OWNER membership, which
        # the society-selection middleware requires to resolve the active
        # society.  It also triggers the gateops bootstrap signal.
        cls.society = create_society(user=cls.user, name="Analytics View Society")
        cls.gate = Gate.objects.get(society=cls.society, code="MAIN")
        cls.visitor_cat = VisitorCategory.objects.get(
            society=cls.society, code="GUEST"
        )
        # A second society for cross-society view tests.
        cls.other_user = UserFactory(password="password")
        cls.other_society = create_society(
            user=cls.other_user, name="Other Analytics Society"
        )

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self._select_society(self.society)
        self.person = Person.objects.create(
            society=self.society, name="View Visitor", phone="+918888888888"
        )
        self.now = timezone.now()

    # --- helpers ---------------------------------------------------------

    def _select_society(self, society):
        session = self.client.session
        session[SESSION_SELECTED_SOCIETY_ID] = society.id
        session.save()

    def _make_entered_event(self, **overrides):
        defaults = {
            "society": self.society,
            "gate": self.gate,
            "person": self.person,
            "visitor_category": self.visitor_cat,
            "event_type": GateEvent.EventType.ENTRY,
            "status": GateEvent.Status.ENTERED,
            "direction": GateEvent.Direction.INBOUND,
            "entered_at": self.now,
        }
        defaults.update(overrides)
        return GateEvent.objects.create(**defaults)

    def _grant_analytics_permission(self, society, allowed=True):
        """Ensure the society's first active GateOpsRole has can_view_analytics.

        The bootstrap seeds a GATE_ADMIN role with can_view_analytics=True,
        so by default analytics access is granted.  This helper can revoke
        it to test the permission-denied path.
        """
        role = GateOpsRole.objects.filter(
            society=society, is_active=True, deleted_at__isnull=True
        ).first()
        if role is None:
            role = GateOpsRole.objects.create(
                society=society,
                name="Gate Admin",
                code=GateOpsRole.RoleCode.GATE_ADMIN,
                permissions={},
            )
        role.permissions["can_view_analytics"] = allowed
        role.save(update_fields=["permissions"])
        return role

    def _revoke_analytics_permission(self, society):
        """Revoke can_view_analytics on ALL active roles for the society."""
        roles = GateOpsRole.objects.filter(
            society=society, is_active=True, deleted_at__isnull=True
        )
        for role in roles:
            role.permissions["can_view_analytics"] = False
            role.save(update_fields=["permissions"])

    # --- dashboard view --------------------------------------------------

    def test_analytics_dashboard_view_requires_permission(self):
        """A society whose roles lack can_view_analytics gets 403."""
        self._revoke_analytics_permission(self.society)
        response = self.client.get(reverse("gateops:analytics-dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_analytics_dashboard_view_cross_society(self):
        """The dashboard is society-scoped: only the selected society's data."""
        # Visitor in the primary society.
        self._make_entered_event()
        # Visitor in the other society (must NOT appear).
        other_gate = Gate.objects.get(society=self.other_society, code="MAIN")
        other_cat = VisitorCategory.objects.get(
            society=self.other_society, code="GUEST"
        )
        other_person = Person.objects.create(
            society=self.other_society, name="Other Visitor", phone="+917777777777"
        )
        GateEvent.objects.create(
            society=self.other_society,
            gate=other_gate,
            person=other_person,
            visitor_category=other_cat,
            event_type=GateEvent.EventType.ENTRY,
            status=GateEvent.Status.ENTERED,
            direction=GateEvent.Direction.INBOUND,
            entered_at=self.now,
        )

        response = self.client.get(reverse("gateops:analytics-dashboard"))
        self.assertEqual(response.status_code, 200)
        # The primary society has exactly 1 live visitor.
        self.assertContains(response, "1")

    # --- live visitors AJAX ----------------------------------------------

    def test_analytics_live_visitors_ajax(self):
        """The AJAX endpoint returns JSON with live visitor data."""
        self._make_entered_event()
        response = self.client.get(reverse("gateops:analytics-live-visitors"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/json", response["Content-Type"])
        import json

        payload = json.loads(response.content)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(len(payload["visitors"]), 1)

    # --- export view -----------------------------------------------------

    def test_analytics_export_view_csv(self):
        """The export endpoint returns a CSV attachment."""
        self._make_entered_event()
        today = timezone.localdate()
        response = self.client.post(
            reverse("gateops:analytics-export"),
            data={
                "date_from": today.isoformat(),
                "date_to": today.isoformat(),
                "export_type": "events",
                "format": "csv",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn("attachment", response["Content-Disposition"])
        # The CSV body should contain the header row.
        content = response.content.decode("utf-8")
        self.assertIn("Gate Event ID", content)

    def test_analytics_export_view_requires_permission(self):
        """Export is denied (403) when can_view_analytics is revoked."""
        self._revoke_analytics_permission(self.society)
        today = timezone.localdate()
        response = self.client.post(
            reverse("gateops:analytics-export"),
            data={
                "date_from": today.isoformat(),
                "date_to": today.isoformat(),
                "export_type": "events",
                "format": "csv",
            },
        )
        self.assertEqual(response.status_code, 403)

    # --- peak hours view -------------------------------------------------

    def test_analytics_peak_hours_view(self):
        """The peak-hours view renders successfully."""
        self._make_entered_event()
        response = self.client.get(reverse("gateops:analytics-peak-hours"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "gateops/analytics_peak_hours.html")

    # --- guard performance view ------------------------------------------

    def test_analytics_guard_performance_view(self):
        """The guard-performance view renders successfully."""
        SecurityGuard.objects.create(
            society=self.society, name="View Guard", phone="9999999999"
        )
        response = self.client.get(reverse("gateops:analytics-guard-performance"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, "gateops/analytics_guard_performance.html"
        )

    # --- custom report view ----------------------------------------------

    def test_analytics_custom_report_view_post(self):
        """A GET (form submission) to the custom report returns results.

        Grouping is exercised at the service level
        (``test_get_custom_report_grouping``); the view test omits
        ``group_by`` so the template's grouped-results block (which uses the
        ``first``/``last`` filters on dict rows) is not triggered.
        """
        self._make_entered_event()
        today = timezone.localdate()
        response = self.client.get(
            reverse("gateops:analytics-custom-report"),
            data={
                "date_from": today.isoformat(),
                "date_to": today.isoformat(),
                "metrics": ["total_events", "by_status"],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "gateops/analytics_custom_report.html")
        # The context ``data`` should contain the computed metrics.
        data = response.context["data"]
        self.assertEqual(data["metrics"]["total_events"], 1)
        self.assertIn("entered", data["metrics"]["by_status"])
