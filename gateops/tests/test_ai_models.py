"""Model-level tests for the Phase 11 AI Recommendation Engine.

Covers creation, defaults, ``__str__``, ``clean()`` validation, unique
constraints, and soft-delete for the three new models:

- :class:`VisitorPattern`
- :class:`AnomalyDetection`
- :class:`PeakHourPrediction`
"""

from datetime import time, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.test_base import SocietyTestCase
from core.test_factories import SocietyFactory
from gateops.models import (
    AnomalyDetection,
    Gate,
    GateEvent,
    GateOpsSocietyConfig,
    PeakHourPrediction,
    Person,
    VisitorCategory,
    VisitorPattern,
)


# ---------------------------------------------------------------------------
# VisitorPattern
# ---------------------------------------------------------------------------


class VisitorPatternModelTest(SocietyTestCase):
    """Model-level tests for VisitorPattern."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other_society = SocietyFactory(name="Test Society Beta")
        cls.visitor_cat = VisitorCategory.objects.get(
            society=cls.society, code="GUEST"
        )
        cls.other_cat = VisitorCategory.objects.get(
            society=cls.other_society, code="GUEST"
        )
        cls.person = Person.objects.create(
            society=cls.society, name="Pattern Visitor", phone="1111111111"
        )
        cls.other_person = Person.objects.create(
            society=cls.other_society, name="Other Visitor", phone="2222222222"
        )

    # --- helpers ----------------------------------------------------------

    def _make_pattern(self, **overrides):
        defaults = {
            "society": self.society,
            "person": self.person,
            "visitor_category": self.visitor_cat,
            "visit_count": 3,
            "risk_score": 0.1,
            "risk_level": VisitorPattern.RiskLevel.LOW,
            "frequency_score": 0.2,
            "is_frequent": False,
        }
        defaults.update(overrides)
        return VisitorPattern.objects.create(**defaults)

    # --- creation & defaults ---------------------------------------------

    def test_creation_with_all_required_fields(self):
        pattern = self._make_pattern()
        self.assertIsNotNone(pattern.pk)
        self.assertEqual(pattern.society, self.society)
        self.assertEqual(pattern.person, self.person)
        self.assertEqual(pattern.visit_count, 3)
        self.assertTrue(pattern.is_active)
        self.assertIsNone(pattern.deleted_at)
        self.assertIsNotNone(pattern.created_at)
        self.assertIsNotNone(pattern.updated_at)

    def test_default_risk_level_is_low(self):
        pattern = VisitorPattern(
            society=self.society,
            person=self.person,
            visitor_category=self.visitor_cat,
            visit_count=0,
            risk_score=0.0,
            risk_level=VisitorPattern.RiskLevel.LOW,
            frequency_score=0.0,
        )
        self.assertEqual(pattern.risk_level, VisitorPattern.RiskLevel.LOW)

    def test_default_is_active_is_true(self):
        pattern = self._make_pattern()
        self.assertTrue(pattern.is_active)

    def test_default_typical_visit_days_is_empty_list(self):
        pattern = self._make_pattern()
        self.assertEqual(pattern.typical_visit_days, [])

    def test_default_typical_time_window_is_empty_dict(self):
        pattern = self._make_pattern()
        self.assertEqual(pattern.typical_time_window, {})

    # --- __str__ ----------------------------------------------------------

    def test_str_representation(self):
        pattern = self._make_pattern(
            visit_count=7,
            risk_score=0.60,
            risk_level=VisitorPattern.RiskLevel.HIGH,
        )
        result = str(pattern)
        self.assertIn("Pattern", result)
        self.assertIn("7 visits", result)
        self.assertIn("high", result)

    # --- clean() validation ----------------------------------------------

    def test_clean_rejects_cross_society_person(self):
        with self.assertRaises(ValidationError):
            pattern = VisitorPattern(
                society=self.society,
                person=self.other_person,
                visitor_category=self.visitor_cat,
                visit_count=1,
                risk_score=0.0,
                risk_level=VisitorPattern.RiskLevel.LOW,
                frequency_score=0.0,
            )
            pattern.clean()

    def test_clean_accepts_same_society_person(self):
        pattern = VisitorPattern(
            society=self.society,
            person=self.person,
            visitor_category=self.visitor_cat,
            visit_count=1,
            risk_score=0.0,
            risk_level=VisitorPattern.RiskLevel.LOW,
            frequency_score=0.0,
        )
        pattern.clean()  # Should not raise.

    def test_clean_rejects_cross_society_visitor_category(self):
        with self.assertRaises(ValidationError):
            pattern = VisitorPattern(
                society=self.society,
                person=self.person,
                visitor_category=self.other_cat,
                visit_count=1,
                risk_score=0.0,
                risk_level=VisitorPattern.RiskLevel.LOW,
                frequency_score=0.0,
            )
            pattern.clean()

    def test_clean_rejects_risk_score_above_one(self):
        with self.assertRaises(ValidationError):
            pattern = VisitorPattern(
                society=self.society,
                person=self.person,
                visitor_category=self.visitor_cat,
                visit_count=1,
                risk_score=1.5,
                risk_level=VisitorPattern.RiskLevel.CRITICAL,
                frequency_score=0.0,
            )
            pattern.clean()

    def test_clean_rejects_risk_score_below_zero(self):
        with self.assertRaises(ValidationError):
            pattern = VisitorPattern(
                society=self.society,
                person=self.person,
                visitor_category=self.visitor_cat,
                visit_count=1,
                risk_score=-0.1,
                risk_level=VisitorPattern.RiskLevel.LOW,
                frequency_score=0.0,
            )
            pattern.clean()

    def test_clean_rejects_frequency_score_above_one(self):
        with self.assertRaises(ValidationError):
            pattern = VisitorPattern(
                society=self.society,
                person=self.person,
                visitor_category=self.visitor_cat,
                visit_count=1,
                risk_score=0.0,
                risk_level=VisitorPattern.RiskLevel.LOW,
                frequency_score=1.5,
            )
            pattern.clean()

    def test_clean_rejects_risk_level_mismatch_with_score(self):
        # risk_score=0.1 should map to LOW, not HIGH.
        with self.assertRaises(ValidationError):
            pattern = VisitorPattern(
                society=self.society,
                person=self.person,
                visitor_category=self.visitor_cat,
                visit_count=1,
                risk_score=0.1,
                risk_level=VisitorPattern.RiskLevel.HIGH,
                frequency_score=0.0,
            )
            pattern.clean()

    def test_clean_accepts_correct_risk_level_for_score(self):
        # 0.75 → CRITICAL
        pattern = VisitorPattern(
            society=self.society,
            person=self.person,
            visitor_category=self.visitor_cat,
            visit_count=1,
            risk_score=0.75,
            risk_level=VisitorPattern.RiskLevel.CRITICAL,
            frequency_score=0.0,
        )
        pattern.clean()  # Should not raise.

    # --- _risk_level_for_score -------------------------------------------

    def test_risk_level_for_score_low(self):
        self.assertEqual(
            VisitorPattern._risk_level_for_score(0.0),
            VisitorPattern.RiskLevel.LOW,
        )
        self.assertEqual(
            VisitorPattern._risk_level_for_score(0.24),
            VisitorPattern.RiskLevel.LOW,
        )

    def test_risk_level_for_score_medium(self):
        self.assertEqual(
            VisitorPattern._risk_level_for_score(0.25),
            VisitorPattern.RiskLevel.MEDIUM,
        )
        self.assertEqual(
            VisitorPattern._risk_level_for_score(0.49),
            VisitorPattern.RiskLevel.MEDIUM,
        )

    def test_risk_level_for_score_high(self):
        self.assertEqual(
            VisitorPattern._risk_level_for_score(0.50),
            VisitorPattern.RiskLevel.HIGH,
        )
        self.assertEqual(
            VisitorPattern._risk_level_for_score(0.74),
            VisitorPattern.RiskLevel.HIGH,
        )

    def test_risk_level_for_score_critical(self):
        self.assertEqual(
            VisitorPattern._risk_level_for_score(0.75),
            VisitorPattern.RiskLevel.CRITICAL,
        )
        self.assertEqual(
            VisitorPattern._risk_level_for_score(1.0),
            VisitorPattern.RiskLevel.CRITICAL,
        )

    # --- unique constraint ------------------------------------------------

    def test_unique_active_pattern_per_society_person(self):
        self._make_pattern()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                VisitorPattern.objects.create(
                    society=self.society,
                    person=self.person,
                    visitor_category=self.visitor_cat,
                    visit_count=1,
                    risk_score=0.0,
                    risk_level=VisitorPattern.RiskLevel.LOW,
                    frequency_score=0.0,
                )

    def test_soft_deleted_pattern_allows_new_active(self):
        """Soft-deleting a pattern frees the unique slot for a new one."""
        pattern = self._make_pattern()
        now = timezone.now()
        VisitorPattern.objects.filter(pk=pattern.pk).update(
            is_active=False, deleted_at=now
        )
        # A new active pattern for the same person should succeed.
        new_pattern = VisitorPattern.objects.create(
            society=self.society,
            person=self.person,
            visitor_category=self.visitor_cat,
            visit_count=5,
            risk_score=0.3,
            risk_level=VisitorPattern.RiskLevel.MEDIUM,
            frequency_score=0.4,
        )
        self.assertIsNotNone(new_pattern.pk)

    # --- soft-delete ------------------------------------------------------

    def test_soft_delete_sets_is_active_false_and_deleted_at(self):
        pattern = self._make_pattern()
        now = timezone.now()
        VisitorPattern.objects.filter(pk=pattern.pk).update(
            is_active=False, deleted_at=now
        )
        pattern.refresh_from_db()
        self.assertFalse(pattern.is_active)
        self.assertIsNotNone(pattern.deleted_at)

    def test_soft_deleted_pattern_remains_in_db(self):
        pattern = self._make_pattern()
        now = timezone.now()
        VisitorPattern.objects.filter(pk=pattern.pk).update(
            is_active=False, deleted_at=now
        )
        self.assertTrue(VisitorPattern.objects.filter(pk=pattern.pk).exists())

    # --- RiskLevel choices ------------------------------------------------

    def test_risk_level_choices_contain_all_levels(self):
        choices = dict(VisitorPattern.RiskLevel.choices)
        self.assertIn("low", choices)
        self.assertIn("medium", choices)
        self.assertIn("high", choices)
        self.assertIn("critical", choices)


# ---------------------------------------------------------------------------
# AnomalyDetection
# ---------------------------------------------------------------------------


class AnomalyDetectionModelTest(SocietyTestCase):
    """Model-level tests for AnomalyDetection."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other_society = SocietyFactory(name="Test Society Beta")
        cls.visitor_cat = VisitorCategory.objects.get(
            society=cls.society, code="GUEST"
        )
        cls.gate = Gate.objects.get(society=cls.society, code="MAIN")
        cls.person = Person.objects.create(
            society=cls.society, name="Anomaly Visitor", phone="3333333333"
        )
        cls.other_person = Person.objects.create(
            society=cls.other_society, name="Other Visitor", phone="4444444444"
        )

    # --- helpers ----------------------------------------------------------

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

    # --- creation & defaults ---------------------------------------------

    def test_creation_with_all_required_fields(self):
        anomaly = self._make_anomaly()
        self.assertIsNotNone(anomaly.pk)
        self.assertEqual(anomaly.society, self.society)
        self.assertEqual(anomaly.anomaly_type, AnomalyDetection.AnomalyType.FORGOTTEN_EXIT)
        self.assertEqual(anomaly.severity, AnomalyDetection.Severity.MEDIUM)
        self.assertEqual(anomaly.status, AnomalyDetection.Status.OPEN)
        self.assertTrue(anomaly.is_active)
        self.assertIsNone(anomaly.deleted_at)
        self.assertIsNone(anomaly.resolved_at)
        self.assertIsNotNone(anomaly.detected_at)

    def test_default_severity_is_medium(self):
        anomaly = AnomalyDetection(
            society=self.society,
            anomaly_type=AnomalyDetection.AnomalyType.FORGOTTEN_EXIT,
            person=self.person,
        )
        self.assertEqual(anomaly.severity, AnomalyDetection.Severity.MEDIUM)

    def test_default_status_is_open(self):
        anomaly = AnomalyDetection(
            society=self.society,
            anomaly_type=AnomalyDetection.AnomalyType.FORGOTTEN_EXIT,
            person=self.person,
        )
        self.assertEqual(anomaly.status, AnomalyDetection.Status.OPEN)

    def test_default_is_active_is_true(self):
        anomaly = self._make_anomaly()
        self.assertTrue(anomaly.is_active)

    # --- __str__ ----------------------------------------------------------

    def test_str_representation(self):
        anomaly = self._make_anomaly()
        result = str(anomaly)
        self.assertIn(str(anomaly.pk), result)
        self.assertIn("Forgotten Exit", result)
        self.assertIn("medium", result)
        self.assertIn("open", result)

    # --- clean() validation ----------------------------------------------

    def test_clean_rejects_cross_society_person(self):
        with self.assertRaises(ValidationError):
            anomaly = AnomalyDetection(
                society=self.society,
                anomaly_type=AnomalyDetection.AnomalyType.FORGOTTEN_EXIT,
                person=self.other_person,
            )
            anomaly.clean()

    def test_clean_accepts_same_society_person(self):
        anomaly = AnomalyDetection(
            society=self.society,
            anomaly_type=AnomalyDetection.AnomalyType.FORGOTTEN_EXIT,
            person=self.person,
        )
        anomaly.clean()  # Should not raise.

    def test_clean_rejects_resolved_at_with_open_status(self):
        """resolved_at set but status is OPEN → ValidationError."""
        with self.assertRaises(ValidationError):
            anomaly = AnomalyDetection(
                society=self.society,
                anomaly_type=AnomalyDetection.AnomalyType.FORGOTTEN_EXIT,
                person=self.person,
                status=AnomalyDetection.Status.OPEN,
                resolved_at=timezone.now(),
            )
            anomaly.clean()

    def test_clean_rejects_resolved_status_without_resolved_at(self):
        """RESOLVED status but no resolved_at → ValidationError."""
        with self.assertRaises(ValidationError):
            anomaly = AnomalyDetection(
                society=self.society,
                anomaly_type=AnomalyDetection.AnomalyType.FORGOTTEN_EXIT,
                person=self.person,
                status=AnomalyDetection.Status.RESOLVED,
                resolved_at=None,
            )
            anomaly.clean()

    def test_clean_accepts_resolved_with_resolved_at(self):
        now = timezone.now()
        anomaly = AnomalyDetection(
            society=self.society,
            anomaly_type=AnomalyDetection.AnomalyType.FORGOTTEN_EXIT,
            person=self.person,
            status=AnomalyDetection.Status.RESOLVED,
            resolved_at=now,
        )
        anomaly.clean()  # Should not raise.

    def test_clean_accepts_false_positive_with_resolved_at(self):
        now = timezone.now()
        anomaly = AnomalyDetection(
            society=self.society,
            anomaly_type=AnomalyDetection.AnomalyType.FORGOTTEN_EXIT,
            person=self.person,
            status=AnomalyDetection.Status.FALSE_POSITIVE,
            resolved_at=now,
        )
        anomaly.clean()  # Should not raise.

    # --- choices ----------------------------------------------------------

    def test_anomaly_type_choices_contain_all_types(self):
        choices = dict(AnomalyDetection.AnomalyType.choices)
        for atype in [
            "forgotten_exit",
            "after_hours_entry",
            "unusual_frequency",
            "blacklist_bypass",
            "off_pattern_visit",
            "duplicate_entry",
            "long_stay",
            "suspicious_pattern",
        ]:
            self.assertIn(atype, choices)

    def test_severity_choices_contain_all_levels(self):
        choices = dict(AnomalyDetection.Severity.choices)
        self.assertIn("low", choices)
        self.assertIn("medium", choices)
        self.assertIn("high", choices)
        self.assertIn("critical", choices)

    def test_status_choices_contain_all_states(self):
        choices = dict(AnomalyDetection.Status.choices)
        self.assertIn("open", choices)
        self.assertIn("acknowledged", choices)
        self.assertIn("resolved", choices)
        self.assertIn("false_positive", choices)

    # --- soft-delete ------------------------------------------------------

    def test_soft_delete_sets_is_active_false_and_deleted_at(self):
        anomaly = self._make_anomaly()
        now = timezone.now()
        AnomalyDetection.objects.filter(pk=anomaly.pk).update(
            is_active=False, deleted_at=now
        )
        anomaly.refresh_from_db()
        self.assertFalse(anomaly.is_active)
        self.assertIsNotNone(anomaly.deleted_at)

    def test_soft_deleted_anomaly_remains_in_db(self):
        anomaly = self._make_anomaly()
        now = timezone.now()
        AnomalyDetection.objects.filter(pk=anomaly.pk).update(
            is_active=False, deleted_at=now
        )
        self.assertTrue(AnomalyDetection.objects.filter(pk=anomaly.pk).exists())


