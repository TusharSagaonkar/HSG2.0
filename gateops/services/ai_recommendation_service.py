"""Service layer for the AI Recommendation Engine (Phase 11).

This service is the single authority over :class:`VisitorPattern`,
:class:`AnomalyDetection`, and :class:`PeakHourPrediction` lifecycle
operations. No caller should mutate these models directly — every
state-changing operation must flow through :class:`AIRecommendationService`
so that:

1. Multi-tenant safety is enforced (every query scoped by ``society``).
2. Historical :class:`GateEvent` data is read for the society.
3. Analysis is performed (pattern detection / anomaly scan / risk scoring /
   peak-hour prediction).
4. Results are persisted (VisitorPattern / AnomalyDetection /
   PeakHourPrediction).
5. A :class:`GateOpsAuditLog` entry is written (append-only, non-blocking).
6. Notifications are dispatched for critical anomalies (non-blocking).

Design notes
------------
- **Multi-tenant safety:** every query is scoped by ``society``. A pattern
  or anomaly recorded in one society can never be looked up or mutated from
  another society's context.
- **Race safety:** pattern upserts use ``update_or_create`` (race-safe via
  the conditional unique constraint); anomaly and prediction upserts follow
  the same pattern. Status transitions use ``QuerySet.update()`` (not
  ``save()``) so concurrent operations cannot lose updates.
- **Non-blocking audit:** audit-log writes are wrapped so a logging failure
  never blocks a legitimate AI operation (the error is logged loudly
  instead).
- **Non-blocking notifications:** anomaly notifications are dispatched via
  :class:`NotificationEngineService` wrapped in ``try/except`` so a
  notification failure never blocks anomaly creation.
- **All methods are ``@staticmethod``** per the service contract; there is no
  shared mutable state.
- **Deduplication:** before creating an :class:`AnomalyDetection` row, the
  service checks for an existing open anomaly of the same type for the same
  gate event (or person). If one exists, the new anomaly is skipped.
- **EWMA peak-hour prediction:** uses an exponentially weighted moving
  average with a decay factor of 0.85 per week, giving more recent weeks
  higher weight. Confidence is proportional to the number of weeks of data.
- **Composite risk scoring:** eight weighted factors (weights sum to 1.0)
  are combined into a single 0.0–1.0 risk score, clamped and mapped to a
  risk level (LOW / MEDIUM / HIGH / CRITICAL).
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import timedelta
from statistics import median

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, QuerySet
from django.shortcuts import get_object_or_404
from django.utils import timezone

from gateops.models import (
    AnomalyDetection,
    GateEvent,
    GateOpsAuditLog,
    GateOpsSocietyConfig,
    GateVehicle,
    NotificationPreference,
    PeakHourPrediction,
    Person,
    VisitorCategory,
    VisitorPattern,
)
from gateops.services.notification_engine import NotificationEngineService

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants (from design doc §6 — Default Thresholds)
# --------------------------------------------------------------------------- #

DEFAULT_ANALYSIS_WINDOW_DAYS = 90
DEFAULT_ANOMALY_SCAN_WINDOW_HOURS = 24
DEFAULT_FORGOTTEN_EXIT_THRESHOLD_HOURS = 12
DEFAULT_FREQUENT_VISITOR_THRESHOLD = 5
DEFAULT_FREQUENT_VISITOR_SPAN_DAYS = 7
DEFAULT_FREQUENCY_SPIKE_RATIO = 2.0
DEFAULT_LONG_STAY_PERCENTILE = 95
DEFAULT_PEAK_HOUR_FORECAST_DAYS = 7
DEFAULT_EWMA_DECAY_FACTOR = 0.85
EXPECTED_MAX_VISITS_PER_WEEK = 10
MIN_DATA_POINTS_FOR_CONFIDENCE = 3
FULL_CONFIDENCE_DATA_POINTS = 12

# Risk score weights (must sum to 1.0; from design doc §3.4).
RISK_WEIGHTS: dict[str, float] = {
    "visit_frequency_anomaly": 0.20,
    "time_pattern_deviation": 0.15,
    "blacklist_watchlist_proximity": 0.20,
    "incomplete_exit_history": 0.15,
    "duration_anomaly": 0.10,
    "cross_category_visits": 0.05,
    "night_time_activity": 0.10,
    "id_verification_gaps": 0.05,
}

# Batch size for person iteration in analyze_visitor_patterns.
_PERSON_BATCH_SIZE = 100

# ISO weekday codes (0=Monday … 6=Sunday).
_WEEKDAY_CODES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


# --------------------------------------------------------------------------- #
# Module-level helpers
# --------------------------------------------------------------------------- #

def _percentile(data: list[float], p: float) -> float:
    """Compute the p-th percentile of *data* using linear interpolation.

    Returns 0.0 for empty data.  Uses the same method as NumPy's default
    ``linear`` interpolation: ``k = (n-1) * p/100``, then interpolates between
    ``floor(k)`` and ``ceil(k)``.
    """
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * p / 100
    f = int(k)
    c = k - f
    if f + 1 < len(sorted_data):
        return sorted_data[f] + c * (sorted_data[f + 1] - sorted_data[f])
    return sorted_data[f]


def _dt_iso(value) -> str | None:
    """ISO-format *value* if it is a datetime/date, else None."""
    return value.isoformat() if value else None


class AIRecommendationService:
    """Service for AI-powered visitor pattern detection, anomaly detection,
    risk scoring, and peak-hour prediction.

    Every operation:
    1. Validates multi-tenant safety (society scoping).
    2. Reads historical GateEvent data for the society.
    3. Performs analysis (pattern detection / anomaly scan / risk scoring /
       peak-hour prediction).
    4. Persists results (VisitorPattern / AnomalyDetection / PeakHourPrediction).
    5. Creates a GateOpsAuditLog entry (append-only, non-blocking).
    6. Dispatches notifications for critical anomalies (non-blocking).
    """

    # ------------------------------------------------------------------ #
    # 1. Pattern Detection
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def analyze_visitor_patterns(
        *, society, person=None, days=DEFAULT_ANALYSIS_WINDOW_DAYS, actor=None
    ) -> dict:
        """Detect and upsert VisitorPattern rows for the society.

        - If *person* is provided, analyzes only that person.
        - Otherwise, analyzes all persons with gate events in the last
          *days* days, processing in batches of 100 to avoid memory pressure.
        - Returns ``{"patterns_updated": int, "patterns_created": int,
          "errors": int}``.
        """
        now = timezone.now()
        since = now - timedelta(days=days)

        if person is not None:
            person_ids = [person.pk]
        else:
            # All persons with at least one gate event in the window.
            person_ids = list(
                GateEvent.objects.filter(
                    society=society,
                    created_at__gte=since,
                    person__isnull=False,
                )
                .values_list("person_id", flat=True)
                .distinct()
            )

        patterns_created = 0
        patterns_updated = 0
        errors = 0

        for i in range(0, len(person_ids), _PERSON_BATCH_SIZE):
            batch_ids = person_ids[i : i + _PERSON_BATCH_SIZE]
            persons = Person.objects.filter(
                id__in=batch_ids, society=society, is_active=True
            )
            for p in persons:
                try:
                    events = GateEvent.objects.filter(
                        society=society,
                        person=p,
                        status__in=[
                            GateEvent.Status.EXITED,
                            GateEvent.Status.AUTO_CLOSED,
                        ],
                        created_at__gte=since,
                    ).order_by("created_at")

                    if not events.exists():
                        continue

                    created = AIRecommendationService._update_or_create_pattern(
                        society=society, person=p, events=events, actor=actor
                    )
                    if created:
                        patterns_created += 1
                    else:
                        patterns_updated += 1
                except Exception:  # noqa: BLE001 — per-person isolation.
                    logger.exception(
                        "Failed to analyze patterns for person %s", p.pk
                    )
                    errors += 1

        return {
            "patterns_updated": patterns_updated,
            "patterns_created": patterns_created,
            "errors": errors,
        }

    @staticmethod
    def get_visitor_pattern(*, society, person) -> VisitorPattern | None:
        """Return the active VisitorPattern for *person*, or None."""
        return (
            VisitorPattern.objects.filter(
                society=society, person=person, is_active=True
            )
            .select_related("person", "visitor_category", "suggested_category")
            .first()
        )

    @staticmethod
    def list_visitor_patterns(
        *, society, is_frequent=None, risk_level=None, include_inactive=False
    ) -> QuerySet:
        """List visitor patterns for the society, optionally filtered."""
        qs = VisitorPattern.objects.filter(society=society)
        if not include_inactive:
            qs = qs.filter(is_active=True)
        if is_frequent is not None:
            qs = qs.filter(is_frequent=is_frequent)
        if risk_level is not None:
            qs = qs.filter(risk_level=risk_level)
        return qs.select_related("person", "visitor_category")

    # ------------------------------------------------------------------ #
    # 2. Anomaly Detection
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def detect_anomalies(*, society, since=None, actor=None) -> dict:
        """Scan recent GateEvents for anomalies and create AnomalyDetection rows.

        - *since* defaults to 24 hours ago.
        - Runs all eight anomaly detectors (see §3.3 of the design doc).
        - Returns ``{"anomalies_created": int, "by_type": dict, "errors": int}``.
        """
        now = timezone.now()
        if since is None:
            since = now - timedelta(hours=DEFAULT_ANOMALY_SCAN_WINDOW_HOURS)

        detectors = [
            AIRecommendationService._detect_forgotten_exits,
            AIRecommendationService._detect_after_hours_entries,
            AIRecommendationService._detect_unusual_frequency,
            AIRecommendationService._detect_blacklist_bypass,
            AIRecommendationService._detect_off_pattern_visits,
            AIRecommendationService._detect_duplicate_entries,
            AIRecommendationService._detect_long_stays,
            AIRecommendationService._detect_suspicious_patterns,
        ]

        anomalies_created = 0
        by_type: dict[str, int] = {}
        errors = 0

        for detector in detectors:
            try:
                results = detector(society=society, since=since)
                for anomaly_data in results:
                    try:
                        anomaly = AIRecommendationService._create_anomaly(
                            society=society,
                            anomaly_type=anomaly_data["anomaly_type"],
                            severity=anomaly_data["severity"],
                            gate_event=anomaly_data.get("gate_event"),
                            person=anomaly_data.get("person"),
                            gate_vehicle=anomaly_data.get("gate_vehicle"),
                            description=anomaly_data.get("description", ""),
                            context=anomaly_data.get("context", {}),
                        )
                        if anomaly is not None:
                            anomalies_created += 1
                            atype = anomaly_data["anomaly_type"]
                            by_type[atype] = by_type.get(atype, 0) + 1
                    except Exception:  # noqa: BLE001 — per-anomaly isolation.
                        logger.exception(
                            "Failed to create anomaly from %s",
                            detector.__name__,
                        )
                        errors += 1
            except Exception:  # noqa: BLE001 — per-detector isolation.
                logger.exception("Detector %s failed", detector.__name__)
                errors += 1

        # Batch-level audit log (summary).
        AIRecommendationService._log_audit(
            society=society,
            action=GateOpsAuditLog.Action.ANOMALY_DETECTED,
            entity_type="AnomalyDetection",
            entity_id="",
            after_value={
                "anomalies_created": anomalies_created,
                "by_type": by_type,
                "errors": errors,
            },
            actor=actor,
        )

        return {
            "anomalies_created": anomalies_created,
            "by_type": by_type,
            "errors": errors,
        }

    @staticmethod
    def get_anomalies(
        *, society, status=None, severity=None, anomaly_type=None,
        include_inactive=False,
    ) -> QuerySet:
        """List anomalies for the society, optionally filtered."""
        qs = AnomalyDetection.objects.filter(society=society)
        if not include_inactive:
            qs = qs.filter(is_active=True)
        if status is not None:
            qs = qs.filter(status=status)
        if severity is not None:
            qs = qs.filter(severity=severity)
        if anomaly_type is not None:
            qs = qs.filter(anomaly_type=anomaly_type)
        return qs.select_related("person", "gate_event", "gate_vehicle")

    @staticmethod
    def get_anomaly(*, society, pk) -> AnomalyDetection:
        """Return a single anomaly, scoped to the society.

        Raises ``Http404`` if not found.
        """
        return get_object_or_404(
            AnomalyDetection, society=society, pk=pk, is_active=True
        )

    @staticmethod
    @transaction.atomic
    def acknowledge_anomaly(*, anomaly, actor=None) -> AnomalyDetection:
        """Transition an OPEN anomaly to ACKNOWLEDGED."""
        old_status = anomaly.status
        if old_status != AnomalyDetection.Status.OPEN:
            raise ValidationError(
                f"Only OPEN anomalies can be acknowledged (current: {old_status})."
            )
        AnomalyDetection.objects.filter(pk=anomaly.pk).update(
            status=AnomalyDetection.Status.ACKNOWLEDGED,
        )
        anomaly.refresh_from_db()
        AIRecommendationService._log_audit(
            society=anomaly.society,
            action=GateOpsAuditLog.Action.UPDATE,
            entity_type="AnomalyDetection",
            entity_id=anomaly.pk,
            before_value={"status": old_status},
            after_value=AIRecommendationService._serialize_anomaly(anomaly),
            actor=actor,
        )
        return anomaly

    @staticmethod
    @transaction.atomic
    def resolve_anomaly(
        *, anomaly, resolved_by, resolution_notes="", is_false_positive=False
    ) -> AnomalyDetection:
        """Transition an anomaly to RESOLVED or FALSE_POSITIVE."""
        old_status = anomaly.status
        if old_status not in (
            AnomalyDetection.Status.OPEN,
            AnomalyDetection.Status.ACKNOWLEDGED,
        ):
            raise ValidationError(
                f"Only OPEN or ACKNOWLEDGED anomalies can be resolved "
                f"(current: {old_status})."
            )
        new_status = (
            AnomalyDetection.Status.FALSE_POSITIVE
            if is_false_positive
            else AnomalyDetection.Status.RESOLVED
        )
        now = timezone.now()
        AnomalyDetection.objects.filter(pk=anomaly.pk).update(
            status=new_status,
            resolved_at=now,
            resolved_by=resolved_by,
            resolution_notes=resolution_notes,
        )
        anomaly.refresh_from_db()
        AIRecommendationService._log_audit(
            society=anomaly.society,
            action=GateOpsAuditLog.Action.UPDATE,
            entity_type="AnomalyDetection",
            entity_id=anomaly.pk,
            before_value={"status": old_status},
            after_value=AIRecommendationService._serialize_anomaly(anomaly),
            actor=resolved_by,
        )
        return anomaly

    # ------------------------------------------------------------------ #
    # 3. Risk Scoring
    # ------------------------------------------------------------------ #

    @staticmethod
    def calculate_risk_score(*, society, person=None, gate_event=None) -> dict:
        """Compute the risk score for a person or a specific gate event.

        Returns ``{"risk_score": float, "risk_level": str, "factors": dict}``.

        The ``factors`` dict breaks down the score by component (see §3.4).
        """
        if gate_event is not None:
            person = gate_event.person
        if person is None:
            return {
                "risk_score": 0.0,
                "risk_level": VisitorPattern.RiskLevel.LOW,
                "factors": {},
            }

        events = GateEvent.objects.filter(
            society=society, person=person
        ).order_by("created_at")

        # Compute society-level duration percentiles (for factor 5).
        now = timezone.now()
        window_start = now - timedelta(days=DEFAULT_ANALYSIS_WINDOW_DAYS)
        society_durations: list[float] = []
        for entered, exited in (
            GateEvent.objects.filter(
                society=society,
                status__in=[
                    GateEvent.Status.EXITED,
                    GateEvent.Status.AUTO_CLOSED,
                ],
                entered_at__isnull=False,
                exited_at__isnull=False,
                entered_at__gte=window_start,
            ).values_list("entered_at", "exited_at")
        ):
            society_durations.append((exited - entered).total_seconds() / 60)

        p95_duration = _percentile(society_durations, 95)
        median_duration = median(society_durations) if society_durations else 0.0

        factors = AIRecommendationService._compute_risk_factors(
            society=society,
            person=person,
            events=events,
            _society_p95=p95_duration,
            _society_median=median_duration,
        )

        risk_score = min(
            1.0, sum(factors[f] * RISK_WEIGHTS[f] for f in RISK_WEIGHTS)
        )
        risk_level = VisitorPattern._risk_level_for_score(risk_score)

        return {
            "risk_score": round(risk_score, 4),
            "risk_level": risk_level,
            "factors": factors,
        }

    @staticmethod
    def get_risk_assessment(
        *, society, person=None, gate_vehicle=None
    ) -> dict:
        """Return the current risk assessment for a person or vehicle.

        Reads from the cached ``VisitorPattern.risk_score`` if available;
        otherwise computes on-the-fly.
        """
        if person is None and gate_vehicle is not None:
            person = gate_vehicle.person
        if person is None:
            return {
                "risk_score": 0.0,
                "risk_level": VisitorPattern.RiskLevel.LOW,
                "factors": {},
            }

        pattern = VisitorPattern.objects.filter(
            society=society, person=person, is_active=True
        ).only("risk_score", "risk_level").first()
        if pattern:
            return {
                "risk_score": pattern.risk_score,
                "risk_level": pattern.risk_level,
                "factors": {},
            }
        return AIRecommendationService.calculate_risk_score(
            society=society, person=person
        )

    # ------------------------------------------------------------------ #
    # 4. Peak-Hour Prediction
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def predict_peak_hours(
        *, society, forecast_days=DEFAULT_PEAK_HOUR_FORECAST_DAYS, actor=None
    ) -> dict:
        """Generate PeakHourPrediction rows for the next *forecast_days* days.

        - Analyzes the last 90 days of GateEvent data.
        - Returns ``{"predictions_created": int, "analysis_date": date,
          "errors": int}``.
        """
        now = timezone.now()
        today = now.date()
        window_start = now - timedelta(days=DEFAULT_ANALYSIS_WINDOW_DAYS)

        # 1. Query history — only entered_at timestamps (single column).
        timestamps = list(
            GateEvent.objects.filter(
                society=society,
                status__in=[
                    GateEvent.Status.ENTERED,
                    GateEvent.Status.EXITED,
                    GateEvent.Status.AUTO_CLOSED,
                ],
                entered_at__gte=window_start,
                entered_at__isnull=False,
            ).values_list("entered_at", flat=True)
        )

        # 2. Group by (weekday, hour, week_index).
        slot_weekly: dict[tuple[int, int], dict[int, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        for ts in timestamps:
            dow = ts.weekday()
            hour = ts.hour
            days_ago = (today - ts.date()).days
            week_idx = days_ago // 7
            slot_weekly[(dow, hour)][week_idx] += 1

        # 3. Compute EWMA and confidence for each slot.
        slot_predictions: dict[tuple[int, int], tuple[int, float]] = {}
        for (dow, hour), weekly_counts in slot_weekly.items():
            max_week = max(weekly_counts.keys())
            data_points = len(weekly_counts)

            if data_points < MIN_DATA_POINTS_FOR_CONFIDENCE:
                confidence = 0.0
            else:
                confidence = min(
                    1.0, data_points / FULL_CONFIDENCE_DATA_POINTS
                )

            # EWMA: more recent weeks weighted higher.
            # weight for week i = decay ^ i  (current week i=0 -> weight 1.0)
            numerator = 0.0
            denominator = 0.0
            for week_idx, count in weekly_counts.items():
                weight = DEFAULT_EWMA_DECAY_FACTOR ** week_idx
                numerator += count * weight
                denominator += weight

            weighted_avg = numerator / denominator if denominator > 0 else 0.0
            slot_predictions[(dow, hour)] = (round(weighted_avg), confidence)

        # 4. Generate predictions for each day/hour slot.
        predictions_created = 0
        errors = 0

        for day_offset in range(forecast_days):
            forecast_date = today + timedelta(days=day_offset)
            dow = forecast_date.weekday()
            for hour in range(24):
                key = (dow, hour)
                if key in slot_predictions:
                    predicted_count, confidence = slot_predictions[key]
                else:
                    predicted_count, confidence = 0, 0.0
                try:
                    _, created = PeakHourPrediction.objects.update_or_create(
                        society=society,
                        day_of_week=dow,
                        hour=hour,
                        analysis_date=today,
                        is_active=True,
                        defaults={
                            "predicted_count": predicted_count,
                            "confidence_score": confidence,
                        },
                    )
                    if created:
                        predictions_created += 1
                except Exception:  # noqa: BLE001 — per-slot isolation.
                    logger.exception(
                        "Failed to create prediction for dow=%s hour=%s",
                        dow,
                        hour,
                    )
                    errors += 1

        # 5. Audit log.
        AIRecommendationService._log_audit(
            society=society,
            action=GateOpsAuditLog.Action.PREDICTION_GENERATED,
            entity_type="PeakHourPrediction",
            entity_id="",
            after_value={
                "predictions_created": predictions_created,
                "analysis_date": today.isoformat(),
                "errors": errors,
            },
            actor=actor,
        )

        return {
            "predictions_created": predictions_created,
            "analysis_date": today,
            "errors": errors,
        }

    @staticmethod
    def get_peak_hour_predictions(
        *, society, analysis_date=None, day_of_week=None
    ) -> QuerySet:
        """List peak-hour predictions for the society."""
        qs = PeakHourPrediction.objects.filter(society=society, is_active=True)
        if analysis_date is not None:
            qs = qs.filter(analysis_date=analysis_date)
        if day_of_week is not None:
            qs = qs.filter(day_of_week=day_of_week)
        return qs

    # ------------------------------------------------------------------ #
    # 5. Batch Analysis (called by management command)
    # ------------------------------------------------------------------ #

    @staticmethod
    def run_full_analysis(*, society, actor=None) -> dict:
        """Run the complete AI analysis pipeline for a society.

        Executes in order:
        1. ``analyze_visitor_patterns``
        2. ``detect_anomalies``
        3. ``predict_peak_hours``

        Each step is independent — a failure in one does not abort the
        others.  Returns a combined summary dict.
        """
        results: dict[str, dict] = {}

        try:
            results["patterns"] = AIRecommendationService.analyze_visitor_patterns(
                society=society, actor=actor
            )
        except Exception:  # noqa: BLE001 — step isolation.
            logger.exception(
                "Pattern analysis failed for society %s", society.pk
            )
            results["patterns"] = {"error": "pattern_analysis_failed"}

        try:
            results["anomalies"] = AIRecommendationService.detect_anomalies(
                society=society, actor=actor
            )
        except Exception:  # noqa: BLE001 — step isolation.
            logger.exception(
                "Anomaly detection failed for society %s", society.pk
            )
            results["anomalies"] = {"error": "anomaly_detection_failed"}

        try:
            results["predictions"] = AIRecommendationService.predict_peak_hours(
                society=society, actor=actor
            )
        except Exception:  # noqa: BLE001 — step isolation.
            logger.exception(
                "Peak-hour prediction failed for society %s", society.pk
            )
            results["predictions"] = {"error": "prediction_failed"}

        return results

    # ------------------------------------------------------------------ #
    # Real-time hooks (called by GateEventLifecycleService — Phase 11 §4.1)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _check_entry_anomalies(event: GateEvent) -> None:
        """Check for immediate anomalies after entry.

        Called by ``GateEventLifecycleService.record_entry()`` after a
        successful entry transition.  Non-blocking: exceptions are caught
        by the caller so a failure never blocks entry.

        Checks:
        1. After-hours entry (if night mode is configured).
        2. Duplicate entry (multiple ENTERED without intervening EXIT).
        3. Blacklist bypass (blacklisted person was approved/entered).
        """
        society = event.society

        # 1. After-hours entry.
        night_hours = AIRecommendationService._get_night_mode_hours(society)
        if night_hours and event.entered_at:
            start_hr, end_hr = night_hours
            if AIRecommendationService._is_night_hour(
                event.entered_at.hour, start_hr, end_hr
            ):
                severity = (
                    AnomalyDetection.Severity.HIGH
                    if 0 <= event.entered_at.hour < 4
                    else AnomalyDetection.Severity.MEDIUM
                )
                AIRecommendationService._create_anomaly(
                    society=society,
                    anomaly_type=AnomalyDetection.AnomalyType.AFTER_HOURS_ENTRY,
                    severity=severity,
                    gate_event=event,
                    person=event.person,
                    gate_vehicle=event.gate_vehicle,
                    description=f"After-hours entry at {event.entered_at:%H:%M}",
                    context={
                        "entered_at": event.entered_at.isoformat(),
                        "night_mode_start": start_hr,
                        "night_mode_end": end_hr,
                        "visitor_category": (
                            event.visitor_category.code
                            if event.visitor_category_id
                            else None
                        ),
                        "gate": str(event.gate) if event.gate_id else None,
                    },
                )

        # 2. Duplicate entry.
        if event.person_id is not None:
            open_entries = (
                GateEvent.objects.filter(
                    society=society,
                    person_id=event.person_id,
                    status=GateEvent.Status.ENTERED,
                )
                .exclude(pk=event.pk)
                .select_related("gate")
            )
            if open_entries.exists():
                AIRecommendationService._create_anomaly(
                    society=society,
                    anomaly_type=AnomalyDetection.AnomalyType.DUPLICATE_ENTRY,
                    severity=AnomalyDetection.Severity.HIGH,
                    gate_event=event,
                    person=event.person,
                    description="Multiple open entries without exit",
                    context={
                        "person_id": event.person_id,
                        "open_entries": [
                            {
                                "event_id": e.pk,
                                "entered_at": _dt_iso(e.entered_at),
                                "gate": str(e.gate) if e.gate_id else None,
                            }
                            for e in open_entries
                        ],
                    },
                )

        # 3. Blacklist bypass.
        if event.person_id is not None and event.person.is_blacklisted:
            AIRecommendationService._create_anomaly(
                society=society,
                anomaly_type=AnomalyDetection.AnomalyType.BLACKLIST_BYPASS,
                severity=AnomalyDetection.Severity.CRITICAL,
                gate_event=event,
                person=event.person,
                description=(
                    f"Blacklisted person {event.person.name} was "
                    f"{event.status}"
                ),
                context={
                    "person_id": event.person_id,
                    "blacklist_reason": event.person.blacklist_reason,
                    "blacklist_until": _dt_iso(
                        event.person.blacklist_until
                    ),
                    "event_status": event.status,
                    "approved_by": (
                        str(event.approved_by) if event.approved_by_id else None
                    ),
                },
            )

    @staticmethod
    def _get_cached_risk_score(*, society, person) -> float:
        """Lightweight read from ``VisitorPattern.risk_score``.

        Returns 0.0 if no pattern exists.  Called by
        ``GateEventLifecycleService._build_rule_context()`` to inject a
        ``risk_score`` key into the rule-evaluation context (Phase 11 §4.2).
        """
        if person is None:
            return 0.0
        pattern = (
            VisitorPattern.objects.filter(
                society=society, person=person, is_active=True
            )
            .only("risk_score")
            .first()
        )
        return pattern.risk_score if pattern else 0.0

    # ------------------------------------------------------------------ #
    # Internal Helpers — Anomaly Detectors (§3.3)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _detect_forgotten_exits(*, society, since) -> list[dict]:
        """Find ENTERED events with no exit after the threshold (12 h)."""
        now = timezone.now()
        threshold = now - timedelta(hours=DEFAULT_FORGOTTEN_EXIT_THRESHOLD_HOURS)

        events = (
            GateEvent.objects.filter(
                society=society,
                status=GateEvent.Status.ENTERED,
                entered_at__lt=threshold,
            )
            .select_related("person", "gate_vehicle", "gate")
        )

        results: list[dict] = []
        for event in events:
            if event.entered_at is None:
                continue
            hours_inside = (now - event.entered_at).total_seconds() / 3600
            severity = (
                AnomalyDetection.Severity.HIGH
                if hours_inside > 24
                else AnomalyDetection.Severity.MEDIUM
            )
            results.append(
                {
                    "anomaly_type": AnomalyDetection.AnomalyType.FORGOTTEN_EXIT,
                    "severity": severity,
                    "gate_event": event,
                    "person": event.person,
                    "gate_vehicle": event.gate_vehicle,
                    "description": (
                        f"Visitor inside for {hours_inside:.1f} hours "
                        f"without exit"
                    ),
                    "context": {
                        "entered_at": event.entered_at.isoformat(),
                        "hours_inside": round(hours_inside, 1),
                        "gate": str(event.gate) if event.gate_id else None,
                        "auto_close_scheduled": event.auto_close_at is not None,
                        "auto_close_at": _dt_iso(event.auto_close_at),
                    },
                }
            )
        return results

    @staticmethod
    def _detect_after_hours_entries(*, society, since) -> list[dict]:
        """Find entries during night-mode hours."""
        night_hours = AIRecommendationService._get_night_mode_hours(society)
        if not night_hours:
            return []
        start_hr, end_hr = night_hours

        events = (
            GateEvent.objects.filter(
                society=society,
                status__in=[GateEvent.Status.ENTERED, GateEvent.Status.EXITED],
                entered_at__gte=since,
                entered_at__isnull=False,
            )
            .select_related("person", "gate_vehicle", "gate", "visitor_category")
        )

        results: list[dict] = []
        for event in events:
            if AIRecommendationService._is_night_hour(
                event.entered_at.hour, start_hr, end_hr
            ):
                severity = (
                    AnomalyDetection.Severity.HIGH
                    if 0 <= event.entered_at.hour < 4
                    else AnomalyDetection.Severity.MEDIUM
                )
                results.append(
                    {
                        "anomaly_type": (
                            AnomalyDetection.AnomalyType.AFTER_HOURS_ENTRY
                        ),
                        "severity": severity,
                        "gate_event": event,
                        "person": event.person,
                        "gate_vehicle": event.gate_vehicle,
                        "description": (
                            f"After-hours entry at {event.entered_at:%H:%M}"
                        ),
                        "context": {
                            "entered_at": event.entered_at.isoformat(),
                            "night_mode_start": start_hr,
                            "night_mode_end": end_hr,
                            "visitor_category": (
                                event.visitor_category.code
                                if event.visitor_category_id
                                else None
                            ),
                            "gate": str(event.gate) if event.gate_id else None,
                        },
                    }
                )
        return results

    @staticmethod
    def _detect_unusual_frequency(*, society, since) -> list[dict]:
        """Find persons with visit frequency spikes.

        Compares the visit count in the last 7 days against the historical
        weekly average.  If the recent count exceeds
        ``2 × max(historical_average, 1)``, flag an anomaly.
        """
        now = timezone.now()
        recent_start = now - timedelta(days=7)

        patterns = (
            VisitorPattern.objects.filter(
                society=society,
                is_active=True,
            )
            .select_related("person")
        )

        results: list[dict] = []
        for pattern in patterns:
            recent_count = GateEvent.objects.filter(
                society=society,
                person=pattern.person,
                created_at__gte=recent_start,
            ).count()

            # Historical weekly average from pattern span.
            if pattern.first_visit_at and pattern.last_visit_at:
                span_days = max(
                    (pattern.last_visit_at - pattern.first_visit_at).days, 1
                )
                historical_avg = pattern.visit_count / (span_days / 7)
            else:
                historical_avg = 0.0

            baseline = max(historical_avg, 1.0)
            if recent_count > DEFAULT_FREQUENCY_SPIKE_RATIO * baseline:
                spike_ratio = recent_count / baseline
                severity = (
                    AnomalyDetection.Severity.CRITICAL
                    if spike_ratio > 3
                    else AnomalyDetection.Severity.HIGH
                )
                results.append(
                    {
                        "anomaly_type": (
                            AnomalyDetection.AnomalyType.UNUSUAL_FREQUENCY
                        ),
                        "severity": severity,
                        "person": pattern.person,
                        "description": (
                            f"Visit frequency spike: {recent_count} visits "
                            f"in 7 days (avg: {historical_avg:.1f})"
                        ),
                        "context": {
                            "person_id": pattern.person_id,
                            "recent_visits_7d": recent_count,
                            "historical_weekly_avg": round(historical_avg, 2),
                            "spike_ratio": round(spike_ratio, 2),
                        },
                    }
                )
        return results

    @staticmethod
    def _detect_blacklist_bypass(*, society, since) -> list[dict]:
        """Find events where a blacklisted person was approved/entered."""
        events = (
            GateEvent.objects.filter(
                society=society,
                person__is_blacklisted=True,
                status__in=[
                    GateEvent.Status.APPROVED,
                    GateEvent.Status.ENTERED,
                ],
                created_at__gte=since,
            )
            .select_related("person", "approved_by")
        )

        results: list[dict] = []
        for event in events:
            results.append(
                {
                    "anomaly_type": (
                        AnomalyDetection.AnomalyType.BLACKLIST_BYPASS
                    ),
                    "severity": AnomalyDetection.Severity.CRITICAL,
                    "gate_event": event,
                    "person": event.person,
                    "description": (
                        f"Blacklisted person {event.person.name} was "
                        f"{event.status}"
                    ),
                    "context": {
                        "person_id": event.person_id,
                        "blacklist_reason": event.person.blacklist_reason,
                        "blacklist_until": _dt_iso(
                            event.person.blacklist_until
                        ),
                        "event_status": event.status,
                        "approved_by": (
                            str(event.approved_by)
                            if event.approved_by_id
                            else None
                        ),
                    },
                }
            )
        return results

    @staticmethod
    def _detect_off_pattern_visits(*, society, since) -> list[dict]:
        """Find frequent visitors visiting outside their typical pattern."""
        patterns = (
            VisitorPattern.objects.filter(
                society=society,
                is_frequent=True,
                is_active=True,
            )
            .select_related("person")
        )

        results: list[dict] = []
        for pattern in patterns:
            recent_events = (
                GateEvent.objects.filter(
                    society=society,
                    person=pattern.person,
                    created_at__gte=since,
                    arrived_at__isnull=False,
                )
                .select_related("visitor_category")
            )

            typical_days = pattern.typical_visit_days or []
            typical_window = pattern.typical_time_window or {}

            for event in recent_events:
                arrival = event.arrived_at
                actual_day_code = _WEEKDAY_CODES[arrival.weekday()]
                is_off_day = (
                    actual_day_code not in typical_days if typical_days else False
                )

                is_off_time = False
                if (
                    typical_window
                    and "start" in typical_window
                    and "end" in typical_window
                ):
                    start_h, start_m = map(
                        int, typical_window["start"].split(":")
                    )
                    end_h, end_m = map(
                        int, typical_window["end"].split(":")
                    )
                    start_min = start_h * 60 + start_m
                    end_min = end_h * 60 + end_m
                    arr_min = arrival.hour * 60 + arrival.minute
                    is_off_time = not (start_min <= arr_min <= end_min)

                if is_off_day or is_off_time:
                    severity = (
                        AnomalyDetection.Severity.MEDIUM
                        if is_off_day
                        else AnomalyDetection.Severity.LOW
                    )
                    results.append(
                        {
                            "anomaly_type": (
                                AnomalyDetection.AnomalyType.OFF_PATTERN_VISIT
                            ),
                            "severity": severity,
                            "gate_event": event,
                            "person": pattern.person,
                            "description": (
                                f"Off-pattern visit on {actual_day_code} "
                                f"at {arrival:%H:%M}"
                            ),
                            "context": {
                                "person_id": pattern.person_id,
                                "typical_days": typical_days,
                                "actual_day": actual_day_code,
                                "typical_window": typical_window,
                                "actual_arrival": arrival.strftime("%H:%M"),
                            },
                        }
                    )
        return results

    @staticmethod
    def _detect_duplicate_entries(*, society, since) -> list[dict]:
        """Find persons with multiple ENTERED events without intervening EXIT."""
        # Persons who entered recently.
        recent_person_ids = (
            GateEvent.objects.filter(
                society=society,
                status=GateEvent.Status.ENTERED,
                person__isnull=False,
                entered_at__gte=since,
            )
            .values_list("person_id", flat=True)
            .distinct()
        )

        # Among those, find persons with > 1 open ENTERED event.
        duplicates = (
            GateEvent.objects.filter(
                society=society,
                status=GateEvent.Status.ENTERED,
                person_id__in=recent_person_ids,
            )
            .values("person_id")
            .annotate(count=Count("id"))
            .filter(count__gt=1)
        )

        results: list[dict] = []
        for dup in duplicates:
            person_id = dup["person_id"]
            open_entries = (
                GateEvent.objects.filter(
                    society=society,
                    person_id=person_id,
                    status=GateEvent.Status.ENTERED,
                )
                .select_related("gate")
                .order_by("entered_at")
            )
            person = Person.objects.filter(pk=person_id).first()
            if person is None:
                continue
            results.append(
                {
                    "anomaly_type": (
                        AnomalyDetection.AnomalyType.DUPLICATE_ENTRY
                    ),
                    "severity": AnomalyDetection.Severity.HIGH,
                    "person": person,
                    "description": (
                        f"{dup['count']} open entries without exit for "
                        f"{person.name}"
                    ),
                    "context": {
                        "person_id": person_id,
                        "open_entries": [
                            {
                                "event_id": e.pk,
                                "entered_at": _dt_iso(e.entered_at),
                                "gate": str(e.gate) if e.gate_id else None,
                            }
                            for e in open_entries
                        ],
                    },
                }
            )
        return results

    @staticmethod
    def _detect_long_stays(*, society, since) -> list[dict]:
        """Find visits exceeding the 95th percentile of historical durations."""
        now = timezone.now()
        window_start = now - timedelta(days=DEFAULT_ANALYSIS_WINDOW_DAYS)

        # Compute society-level duration percentiles from last 90 days.
        durations: list[float] = []
        for entered, exited in (
            GateEvent.objects.filter(
                society=society,
                status__in=[
                    GateEvent.Status.EXITED,
                    GateEvent.Status.AUTO_CLOSED,
                ],
                entered_at__isnull=False,
                exited_at__isnull=False,
                entered_at__gte=window_start,
            ).values_list("entered_at", "exited_at")
        ):
            durations.append((exited - entered).total_seconds() / 60)

        if not durations:
            return []

        p95 = _percentile(durations, DEFAULT_LONG_STAY_PERCENTILE)
        p99 = _percentile(durations, 99)

        # Find recent EXITED events with long stays.
        recent_exited = (
            GateEvent.objects.filter(
                society=society,
                status=GateEvent.Status.EXITED,
                entered_at__isnull=False,
                exited_at__isnull=False,
                exited_at__gte=since,
            )
            .select_related("person", "gate_vehicle")
        )

        results: list[dict] = []
        for event in recent_exited:
            duration = (
                event.exited_at - event.entered_at
            ).total_seconds() / 60
            if duration > p99:
                severity = AnomalyDetection.Severity.HIGH
                percentile = 99
            elif duration > p95:
                severity = AnomalyDetection.Severity.MEDIUM
                percentile = DEFAULT_LONG_STAY_PERCENTILE
            else:
                continue
            results.append(
                {
                    "anomaly_type": AnomalyDetection.AnomalyType.LONG_STAY,
                    "severity": severity,
                    "gate_event": event,
                    "person": event.person,
                    "gate_vehicle": event.gate_vehicle,
                    "description": (
                        f"Abnormally long stay: {duration:.0f} minutes "
                        f"(p{percentile}: {p95:.0f})"
                    ),
                    "context": {
                        "event_id": event.pk,
                        "entered_at": event.entered_at.isoformat(),
                        "exited_at": event.exited_at.isoformat(),
                        "duration_minutes": round(duration),
                        "p95_duration_minutes": round(p95),
                        "percentile": percentile,
                    },
                }
            )
        return results

    @staticmethod
    def _detect_suspicious_patterns(*, society, since) -> list[dict]:
        """Find persons whose risk score crossed HIGH for the first time."""
        patterns = (
            VisitorPattern.objects.filter(
                society=society,
                is_active=True,
                risk_level__in=[
                    VisitorPattern.RiskLevel.HIGH,
                    VisitorPattern.RiskLevel.CRITICAL,
                ],
            )
            .select_related("person")
        )

        results: list[dict] = []
        for pattern in patterns:
            # Check if this is the first time crossing HIGH by looking for
            # an existing SUSPICIOUS_PATTERN anomaly for this person.
            already_flagged = AnomalyDetection.objects.filter(
                society=society,
                anomaly_type=AnomalyDetection.AnomalyType.SUSPICIOUS_PATTERN,
                person=pattern.person,
                is_active=True,
            ).exists()

            if already_flagged:
                continue

            # Get top 3 risk factors.
            risk_result = AIRecommendationService.calculate_risk_score(
                society=society, person=pattern.person
            )
            factors = risk_result.get("factors", {})
            top_factors = dict(
                sorted(factors.items(), key=lambda x: x[1], reverse=True)[:3]
            )

            severity = (
                AnomalyDetection.Severity.CRITICAL
                if pattern.risk_level == VisitorPattern.RiskLevel.CRITICAL
                else AnomalyDetection.Severity.HIGH
            )

            results.append(
                {
                    "anomaly_type": (
                        AnomalyDetection.AnomalyType.SUSPICIOUS_PATTERN
                    ),
                    "severity": severity,
                    "person": pattern.person,
                    "description": (
                        f"Risk score crossed HIGH threshold: "
                        f"{pattern.risk_score:.2f}"
                    ),
                    "context": {
                        "person_id": pattern.person_id,
                        "previous_risk_level": "medium",
                        "new_risk_level": pattern.risk_level,
                        "risk_score": pattern.risk_score,
                        "top_factors": top_factors,
                    },
                }
            )
        return results

    # ------------------------------------------------------------------ #
    # Internal Helpers — Pattern Metrics (§3.2)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _compute_frequency_score(events: QuerySet) -> float:
        """Compute a 0.0–1.0 frequency score from a queryset of events.

        ``min(1.0, (visit_count / span_days) * 7 / EXPECTED_MAX_VISITS_PER_WEEK)``
        where *span_days* is the number of days between the first and last
        event (minimum 1 to avoid division by zero).
        """
        visit_count = events.count()
        if visit_count == 0:
            return 0.0

        timestamps = list(events.values_list("created_at", flat=True))
        if not timestamps:
            return 0.0

        if len(timestamps) == 1:
            span_days = 1
        else:
            span_days = max((timestamps[-1] - timestamps[0]).days, 1)

        return min(
            1.0,
            (visit_count / span_days) * 7 / EXPECTED_MAX_VISITS_PER_WEEK,
        )

    @staticmethod
    def _compute_risk_factors(
        *, society, person, events,
        _society_p95: float | None = None,
        _society_median: float | None = None,
    ) -> dict:
        """Compute individual risk factor scores (0.0–1.0 each).

        See design doc §3.4 for the full methodology.  The optional
        ``_society_p95`` and ``_society_median`` kwargs allow callers to
        pass pre-computed society-level duration percentiles to avoid
        redundant queries when processing many persons in a batch.
        """
        now = timezone.now()
        recent_since = now - timedelta(days=7)

        # All events for this person (not just the passed-in queryset, which
        # may be filtered to completed visits only).
        all_events = GateEvent.objects.filter(
            society=society, person=person
        ).order_by("created_at")

        recent_events = all_events.filter(created_at__gte=recent_since)
        total_recent = recent_events.count()

        # --- Factor 1: Visit Frequency Anomaly (0.20) ---
        # Score = min(1.0, |recent_freq - historical_freq| / max(historical_freq, 1))
        recent_count = total_recent
        first_event = all_events.first()
        if first_event:
            historical_days = max(
                (recent_since - first_event.created_at).days, 1
            )
            historical_weeks = max(historical_days / 7, 1)
            historical_count = all_events.filter(
                created_at__lt=recent_since
            ).count()
            historical_freq = historical_count / historical_weeks
        else:
            historical_freq = 0.0
        recent_freq = recent_count  # visits in last 7 days ≈ visits/week
        freq_anomaly = min(
            1.0,
            abs(recent_freq - historical_freq) / max(historical_freq, 1.0),
        )

        # --- Factor 2: Time Pattern Deviation (0.15) ---
        # Score = off_pattern_visits / total_recent_visits
        pattern = (
            VisitorPattern.objects.filter(
                society=society, person=person, is_active=True
            )
            .only("typical_time_window")
            .first()
        )
        if pattern and total_recent > 0 and pattern.typical_time_window:
            window = pattern.typical_time_window
            start = window.get("start")
            end = window.get("end")
            if start and end:
                start_h, start_m = map(int, start.split(":"))
                end_h, end_m = map(int, end.split(":"))
                start_min = start_h * 60 + start_m
                end_min = end_h * 60 + end_m
                off_pattern = 0
                for event in recent_events.filter(arrived_at__isnull=False):
                    arr_min = (
                        event.arrived_at.hour * 60 + event.arrived_at.minute
                    )
                    if not (start_min <= arr_min <= end_min):
                        off_pattern += 1
                time_deviation = off_pattern / total_recent
            else:
                time_deviation = 0.0
        else:
            time_deviation = 0.0

        # --- Factor 3: Blacklist/Watchlist Proximity (0.20) ---
        # 1.0 if blacklisted, 0.5 if watchlisted vehicle, 0.0 otherwise.
        if person.is_blacklisted:
            blacklist_proximity = 1.0
        else:
            watchlisted = GateVehicle.objects.filter(
                society=society,
                person=person,
                is_watchlisted=True,
                is_active=True,
            ).exists()
            blacklist_proximity = 0.5 if watchlisted else 0.0

        # --- Factor 4: Incomplete Exit History (0.15) ---
        # Score = auto_closed_count / total_visits
        total_visits = all_events.count()
        auto_closed_count = all_events.filter(
            status=GateEvent.Status.AUTO_CLOSED
        ).count()
        incomplete_exit = (
            auto_closed_count / total_visits if total_visits > 0 else 0.0
        )

        # --- Factor 5: Duration Anomaly (0.10) ---
        # 1.0 if most recent visit > p95, 0.5 if > median × 2, 0.0 otherwise.
        if _society_p95 is None or _society_median is None:
            window_start = now - timedelta(days=DEFAULT_ANALYSIS_WINDOW_DAYS)
            society_durations: list[float] = []
            for entered, exited in (
                GateEvent.objects.filter(
                    society=society,
                    status__in=[
                        GateEvent.Status.EXITED,
                        GateEvent.Status.AUTO_CLOSED,
                    ],
                    entered_at__isnull=False,
                    exited_at__isnull=False,
                    entered_at__gte=window_start,
                ).values_list("entered_at", "exited_at")
            ):
                society_durations.append(
                    (exited - entered).total_seconds() / 60
                )
            p95_duration = _percentile(society_durations, 95)
            median_duration = (
                median(society_durations) if society_durations else 0.0
            )
        else:
            p95_duration = _society_p95
            median_duration = _society_median

        duration_anomaly = 0.0
        recent_completed = (
            all_events.filter(
                entered_at__isnull=False,
                exited_at__isnull=False,
            )
            .order_by("-created_at")
            .first()
        )
        if recent_completed and p95_duration > 0:
            recent_duration = (
                recent_completed.exited_at
                - recent_completed.entered_at
            ).total_seconds() / 60
            if recent_duration > p95_duration:
                duration_anomaly = 1.0
            elif median_duration > 0 and recent_duration > median_duration * 2:
                duration_anomaly = 0.5

        # --- Factor 6: Cross-Category Visits (0.05) ---
        # 1.0 if 3+ categories, 0.5 for 2, 0.0 for 1.
        category_count = (
            all_events.values("visitor_category_id").distinct().count()
        )
        if category_count >= 3:
            cross_category = 1.0
        elif category_count == 2:
            cross_category = 0.5
        else:
            cross_category = 0.0

        # --- Factor 7: Night-Time Activity (0.10) ---
        # Score = night_visits / total_recent_visits
        night_hours = AIRecommendationService._get_night_mode_hours(society)
        if night_hours and total_recent > 0:
            start_hr, end_hr = night_hours
            night_visits = 0
            for event in recent_events.filter(entered_at__isnull=False):
                if AIRecommendationService._is_night_hour(
                    event.entered_at.hour, start_hr, end_hr
                ):
                    night_visits += 1
            night_activity = night_visits / total_recent
        else:
            night_activity = 0.0

        # --- Factor 8: ID Verification Gaps (0.05) ---
        # Score = unverified_count / total_recent_visits
        if total_recent > 0:
            unverified = recent_events.filter(id_verified=False).count()
            id_gaps = unverified / total_recent
        else:
            id_gaps = 0.0

        return {
            "visit_frequency_anomaly": round(freq_anomaly, 4),
            "time_pattern_deviation": round(time_deviation, 4),
            "blacklist_watchlist_proximity": round(blacklist_proximity, 4),
            "incomplete_exit_history": round(incomplete_exit, 4),
            "duration_anomaly": round(duration_anomaly, 4),
            "cross_category_visits": round(cross_category, 4),
            "night_time_activity": round(night_activity, 4),
            "id_verification_gaps": round(id_gaps, 4),
        }

    @staticmethod
    def _compute_typical_days(events: QuerySet) -> list[str]:
        """Extract typical visit days from event history.

        Groups events by ``created_at.weekday()`` (0=Monday).  If any weekday
        accounts for ≥ 20 % of visits, include it.  Returns ISO codes
        ``["mon", "tue", ...]``.
        """
        created_ats = list(events.values_list("created_at", flat=True))
        if not created_ats:
            return []

        weekday_counts = Counter(dt.weekday() for dt in created_ats)
        total = sum(weekday_counts.values())

        typical: list[str] = []
        for weekday in sorted(weekday_counts):
            if weekday_counts[weekday] / total >= 0.20:
                typical.append(_WEEKDAY_CODES[weekday])
        return typical

    @staticmethod
    def _compute_typical_time_window(events: QuerySet) -> dict:
        """Extract typical arrival time window from event history.

        Extracts ``arrived_at.time()`` for all events, computes the median
        arrival time, and returns ``[median - 1 h, median + 1 h]`` clamped
        to ``00:00``–``23:59``.  Returns ``{}`` if fewer than 3 events.
        """
        arrived_ats = list(
            events.filter(arrived_at__isnull=False).values_list(
                "arrived_at", flat=True
            )
        )
        if len(arrived_ats) < 3:
            return {}

        minutes = [dt.hour * 60 + dt.minute for dt in arrived_ats]
        median_min = median(minutes)

        start_min = max(0, int(median_min) - 60)
        end_min = min(1439, int(median_min) + 60)

        return {
            "start": f"{start_min // 60:02d}:{start_min % 60:02d}",
            "end": f"{end_min // 60:02d}:{end_min % 60:02d}",
        }

    @staticmethod
    def _compute_avg_duration(events: QuerySet) -> int | None:
        """Compute average visit duration in minutes.

        Averages ``(exited_at - entered_at)`` for events where both
        timestamps are set.  Returns ``None`` if no events have both.
        """
        timestamps = list(
            events.filter(
                entered_at__isnull=False, exited_at__isnull=False
            ).values_list("entered_at", "exited_at")
        )
        if not timestamps:
            return None

        durations = [
            (exited - entered).total_seconds() / 60
            for entered, exited in timestamps
        ]
        return int(sum(durations) / len(durations))

    @staticmethod
    def _update_or_create_pattern(
        *, society, person, events, actor=None
    ) -> bool:
        """Upsert a VisitorPattern row from computed metrics.

        Returns ``True`` if a new pattern was created, ``False`` if an
        existing one was updated.
        """
        visit_count = events.count()
        first_event = events.first()
        last_event = events.last()

        # Compute metrics.
        avg_duration = AIRecommendationService._compute_avg_duration(events)
        typical_days = AIRecommendationService._compute_typical_days(events)
        typical_window = AIRecommendationService._compute_typical_time_window(
            events
        )
        frequency_score = AIRecommendationService._compute_frequency_score(
            events
        )

        # Determine is_frequent.
        threshold = AIRecommendationService._get_frequent_visitor_threshold(
            society
        )
        if first_event and last_event:
            span_days = (last_event.created_at - first_event.created_at).days
        else:
            span_days = 0
        is_frequent = (
            visit_count >= threshold
            and span_days >= DEFAULT_FREQUENT_VISITOR_SPAN_DAYS
        )

        # Compute risk score.
        risk_result = AIRecommendationService.calculate_risk_score(
            society=society, person=person
        )
        risk_score = risk_result["risk_score"]
        risk_level = risk_result["risk_level"]

        # Determine suggested category (most common, if different from current).
        category_counts = Counter(
            events.values_list("visitor_category_id", flat=True)
        )
        if category_counts:
            top_category_id = category_counts.most_common(1)[0][0]
            top_category = VisitorCategory.objects.filter(
                id=top_category_id
            ).first()
        else:
            top_category = None

        current_category_id = (
            last_event.visitor_category_id if last_event else None
        )
        suggested_category = (
            top_category
            if top_category_id != current_category_id
            else None
        )

        current_category = (
            last_event.visitor_category if last_event else None
        )
        gate_vehicle = last_event.gate_vehicle if last_event else None

        defaults = {
            "gate_vehicle": gate_vehicle,
            "visitor_category": current_category,
            "suggested_category": suggested_category,
            "visit_count": visit_count,
            "first_visit_at": (
                first_event.created_at if first_event else None
            ),
            "last_visit_at": last_event.created_at if last_event else None,
            "last_event": last_event,
            "avg_visit_duration_minutes": avg_duration,
            "typical_visit_days": typical_days,
            "typical_time_window": typical_window,
            "is_frequent": is_frequent,
            "frequency_score": frequency_score,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "last_analyzed_at": timezone.now(),
        }

        # Race-safe upsert: is_active=True in the lookup ensures we only
        # match active patterns (avoids MultipleObjectsReturned if a
        # soft-deleted pattern exists).
        pattern, created = VisitorPattern.objects.update_or_create(
            society=society,
            person=person,
            is_active=True,
            defaults=defaults,
        )

        AIRecommendationService._log_audit(
            society=society,
            action=GateOpsAuditLog.Action.PATTERN_UPDATED,
            entity_type="VisitorPattern",
            entity_id=pattern.pk,
            after_value=AIRecommendationService._serialize_pattern(pattern),
            actor=actor,
        )

        return created

    # ------------------------------------------------------------------ #
    # Internal Helpers — Anomaly Creation & Notification
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def _create_anomaly(
        *, society, anomaly_type, severity, gate_event=None,
        person=None, gate_vehicle=None, description="", context=None,
    ) -> AnomalyDetection | None:
        """Create an AnomalyDetection row with audit logging.

        Deduplication: before creating, checks for an existing open anomaly
        of the same *anomaly_type* for the same *gate_event* (or *person*
        if *gate_event* is null).  If one exists, returns ``None`` (skip).

        For CRITICAL anomalies, dispatches a notification via
        :class:`NotificationEngineService` (non-blocking).
        """
        # Deduplication.
        existing = AnomalyDetection.objects.filter(
            society=society,
            anomaly_type=anomaly_type,
            status=AnomalyDetection.Status.OPEN,
            is_active=True,
        )
        if gate_event is not None:
            existing = existing.filter(gate_event=gate_event)
        elif person is not None:
            existing = existing.filter(
                person=person, gate_event__isnull=True
            )
        if existing.exists():
            return None

        anomaly = AnomalyDetection.objects.create(
            society=society,
            anomaly_type=anomaly_type,
            severity=severity,
            gate_event=gate_event,
            person=person,
            gate_vehicle=gate_vehicle,
            description=description,
            context=context or {},
        )

        AIRecommendationService._log_audit(
            society=society,
            action=GateOpsAuditLog.Action.ANOMALY_DETECTED,
            entity_type="AnomalyDetection",
            entity_id=anomaly.pk,
            after_value=AIRecommendationService._serialize_anomaly(anomaly),
        )

        # Notify for critical anomalies (non-blocking).
        AIRecommendationService._notify_anomaly(anomaly=anomaly)

        return anomaly

    @staticmethod
    def _notify_anomaly(*, anomaly) -> None:
        """Dispatch a notification for a critical anomaly.

        Calls :meth:`NotificationEngineService.dispatch_for_event` for
        CRITICAL anomalies that have a linked gate event.  Wrapped in
        ``try/except`` so notification failure never blocks anomaly creation.
        """
        if anomaly.severity != AnomalyDetection.Severity.CRITICAL:
            return
        if anomaly.gate_event_id is None:
            return
        try:
            NotificationEngineService.dispatch_for_event(
                event=anomaly.gate_event,
                trigger=NotificationPreference.Trigger.ANOMALY,
            )
        except Exception:  # noqa: BLE001 — never block anomaly creation.
            logger.exception(
                "Failed to notify anomaly %s (type=%s, severity=%s)",
                anomaly.pk,
                anomaly.anomaly_type,
                anomaly.severity,
            )

    # ------------------------------------------------------------------ #
    # Internal Helpers — Configuration
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_night_mode_hours(society) -> tuple[int, int] | None:
        """Return ``(start_hour, end_hour)`` from GateOpsSocietyConfig.

        Returns ``None`` if no config exists or night mode is not configured.
        """
        try:
            config = society.gateops_config
        except GateOpsSocietyConfig.DoesNotExist:
            return None
        if config.night_mode_start is None or config.night_mode_end is None:
            return None
        return (
            config.night_mode_start.hour,
            config.night_mode_end.hour,
        )

    @staticmethod
    def _get_frequent_visitor_threshold(society) -> int:
        """Return the minimum visit count for ``is_frequent=True``.

        Default: 5.  In a future enhancement, this could be configurable
        via :class:`GateOpsSocietyConfig`.
        """
        return DEFAULT_FREQUENT_VISITOR_THRESHOLD

    @staticmethod
    def _is_night_hour(hour: int, start: int, end: int) -> bool:
        """Check if *hour* falls within night-mode hours.

        Handles the midnight-spanning case (e.g., 22:00–06:00):
        if ``start > end``, the window wraps around midnight.
        """
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        # Spans midnight.
        return hour >= start or hour < end

    # ------------------------------------------------------------------ #
    # Internal Helpers — Serialization & Audit
    # ------------------------------------------------------------------ #

    @staticmethod
    def _serialize_pattern(pattern) -> dict:
        """Return a JSON-safe dict of the pattern's key fields for audit."""
        return {
            "id": str(pattern.pk),
            "person_id": str(pattern.person_id) if pattern.person_id else None,
            "visit_count": pattern.visit_count,
            "risk_score": pattern.risk_score,
            "risk_level": pattern.risk_level,
            "is_frequent": pattern.is_frequent,
            "frequency_score": pattern.frequency_score,
            "last_analyzed_at": _dt_iso(pattern.last_analyzed_at),
        }

    @staticmethod
    def _serialize_anomaly(anomaly) -> dict:
        """Return a JSON-safe dict of the anomaly's key fields for audit."""
        return {
            "id": str(anomaly.pk),
            "anomaly_type": anomaly.anomaly_type,
            "severity": anomaly.severity,
            "status": anomaly.status,
            "gate_event_id": (
                str(anomaly.gate_event_id) if anomaly.gate_event_id else None
            ),
            "person_id": (
                str(anomaly.person_id) if anomaly.person_id else None
            ),
            "resolved_at": _dt_iso(anomaly.resolved_at),
        }

    @staticmethod
    def _log_audit(
        *, society, action, entity_type, entity_id,
        before_value=None, after_value=None, actor=None,
    ) -> None:
        """Write an append-only GateOpsAuditLog entry.

        Wrapped so a logging failure never blocks a legitimate AI
        operation; the error is logged at ERROR level instead.
        """
        try:
            GateOpsAuditLog.log(
                society=society,
                action=action,
                entity_type=entity_type,
                entity_id=str(entity_id) if entity_id is not None else "",
                actor=actor,
                before_value=before_value,
                after_value=after_value,
            )
        except Exception:  # noqa: BLE001 — audit must not break the operation.
            logger.exception(
                "Failed to write %s audit log for entity %s (action=%s)",
                entity_type,
                entity_id,
                action,
            )
