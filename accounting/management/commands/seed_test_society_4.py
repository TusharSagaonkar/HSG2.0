from django.core.management.base import BaseCommand
from django.utils.timezone import localdate

from accounting.models import AccountingPeriod, FinancialYear
from accounting.services.standard_accounts import create_default_accounts_for_society
from accounting.services.standard_accounts import ensure_standard_categories
from societies.models import Society


class Command(BaseCommand):
    help = (
        "Seed Test Society 4 and verify cumulative multi-open accounting period "
        "logic: when a FinancialYear is created, all periods from FY start up to "
        "today are automatically opened."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--society",
            default="Test Society 4",
            help="Society name to seed. Default: Test Society 4",
        )

    def handle(self, *args, **options):
        society_name = (options["society"] or "").strip() or "Test Society 4"
        today = localdate()

        # Step 1: Create or get the society.
        society, created = Society.objects.get_or_create(name=society_name)
        if created:
            self.stdout.write(f"Created society: {society.name}")
        else:
            self.stdout.write(f"Using existing society: {society.name}")

        # Step 2: Bootstrap standard accounting infrastructure.
        ensure_standard_categories(society)
        create_default_accounts_for_society(society)

        # Step 3: Create the current financial year.
        # This triggers FinancialYear._create_accounting_periods() which
        # bulk-creates all monthly periods and marks all periods from FY
        # start through today as is_open=True (cumulative multi-open).
        fy = self._ensure_current_financial_year(society, today)

        # Step 4: Collect and display the period summary.
        periods = AccountingPeriod.objects.filter(
            society=society,
            financial_year=fy,
        ).order_by("start_date")

        open_periods = [p for p in periods if p.is_open]
        closed_periods = [p for p in periods if not p.is_open]

        self.stdout.write(self.style.SUCCESS("\n=== Financial Year ==="))
        self.stdout.write(f"  Name:       {fy.name}")
        self.stdout.write(f"  Start Date: {fy.start_date}")
        self.stdout.write(f"  End Date:   {fy.end_date}")
        self.stdout.write(f"  Is Open:    {fy.is_open}")
        self.stdout.write(f"  Today:      {today}")

        self.stdout.write(self.style.SUCCESS("\n=== Accounting Periods ==="))
        self.stdout.write(f"  Total:  {len(periods)}")
        self.stdout.write(f"  Open:   {len(open_periods)}")
        self.stdout.write(f"  Closed: {len(closed_periods)}")

        self.stdout.write("\n  Period Details:")
        self.stdout.write(f"  {'Start':>12}  {'End':>12}  {'Status':>8}")
        self.stdout.write(f"  {'-' * 12}  {'-' * 12}  {'-' * 8}")
        for period in periods:
            status = "OPEN" if period.is_open else "CLOSED"
            self.stdout.write(
                f"  {str(period.start_date):>12}  {str(period.end_date):>12}  {status:>8}"
            )

        if len(open_periods) > 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\n✓ Cumulative multi-open is working: "
                    f"{len(open_periods)} periods open, {len(closed_periods)} closed "
                    f"(out of {len(periods)} total)."
                )
            )
        else:
            self.stdout.write(
                f"\nℹ Only {len(open_periods)} period open — either the FY just "
                f"started this month or only one period was created."
            )

    def _ensure_current_financial_year(self, society, today):
        """Create or reuse the current financial year (April–March cycle)."""
        if today.month >= 4:
            start_year = today.year
        else:
            start_year = today.year - 1

        start_date = today.replace(year=start_year, month=4, day=1)
        end_date = today.replace(year=start_year + 1, month=3, day=31)
        fy_name = f"FY {start_year}-{str(start_year + 1)[-2:]}"

        fy = FinancialYear.objects.filter(
            society=society,
            start_date=start_date,
            end_date=end_date,
        ).first()

        if fy:
            self.stdout.write(f"FinancialYear already exists: {fy.name}")
            if not fy.is_open:
                fy.is_open = True
                fy.save(update_fields=["is_open"])
            period_count = AccountingPeriod.objects.filter(
                society=society, financial_year=fy
            ).count()
            self.stdout.write(f"  Existing periods: {period_count}")
            return fy

        # A new FinancialYear.save() triggers _create_accounting_periods(),
        # which bulk-creates monthly periods and opens all periods from
        # FY start through today.
        fy = FinancialYear.objects.create(
            society=society,
            name=fy_name,
            start_date=start_date,
            end_date=end_date,
            is_open=True,
        )
        self.stdout.write(
            f"Created FinancialYear: {fy.name} "
            f"(triggered auto period creation via _create_accounting_periods)"
        )
        return fy