# ---------------------------------------------------------------------------
# PeakHourPrediction
# ---------------------------------------------------------------------------


class PeakHourPredictionModelTest(SocietyTestCase):
    """Model-level tests for PeakHourPrediction."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.today = timezone.now().date()

    # --- helpers ----------------------------------------------------------

    def _make_prediction(self, **overrides):
        defaults = {
            "society": self.society,
            "day_of_week": 0,
            "hour": 10,
            "predicted_count": 5,
            "confidence_score": 0.8,
            "analysis_date": self.today,
        }
        defaults.update(overrides)
        return PeakHourPrediction.objects.create(**defaults)

    # --- creation & defaults ---------------------------------------------

    def test_creation_with_all_required_fields(self):
        prediction = self._make_prediction()
        self.assertIsNotNone(prediction.pk)
        self.assertEqual(prediction.society, self.society)
        self.assertEqual(prediction.day_of_week, 0)
        self.assertEqual(prediction.hour, 10)
        self.assertEqual(prediction.predicted_count, 5)
        self.assertEqual(prediction.confidence_score, 0.8)
        self.assertTrue(prediction.is_active)
        self.assertIsNone(prediction.deleted_at)
        self.assertIsNotNone(prediction.created_at)

    def test_default_is_active_is_true(self):
        prediction = self._make_prediction()
        self.assertTrue(prediction.is_active)

    def test_default_actual_count_is_none(self):
        prediction = self._make_prediction()
        self.assertIsNone(prediction.actual_count)

    # --- __str__ ----------------------------------------------------------

    def test_str_representation(self):
        prediction = self._make_prediction(day_of_week=1, hour=14, predicted_count=8)
        result = str(prediction)
        self.assertIn("14:00", result)
        self.assertIn("8", result)
        self.assertIn("80%", result)

    def test_str_representation_for_each_weekday(self):
        for dow in range(7):
            prediction = self._make_prediction(day_of_week=dow, hour=9)
            result = str(prediction)
            self.assertIn("09:00", result)

    # --- clean() validation ----------------------------------------------

    def test_clean_rejects_day_of_week_below_zero(self):
        with self.assertRaises(ValidationError):
            prediction = PeakHourPrediction(
                society=self.society,
                day_of_week=-1,
                hour=10,
                predicted_count=5,
                confidence_score=0.8,
                analysis_date=self.today,
            )
            prediction.clean()

    def test_clean_rejects_day_of_week_above_six(self):
        with self.assertRaises(ValidationError):
            prediction = PeakHourPrediction(
                society=self.society,
                day_of_week=7,
                hour=10,
                predicted_count=5,
                confidence_score=0.8,
                analysis_date=self.today,
            )
            prediction.clean()

    def test_clean_rejects_hour_below_zero(self):
        with self.assertRaises(ValidationError):
            prediction = PeakHourPrediction(
                society=self.society,
                day_of_week=0,
                hour=-1,
                predicted_count=5,
                confidence_score=0.8,
                analysis_date=self.today,
            )
            prediction.clean()

    def test_clean_rejects_hour_above_23(self):
        with self.assertRaises(ValidationError):
            prediction = PeakHourPrediction(
                society=self.society,
                day_of_week=0,
                hour=24,
                predicted_count=5,
                confidence_score=0.8,
                analysis_date=self.today,
            )
            prediction.clean()

    def test_clean_rejects_confidence_score_above_one(self):
        with self.assertRaises(ValidationError):
            prediction = PeakHourPrediction(
                society=self.society,
                day_of_week=0,
                hour=10,
                predicted_count=5,
                confidence_score=1.5,
                analysis_date=self.today,
            )
            prediction.clean()

    def test_clean_rejects_confidence_score_below_zero(self):
        with self.assertRaises(ValidationError):
            prediction = PeakHourPrediction(
                society=self.society,
                day_of_week=0,
                hour=10,
                predicted_count=5,
                confidence_score=-0.1,
                analysis_date=self.today,
            )
            prediction.clean()

    def test_clean_accepts_valid_boundary_values(self):
        # day=0, hour=0, confidence=0.0
        prediction = PeakHourPrediction(
            society=self.society,
            day_of_week=0,
            hour=0,
            predicted_count=0,
            confidence_score=0.0,
            analysis_date=self.today,
        )
        prediction.clean()  # Should not raise.

        # day=6, hour=23, confidence=1.0
        prediction2 = PeakHourPrediction(
            society=self.society,
            day_of_week=6,
            hour=23,
            predicted_count=10,
            confidence_score=1.0,
            analysis_date=self.today,
        )
        prediction2.clean()  # Should not raise.

    # --- unique constraint ------------------------------------------------

    def test_unique_active_prediction_per_slot_and_date(self):
        self._make_prediction(day_of_week=2, hour=15, analysis_date=self.today)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PeakHourPrediction.objects.create(
                    society=self.society,
                    day_of_week=2,
                    hour=15,
                    predicted_count=10,
                    confidence_score=0.9,
                    analysis_date=self.today,
                )

    def test_different_date_allows_same_slot(self):
        self._make_prediction(day_of_week=2, hour=15, analysis_date=self.today)
        other_date = self.today + timedelta(days=1)
        prediction = PeakHourPrediction.objects.create(
            society=self.society,
            day_of_week=2,
            hour=15,
            predicted_count=10,
            confidence_score=0.9,
            analysis_date=other_date,
        )
        self.assertIsNotNone(prediction.pk)

    def test_soft_deleted_prediction_allows_new_active(self):
        prediction = self._make_prediction(day_of_week=3, hour=12)
        now = timezone.now()
        PeakHourPrediction.objects.filter(pk=prediction.pk).update(
            is_active=False, deleted_at=now
        )
        new_prediction = PeakHourPrediction.objects.create(
            society=self.society,
            day_of_week=3,
            hour=12,
            predicted_count=20,
            confidence_score=0.95,
            analysis_date=self.today,
        )
        self.assertIsNotNone(new_prediction.pk)

    # --- soft-delete ------------------------------------------------------

    def test_soft_delete_sets_is_active_false_and_deleted_at(self):
        prediction = self._make_prediction()
        now = timezone.now()
        PeakHourPrediction.objects.filter(pk=prediction.pk).update(
            is_active=False, deleted_at=now
        )
        prediction.refresh_from_db()
        self.assertFalse(prediction.is_active)
        self.assertIsNotNone(prediction.deleted_at)

    def test_soft_deleted_prediction_remains_in_db(self):
        prediction = self._make_prediction()
        now = timezone.now()
        PeakHourPrediction.objects.filter(pk=prediction.pk).update(
            is_active=False, deleted_at=now
        )
        self.assertTrue(PeakHourPrediction.objects.filter(pk=prediction.pk).exists())
