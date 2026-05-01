"""
Management command to rebuild the account tree for societies.

This command deletes all existing accounts for a society and recreates them
from the NEW_ACCOUNT_TREE definition in standard_accounts.py.

Usage:
    python manage.py rebuild_account_tree --all
    python manage.py rebuild_account_tree --society-id 1
    python manage.py rebuild_account_tree --society-name "Deepsagar"
    python manage.py rebuild_account_tree --dry-run --all
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from accounting.services.standard_accounts import rebuild_accounts_for_society
from accounting.services.standard_accounts import create_default_accounts_for_society
from societies.models import Society


class Command(BaseCommand):
    help = "Rebuild account tree for societies from the standard definition."

    def add_arguments(self, parser):
        parser.add_argument(
            "--society-id",
            type=int,
            help="Rebuild accounts for a specific society by ID",
        )
        parser.add_argument(
            "--society-name",
            type=str,
            help="Rebuild accounts for a specific society by name (case-insensitive partial match)",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Rebuild accounts for ALL societies",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without making changes",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force rebuild even if society has transactions (DANGEROUS)",
        )

    def handle(self, *args, **options):
        society_id = options.get("society_id")
        society_name = options.get("society_name")
        rebuild_all = options.get("all", False)
        dry_run = options.get("dry_run", False)
        force = options.get("force", False)

        # Validate arguments
        if not (society_id or society_name or rebuild_all):
            raise CommandError(
                "Must specify one of: --society-id, --society-name, or --all"
            )

        # Get target societies
        if society_id:
            societies = [self._get_society_by_id(society_id)]
        elif society_name:
            societies = self._get_societies_by_name(society_name)
        else:
            societies = Society.objects.all()

        self.stdout.write(
            self.style.WARNING(
                f"Found {len(societies)} society(s) to process..."
            )
        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN MODE - no changes will be made"
                )
            )

        # Process each society
        success_count = 0
        error_count = 0

        for society in societies:
            self.stdout.write(f"\nProcessing society: {society.name} (ID: {society.id})")

            # Check for existing transactions if not forcing
            if not force and not dry_run:
                if self._has_transactions(society):
                    self.stdout.write(
                        self.style.WARNING(
                            f"  WARNING: Society '{society.name}' has existing transactions. "
                            "Use --force to rebuild anyway."
                        )
                    )
                    error_count += 1
                    continue

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  Would rebuild accounts for '{society.name}'"
                    )
                )
                success_count += 1
                continue

            try:
                with transaction.atomic():
                    # Use rebuild (delete + recreate) or create (idempotent)
                    if force:
                        self.stdout.write("  Rebuilding accounts (delete + recreate)...")
                        rebuild_accounts_for_society(society)
                    else:
                        self.stdout.write("  Creating/updating accounts...")
                        create_default_accounts_for_society(society)

                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ Successfully processed '{society.name}'"
                    )
                )
                success_count += 1

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(
                        f"  ✗ Error processing '{society.name}': {e}"
                    )
                )
                error_count += 1

        # Summary
        self.stdout.write("\n" + "=" * 50)
        self.stdout.write(f"Summary:")
        self.stdout.write(f"  Successful: {success_count}")
        self.stdout.write(f"  Errors: {error_count}")
        self.stdout.write("=" * 50)

    def _get_society_by_id(self, society_id):
        """Get a society by ID or raise CommandError."""
        try:
            return Society.objects.get(id=society_id)
        except Society.DoesNotExist:
            raise CommandError(f"Society with ID {society_id} does not exist")

    def _get_societies_by_name(self, name):
        """Get societies by name (case-insensitive partial match)."""
        societies = Society.objects.filter(name__icontains=name)
        if not societies.exists():
            raise CommandError(f"No societies found matching '{name}'")
        return list(societies)

    def _has_transactions(self, society):
        """Check if society has any voucher entries (transactions)."""
        from accounting.models import LedgerEntry
        return LedgerEntry.objects.filter(
            voucher__society=society
        ).exists()
