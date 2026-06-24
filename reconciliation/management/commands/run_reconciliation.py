"""
Management command to run the bank reconciliation matching engine.

Usage:
    python manage.py run_reconciliation --society 1
    python manage.py run_reconciliation --society "Deepsagar" --auto-confirm
    python manage.py run_reconciliation --society 1 --dry-run
"""

from django.core.management.base import BaseCommand, CommandError

from reconciliation.services.matcher import MatchingEngine
from reconciliation.models import BankStatementImport
from societies.models import Society


class Command(BaseCommand):
    help = "Run the bank reconciliation matching engine for a society."

    def add_arguments(self, parser):
        parser.add_argument(
            "--society",
            required=True,
            help="Society ID or name to run reconciliation for.",
        )
        parser.add_argument(
            "--auto-confirm",
            action="store_true",
            default=False,
            help="Auto-confirm matches above the confidence threshold.",
        )
        parser.add_argument(
            "--threshold",
            type=int,
            default=85,
            help="Confidence threshold for auto-confirmation (default: 85).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Preview matches without persisting changes.",
        )

    def handle(self, *args, **options):
        society_arg = options["society"]
        auto_confirm = options["auto_confirm"]
        threshold = options["threshold"]
        dry_run = options["dry_run"]

        # Resolve society by ID or name
        society = self._resolve_society(society_arg)

        # Verify society has reconciliation data
        import_count = BankStatementImport.objects.filter(
            society=society,
        ).count()
        if import_count == 0:
            raise CommandError(
                f"Society '{society.name}' has no bank statement imports. "
                f"Import a statement first using 'import_statement'."
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Running reconciliation for society: {society.name} "
                f"(ID: {society.id})"
            )
        )
        self.stdout.write(f"  Auto-confirm: {auto_confirm}")
        self.stdout.write(f"  Threshold: {threshold}")
        self.stdout.write(f"  Dry run: {dry_run}")
        self.stdout.write(f"  Existing imports: {import_count}")

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN — matches will be computed but NOT persisted."
                )
            )

        # Instantiate the matching engine
        engine = MatchingEngine(society)

        if dry_run:
            from django.db import transaction

            with transaction.atomic():
                sid = transaction.savepoint()
                results = engine.run_matching(
                    auto_confirm=auto_confirm,
                    create_suggestions=True,
                )
                transaction.savepoint_rollback(sid)
        else:
            results = engine.run_matching(
                auto_confirm=auto_confirm,
                create_suggestions=True,
            )

        # Print results
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== Reconciliation Results ==="))

        auto_matched = results.get("auto_matched", [])
        suggested = results.get("suggested", [])
        candidates = results.get("candidates", [])
        stats = results.get("stats", {})

        self.stdout.write(f"  Total candidates found: {len(candidates)}")
        self.stdout.write(f"  Auto-matched links:     {len(auto_matched)}")
        self.stdout.write(f"  Suggestions created:    {len(suggested)}")

        # Statistics by rule
        if stats:
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS("--- Statistics by Rule ---"))
            for rule_name, data in sorted(stats.items()):
                self.stdout.write(
                    f"  {rule_name:<35} "
                    f"Count: {data['count']:>4}  "
                    f"Avg Confidence: {data['avg_confidence']:>5.1f}%"
                )

        # Statistics by confidence level
        if candidates:
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS("--- By Confidence Level ---"))
            confidence_buckets = {
                "99-100": [],
                "95-98": [],
                "85-94": [],
                "70-84": [],
                "50-69": [],
            }
            for c in candidates:
                if c.confidence >= 99:
                    confidence_buckets["99-100"].append(c)
                elif c.confidence >= 95:
                    confidence_buckets["95-98"].append(c)
                elif c.confidence >= 85:
                    confidence_buckets["85-94"].append(c)
                elif c.confidence >= 70:
                    confidence_buckets["70-84"].append(c)
                else:
                    confidence_buckets["50-69"].append(c)

            for bucket, items in confidence_buckets.items():
                if items:
                    self.stdout.write(f"  {bucket}%: {len(items)} candidates")

        if dry_run:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN — no changes were persisted."
                )
            )
        else:
            self.stdout.write("")
            self.stdout.write(
                self.style.SUCCESS("Reconciliation completed successfully.")
            )

    @staticmethod
    def _resolve_society(society_arg):
        """Resolve a society by ID (int) or name (str)."""
        # Try by ID first
        try:
            society_id = int(society_arg)
            return Society.objects.get(id=society_id)
        except ValueError:
            pass
        except Society.DoesNotExist:
            raise CommandError(f"Society with ID '{society_arg}' does not exist.")

        # Try by name
        try:
            return Society.objects.get(name__iexact=society_arg)
        except Society.DoesNotExist:
            pass

        # Try case-insensitive contains
        matches = Society.objects.filter(name__icontains=society_arg)
        count = matches.count()
        if count == 1:
            return matches.first()
        elif count > 1:
            names = ", ".join(m.name for m in matches[:10])
            raise CommandError(
                f"Multiple societies match '{society_arg}': {names}. "
                f"Use the exact name or ID."
            )

        raise CommandError(
            f"Society '{society_arg}' not found. "
            f"Provide a valid society ID or name."
        )