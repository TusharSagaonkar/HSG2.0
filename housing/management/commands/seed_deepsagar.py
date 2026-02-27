from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounting.models import Account
from accounting.models import AccountCategory
from accounting.models import AccountingPeriod
from accounting.models import FinancialYear
from accounting.services.standard_accounts import create_default_accounts_for_society
from accounting.services.standard_accounts import ensure_standard_categories
from billing.models import Bill
from billing.models import ChargeTemplate
from billing.services import apply_late_fees
from billing.services import generate_bills_for_period
from members.models import Member
from members.models import Structure
from members.models import Unit
from notifications.models import ReminderLog
from notifications.services import schedule_payment_reminders
from receipts.models import PaymentReceipt
from receipts.services import post_receipt_for_bill
from societies.models import Society

FINANCIAL_YEAR_START_MONTH = 4


class Command(BaseCommand):
    help = "Create or refresh sample data for Deepsagar society."

    def add_arguments(self, parser):
        parser.add_argument(
            "--society",
            default="Deepsagar",
            help="Society name (case-insensitive). Default: Deepsagar",
        )

    def handle(self, *args, **options):
        society_name = (options["society"] or "").strip() or "Deepsagar"
        today = timezone.localdate()

        society = Society.objects.filter(name__iexact=society_name).first()
        if society is None:
            society = Society.objects.create(name=society_name)
            self.stdout.write(f"Created society: {society.name}")
        else:
            self.stdout.write(f"Using existing society: {society.name}")

        ensure_standard_categories(society)
        create_default_accounts_for_society(society)

        receivable = self._get_or_create_account(
            society=society,
            name="Maintenance Receivable",
            category_name="Member Receivables",
            account_type=AccountCategory.AccountType.ASSET,
        )
        main_bank = self._get_or_create_account(
            society=society,
            name="Bank Account - Main",
            category_name="Bank & Cash",
            account_type=AccountCategory.AccountType.ASSET,
        )
        maintenance_income = self._get_or_create_account(
            society=society,
            name="Maintenance Charges",
            category_name="Maintenance Charges",
            account_type=AccountCategory.AccountType.INCOME,
        )
        sinking_income = self._get_or_create_account(
            society=society,
            name="Other Income",
            category_name="Maintenance Charges",
            account_type=AccountCategory.AccountType.INCOME,
        )

        fy = self._ensure_open_financial_year(society=society, today=today)
        period = self._ensure_open_period(
            society=society,
            financial_year=fy,
            today=today,
        )

        units = self._ensure_structure_and_units(society=society)
        members = self._ensure_members(
            society=society,
            units=units,
            receivable_account=receivable,
            start_date=period.start_date,
        )

        self._ensure_charge_templates(
            society=society,
            income_account=maintenance_income,
            sinking_account=sinking_income,
            receivable_account=receivable,
        )

        bill_date = period.start_date + timedelta(days=4)
        bill_date = min(bill_date, today)
        bill_date = max(bill_date, period.start_date)

        generated = generate_bills_for_period(
            society=society,
            period_start=period.start_date,
            period_end=period.end_date,
            bill_date=bill_date,
        )

        period_bills = list(
            Bill.objects.filter(
                society=society,
                bill_period_start=period.start_date,
                bill_period_end=period.end_date,
            )
            .select_related("member")
            .order_by("id"),
        )
        receipts_created = self._create_sample_receipts(
            society=society,
            bills=period_bills,
            bank_account=main_bank,
            receipt_date=min(today, period.end_date),
        )
        late_fees_updated = apply_late_fees(society=society, as_of_date=today)
        reminders_scheduled = schedule_payment_reminders(
            society=society,
            as_of_date=today,
            channels=[ReminderLog.Channel.EMAIL],
        )

        charge_templates_count = ChargeTemplate.objects.filter(society=society).count()
        total_receipts_count = PaymentReceipt.objects.filter(society=society).count()
        total_reminders_count = ReminderLog.objects.filter(society=society).count()

        self.stdout.write(self.style.SUCCESS("Deepsagar sample data loaded."))
        self.stdout.write(
            "\n".join(
                [
                    f"Society: {society.name} (id={society.id})",
                    f"Financial year: {fy.name} ({fy.start_date} to {fy.end_date})",
                    f"Open period used: {period.start_date} to {period.end_date}",
                    f"Structures: {Structure.objects.filter(society=society).count()}",
                    f"Units: {len(units)}",
                    f"Members: {len(members)}",
                    f"Charge templates: {charge_templates_count}",
                    f"Bills in period: {len(period_bills)}",
                    f"New bills generated now: {len(generated)}",
                    f"New sample receipts created now: {receipts_created}",
                    f"Late fees updated now: {late_fees_updated}",
                    f"Reminders scheduled now: {reminders_scheduled}",
                    f"Total receipts: {total_receipts_count}",
                    f"Total reminders: {total_reminders_count}",
                ],
            ),
        )

    def _get_or_create_account(self, *, society, name, category_name, account_type):
        alternate_name = None
        if " - " in name:
            alternate_name = name.replace(" - ", " \u2013 ")
        elif " \u2013 " in name:
            alternate_name = name.replace(" \u2013 ", " - ")

        account = Account.objects.filter(society=society, name=name).first()
        if account is None and alternate_name:
            account = Account.objects.filter(
                society=society,
                name=alternate_name,
            ).first()
        if account:
            return account

        category, _ = AccountCategory.objects.get_or_create(
            society=society,
            name=category_name,
            account_type=account_type,
        )
        account, _ = Account.objects.get_or_create(
            society=society,
            name=name,
            parent=None,
            defaults={"category": category},
        )
        return account

    def _ensure_open_financial_year(self, *, society, today):
        fy = FinancialYear.get_open_year_for_date(today, society=society)
        if fy:
            return fy

        start_year = (
            today.year
            if today.month >= FINANCIAL_YEAR_START_MONTH
            else today.year - 1
        )
        start_date = today.replace(
            year=start_year,
            month=FINANCIAL_YEAR_START_MONTH,
            day=1,
        )
        end_date = today.replace(year=start_year + 1, month=3, day=31)
        name = f"FY {start_year}-{str(start_year + 1)[-2:]}"
        fy, _ = FinancialYear.objects.get_or_create(
            society=society,
            start_date=start_date,
            end_date=end_date,
            defaults={"name": name, "is_open": True},
        )
        if not fy.is_open:
            fy.is_open = True
            fy.save(update_fields=["is_open"])
        return fy

    def _ensure_open_period(self, *, society, financial_year, today):
        period = AccountingPeriod.objects.filter(
            society=society,
            financial_year=financial_year,
            start_date__lte=today,
            end_date__gte=today,
        ).first()
        if period is None:
            period = (
                AccountingPeriod.objects.filter(
                    society=society,
                    financial_year=financial_year,
                )
                .order_by("start_date")
                .first()
            )
        if period is None:
            start_date = financial_year.start_date
            next_month = (start_date.replace(day=28) + timedelta(days=4)).replace(day=1)
            end_date = min(next_month - timedelta(days=1), financial_year.end_date)
            period = AccountingPeriod.objects.create(
                society=society,
                financial_year=financial_year,
                start_date=start_date,
                end_date=end_date,
                is_open=True,
            )
        if not period.is_open:
            period.is_open = True
            period.save(update_fields=["is_open"])
        return period

    def _ensure_structure_and_units(self, *, society):
        building_a, _ = Structure.objects.get_or_create(
            society=society,
            parent=None,
            structure_type=Structure.StructureType.BUILDING,
            name="A",
            defaults={"display_order": 1},
        )
        unit_specs = [
            ("101", Decimal("980.00")),
            ("102", Decimal("1025.00")),
            ("201", Decimal("1180.00")),
            ("202", Decimal("1230.00")),
        ]
        units = []
        for identifier, area in unit_specs:
            unit, _ = Unit.objects.get_or_create(
                structure=building_a,
                identifier=identifier,
                defaults={
                    "unit_type": Unit.UnitType.FLAT,
                    "area_sqft": area,
                    "is_active": True,
                },
            )
            units.append(unit)
        return units

    def _ensure_members(self, *, society, units, receivable_account, start_date):
        names = ["Amit Patil", "Neha Deshmukh", "Rohan Shah", "Priya Kulkarni"]
        members = []
        for unit, full_name in zip(units, names, strict=False):
            member, _ = Member.objects.get_or_create(
                society=society,
                unit=unit,
                full_name=full_name,
                role=Member.MemberRole.OWNER,
                defaults={
                    "status": Member.MemberStatus.ACTIVE,
                    "start_date": start_date,
                    "receivable_account": receivable_account,
                },
            )
            updates = []
            if member.status != Member.MemberStatus.ACTIVE:
                member.status = Member.MemberStatus.ACTIVE
                updates.append("status")
            if member.receivable_account_id != receivable_account.id:
                member.receivable_account = receivable_account
                updates.append("receivable_account")
            if member.start_date > start_date:
                member.start_date = start_date
                updates.append("start_date")
            if updates:
                member.save(update_fields=updates)
            members.append(member)
        return members

    def _ensure_charge_templates(
        self,
        *,
        society,
        income_account,
        sinking_account,
        receivable_account,
    ):
        templates = [
            {
                "name": "Maintenance",
                "rate": Decimal("2500.00"),
                "due_days": 10,
                "late_fee_percent": Decimal("10.00"),
                "income_account": income_account,
            },
            {
                "name": "Sinking Fund",
                "rate": Decimal("500.00"),
                "due_days": 15,
                "late_fee_percent": Decimal("0.00"),
                "income_account": sinking_account,
            },
        ]
        today = timezone.localdate()
        for item in templates:
            latest = (
                ChargeTemplate.objects.filter(
                    society=society,
                    name=item["name"],
                )
                .order_by("-version_no")
                .first()
            )
            expected = {
                "description": f"Sample template: {item['name']}",
                "charge_type": ChargeTemplate.ChargeType.FIXED,
                "rate": item["rate"],
                "frequency": ChargeTemplate.Frequency.MONTHLY,
                "due_days": item["due_days"],
                "late_fee_percent": item["late_fee_percent"],
                "income_account": item["income_account"],
                "receivable_account": receivable_account,
                "is_active": True,
                "effective_to": None,
            }
            if latest and all(getattr(latest, key) == value for key, value in expected.items()):
                continue

            if latest and not latest.bill_lines.exists() and latest.effective_from == today:
                update_fields = []
                for key, value in expected.items():
                    if getattr(latest, key) != value:
                        setattr(latest, key, value)
                        update_fields.append(key)
                if update_fields:
                    latest.save(update_fields=update_fields)
                continue

            if latest and latest.is_active:
                latest.is_active = False
                latest.effective_to = today - timedelta(days=1)
                latest.save(update_fields=["is_active", "effective_to"])

            ChargeTemplate.objects.create(
                society=society,
                name=item["name"],
                description=expected["description"],
                charge_type=expected["charge_type"],
                rate=expected["rate"],
                frequency=expected["frequency"],
                due_days=expected["due_days"],
                late_fee_percent=expected["late_fee_percent"],
                income_account=expected["income_account"],
                receivable_account=expected["receivable_account"],
                is_active=expected["is_active"],
                effective_from=today,
                previous_version=latest,
            )

    def _create_sample_receipts(self, *, society, bills, bank_account, receipt_date):
        created = 0
        for index, bill in enumerate(bills):
            if bill.outstanding_amount <= 0:
                continue
            if bill.receipt_allocations.exists():
                continue

            if index == 0:
                amount = (bill.total_amount * Decimal("0.40")).quantize(Decimal("0.01"))
                payment_mode = "BANK_TRANSFER"
                reference = f"DEEP-TR-{bill.bill_number}"
            else:
                amount = bill.outstanding_amount
                payment_mode = "CASH"
                reference = ""

            post_receipt_for_bill(
                society=society,
                member=bill.member,
                bill=bill,
                amount=amount,
                receipt_date=receipt_date,
                payment_mode=payment_mode,
                deposited_account=bank_account,
                reference_number=reference,
            )
            created += 1
        return created
