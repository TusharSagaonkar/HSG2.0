"""
Management command to generate reconciliation reports.

Usage:
    python manage.py reconciliation_report --society 1 --report-type brs
    python manage.py reconciliation_report --society "Deepsagar" --report-type all --format json
    python manage.py reconciliation_report --society 1 --report-type unmatched --output report.json
    python manage.py reconciliation_report --society 1 --report-type brs --as-of-date 2025-03-31
"""

import json
from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError

from reconciliation.services.reports import ReportService
from societies.models import Society


class Command(BaseCommand):
    help = "Generate reconciliation reports from the command line."

    REPORT_TYPES = ("brs", "unmatched", "duplicates", "exceptions", "all")

    def add_arguments(self, parser):
        parser.add_argument(
            "--society",
            required=True,
            help="Society ID or name.",
        )
        parser.add_argument(
            "--report-type",
            required=True,
            choices=self.REPORT_TYPES,
            help="Type of report to generate.",
        )
        parser.add_argument(
            "--output",
            default=None,
            help="File path to write JSON output. If not provided, prints to stdout.",
        )
        parser.add_argument(
            "--as-of-date",
            default=None,
            help="Date for BRS report in YYYY-MM-DD format.",
        )
        parser.add_argument(
            "--format",
            choices=["table", "json"],
            default="table",
            help="Output format: 'table' (default) or 'json'.",
        )

    def handle(self, *args, **options):
        society_arg = options["society"]
        report_type = options["report_type"]
        output_path = options["output"]
        as_of_date_str = options["as_of_date"]
        output_format = options["format"]

        # Resolve society
        society = self._resolve_society(society_arg)

        # Parse as-of date
        as_of_date = None
        if as_of_date_str:
            try:
                as_of_date = datetime.strptime(as_of_date_str, "%Y-%m-%d").date()
            except ValueError:
                raise CommandError(
                    f"Invalid date format: '{as_of_date_str}'. "
                    f"Use YYYY-MM-DD format."
                )

        self.stdout.write(
            f"Generating '{report_type}' report for: {society.name} (ID: {society.id})"
        )

        # Collect report data
        if report_type == "all":
            report_data = {
                "society": society.name,
                "society_id": society.id,
                "generated_at": str(date.today()),
                "brs": ReportService.get_brs_data(society, as_of_date),
                "unmatched": ReportService.get_unmatched_report(society),
                "duplicates": ReportService.get_duplicates_report(society),
                "exceptions": ReportService.get_exception_summary(society),
            }
        elif report_type == "brs":
            report_data = ReportService.get_brs_data(society, as_of_date)
            report_data["society"] = society.name
            report_data["society_id"] = society.id
            report_data["generated_at"] = str(date.today())
        elif report_type == "unmatched":
            report_data = ReportService.get_unmatched_report(society)
            report_data["society"] = society.name
            report_data["society_id"] = society.id
            report_data["generated_at"] = str(date.today())
        elif report_type == "duplicates":
            report_data = ReportService.get_duplicates_report(society)
            report_data["society"] = society.name
            report_data["society_id"] = society.id
            report_data["generated_at"] = str(date.today())
        elif report_type == "exceptions":
            report_data = ReportService.get_exception_summary(society)
            report_data["society"] = society.name
            report_data["society_id"] = society.id
            report_data["generated_at"] = str(date.today())
        else:
            raise CommandError(f"Unknown report type: '{report_type}'")

        # Output
        if output_format == "json":
            self._output_json(report_data, output_path)
        else:
            self._output_table(report_data, report_type)

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS("Report generated successfully.")
        )

    def _output_json(self, data, output_path):
        """Output report data as JSON."""

        def default_serializer(obj):
            """Handle Decimal, date, and other non-serializable types."""
            from decimal import Decimal

            if isinstance(obj, Decimal):
                return float(obj)
            if isinstance(obj, (date, datetime)):
                return str(obj)
            if hasattr(obj, "__str__"):
                return str(obj)
            return repr(obj)

        json_str = json.dumps(data, indent=2, default=default_serializer)

        if output_path:
            try:
                with open(output_path, "w") as fh:
                    fh.write(json_str)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"JSON report written to: {output_path}"
                    )
                )
            except IOError as e:
                raise CommandError(
                    f"Failed to write output file '{output_path}': {e}"
                ) from e
        else:
            self.stdout.write(json_str)

    def _output_table(self, data, report_type):
        """Pretty-print report data in tabular format."""

        # For 'all' type, delegate to individual printers
        if report_type == "all":
            self._print_brs_table(data.get("brs", {}))
            self.stdout.write("\n" + "=" * 60 + "\n")
            self._print_unmatched_table(data.get("unmatched", {}))
            self.stdout.write("\n" + "=" * 60 + "\n")
            self._print_duplicates_table(data.get("duplicates", {}))
            self.stdout.write("\n" + "=" * 60 + "\n")
            self._print_exceptions_table(data.get("exceptions", {}))
            return

        if report_type == "brs":
            self._print_brs_table(data)
        elif report_type == "unmatched":
            self._print_unmatched_table(data)
        elif report_type == "duplicates":
            self._print_duplicates_table(data)
        elif report_type == "exceptions":
            self._print_exceptions_table(data)

    def _print_brs_table(self, data):
        """Print Bank Reconciliation Statement in tabular format."""
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== Bank Reconciliation Statement ==="))

        book_balance = data.get("book_balance", 0)
        bank_balance = data.get("bank_balance", 0)
        adjusted_book = data.get("adjusted_book_balance", 0)
        adjusted_bank = data.get("adjusted_bank_balance", 0)
        difference = data.get("difference", 0)
        is_balanced = data.get("is_balanced", False)

        self.stdout.write(f"  Book Balance:                ₹{book_balance:,.2f}")
        self.stdout.write(f"  Bank Balance:                ₹{bank_balance:,.2f}")
        self.stdout.write(f"  Unpresented Credits:         ₹{data.get('unpresented_credits_total', 0):,.2f}")
        self.stdout.write(f"  Unpresented Debits:          ₹{data.get('unpresented_debits_total', 0):,.2f}")
        self.stdout.write(f"  Uncredited Items:            ₹{data.get('uncredited_total', 0):,.2f}")
        self.stdout.write(f"  Outstanding Cheques:         ₹{data.get('outstanding_total', 0):,.2f}")
        self.stdout.write(f"  ---")
        self.stdout.write(f"  Adjusted Book Balance:       ₹{adjusted_book:,.2f}")
        self.stdout.write(f"  Adjusted Bank Balance:       ₹{adjusted_bank:,.2f}")
        self.stdout.write(f"  Difference:                  ₹{difference:,.2f}")

        if is_balanced:
            self.stdout.write(
                self.style.SUCCESS("  Status: BALANCED ✓")
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"  Status: NOT BALANCED — difference of ₹{difference:,.2f}"
                )
            )

        self.stdout.write(f"  Matched Transactions:        {data.get('matched_count', 0)}")
        self.stdout.write(f"  Unmatched Transactions:      {data.get('unmatched_count', 0)}")
        self.stdout.write(f"  Exceptions:                  {data.get('exception_count', 0)}")

        bank_accounts = data.get("bank_accounts", [])
        if bank_accounts:
            self.stdout.write(f"  Bank Accounts: {len(bank_accounts)}")
            for acct in bank_accounts:
                self.stdout.write(f"    - {acct.get('name', 'N/A')} ({acct.get('code', '')})")

    def _print_unmatched_table(self, data):
        """Print unmatched transactions report."""
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== Unmatched Transactions Report ==="))

        book_only = data.get("book_only", [])
        bank_only = data.get("bank_only", [])
        book_only_total = data.get("book_only_total", 0)
        bank_only_total = data.get("bank_only_total", 0)

        self.stdout.write(f"  Book-Only (missing in bank): {len(book_only)} entries")
        self.stdout.write(f"    Total Amount: ₹{book_only_total:,.2f}")
        self.stdout.write(f"  Bank-Only (missing in books): {len(bank_only)} entries")
        self.stdout.write(f"    Total Amount: ₹{bank_only_total:,.2f}")

        if book_only:
            self.stdout.write("")
            self.stdout.write("  --- Top 10 Book-Only Entries ---")
            for item in book_only[:10]:
                date_str = item.get("date", "N/A")
                narration = (item.get("narration", "") or "")[:60]
                self.stdout.write(
                    f"    {date_str}  {item.get('voucher', 'N/A'):>10}  "
                    f"DR: ₹{item.get('debit', 0):,.2f}  "
                    f"CR: ₹{item.get('credit', 0):,.2f}  "
                    f"{narration}"
                )

        if bank_only:
            self.stdout.write("")
            self.stdout.write("  --- Top 10 Bank-Only Entries ---")
            for item in bank_only[:10]:
                date_str = item.get("date", "N/A")
                narration = (item.get("narration", "") or "")[:60]
                self.stdout.write(
                    f"    {date_str}  {item.get('dr_cr', 'N/A'):>6}  "
                    f"₹{item.get('amount', 0):>10,.2f}  "
                    f"{narration}"
                )

    def _print_duplicates_table(self, data):
        """Print duplicates report."""
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== Duplicates Report ==="))

        duplicate_links = data.get("duplicate_links", [])
        # Support both querysets and lists
        dup_link_count = (
            duplicate_links.count()
            if hasattr(duplicate_links, "count")
            else len(duplicate_links)
        )
        dup_bank = data.get("duplicate_bank_transactions", [])
        suspected_book = data.get("suspected_book_duplicates", [])

        self.stdout.write(f"  Duplicate Links:              {dup_link_count}")
        self.stdout.write(f"  Duplicate Bank Transactions:  {len(dup_bank)}")
        self.stdout.write(f"  Suspected Book Duplicates:    {len(suspected_book)}")

        if suspected_book:
            self.stdout.write("")
            self.stdout.write("  --- Top 10 Suspected Book Duplicates ---")
            for item in suspected_book[:10]:
                self.stdout.write(
                    f"    {item.get('date', 'N/A')}  "
                    f"{item.get('account', 'N/A')}  "
                    f"₹{item.get('amount', 0):,.2f}  "
                    f"x{item.get('count', 0)} entries"
                )

    def _print_exceptions_table(self, data):
        """Print exception summary."""
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== Exception Summary ==="))

        by_type = data.get("by_type", {})
        total_exceptions = data.get("total_exceptions", 0)
        total_amount = data.get("total_amount", 0)

        self.stdout.write(f"  Total Exceptions: {total_exceptions}")
        self.stdout.write(f"  Total Amount:     ₹{total_amount:,.2f}")

        if by_type:
            self.stdout.write("")
            self.stdout.write("  --- By Type ---")
            for exc_type, count in sorted(
                by_type.items(), key=lambda x: -x[1]
            ):
                self.stdout.write(f"    {exc_type:<25} {count:>4}")

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