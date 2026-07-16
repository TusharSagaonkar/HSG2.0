"""Service layer for analytics (Phase 13 — Analytics).

Read-only queries against :class:`GateEvent`, :class:`RuleEvaluation`,
:class:`AnomalyDetection`, :class:`VisitorPattern`, and
:class:`PeakHourPrediction`.  Snapshot generation writes only to the
:class:`AnalyticsSnapshot` table.

Design principles (from the Phase 13 design doc):

- **Read-only by default.**  Analytics queries never write to the database.
  The only write path is :meth:`AnalyticsService.generate_snapshot` /
  :meth:`AnalyticsService.get_or_create_snapshot`, which operate exclusively
  on the ``AnalyticsSnapshot`` table.
- **Multi-tenant safety.**  Every query is society-scoped.  Every method
  accepts ``*, society`` as its first keyword argument.
- **Service contract.**  All methods are ``@staticmethod``, use keyword-only
  args, and wrap writes in ``@transaction.atomic``.
- **Graceful empty handling.**  Methods return zeros / empty dicts / empty
  lists — never ``None`` — so callers can render dashboards without
  null-checks.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Avg, Count, Q
from django.utils import timezone

from gateops.models import (
    AnalyticsSnapshot,
    AnomalyDetection,
    GateEvent,
    PeakHourPrediction,
    RuleEvaluation,
    VisitorPattern,
)
from gateops.models.model_GateOpsAuditLog import GateOpsAuditLog

logger = logging.getLogger(__name__)

# Actions that count as "violations" (anything that is not a clean
# auto-approve or a no-match).  Used by guard-performance and
# rule-violation stats.
_VIOLATION_ACTIONS = (
    RuleEvaluation.ActionTaken.REJECT,
    RuleEvaluation.ActionTaken.REQUIRE_APPROVAL,
    RuleEvaluation.ActionTaken.REQUIRE_RESIDENT_APPROVAL,
    RuleEvaluation.ActionTaken.FLAG_FOR_REVIEW,
    RuleEvaluation.ActionTaken.ESCALATE,
    RuleEvaluation.ActionTaken.EMERGENCY_OVERRIDE,
    RuleEvaluation.ActionTaken.NOTIFY_SECURITY,
)


class AnalyticsService:
    """Read-only analytics queries + snapshot generation.

    All methods accept ``*, society`` as the first keyword argument for
    multi-tenant safety.  No method mutates any model other than
    :class:`AnalyticsSnapshot`.
    """

    # ------------------------------------------------------------------ #
    # 1. Live visitors
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_live_visitors(*, society, filters=None) -> dict:
        """Return real-time data about persons currently inside the society.

        Queries :class:`GateEvent` for events with ``status=ENTERED``
        (entered but not yet exited).  This is the data source for the
        AJAX-polled live counter on the dashboard.

        ``filters`` (optional dict) accepts:

        - ``gate_id`` — filter by entry gate.
        - ``visitor_category_id`` — filter by visitor category.

        Returns a dict with ``count``, ``by_category``, ``by_gate``, and
        ``visitors`` (list of serialized event dicts).
        """
        filters = filters or {}
        qs = (
            GateEvent.objects.filter(
                society=society,
                status=GateEvent.Status.ENTERED,
            )
            .select_related("person", "gate", "visitor_category", "gate_vehicle")
            .order_by("-entered_at")
        )
        if filters.get("gate_id"):
            qs = qs.filter(gate_id=filters["gate_id"])
        if filters.get("visitor_category_id"):
            qs = qs.filter(visitor_category_id=filters["visitor_category_id"])

        now = timezone.now()
        visitors = []
        for event in qs:
            duration = (
                (now - event.entered_at).total_seconds() / 60
                if event.entered_at
                else 0
            )
            visitors.append(
                {
                    "gate_event_id": event.id,
                    "gate_event_uuid": str(event.event_uuid),
                    "person_name": event.person.name if event.person else "Unknown",
                    "person_id": event.person_id,
                    "visitor_category": (
                        event.visitor_category.name
                        if event.visitor_category
                        else None
                    ),
                    "gate_name": event.gate.name if event.gate else None,
                    "gate_id": event.gate_id,
                    "entered_at": (
                        event.entered_at.isoformat() if event.entered_at else None
                    ),
                    "duration_minutes": round(duration),
                    "event_type": event.event_type,
                    "vehicle_number": (
                        event.gate_vehicle.vehicle_number
                        if event.gate_vehicle
                        else None
                    ),
                }
            )

        # Aggregations computed DB-side for efficiency.
        by_category = dict(
            qs.exclude(visitor_category__isnull=True)
            .values("visitor_category__name")
            .annotate(c=Count("id"))
            .values_list("visitor_category__name", "c")
        )
        by_gate = dict(
            qs.exclude(gate__isnull=True)
            .values("gate__name")
            .annotate(c=Count("id"))
            .values_list("gate__name", "c")
        )

        return {
            "count": len(visitors),
            "by_category": by_category,
            "by_gate": by_gate,
            "visitors": visitors,
        }

    # ------------------------------------------------------------------ #
    # 2. Peak hours
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_peak_hours(*, society, date_from=None, date_to=None) -> dict:
        """Return hourly (0-23) and daily (0-6) traffic distribution.

        ``date_from`` / ``date_to`` default to today.  Actual traffic is
        derived from :class:`GateEvent` timestamps; predicted traffic is
        overlaid from :class:`PeakHourPrediction` for the weekdays in the
        range.
        """
        today = timezone.localdate()
        start_date = date_from or today
        end_date = date_to or today

        # --- Actual: GROUP BY hour of entered_at ---
        qs = GateEvent.objects.filter(
            society=society,
            status__in=[
                GateEvent.Status.ENTERED,
                GateEvent.Status.EXITED,
                GateEvent.Status.AUTO_CLOSED,
            ],
            entered_at__date__gte=start_date,
            entered_at__date__lte=end_date,
        )

        actual_hourly = {str(h): 0 for h in range(24)}
        actual_daily = {str(d): 0 for d in range(7)}
        for event in qs:
            if event.entered_at:
                actual_hourly[str(event.entered_at.hour)] += 1
                actual_daily[str(event.entered_at.weekday())] += 1

        total_entries = sum(actual_hourly.values())
        peak_hour = (
            max(actual_hourly, key=actual_hourly.get)
            if total_entries > 0
            else 0
        )
        peak_hour_count = actual_hourly[peak_hour] if total_entries > 0 else 0

        # --- Predicted: average PeakHourPrediction across matching days ---
        days_in_range = set()
        current = start_date
        while current <= end_date:
            days_in_range.add(current.weekday())
            current += timedelta(days=1)

        predicted_qs = PeakHourPrediction.objects.filter(
            society=society,
            day_of_week__in=list(days_in_range),
            is_active=True,
        )
        predicted = {str(h): 0 for h in range(24)}
        day_count = len(days_in_range)
        if day_count > 0:
            for pred in predicted_qs:
                predicted[str(pred.hour)] += pred.predicted_count
            # Average across the number of distinct days in the range.
            for h in range(24):
                predicted[str(h)] = round(predicted[str(h)] / day_count, 1)

        return {
            "actual": {
                "hourly": actual_hourly,
                "daily": actual_daily,
            },
            "predicted": predicted,
            "peak_hour": int(peak_hour),
            "peak_hour_count": peak_hour_count,
            "total_entries": total_entries,
            "date_range": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
        }

    # ------------------------------------------------------------------ #
    # 3. Guard performance
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_guard_performance(
        *, society, guard=None, date_from=None, date_to=None
    ) -> dict:
        """Return per-guard throughput metrics for a date range.

        ``date_from`` / ``date_to`` default to today.  ``guard`` optionally
        filters to a single :class:`SecurityGuard`.

        Returns a dict with ``guards`` (list of per-guard metric dicts) and
        ``totals`` (aggregate counts).
        """
        from gateops.models import SecurityGuard

        today = timezone.localdate()
        start_date = date_from or today
        end_date = date_to or today

        guards_qs = SecurityGuard.objects.filter(society=society, is_active=True)
        if guard is not None:
            guards_qs = guards_qs.filter(pk=guard.pk)

        # Entries: GateEvents where the guard processed the entry.
        entries_qs = (
            GateEvent.objects.filter(
                society=society,
                entered_at__date__gte=start_date,
                entered_at__date__lte=end_date,
            )
            .exclude(guard__isnull=True)
            .values("guard")
            .annotate(count=Count("id"))
        )

        # Exits: GateEvents where the guard processed the exit.
        exits_qs = (
            GateEvent.objects.filter(
                society=society,
                exited_at__date__gte=start_date,
                exited_at__date__lte=end_date,
            )
            .exclude(guard__isnull=True)
            .values("guard")
            .annotate(count=Count("id"))
        )

        # Approvals: events that reached APPROVED/ENTERED/EXITED status
        # (i.e. were allowed in) processed by this guard.
        approvals_qs = (
            GateEvent.objects.filter(
                society=society,
                status__in=[
                    GateEvent.Status.APPROVED,
                    GateEvent.Status.ENTERED,
                    GateEvent.Status.EXITED,
                    GateEvent.Status.AUTO_CLOSED,
                ],
                entered_at__date__gte=start_date,
                entered_at__date__lte=end_date,
            )
            .exclude(guard__isnull=True)
            .values("guard")
            .annotate(count=Count("id"))
        )

        # Rejections: events rejected at the gate.
        rejections_qs = (
            GateEvent.objects.filter(
                society=society,
                status=GateEvent.Status.REJECTED,
                arrived_at__date__gte=start_date,
                arrived_at__date__lte=end_date,
            )
            .exclude(guard__isnull=True)
            .values("guard")
            .annotate(count=Count("id"))
        )

        # Rule violations: RuleEvaluations linked to events processed by
        # this guard, excluding clean auto-approves and no-matches.
        violations_qs = (
            RuleEvaluation.objects.filter(
                society=society,
                gate_event__guard__isnull=False,
                evaluated_at__date__gte=start_date,
                evaluated_at__date__lte=end_date,
            )
            .exclude(
                action_taken__in=[
                    RuleEvaluation.ActionTaken.AUTO_APPROVE,
                    RuleEvaluation.ActionTaken.NO_MATCH,
                ]
            )
            .values("gate_event__guard")
            .annotate(count=Count("id"))
        )

        # Average processing time from RuleEvaluation.execution_time_ms.
        avg_time_qs = (
            RuleEvaluation.objects.filter(
                society=society,
                gate_event__guard__isnull=False,
                evaluated_at__date__gte=start_date,
                evaluated_at__date__lte=end_date,
            )
            .values("gate_event__guard")
            .annotate(avg_time=Avg("execution_time_ms"))
        )

        # Build lookup dicts keyed by guard PK.
        entries_map = {item["guard"]: item["count"] for item in entries_qs}
        exits_map = {item["guard"]: item["count"] for item in exits_qs}
        approvals_map = {item["guard"]: item["count"] for item in approvals_qs}
        rejections_map = {item["guard"]: item["count"] for item in rejections_qs}
        violations_map = {
            item["gate_event__guard"]: item["count"] for item in violations_qs
        }
        avg_time_map = {
            item["gate_event__guard"]: item["avg_time"] for item in avg_time_qs
        }

        guards = []
        totals = {
            "entries_processed": 0,
            "exits_processed": 0,
            "approvals_given": 0,
            "rejections_given": 0,
            "rule_violations_triggered": 0,
        }

        for g in guards_qs:
            g_id = g.id
            avg_ms = avg_time_map.get(g_id)
            entry = {
                "guard_id": g_id,
                "guard_name": g.name,
                "entries_processed": entries_map.get(g_id, 0),
                "exits_processed": exits_map.get(g_id, 0),
                "approvals_given": approvals_map.get(g_id, 0),
                "rejections_given": rejections_map.get(g_id, 0),
                "rule_violations_triggered": violations_map.get(g_id, 0),
                "avg_processing_time_ms": (
                    round(avg_ms, 2) if avg_ms is not None else None
                ),
            }
            guards.append(entry)
            for key in totals:
                totals[key] += entry[key]

        return {"guards": guards, "totals": totals}

    # ------------------------------------------------------------------ #
    # 4. Custom report
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_custom_report(
        *,
        society,
        metrics,
        date_from,
        date_to,
        group_by=None,
        filters=None,
    ) -> dict:
        """Return a flexible, user-filtered report of gate events.

        ``metrics`` is a list of metric keys to compute (e.g.
        ``["total_events", "by_status", "by_visitor_category"]``).
        ``group_by`` optionally groups results by one of
        ``gate`` / ``category`` / ``guard`` / ``hour`` / ``day`` / ``status``.
        ``filters`` is an optional dict of dimension filters.
        """
        filters = filters or {}
        qs = AnalyticsService._apply_date_range(
            GateEvent.objects.filter(society=society),
            date_from,
            date_to,
            field="entered_at",
        )
        qs = AnalyticsService._apply_filters(qs, filters)

        # --- Grouped series (if group_by is set) ---
        grouped = []
        if group_by:
            group_map = {
                "gate": "gate__name",
                "category": "visitor_category__name",
                "guard": "guard__name",
                "hour": "entered_at__hour",
                "day": "entered_at__date",
                "status": "status",
            }
            group_field = group_map.get(group_by)
            if group_field is not None:
                grouped = list(
                    qs.exclude(**{f"{group_field.split('__')[0]}__isnull": True})
                    .values(group_field)
                    .annotate(count=Count("id"))
                    .order_by(group_field)
                )

        # --- Metric computation ---
        result_metrics = {}
        metric_set = set(metrics)

        if "total_events" in metric_set:
            result_metrics["total_events"] = qs.count()

        if "by_status" in metric_set:
            result_metrics["by_status"] = dict(
                qs.values("status")
                .annotate(c=Count("id"))
                .values_list("status", "c")
            )

        if "by_visitor_category" in metric_set:
            result_metrics["by_visitor_category"] = dict(
                qs.exclude(visitor_category__isnull=True)
                .values("visitor_category__name")
                .annotate(c=Count("id"))
                .values_list("visitor_category__name", "c")
            )

        if "by_gate" in metric_set:
            result_metrics["by_gate"] = dict(
                qs.exclude(gate__isnull=True)
                .values("gate__name")
                .annotate(c=Count("id"))
                .values_list("gate__name", "c")
            )

        if "by_event_type" in metric_set:
            result_metrics["by_event_type"] = dict(
                qs.values("event_type")
                .annotate(c=Count("id"))
                .values_list("event_type", "c")
            )

        if "by_guard" in metric_set:
            result_metrics["by_guard"] = dict(
                qs.exclude(guard__isnull=True)
                .values("guard__name")
                .annotate(c=Count("id"))
                .values_list("guard__name", "c")
            )

        if "by_hour" in metric_set:
            hourly = {str(h): 0 for h in range(24)}
            for event in qs:
                if event.entered_at:
                    hourly[str(event.entered_at.hour)] += 1
            result_metrics["by_hour"] = hourly

        if "by_day" in metric_set:
            result_metrics["by_day"] = dict(
                qs.exclude(entered_at__isnull=True)
                .values("entered_at__date")
                .annotate(c=Count("id"))
                .order_by("entered_at__date")
                .values_list("entered_at__date", "c")
            )

        # --- Items list (serialized events) ---
        items_qs = (
            qs.select_related(
                "person", "gate", "visitor_category", "gate_vehicle"
            ).order_by("-entered_at")
        )
        items = []
        for event in items_qs:
            duration = None
            if event.entered_at and event.exited_at:
                duration = (event.exited_at - event.entered_at).total_seconds() / 60
            items.append(
                {
                    "gate_event_id": event.id,
                    "gate_event_uuid": str(event.event_uuid),
                    "person_name": event.person.name if event.person else "Unknown",
                    "visitor_category": (
                        event.visitor_category.name
                        if event.visitor_category
                        else None
                    ),
                    "gate_name": event.gate.name if event.gate else None,
                    "event_type": event.event_type,
                    "status": event.status,
                    "direction": event.direction,
                    "arrived_at": (
                        event.arrived_at.isoformat() if event.arrived_at else None
                    ),
                    "entered_at": (
                        event.entered_at.isoformat() if event.entered_at else None
                    ),
                    "exited_at": (
                        event.exited_at.isoformat() if event.exited_at else None
                    ),
                    "duration_minutes": round(duration) if duration else None,
                    "vehicle_number": (
                        event.gate_vehicle.vehicle_number
                        if event.gate_vehicle
                        else None
                    ),
                }
            )

        return {
            "metrics": result_metrics,
            "grouped": grouped,
            "group_by": group_by,
            "items": items,
            "filters": {
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                **{k: v for k, v in filters.items() if v is not None},
            },
        }

    # ------------------------------------------------------------------ #
    # 5. Rule violation stats
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_rule_violation_stats(
        *, society, date_from=None, date_to=None
    ) -> dict:
        """Return aggregated rule-evaluation counts for a date range.

        Returns counts by action, top violated rules, daily trend, error
        count, and average execution time.
        """
        today = timezone.localdate()
        start_date = date_from or today
        end_date = date_to or today

        qs = RuleEvaluation.objects.filter(
            society=society,
            evaluated_at__date__gte=start_date,
            evaluated_at__date__lte=end_date,
        )

        by_action = dict(
            qs.values("action_taken")
            .annotate(c=Count("id"))
            .values_list("action_taken", "c")
        )

        # Top violated rules: rules with most violation actions.
        top_rules = (
            qs.filter(action_taken__in=_VIOLATION_ACTIONS, rule__isnull=False)
            .values("rule__id", "rule__name")
            .annotate(violation_count=Count("id"))
            .order_by("-violation_count")[:10]
        )
        top_violated = [
            {
                "rule_id": item["rule__id"],
                "rule_name": item["rule__name"],
                "violation_count": item["violation_count"],
            }
            for item in top_rules
        ]

        # Violations by gate (via the linked gate_event).
        by_gate = dict(
            qs.filter(
                action_taken__in=_VIOLATION_ACTIONS,
                gate_event__isnull=False,
                gate_event__gate__isnull=False,
            )
            .values("gate_event__gate__name")
            .annotate(c=Count("id"))
            .values_list("gate_event__gate__name", "c")
        )

        # Daily trend.
        daily = dict(
            qs.values("evaluated_at__date")
            .annotate(c=Count("id"))
            .values_list("evaluated_at__date", "c")
        )
        daily_trend = {k.isoformat(): v for k, v in sorted(daily.items())}

        error_count = qs.filter(
            action_taken=RuleEvaluation.ActionTaken.ERROR
        ).count()
        avg_time = qs.aggregate(avg=Avg("execution_time_ms"))["avg"]

        return {
            "total_evaluations": qs.count(),
            "by_action": by_action,
            "top_violated_rules": top_violated,
            "by_gate": by_gate,
            "daily_trend": daily_trend,
            "error_count": error_count,
            "avg_execution_time_ms": round(avg_time, 2) if avg_time else 0.0,
        }

    # ------------------------------------------------------------------ #
    # 6. Anomaly stats
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_anomaly_stats(*, society, date_from=None, date_to=None) -> dict:
        """Return breakdown of :class:`AnomalyDetection` records.

        Counts by type, severity, and status, plus resolution rate and
        critical-open count.
        """
        today = timezone.localdate()
        start_date = date_from or today
        end_date = date_to or today

        qs = AnomalyDetection.objects.filter(
            society=society,
            is_active=True,
            detected_at__date__gte=start_date,
            detected_at__date__lte=end_date,
        )

        total = qs.count()
        by_type = dict(
            qs.values("anomaly_type")
            .annotate(c=Count("id"))
            .values_list("anomaly_type", "c")
        )
        by_severity = dict(
            qs.values("severity")
            .annotate(c=Count("id"))
            .values_list("severity", "c")
        )
        by_status = dict(
            qs.values("status")
            .annotate(c=Count("id"))
            .values_list("status", "c")
        )

        resolved = by_status.get(AnomalyDetection.Status.RESOLVED, 0)
        false_positive = by_status.get(
            AnomalyDetection.Status.FALSE_POSITIVE, 0
        )
        resolution_rate = (
            round(((resolved + false_positive) / total * 100), 1)
            if total > 0
            else 0.0
        )

        critical_open = qs.filter(
            severity=AnomalyDetection.Severity.CRITICAL,
            status=AnomalyDetection.Status.OPEN,
        ).count()

        return {
            "total_anomalies": total,
            "by_type": by_type,
            "by_severity": by_severity,
            "by_status": by_status,
            "resolution_rate": resolution_rate,
            "critical_open": critical_open,
        }

    # ------------------------------------------------------------------ #
    # 7. Visitor trends
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_visitor_trends(
        *, society, date_from, date_to, granularity="daily"
    ) -> dict:
        """Return time-series of visitor counts (daily/weekly/monthly).

        For ``daily`` granularity with a range ≤ 31 days, queries
        :class:`GateEvent` directly.  For ``weekly`` / ``monthly``
        granularity, or ranges > 31 days, reads from pre-computed
        :class:`AnalyticsSnapshot` rows for performance.
        """
        granularity = granularity.lower()
        range_days = (date_to - date_from).days + 1

        if granularity == "daily" and range_days <= 31:
            # Live query for short ranges.
            data_points = []
            current = date_from
            while current <= date_to:
                day_qs = GateEvent.objects.filter(
                    society=society,
                    entered_at__date=current,
                )
                entries = day_qs.filter(
                    status__in=[
                        GateEvent.Status.ENTERED,
                        GateEvent.Status.EXITED,
                        GateEvent.Status.AUTO_CLOSED,
                    ]
                ).count()
                exits = day_qs.filter(
                    status__in=[
                        GateEvent.Status.EXITED,
                        GateEvent.Status.AUTO_CLOSED,
                    ]
                ).count()
                auto_closed = day_qs.filter(
                    status=GateEvent.Status.AUTO_CLOSED
                ).count()
                rejected = day_qs.filter(
                    status=GateEvent.Status.REJECTED
                ).count()
                data_points.append(
                    {
                        "date": current.isoformat(),
                        "entries": entries,
                        "exits": exits,
                        "auto_closed": auto_closed,
                        "rejected": rejected,
                    }
                )
                current += timedelta(days=1)
        else:
            # Read from pre-computed snapshots.
            snap_type = {
                "daily": AnalyticsSnapshot.SnapshotType.DAILY,
                "weekly": AnalyticsSnapshot.SnapshotType.WEEKLY,
                "monthly": AnalyticsSnapshot.SnapshotType.MONTHLY,
            }.get(granularity, AnalyticsSnapshot.SnapshotType.DAILY)

            snapshots = AnalyticsSnapshot.objects.filter(
                society=society,
                is_active=True,
                date__gte=date_from,
                date__lte=date_to,
                snapshot_type=snap_type,
            ).order_by("date")

            data_points = []
            for snap in snapshots:
                m = snap.metrics
                data_points.append(
                    {
                        "date": snap.date.isoformat(),
                        "entries": m.get("total_entries", 0),
                        "exits": m.get("total_exits", 0),
                        "auto_closed": m.get("auto_closed", 0),
                        "rejected": m.get("rejected", 0),
                    }
                )

        # Risk distribution from VisitorPattern.
        risk_qs = VisitorPattern.objects.filter(society=society, is_active=True)
        risk_distribution = dict(
            risk_qs.values("risk_level")
            .annotate(c=Count("id"))
            .values_list("risk_level", "c")
        )

        totals = {
            "entries": sum(dp["entries"] for dp in data_points),
            "exits": sum(dp["exits"] for dp in data_points),
            "auto_closed": sum(dp["auto_closed"] for dp in data_points),
            "rejected": sum(dp["rejected"] for dp in data_points),
        }

        return {
            "granularity": granularity,
            "data_points": data_points,
            "risk_distribution": risk_distribution,
            "totals": totals,
        }

    # ------------------------------------------------------------------ #
    # 8. Snapshot generation
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def generate_snapshot(
        *, society, date, snapshot_type="daily", actor=None
    ) -> AnalyticsSnapshot:
        """Generate and save a new :class:`AnalyticsSnapshot`.

        Soft-deletes any existing active snapshot for the same
        ``(society, date, snapshot_type)`` tuple before creating the new
        one.  This is the only write path for the analytics module.
        """
        # Soft-delete any existing active snapshot for this tuple.
        AnalyticsSnapshot.objects.filter(
            society=society,
            date=date,
            snapshot_type=snapshot_type,
            is_active=True,
        ).update(is_active=False, deleted_at=timezone.now())

        # Compute the date range for this snapshot.
        if snapshot_type == AnalyticsSnapshot.SnapshotType.DAILY:
            start = date
            end = date
        elif snapshot_type == AnalyticsSnapshot.SnapshotType.WEEKLY:
            start = date  # date is already a Monday (validated by clean())
            end = date + timedelta(days=6)
        elif snapshot_type == AnalyticsSnapshot.SnapshotType.MONTHLY:
            start = date  # date is the 1st
            # End of month: go to next month, subtract 1 day.
            if date.month == 12:
                end = date.replace(day=31)
            else:
                end = date.replace(month=date.month + 1) - timedelta(days=1)
        else:
            start = date
            end = date

        # Gather metrics.
        metrics = AnalyticsService._compute_metrics(
            society=society, date_from=start, date_to=end
        )

        snapshot = AnalyticsSnapshot.objects.create(
            society=society,
            date=date,
            snapshot_type=snapshot_type,
            metrics=metrics,
        )

        # Audit log — failure must not block snapshot creation.
        try:
            GateOpsAuditLog.log(
                society=society,
                action=GateOpsAuditLog.Action.CREATE,
                entity_type="AnalyticsSnapshot",
                entity_id=snapshot.id,
                before_value=None,
                after_value={
                    "snapshot_type": snapshot_type,
                    "date": date.isoformat(),
                },
                actor=actor,
            )
        except Exception:
            logger.exception(
                "Failed to write audit log for snapshot %s (society=%s).",
                snapshot.id,
                society.pk,
            )

        return snapshot

    # ------------------------------------------------------------------ #
    # 9. Idempotent snapshot get-or-create
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_or_create_snapshot(
        *, society, date, snapshot_type="daily", actor=None
    ) -> AnalyticsSnapshot:
        """Return the existing active snapshot or generate a new one.

        Idempotent entry point used by the management command and by views
        that need historical data.
        """
        existing = AnalyticsSnapshot.objects.filter(
            society=society,
            date=date,
            snapshot_type=snapshot_type,
            is_active=True,
        ).first()
        if existing:
            return existing
        return AnalyticsService.generate_snapshot(
            society=society,
            date=date,
            snapshot_type=snapshot_type,
            actor=actor,
        )

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _apply_date_range(queryset, date_from, date_to, field="entered_at"):
        """Apply a date-range filter to a queryset.

        ``field`` is the datetime field to filter on (default
        ``entered_at``).  Uses ``__date`` lookups so callers can pass
        plain ``date`` objects.
        """
        filters = {}
        if date_from is not None:
            filters[f"{field}__date__gte"] = date_from
        if date_to is not None:
            filters[f"{field}__date__lte"] = date_to
        return queryset.filter(**filters)

    @staticmethod
    def _apply_filters(queryset, filters):
        """Apply optional dimension filters to a GateEvent queryset.

        ``filters`` is a dict that may contain ``gate_id``,
        ``visitor_category_id``, ``event_type``, ``status``, ``guard_id``,
        and ``direction``.  Falsy values are skipped.
        """
        if not filters:
            return queryset

        if filters.get("gate_id"):
            queryset = queryset.filter(gate_id=filters["gate_id"])
        if filters.get("visitor_category_id"):
            queryset = queryset.filter(
                visitor_category_id=filters["visitor_category_id"]
            )
        if filters.get("event_type"):
            queryset = queryset.filter(event_type=filters["event_type"])
        if filters.get("status"):
            queryset = queryset.filter(status=filters["status"])
        if filters.get("guard_id"):
            queryset = queryset.filter(guard_id=filters["guard_id"])
        if filters.get("direction"):
            queryset = queryset.filter(direction=filters["direction"])
        return queryset

    @staticmethod
    def _compute_metrics(*, society, date_from, date_to) -> dict:
        """Gather all metrics for a date range.

        Private helper called by :meth:`generate_snapshot`.  Returns the
        metrics JSON dictionary that is stored on the snapshot.
        """
        qs = GateEvent.objects.filter(
            society=society,
            entered_at__date__gte=date_from,
            entered_at__date__lte=date_to,
        )

        total_entries = qs.filter(
            status__in=[
                GateEvent.Status.ENTERED,
                GateEvent.Status.EXITED,
                GateEvent.Status.AUTO_CLOSED,
            ]
        ).count()
        total_exits = qs.filter(
            status__in=[
                GateEvent.Status.EXITED,
                GateEvent.Status.AUTO_CLOSED,
            ]
        ).count()
        currently_inside = GateEvent.objects.filter(
            society=society, status=GateEvent.Status.ENTERED
        ).count()
        auto_closed = qs.filter(status=GateEvent.Status.AUTO_CLOSED).count()
        rejected = qs.filter(status=GateEvent.Status.REJECTED).count()
        cancelled = qs.filter(status=GateEvent.Status.CANCELLED).count()
        expired = qs.filter(status=GateEvent.Status.EXPIRED).count()

        by_visitor_category = dict(
            qs.exclude(visitor_category__isnull=True)
            .values("visitor_category__name")
            .annotate(c=Count("id"))
            .values_list("visitor_category__name", "c")
        )
        by_gate = dict(
            qs.exclude(gate__isnull=True)
            .values("gate__name")
            .annotate(c=Count("id"))
            .values_list("gate__name", "c")
        )
        by_event_type = dict(
            qs.values("event_type")
            .annotate(c=Count("id"))
            .values_list("event_type", "c")
        )
        by_status = dict(
            qs.values("status")
            .annotate(c=Count("id"))
            .values_list("status", "c")
        )

        # Hourly distribution.
        hourly = {str(h): 0 for h in range(24)}
        for event in qs:
            if event.entered_at:
                hourly[str(event.entered_at.hour)] += 1

        # Rule evaluations.
        rule_qs = RuleEvaluation.objects.filter(
            society=society,
            evaluated_at__date__gte=date_from,
            evaluated_at__date__lte=date_to,
        )
        rule_actions = dict(
            rule_qs.values("action_taken")
            .annotate(c=Count("id"))
            .values_list("action_taken", "c")
        )

        # Anomalies.
        anomaly_qs = AnomalyDetection.objects.filter(
            society=society,
            is_active=True,
            detected_at__date__gte=date_from,
            detected_at__date__lte=date_to,
        )
        anomalies_by_type = dict(
            anomaly_qs.values("anomaly_type")
            .annotate(c=Count("id"))
            .values_list("anomaly_type", "c")
        )
        anomalies_by_severity = dict(
            anomaly_qs.values("severity")
            .annotate(c=Count("id"))
            .values_list("severity", "c")
        )

        # Guard performance.
        guard_perf = AnalyticsService.get_guard_performance(
            society=society, date_from=date_from, date_to=date_to
        )["guards"]

        # Peak hour.
        peak_hour = max(hourly, key=hourly.get) if any(hourly.values()) else 0
        peak_hour_count = hourly[peak_hour] if any(hourly.values()) else 0

        span_days = max((date_to - date_from).days + 1, 1)

        return {
            "total_entries": total_entries,
            "total_exits": total_exits,
            "currently_inside": currently_inside,
            "auto_closed": auto_closed,
            "rejected": rejected,
            "cancelled": cancelled,
            "expired": expired,
            "by_visitor_category": by_visitor_category,
            "by_gate": by_gate,
            "by_event_type": by_event_type,
            "by_status": by_status,
            "hourly_distribution": hourly,
            "rule_evaluations": rule_qs.count(),
            "rule_actions": rule_actions,
            "anomalies_detected": anomaly_qs.count(),
            "anomalies_by_type": anomalies_by_type,
            "anomalies_by_severity": anomalies_by_severity,
            "guard_performance": guard_perf,
            "peak_hour": int(peak_hour),
            "peak_hour_count": peak_hour_count,
            "avg_daily_visitors": round(total_entries / span_days, 1),
        }
