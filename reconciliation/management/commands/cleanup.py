"""
Management command to clean up old or stale reconciliation data.

Usage:
    python manage.py cleanup --society 1
    python manage.py cleanup --society "Deepsagar" --older-than 90 --dry-run
    python manage.py cleanup --society 1 --scope imports --keep 6
    python manage.py cleanup --society 1 --dry-run
"""

from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from reconciliation.models import BankStatementImport, ReconciliationLink
from societies.models import Society


class Command(BaseCommand):
    help = "Clean up old or stale reconciliation data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--society",
            required=True,
            help="Society ID or name.",
        )
        parser.add_argument(
            "--older-than",
            type=int,
            default=365,
            help="Delete imports/links older than N days (default: 365).",
        )
        parser.add_argument(
            "--keep",
            type=int,
            default=12,
            help="Keep at least N most recent imports (default: 12).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Show what would be deleted without actually deleting.",
        )
        parser.add_argument(
            "--scope",
            choices=["imports", "links", "all"],
            default="all",
            help="Scope of cleanup: 'imports', 'links', or 'all' (default).",
        )

    def handle(self, *args, **options):
        society_arg = options["society"]
        older_than_days = options["older_than"]
        keep_count = options["keep"]
        dry_run = options["dry_run"]
        scope = options["scope"]

        # Resolve society
        society = self._resolve_society(society_arg)

        # Calculate cutoff date
        cutoff_date = date.today() - timedelta(days=older_than_days)

        self.stdout.write(
            f"Cleanup for society: {society.name} (ID: {society.id})"
        )
        self.stdout.write(
            f"  Cutoff date:     {cutoff_date} "
            f"(older than {older_than_days} days)"
        )
        self.stdout.write(f"  Keep at least:   {keep_count} most recent imports")
        self.stdout.write(f"  Scope:           {scope}")
        self.stdout.write(f"  Dry run:         {dry_run}")

        if dry_run:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN — no data will be deleted. "
                    "The following shows what WOULD be deleted."
                )
            )

        total_imports_deleted = 0
        total_links_deleted = 0

        # --- Clean up imports ---
        if scope in ("imports", "all"):
            total_imports_deleted = self._cleanup_imports(
                society, cutoff_date, keep_count, dry_run
            )

        # --- Clean up links ---
        if scope in ("links", "all"):
            total_links_deleted = self._cleanup_links(
                society, cutoff_date, dry_run
            )

        # --- Summary ---
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== Cleanup Summary ==="))
        if scope in ("imports", "all"):
            self.stdout.write(
                f"  Imports {'would be' if dry_run else ''} deleted: "
                f"{total_imports_deleted}"
            )
        if scope in ("links", "all"):
            self.stdout.write(
                f"  Links {'would be' if dry_run else ''} deleted:   "
                f"{total_links_deleted}"
            )

        if dry_run:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN — no changes were persisted. "
                    "Remove --dry-run to execute the cleanup."
                )
            )
        else:
            self.stdout.write("")
            self.stdout.write(
                self.style.SUCCESS("Cleanup completed successfully.")
            )

    def _cleanup_imports(self, society, cutoff_date, keep_count, dry_run):
        """Clean up old BankStatementImport records."""

        # Find all completed imports for the society, ordered by date descending
        all_imports = BankStatementImport.objects.filter(
            society=society,
            import_status=BankStatementImport.ImportStatus.COMPLETED,
        ).order_by("-uploaded_at")

        # Determine which to keep (the N most recent)
        keep_ids = set(
            all_imports.values_list("id", flat=True)[:keep_count]
        )

        # Candidates for deletion: older than cutoff and not in keep_ids
        candidates = BankStatementImport.objects.filter(
            society=society,
            import_status__in=[
                BankStatementImport.ImportStatus.COMPLETED,
                BankStatementImport.ImportStatus.FAILED,
            ],
            uploaded_at__date__lt=cutoff_date,
        ).exclude(
            id__in=keep_ids,
        ).annotate(
            transaction_count=Count("bank_transactions"),
        ).order_by("-uploaded_at")

        # Also check for imports with status PROCESSING — warn about these
        processing_imports = BankStatementImport.objects.filter(
            society=society,
            import_status=BankStatementImport.ImportStatus.PROCESSING,
        )
        for pi in processing_imports:
            self.stdout.write(
                self.style.WARNING(
                    f"  Skipping PROCESSING import #{pi.id}: "
                    f"'{pi.file_name}' — resolve its status before cleanup."
                )
            )

        total_deleted = 0
        if not candidates.exists():
            self.stdout.write("  No import candidates to delete.")
            return total_deleted

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"=== Imports to {'delete' if not dry_run else 'be deleted'} "
                f"({candidates.count()} total) ==="
            )
        )

        for imp in candidates:
            txn_count = getattr(imp, "transaction_count", 0)
            self.stdout.write(
                f"  #{imp.id:<6} "
                f"{imp.uploaded_at.date()}  "
                f"'{imp.file_name}'  "
                f"txns: {txn_count:<4}  "
                f"status: {imp.import_status}"
            )
            if not dry_run:
                imp.delete()
            total_deleted += 1

        return total_deleted

    def _cleanup_links(self, society, cutoff_date, dry_run):
        """Clean up old REVERSED or IGNORED ReconciliationLink records."""

        candidates = ReconciliationLink.objects.filter(
            society=society,
            status__in=[
                ReconciliationLink.Status.REVERSED,
                ReconciliationLink.Status.IGNORED,
            ],
            matched_at__date__lt=cutoff_date,
        ).select_related(
            "bank_transaction",
            "voucher_entry",
        ).order_by("-matched_at")

        total_deleted = 0
        if not candidates.exists():
            self.stdout.write("  No link candidates to delete.")
            return total_deleted

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"=== Links to {'delete' if not dry_run else 'be deleted'} "
                f"({candidates.count()} total) ==="
            )
        )

        for link in candidates:
            matched = link.matched_at.date() if link.matched_at else "N/A"
            bt_info = (
                f"BT#{link.bank_transaction_id}"
                if link.bank_transaction_id
                else "no-bank-tx"
            )
            le_info = (
                f"LE#{link.voucher_entry_id}"
                if link.voucher_entry_id
                else "no-ledger-entry"
            )
            self.stdout.write(
                f"  Link #{link.id:<6} "
                f"status: {link.status:<10} "
                f"matched: {matched}  "
                f"{bt_info} ↔ {le_info}  "
                f"₹{link.matched_amount:,.2f}"
            )
            if not dry_run:
                link.delete()
            total_deleted += 1

        return total_deleted

    @staticmethod
    def _resolve_society(society_arg):
        """Resolve a society by ID (int) or name (str)."""
        try:
            society_id = int(society_arg)
            return Society.objects.get(id=society_id)
        except ValueError:
            pass
        except Society.DoesNotExist:
            raise CommandError(f"Society with ID '{society_arg}' does not exist.")

        try:
            return Society.objects.get(name__iexact=society_arg)
        except Society.DoesNotExist:
            pass

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