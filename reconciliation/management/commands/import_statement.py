"""
Management command to import a bank statement file for a society.

Usage:
    python manage.py import_statement --society 1 --file /path/to/statement.csv
    python manage.py import_statement --society "Deepsagar" --file statement.xlsx --bank-account 5
    python manage.py import_statement --society 1 --file stmt.csv --bank-account-name "HDFC Current"
"""

import os
from pathlib import Path

from django.core.files import File as DjangoFile
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model

from reconciliation.services.importer import StatementImportService, ImportError
from reconciliation.services.normalizer import NormalizerService
from reconciliation.models import BankTransaction
from accounting.models.model_Account import Account
from societies.models import Society

User = get_user_model()


class Command(BaseCommand):
    help = "Import a bank statement file for a society."

    def add_arguments(self, parser):
        parser.add_argument(
            "--society",
            required=True,
            help="Society ID or name.",
        )
        parser.add_argument(
            "--file",
            required=True,
            help="Path to CSV or XLSX bank statement file.",
        )
        parser.add_argument(
            "--bank-account",
            type=int,
            default=None,
            help="Bank account ID. If not provided, uses --bank-account-name or first bank account.",
        )
        parser.add_argument(
            "--bank-account-name",
            default=None,
            help="Find bank account by name instead of ID.",
        )
        parser.add_argument(
            "--format",
            choices=["csv", "xlsx"],
            default=None,
            help="File format. Auto-detected from extension if not provided.",
        )

    def handle(self, *args, **options):
        society_arg = options["society"]
        file_path = options["file"]
        bank_account_id = options["bank_account"]
        bank_account_name = options["bank_account_name"]
        file_format = options["format"]

        # Resolve society
        society = self._resolve_society(society_arg)

        # Verify file exists and is readable
        path = Path(file_path)
        if not path.exists():
            raise CommandError(f"File not found: '{file_path}'")
        if not path.is_file():
            raise CommandError(f"Path is not a file: '{file_path}'")
        if not os.access(file_path, os.R_OK):
            raise CommandError(f"File is not readable: '{file_path}'")

        # Auto-detect format if not provided
        if file_format is None:
            extension = path.suffix.lower().lstrip(".")
            if extension in ("csv", "txt"):
                file_format = "csv"
            elif extension in ("xlsx", "xls"):
                file_format = "xlsx"
            else:
                raise CommandError(
                    f"Cannot auto-detect format from extension '.{extension}'. "
                    f"Use --format csv or --format xlsx."
                )

        self.stdout.write(f"Society: {society.name} (ID: {society.id})")
        self.stdout.write(f"File: {file_path}")
        self.stdout.write(f"Format: {file_format}")

        # Resolve bank account
        bank_account = self._resolve_bank_account(
            society, bank_account_id, bank_account_name
        )
        self.stdout.write(
            f"Bank Account: {bank_account.name} (ID: {bank_account.id})"
        )

        # Resolve user for uploaded_by (required FK)
        user = self._get_user()

        # Import the statement
        self.stdout.write("Importing statement...")

        try:
            with open(file_path, "rb") as fh:
                django_file = DjangoFile(fh, name=path.name)
                service = StatementImportService(
                    user=user,
                    society=society,
                    bank_account=bank_account,
                )
                statement_import = service.import_file(django_file)
        except ImportError as e:
            raise CommandError(f"Import failed: {e}") from e
        except Exception as e:
            raise CommandError(f"Unexpected error during import: {e}") from e

        self.stdout.write(
            self.style.SUCCESS(
                f"Import #{statement_import.id} completed: "
                f"{statement_import.row_count} transactions from '{path.name}'"
            )
        )

        # Trigger normalization
        self.stdout.write("Normalizing transactions...")
        normalizer = NormalizerService(society)

        bank_transactions = list(
            BankTransaction.objects.filter(
                bank_statement_import=statement_import,
            )
        )
        if bank_transactions:
            normalized = normalizer.normalize_batch(bank_transactions)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Normalized {len(normalized)} transactions."
                )
            )

            # Print normalization stats
            with_utr = sum(1 for n in normalized if n.extracted_utr)
            with_flat = sum(1 for n in normalized if n.extracted_flat_no)
            with_ref = sum(1 for n in normalized if n.extracted_reference)
            self.stdout.write(f"  Extracted UTR:       {with_utr}")
            self.stdout.write(f"  Extracted Flat No:   {with_flat}")
            self.stdout.write(f"  Extracted Reference: {with_ref}")
        else:
            self.stdout.write(
                self.style.WARNING("No transactions to normalize.")
            )

        # Print import summary
        duplicates_count = BankTransaction.objects.filter(
            bank_statement_import=statement_import,
            is_duplicate=True,
        ).count()

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== Import Summary ==="))
        self.stdout.write(
            f"  Import ID:           {statement_import.id}"
        )
        self.stdout.write(
            f"  Transactions found:  {statement_import.row_count}"
        )
        self.stdout.write(
            f"  Duplicates detected: {duplicates_count}"
        )
        self.stdout.write(
            f"  Status:              {statement_import.import_status}"
        )
        if statement_import.statement_start_date:
            self.stdout.write(
                f"  Statement period:    {statement_import.statement_start_date}"
                f" → {statement_import.statement_end_date}"
            )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS("Import completed successfully.")
        )

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

    @staticmethod
    def _resolve_bank_account(society, bank_account_id, bank_account_name):
        """Resolve bank account by ID, name, or fallback to first is_bank account."""
        # By ID first
        if bank_account_id is not None:
            try:
                account = Account.objects.get(
                    id=bank_account_id,
                    society=society,
                    is_bank=True,
                )
                return account
            except Account.DoesNotExist:
                raise CommandError(
                    f"Bank account with ID '{bank_account_id}' not found "
                    f"or is not a bank account for society '{society.name}'."
                )

        # By name
        if bank_account_name is not None:
            try:
                account = Account.objects.get(
                    society=society,
                    is_bank=True,
                    name__iexact=bank_account_name,
                )
                return account
            except Account.DoesNotExist:
                pass
            # Try contains match
            matches = Account.objects.filter(
                society=society,
                is_bank=True,
                name__icontains=bank_account_name,
            )
            if matches.count() == 1:
                return matches.first()
            elif matches.count() > 1:
                names = ", ".join(a.name for a in matches[:10])
                raise CommandError(
                    f"Multiple bank accounts match '{bank_account_name}': {names}. "
                    f"Use --bank-account with a specific ID."
                )
            raise CommandError(
                f"Bank account with name '{bank_account_name}' not found "
                f"for society '{society.name}'."
            )

        # Fallback: first bank account
        account = Account.objects.filter(
            society=society,
            is_bank=True,
            is_active=True,
        ).first()
        if account is None:
            raise CommandError(
                f"No bank accounts found for society '{society.name}'. "
                f"Create a bank account first or specify one with --bank-account."
            )
        return account

    @staticmethod
    def _get_user():
        """Get a user to use as uploaded_by. Prefers superuser, then first user."""
        user = User.objects.filter(is_superuser=True).first()
        if user is None:
            user = User.objects.first()
        if user is None:
            raise CommandError(
                "No users exist in the system. Create at least one user first."
            )
        return user