"""Tests for the ``gateops_ai_analysis`` management command (Phase 11 §5).

Covers:
- Basic execution (all 3 analysis steps for all societies)
- ``--society`` filter (by ID and by name, case-insensitive)
- ``--dry-run`` mode (no persistence)
- ``--skip`` flag (skip specific analysis types)
- Error handling (one society failure doesn't abort others)
- Output formatting (success/error messages)
- No societies found edge case
"""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from core.test_factories import SocietyFactory
from gateops.models import AnomalyDetection, PeakHourPrediction, VisitorPattern
from gateops.services.ai_recommendation_service import AIRecommendationService
from housing.models import Society


# =========================================================================
# Shared base
# =========================================================================

class AICommandTestBase(TestCase):
    """Shared fixtures for management command tests.

    Uses ``TestCase`` (not ``SocietyTestCase``) because we need to create
    multiple societies and test filtering.  The SocietyFactory triggers
    the gateops bootstrap signal which seeds default categories, gates,
    config, etc.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.society1 = SocietyFactory(name="AI Command Alpha")
        cls.society2 = SocietyFactory(name="AI Command Beta")

    def setUp(self):
        super().setUp()
        self.output = StringIO()

    def _run_command(self, **kwargs):
        """Run the command and capture output."""
        kwargs.setdefault("stdout", self.output)
        call_command("gateops_ai_analysis", **kwargs)
        return self.output.getvalue()


# =========================================================================
# Basic execution
# =========================================================================

class AICommandBasicTest(AICommandTestBase):
    """Tests for basic command execution."""

    def test_command_runs_without_error(self):
        """The command completes successfully with no arguments."""
        output = self._run_command()
        self.assertIn("Done", output)

    def test_command_processes_all_societies(self):
        """Without --society, all societies are processed."""
        output = self._run_command()
        self.assertIn(self.society1.name, output)
        self.assertIn(self.society2.name, output)

    def test_command_reports_society_count(self):
        """Output includes the number of societies being processed."""
        output = self._run_command()
        self.assertIn("2 society(ies)", output)

    def test_command_reports_success_count(self):
        """Output includes the success count."""
        output = self._run_command()
        self.assertIn("2 society(ies) processed", output)

    def test_command_reports_zero_errors_on_success(self):
        """Output shows 0 errors when all societies succeed."""
        output = self._run_command()
        self.assertIn("0 error(s)", output)

    def test_command_calls_all_three_steps(self):
        """Without --skip, all three analysis steps are called."""
        with patch.object(
            AIRecommendationService, "analyze_visitor_patterns"
        ) as mock_patterns, patch.object(
            AIRecommendationService, "detect_anomalies"
        ) as mock_anomalies, patch.object(
            AIRecommendationService, "predict_peak_hours"
        ) as mock_predictions:
            mock_patterns.return_value = {
                "patterns_created": 0, "patterns_updated": 0, "errors": 0
            }
            mock_anomalies.return_value = {
                "anomalies_created": 0, "by_type": {}, "errors": 0
            }
            mock_predictions.return_value = {
                "predictions_created": 0, "analysis_date": None, "errors": 0
            }
            self._run_command()

        # Each service method called once per society (2 societies).
        self.assertEqual(mock_patterns.call_count, 2)
        self.assertEqual(mock_anomalies.call_count, 2)
        self.assertEqual(mock_predictions.call_count, 2)


# =========================================================================
# --society filter
# =========================================================================

class AICommandSocietyFilterTest(AICommandTestBase):
    """Tests for the --society filter."""

    def test_filter_by_id(self):
        """--society <id> limits analysis to one society."""
        output = self._run_command(society=str(self.society1.pk))
        self.assertIn(self.society1.name, output)
        self.assertNotIn(self.society2.name, output)
        self.assertIn("1 society(ies)", output)

    def test_filter_by_name(self):
        """--society <name> limits analysis by name (case-insensitive)."""
        output = self._run_command(society="ai command alpha")
        self.assertIn(self.society1.name, output)
        self.assertNotIn(self.society2.name, output)

    def test_filter_by_exact_name(self):
        """--society <exact name> works."""
        output = self._run_command(society=self.society2.name)
        self.assertIn(self.society2.name, output)
        self.assertNotIn(self.society1.name, output)

    def test_filter_nonexistent_society(self):
        """--society with no match shows a warning."""
        output = self._run_command(society="Nonexistent Society")
        self.assertIn("No societies found", output)

    def test_filter_nonexistent_id(self):
        """--society with non-existent ID shows a warning."""
        output = self._run_command(society="999999")
        self.assertIn("No societies found", output)

    def test_filter_only_processes_matching_society(self):
        """Only the matching society's service methods are called."""
        with patch.object(
            AIRecommendationService, "analyze_visitor_patterns"
        ) as mock_patterns:
            mock_patterns.return_value = {
                "patterns_created": 0, "patterns_updated": 0, "errors": 0
            }
            self._run_command(society=str(self.society1.pk))
        self.assertEqual(mock_patterns.call_count, 1)


