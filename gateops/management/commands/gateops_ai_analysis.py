"""Management command to run the AI recommendation engine batch analysis.

Scheduled (e.g. via Celery beat or cron) to:

1. Rebuild visitor patterns from recent gate-event history.
2. Scan for anomalies (forgotten exits, after-hours entries, etc.).
3. Generate peak-hour predictions via EWMA smoothing.

Each step is independent — a failure in one society or one step does not abort
the others.

Usage::

    python manage.py gateops_ai_analysis
    python manage.py gateops_ai_analysis --society 3
    python manage.py gateops_ai_analysis --society Deepsagar --dry-run
    python manage.py gateops_ai_analysis --skip patterns predictions
    python manage.py gateops_ai_analysis --skip anomalies

Recommended schedule:
    - Hourly (anomaly detection only):  ``--skip patterns predictions``
    - Daily at 02:00 (full analysis):   no ``--skip`` flags
"""

from django.core.management.base import BaseCommand

from gateops.services.ai_recommendation_service import AIRecommendationService
from housing.models import Society


class Command(BaseCommand):
    help = "Run AI recommendation engine analysis (patterns, anomalies, peak-hour predictions) for gate events."

    def add_arguments(self, parser):
        parser.add_argument(
            "--society",
            type=str,
            default=None,
            help="Limit analysis to a specific society (name or ID). Default: all societies.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Show what would be analyzed without persisting results.",
        )
        parser.add_argument(
            "--skip",
            nargs="*",
            choices=["patterns", "anomalies", "predictions"],
            default=[],
            help="Skip specific analysis types (space-separated).",
        )

    def handle(self, *args, **options):
        society_filter = options["society"]
        dry_run = options["dry_run"]
        skip = set(options["skip"])

        # Build the society queryset.
        qs = Society.objects.all()
        if society_filter:
            # Try by ID first, then by name (case-insensitive).
            try:
                society_id = int(society_filter)
                qs = qs.filter(id=society_id)
            except (ValueError, TypeError):
                qs = qs.filter(name__iexact=society_filter)

        societies = list(qs)
        if not societies:
            self.stdout.write(
                self.style.WARNING("No societies found matching the filter.")
            )
            return

        self.stdout.write(
            f"Running AI analysis for {len(societies)} society(ies)"
            f"{' [DRY RUN]' if dry_run else ''}..."
        )
        if skip:
            self.stdout.write(f"  Skipping: {', '.join(sorted(skip))}")

        total_success = 0
        total_errors = 0

        for society in societies:
            self.stdout.write(f"\n  Society: {society.name} (ID={society.pk})")
            try:
                if dry_run:
                    # In dry-run mode, report what would be done without
                    # calling the service (which persists to the DB).
                    self._dry_run_report(society, skip)
                else:
                    result = self._run_analysis(society, skip)
                    self._report_result(society, result)
                total_success += 1
            except Exception as exc:  # noqa: BLE001 — keep processing remaining societies.
                self.stdout.write(
                    self.style.ERROR(
                        f"    FAILED: {exc}"
                    )
                )
                total_errors += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {total_success} society(ies) processed, "
                f"{total_errors} error(s)."
            )
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _run_analysis(self, society, skip: set) -> dict:
        """Execute the requested analysis steps and return a combined result."""
        result: dict = {}

        if "patterns" not in skip:
            result["patterns"] = AIRecommendationService.analyze_visitor_patterns(
                society=society
            )
        if "anomalies" not in skip:
            result["anomalies"] = AIRecommendationService.detect_anomalies(
                society=society
            )
        if "predictions" not in skip:
            result["predictions"] = AIRecommendationService.predict_peak_hours(
                society=society
            )

        return result

    def _dry_run_report(self, society, skip: set) -> None:
        """Print what would be analyzed without persisting anything."""
        steps = []
        if "patterns" not in skip:
            steps.append("visitor patterns")
        if "anomalies" not in skip:
            steps.append("anomaly detection")
        if "predictions" not in skip:
            steps.append("peak-hour predictions")

        if steps:
            self.stdout.write(
                f"    [DRY RUN] Would run: {', '.join(steps)}"
            )
        else:
            self.stdout.write(
                self.style.WARNING("    [DRY RUN] All steps skipped — nothing to do.")
            )

    def _report_result(self, society, result: dict) -> None:
        """Pretty-print the analysis result for a single society."""
        if "patterns" in result:
            p = result["patterns"]
            if "error" in p:
                self.stdout.write(
                    self.style.ERROR(
                        f"    Patterns: FAILED ({p['error']})"
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"    Patterns: {p.get('patterns_created', 0)} created, "
                        f"{p.get('patterns_updated', 0)} updated, "
                        f"{p.get('errors', 0)} error(s)"
                    )
                )

        if "anomalies" in result:
            a = result["anomalies"]
            if "error" in a:
                self.stdout.write(
                    self.style.ERROR(
                        f"    Anomalies: FAILED ({a['error']})"
                    )
                )
            else:
                by_type = a.get("by_type", {})
                type_summary = ", ".join(
                    f"{k}={v}" for k, v in sorted(by_type.items()) if v
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"    Anomalies: {a.get('anomalies_created', 0)} created"
                        + (f" ({type_summary})" if type_summary else "")
                        + f", {a.get('errors', 0)} error(s)"
                    )
                )

        if "predictions" in result:
            pr = result["predictions"]
            if "error" in pr:
                self.stdout.write(
                    self.style.ERROR(
                        f"    Predictions: FAILED ({pr['error']})"
                    )
                )
            else:
                analysis_date = pr.get("analysis_date", "")
                self.stdout.write(
                    self.style.SUCCESS(
                        f"    Predictions: {pr.get('predictions_created', 0)} created"
                        + (f" for {analysis_date}" if analysis_date else "")
                        + f", {pr.get('errors', 0)} error(s)"
                    )
                )
