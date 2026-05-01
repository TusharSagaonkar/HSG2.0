"""
Management command to print all accounts in a tree-like text structure.

Usage:
    python manage.py print_account_tree
    python manage.py print_account_tree --society-id 1
    python manage.py print_account_tree --indent-char "  " --max-depth 5
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import models
from accounting.models import Account
from societies.models import Society


class Command(BaseCommand):
    help = "Print all accounts in a tree-like text structure showing parent-child relationships"

    def add_arguments(self, parser):
        parser.add_argument(
            "--society-id",
            type=int,
            help="Filter accounts by society ID",
        )
        parser.add_argument(
            "--society-name",
            type=str,
            help="Filter accounts by society name (case-insensitive partial match)",
        )
        parser.add_argument(
            "--indent-char",
            type=str,
            default="  ",
            help="Character(s) to use for indentation (default: two spaces)",
        )
        parser.add_argument(
            "--max-depth",
            type=int,
            default=20,
            help="Maximum depth to traverse (default: 20)",
        )
        parser.add_argument(
            "--show-inactive",
            action="store_true",
            help="Include inactive accounts in the tree",
        )
        parser.add_argument(
            "--format",
            type=str,
            choices=["tree", "flat", "csv"],
            default="tree",
            help="Output format: tree (default), flat, or csv",
        )

    def handle(self, *args, **options):
        society_id = options.get("society_id")
        society_name = options.get("society_name")
        indent_char = options.get("indent_char", "  ")
        max_depth = options.get("max_depth", 20)
        show_inactive = options.get("show_inactive", False)
        output_format = options.get("format", "tree")

        # Build queryset
        queryset = Account.objects.select_related("society", "category", "parent").order_by(
            "society__name", "name"
        )

        if not show_inactive:
            queryset = queryset.filter(is_active=True)

        # Filter by society
        if society_id:
            queryset = queryset.filter(society_id=society_id)
            try:
                society = Society.objects.get(id=society_id)
            except Society.DoesNotExist:
                raise CommandError(f"Society with ID {society_id} does not exist")
        elif society_name:
            queryset = queryset.filter(society__name__icontains=society_name)
            if not queryset.exists():
                raise CommandError(f"No societies found matching '{society_name}'")

        if output_format == "csv":
            self._print_csv(queryset)
        elif output_format == "flat":
            self._print_flat(queryset)
        else:
            self._print_tree(queryset, indent_char, max_depth)

    def _print_tree(self, queryset, indent_char, max_depth):
        """Print accounts in a tree structure."""
        accounts = list(queryset)

        if not accounts:
            self.stdout.write(self.style.WARNING("No accounts found."))
            return

        # Group accounts by society
        society_groups = {}
        for account in accounts:
            society_key = account.society_id
            if society_key not in society_groups:
                society_groups[society_key] = {
                    "society": account.society,
                    "accounts": [],
                }
            society_groups[society_key]["accounts"].append(account)

        total_accounts = len(accounts)
        self.stdout.write(self.style.SUCCESS(f"\n{'=' * 80}"))
        self.stdout.write(self.style.SUCCESS(f"ACCOUNT TREE STRUCTURE"))
        self.stdout.write(self.style.SUCCESS(f"Total Accounts: {total_accounts}"))
        self.stdout.write(self.style.SUCCESS(f"{'=' * 80}\n"))

        for society_key in sorted(society_groups.keys(), key=lambda k: society_groups[k]["society"].name.lower()):
            group = society_groups[society_key]
            society = group["society"]
            society_accounts = group["accounts"]

            self.stdout.write(self.style.WARNING(f"\nSociety: {society.name} (ID: {society.id})"))
            self.stdout.write(self.style.WARNING(f"{'-' * 60}"))

            # Build tree structure
            account_map = {acc.id: acc for acc in society_accounts}
            root_accounts = [acc for acc in society_accounts if acc.parent_id is None]

            if not root_accounts:
                self.stdout.write("  No root accounts found.")
                continue

            # Sort root accounts by account_type, then name
            root_accounts.sort(key=lambda a: (a.account_type or "", a.name.lower()))

            for root in root_accounts:
                self._print_account_node(root, account_map, indent_char, 0, max_depth)

        self.stdout.write(self.style.SUCCESS(f"\n{'=' * 80}"))
        self.stdout.write(self.style.SUCCESS(f"END OF ACCOUNT TREE"))
        self.stdout.write(self.style.SUCCESS(f"{'=' * 80}\n"))

    def _print_account_node(self, account, account_map, indent_char, depth, max_depth):
        """Recursively print an account and its children."""
        if depth >= max_depth:
            return

        indent = indent_char * depth
        account_type_display = account.get_account_type_display() if account.account_type else "N/A"
        status = " [INACTIVE]" if not account.is_active else ""
        protected = " [PROTECTED]" if account.system_protected else ""
        bank = " [BANK]" if account.is_bank else ""
        gst = f" [GST-{account.gst_type}]" if account.is_gst else ""

        # Build the line
        line = f"{indent}├─ {account.name}"
        if account.code:
            line += f" ({account.code})"
        line += f" [{account_type_display}]"
        line += f"{status}{protected}{bank}{gst}"

        self.stdout.write(line)

        # Print children
        children = [acc for acc in account_map.values() if acc.parent_id == account.id]
        children.sort(key=lambda a: a.name.lower())

        for child in children:
            self._print_account_node(child, account_map, indent_char, depth + 1, max_depth)

    def _print_flat(self, queryset):
        """Print accounts in a flat list with parent info."""
        self.stdout.write(self.style.SUCCESS(f"\n{'=' * 80}"))
        self.stdout.write(self.style.SUCCESS(f"FLAT ACCOUNT LIST"))
        self.stdout.write(self.style.SUCCESS(f"{'=' * 80}\n"))

        for account in queryset.order_by("society__name", "name"):
            parent_name = account.parent.name if account.parent else "(root)"
            account_type = account.get_account_type_display() if account.account_type else "N/A"
            self.stdout.write(
                f"[{account.society.name}] {account.name} "
                f"(Type: {account_type}, Parent: {parent_name})"
            )

        self.stdout.write(self.style.SUCCESS(f"\nTotal: {queryset.count()} accounts"))

    def _print_csv(self, queryset):
        """Print accounts in CSV format."""
        self.stdout.write("Society,Account Name,Code,Account Type,Sub Type,Parent,Is Active,Is Bank,Is GST")
        for account in queryset.order_by("society__name", "name"):
            parent_name = account.parent.name if account.parent else ""
            account_type = account.get_account_type_display() if account.account_type else ""
            sub_type = account.get_sub_type_display() if account.sub_type else ""
            self.stdout.write(
                f"{account.society.name},"
                f"{account.name},"
                f"{account.code or ''},"
                f"{account_type},"
                f"{sub_type},"
                f"{parent_name},"
                f"{account.is_active},"
                f"{account.is_bank},"
                f"{account.is_gst}"
            )