# =========================================================================
# --dry-run mode
# =========================================================================

class AICommandDryRunTest(AICommandTestBase):
    """Tests for --dry-run mode."""

    def test_dry_run_does_not_call_service(self):
        """In dry-run mode, no service methods are called."""
        with patch.object(
            AIRecommendationService, "analyze_visitor_patterns"
        ) as mock_patterns, patch.object(
            AIRecommendationService, "detect_anomalies"
        ) as mock_anomalies, patch.object(
            AIRecommendationService, "predict_peak_hours"
        ) as mock_predictions:
            self._run_command(dry_run=True)
        mock_patterns.assert_not_called()
        mock_anomalies.assert_not_called()
        mock_predictions.assert_not_called()

    def test_dry_run_shows_dry_run_label(self):
        """Dry-run output includes [DRY RUN] label."""
        output = self._run_command(dry_run=True)
        self.assertIn("[DRY RUN]", output)

    def test_dry_run_lists_steps(self):
        """Dry-run output lists the steps that would run."""
        output = self._run_command(dry_run=True)
        self.assertIn("visitor patterns", output)
        self.assertIn("anomaly detection", output)
        self.assertIn("peak-hour predictions", output)

    def test_dry_run_with_skip_shows_remaining_steps(self):
        """Dry-run with --skip only lists non-skipped steps."""
        output = self._run_command(dry_run=True, skip=["patterns"])
        self.assertNotIn("visitor patterns", output)
        self.assertIn("anomaly detection", output)
        self.assertIn("peak-hour predictions", output)

    def test_dry_run_skip_all_shows_warning(self):
        """Dry-run with all steps skipped shows a warning."""
        output = self._run_command(
            dry_run=True, skip=["patterns", "anomalies", "predictions"]
        )
        self.assertIn("nothing to do", output)

    def test_dry_run_does_not_persist(self):
        """Dry-run mode creates no DB records."""
        self._run_command(dry_run=True)
        # No patterns, anomalies, or predictions should exist.
        self.assertEqual(VisitorPattern.objects.count(), 0)
        self.assertEqual(AnomalyDetection.objects.count(), 0)
        # Predictions may exist from other tests with --keepdb, so check
        # only for this society.
        self.assertEqual(
            PeakHourPrediction.objects.filter(
                society=self.society1
            ).count(),
            0,
        )


# =========================================================================
# --skip flag
# =========================================================================

class AICommandSkipTest(AICommandTestBase):
    """Tests for the --skip flag."""

    def test_skip_patterns(self):
        """--skip patterns skips the pattern analysis step."""
        with patch.object(
            AIRecommendationService, "analyze_visitor_patterns"
        ) as mock_patterns, patch.object(
            AIRecommendationService, "detect_anomalies"
        ) as mock_anomalies, patch.object(
            AIRecommendationService, "predict_peak_hours"
        ) as mock_predictions:
            mock_anomalies.return_value = {
                "anomalies_created": 0, "by_type": {}, "errors": 0
            }
            mock_predictions.return_value = {
                "predictions_created": 0, "analysis_date": None, "errors": 0
            }
            self._run_command(society=str(self.society1.pk), skip=["patterns"])
        mock_patterns.assert_not_called()
        mock_anomalies.assert_called_once()
        mock_predictions.assert_called_once()

    def test_skip_anomalies(self):
        """--skip anomalies skips the anomaly detection step."""
        with patch.object(
            AIRecommendationService, "analyze_visitor_patterns"
        ) as mock_patterns, patch.object(
            AIRecommendationService, "detect_anomalies"
        ) as mock_anomalies, patch.object(
            AIRecommendationService, "predict_peak_hours"
        ) as mock_predictions:
            mock_patterns.return_value = {
                "patterns_created": 0, "patterns_updated": 0, "errors": 0
            }
            mock_predictions.return_value = {
                "predictions_created": 0, "analysis_date": None, "errors": 0
            }
            self._run_command(society=str(self.society1.pk), skip=["anomalies"])
        mock_patterns.assert_called_once()
        mock_anomalies.assert_not_called()
        mock_predictions.assert_called_once()

    def test_skip_predictions(self):
        """--skip predictions skips the peak-hour prediction step."""
        with patch.object(
            AIRecommendationService, "analyze_visitor_patterns"
        ) as mock_patterns, patch.object(
            AIRecommendationService, "detect_anomalies"
        ) as mock_anomalies, patch.object(
            AIRecommendationService, "predict_peak_hours"
        ) as mock_predictions:
            mock_patterns.return_value = {
                "patterns_created": 0, "patterns_updated": 0, "errors": 0
            }
            mock_anomalies.return_value = {
                "anomalies_created": 0, "by_type": {}, "errors": 0
            }
            self._run_command(
                society=str(self.society1.pk), skip=["predictions"]
            )
        mock_patterns.assert_called_once()
        mock_anomalies.assert_called_once()
        mock_predictions.assert_not_called()

    def test_skip_multiple(self):
        """--skip with multiple values skips all listed steps."""
        with patch.object(
            AIRecommendationService, "analyze_visitor_patterns"
        ) as mock_patterns, patch.object(
            AIRecommendationService, "detect_anomalies"
        ) as mock_anomalies, patch.object(
            AIRecommendationService, "predict_peak_hours"
        ) as mock_predictions:
            self._run_command(
                society=str(self.society1.pk),
                skip=["patterns", "predictions"],
            )
        mock_patterns.assert_not_called()
        mock_anomalies.assert_called_once()
        mock_predictions.assert_not_called()

    def test_skip_output_shows_skipped_steps(self):
        """Output shows which steps were skipped."""
        output = self._run_command(skip=["patterns"])
        self.assertIn("Skipping:", output)
        self.assertIn("patterns", output)


# =========================================================================
# Error handling
# =========================================================================

class AICommandErrorTest(AICommandTestBase):
    """Tests for error handling in the command."""

    def test_society_failure_does_not_abort_others(self):
        """If one society fails, the others are still processed."""
        with patch.object(
            AIRecommendationService, "analyze_visitor_patterns"
        ) as mock_patterns:
            mock_patterns.side_effect = [
                RuntimeError("Society 1 failed"),
                {
                    "patterns_created": 0,
                    "patterns_updated": 0,
                    "errors": 0,
                },
            ]
            output = self._run_command()
        self.assertIn("FAILED", output)
        self.assertIn("1 error(s)", output)
        self.assertIn("1 society(ies) processed", output)

    def test_error_message_shown_for_failed_society(self):
        """The error message is shown for the failed society."""
        with patch.object(
            AIRecommendationService, "analyze_visitor_patterns"
        ) as mock_patterns:
            mock_patterns.side_effect = RuntimeError("DB connection lost")
            output = self._run_command(society=str(self.society1.pk))
        self.assertIn("FAILED", output)
        self.assertIn("DB connection lost", output)

    def test_step_error_reported_in_result(self):
        """When run_full_analysis returns an error key, it's reported."""
        with patch.object(
            AIRecommendationService, "analyze_visitor_patterns"
        ) as mock_patterns:
            mock_patterns.return_value = {"error": "pattern_analysis_failed"}
            output = self._run_command(society=str(self.society1.pk))
        self.assertIn("Patterns: FAILED", output)
        self.assertIn("pattern_analysis_failed", output)


# =========================================================================
# Output formatting
# =========================================================================

class AICommandOutputTest(AICommandTestBase):
    """Tests for output formatting."""

    def test_output_shows_society_name_and_id(self):
        """Output includes society name and ID."""
        output = self._run_command(society=str(self.society1.pk))
        self.assertIn(self.society1.name, output)
        self.assertIn(f"ID={self.society1.pk}", output)

    def test_output_shows_patterns_summary(self):
        """Output includes pattern creation summary."""
        with patch.object(
            AIRecommendationService, "analyze_visitor_patterns"
        ) as mock_patterns:
            mock_patterns.return_value = {
                "patterns_created": 3,
                "patterns_updated": 2,
                "errors": 0,
            }
            output = self._run_command(society=str(self.society1.pk))
        self.assertIn("Patterns:", output)
        self.assertIn("3 created", output)
        self.assertIn("2 updated", output)

    def test_output_shows_anomalies_summary(self):
        """Output includes anomaly creation summary."""
        with patch.object(
            AIRecommendationService, "detect_anomalies"
        ) as mock_anomalies:
            mock_anomalies.return_value = {
                "anomalies_created": 5,
                "by_type": {"after_hours_entry": 3, "duplicate_entry": 2},
                "errors": 0,
            }
            output = self._run_command(society=str(self.society1.pk))
        self.assertIn("Anomalies:", output)
        self.assertIn("5 created", output)

    def test_output_shows_predictions_summary(self):
        """Output includes prediction creation summary."""
        with patch.object(
            AIRecommendationService, "predict_peak_hours"
        ) as mock_predictions:
            from datetime import date
            mock_predictions.return_value = {
                "predictions_created": 168,
                "analysis_date": date.today(),
                "errors": 0,
            }
            output = self._run_command(society=str(self.society1.pk))
        self.assertIn("Predictions:", output)
        self.assertIn("168 created", output)

    def test_output_shows_done_summary(self):
        """Output ends with a Done summary line."""
        output = self._run_command(society=str(self.society1.pk))
        self.assertIn("Done.", output)
