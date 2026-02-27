from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db.models import Q
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
from members.models import UnitOccupancy
from members.models import UnitOwnership
from notifications.models import ReminderLog
from notifications.services import schedule_payment_reminders
from receipts.models import PaymentReceipt
from receipts.services import post_receipt_for_bill
from societies.models import Society

FINANCIAL_YEAR_START_MONTH = 4
DEFAULT_OWNER_COUNT = 100
DEFAULT_TENANT_COUNT = 50
FLOORS_PER_BUILDING = 5
UNITS_PER_FLOOR = 4
HISTORICAL_RECEIPT_CYCLE = 10
HISTORICAL_FULLY_PAID_MAX = 4
HISTORICAL_PARTIAL_MAX = 7
CURRENT_RECEIPT_CYCLE = 6
CURRENT_FULL_RECEIPT_SLOT = 3
DECIMAL_CENTS = Decimal("0.01")


class Command(BaseCommand):
    help = "Create or refresh sample data for Deepsagar society."

    def add_arguments(self, parser):
        parser.add_argument(
            "--society",
            default="Deepsagar",
            help="Society name (case-insensitive). Default: Deepsagar",
        )
        parser.add_argument(
            "--owners",
            type=int,
            default=DEFAULT_OWNER_COUNT,
            help=f"Number of owner members to seed. Default: {DEFAULT_OWNER_COUNT}",
        )
        parser.add_argument(
            "--tenants",
            type=int,
            default=DEFAULT_TENANT_COUNT,
            help=f"Number of tenant members to seed. Default: {DEFAULT_TENANT_COUNT}",
        )

    def handle(self, *args, **options):  # noqa: PLR0915
        society_name = (options["society"] or "").strip() or "Deepsagar"
        owners_requested = options["owners"]
        tenants_requested = options["tenants"]
        if owners_requested <= 0:
            msg = "Owners count must be greater than 0."
            raise CommandError(msg)
        if tenants_requested < 0:
            msg = "Tenants count cannot be negative."
            raise CommandError(msg)
        if tenants_requested > owners_requested:
            msg = "Tenants count cannot exceed owners count."
            raise CommandError(msg)

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

        units = self._ensure_structure_and_units(
            society=society,
            required_units=owners_requested,
        )
        if len(units) < owners_requested:
            msg = (
                f"Only {len(units)} units are available, "
                f"but {owners_requested} owners were requested."
            )
            raise CommandError(msg)
        owner_units = units[:owners_requested]
        tenant_units = owner_units[:tenants_requested]

        owner_members, owner_users_by_unit = self._ensure_owner_members(
            society=society,
            units=owner_units,
            receivable_account=receivable,
            start_date=period.start_date,
        )
        tenant_members, tenant_users_by_unit = self._ensure_tenant_members(
            society=society,
            units=tenant_units,
            receivable_account=receivable,
            start_date=min(today, period.start_date + timedelta(days=10)),
        )
        ownership_stats = self._ensure_ownership_and_occupancy(
            units=owner_units,
            owner_users_by_unit=owner_users_by_unit,
            tenant_users_by_unit=tenant_users_by_unit,
            owner_start_date=period.start_date,
            tenant_start_date=min(today, period.start_date + timedelta(days=10)),
        )

        periods_to_seed = self._periods_to_seed(
            society=society,
            financial_year=fy,
            open_period=period,
        )
        self._ensure_charge_templates(
            society=society,
            income_account=maintenance_income,
            sinking_account=sinking_income,
            receivable_account=receivable,
            seed_effective_from=periods_to_seed[0].start_date,
        )

        generated_bills_count = 0
        receipts_created = 0
        bills_in_seed_periods = 0
        for index, target_period in enumerate(periods_to_seed):
            generated = generate_bills_for_period(
                society=society,
                period_start=target_period.start_date,
                period_end=target_period.end_date,
                bill_date=self._bill_date_for_period(
                    period_start=target_period.start_date,
                    period_end=target_period.end_date,
                    today=today,
                ),
            )
            generated_bills_count += len(generated)
            period_bills = list(
                Bill.objects.filter(
                    society=society,
                    bill_period_start=target_period.start_date,
                    bill_period_end=target_period.end_date,
                )
                .select_related("member")
                .order_by("id"),
            )
            bills_in_seed_periods += len(period_bills)
            receipt_mode = (
                "historical"
                if index == 0 and len(periods_to_seed) > 1
                else "current"
            )
            receipts_created += self._create_sample_receipts(
                society=society,
                bills=period_bills,
                bank_account=main_bank,
                receipt_date=min(today, target_period.end_date),
                mode=receipt_mode,
            )

        late_fees_updated = apply_late_fees(society=society, as_of_date=today)
        reminders_scheduled = schedule_payment_reminders(
            society=society,
            as_of_date=today,
            channels=[ReminderLog.Channel.EMAIL, ReminderLog.Channel.SMS],
            upcoming_days=30,
        )
        owners_total = Member.objects.filter(
            society=society,
            role=Member.MemberRole.OWNER,
        ).count()
        tenants_total = Member.objects.filter(
            society=society,
            role=Member.MemberRole.TENANT,
        ).count()
        bills_total = Bill.objects.filter(society=society).count()
        charge_templates_count = ChargeTemplate.objects.filter(society=society).count()
        total_receipts_count = PaymentReceipt.objects.filter(society=society).count()
        total_reminders_count = ReminderLog.objects.filter(society=society).count()

        self.stdout.write(self.style.SUCCESS("Deepsagar sample data loaded."))
        self.stdout.write(
            "\n".join(
                [
                    f"Society: {society.name} (id={society.id})",
                    f"Financial year: {fy.name} ({fy.start_date} to {fy.end_date})",
                    f"Periods seeded: {self._format_periods(periods_to_seed)}",
                    f"Structures: {Structure.objects.filter(society=society).count()}",
                    f"Units seeded for owners: {len(owner_units)}",
                    (
                        f"Owner members seeded: {len(owner_members)} "
                        f"(total owners: {owners_total})"
                    ),
                    (
                        f"Tenant members seeded: {len(tenant_members)} "
                        f"(total tenants: {tenants_total})"
                    ),
                    (
                        "Ownership records created: "
                        f"{ownership_stats['ownership_created']}, "
                        f"closed: {ownership_stats['ownership_closed']}"
                    ),
                    (
                        "Occupancy records created: "
                        f"{ownership_stats['occupancy_created']}, "
                        f"closed: {ownership_stats['occupancy_closed']}"
                    ),
                    f"Charge templates: {charge_templates_count}",
                    f"Bills across seeded periods: {bills_in_seed_periods}",
                    f"New bills generated now: {generated_bills_count}",
                    f"Total bills: {bills_total}",
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

    def _get_or_create_user(self, *, email, full_name):
        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(
            email=email,
            defaults={
                "name": full_name,
                "is_active": True,
            },
        )
        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])
            return user

        update_fields = []
        if user.name != full_name:
            user.name = full_name
            update_fields.append("name")
        if not user.is_active:
            user.is_active = True
            update_fields.append("is_active")
        if update_fields:
            user.save(update_fields=update_fields)
        return user

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

    def _building_names(self, *, required_units):
        per_building_capacity = FLOORS_PER_BUILDING * UNITS_PER_FLOOR
        building_count = (
            required_units + per_building_capacity - 1
        ) // per_building_capacity
        names = []
        for index in range(building_count):
            letter = chr(ord("A") + (index % 26))
            suffix = index // 26
            names.append(letter if suffix == 0 else f"{letter}{suffix}")
        return names

    def _sample_area_for_index(self, *, index):
        return Decimal("850.00") + Decimal(index % 25) * Decimal("12.00")

    def _ensure_structure_and_units(self, *, society, required_units):  # noqa: C901
        building_names = self._building_names(required_units=required_units)
        units = []
        generated_index = 0
        for display_order, building_name in enumerate(building_names, start=1):
            building, _ = Structure.objects.get_or_create(
                society=society,
                parent=None,
                structure_type=Structure.StructureType.BUILDING,
                name=building_name,
                defaults={"display_order": display_order},
            )
            if building.display_order != display_order:
                building.display_order = display_order
                building.save(update_fields=["display_order"])

            for floor in range(1, FLOORS_PER_BUILDING + 1):
                for sequence in range(1, UNITS_PER_FLOOR + 1):
                    generated_index += 1
                    area = self._sample_area_for_index(index=generated_index)
                    chargeable_area = area - Decimal("10.00")
                    unit, _ = Unit.objects.get_or_create(
                        structure=building,
                        identifier=f"{floor}{sequence:02d}",
                        defaults={
                            "unit_type": Unit.UnitType.FLAT,
                            "area_sqft": area,
                            "chargeable_area_sqft": chargeable_area,
                            "is_active": True,
                        },
                    )
                    update_fields = []
                    if unit.unit_type != Unit.UnitType.FLAT:
                        unit.unit_type = Unit.UnitType.FLAT
                        update_fields.append("unit_type")
                    if unit.area_sqft != area:
                        unit.area_sqft = area
                        update_fields.append("area_sqft")
                    if unit.chargeable_area_sqft != chargeable_area:
                        unit.chargeable_area_sqft = chargeable_area
                        update_fields.append("chargeable_area_sqft")
                    if not unit.is_active:
                        unit.is_active = True
                        update_fields.append("is_active")
                    if update_fields:
                        unit.save(update_fields=update_fields)
                    units.append(unit)
                    if len(units) >= required_units:
                        return units
        return units

    def _upsert_member(  # noqa: PLR0913
        self,
        *,
        society,
        unit,
        full_name,
        email,
        phone,
        role,
        user,
        receivable_account,
        start_date,
    ):
        member, _ = Member.objects.get_or_create(
            society=society,
            unit=unit,
            full_name=full_name,
            role=role,
            defaults={
                "user": user,
                "email": email,
                "phone": phone,
                "status": Member.MemberStatus.ACTIVE,
                "start_date": start_date,
                "receivable_account": receivable_account,
            },
        )
        update_fields = []
        if member.user_id != user.id:
            member.user = user
            update_fields.append("user")
        if member.email != email:
            member.email = email
            update_fields.append("email")
        if member.phone != phone:
            member.phone = phone
            update_fields.append("phone")
        if member.status != Member.MemberStatus.ACTIVE:
            member.status = Member.MemberStatus.ACTIVE
            update_fields.append("status")
        if member.start_date != start_date:
            member.start_date = start_date
            update_fields.append("start_date")
        if member.end_date is not None:
            member.end_date = None
            update_fields.append("end_date")
        if member.receivable_account_id != receivable_account.id:
            member.receivable_account = receivable_account
            update_fields.append("receivable_account")
        if update_fields:
            member.save(update_fields=update_fields)
        return member

    def _ensure_owner_members(self, *, society, units, receivable_account, start_date):
        members = []
        owner_users_by_unit = {}
        for index, unit in enumerate(units, start=1):
            full_name = f"Owner {index:03d}"
            email = f"deepsagar.owner{index:03d}@example.com"
            phone = f"98{index:08d}"
            user = self._get_or_create_user(email=email, full_name=full_name)
            member = self._upsert_member(
                society=society,
                unit=unit,
                full_name=full_name,
                email=email,
                phone=phone,
                role=Member.MemberRole.OWNER,
                user=user,
                receivable_account=receivable_account,
                start_date=start_date,
            )
            members.append(member)
            owner_users_by_unit[unit.id] = user
        return members, owner_users_by_unit

    def _ensure_tenant_members(self, *, society, units, receivable_account, start_date):
        members = []
        tenant_users_by_unit = {}
        for index, unit in enumerate(units, start=1):
            full_name = f"Tenant {index:03d}"
            email = f"deepsagar.tenant{index:03d}@example.com"
            phone = f"97{index:08d}"
            user = self._get_or_create_user(email=email, full_name=full_name)
            member = self._upsert_member(
                society=society,
                unit=unit,
                full_name=full_name,
                email=email,
                phone=phone,
                role=Member.MemberRole.TENANT,
                user=user,
                receivable_account=receivable_account,
                start_date=start_date,
            )
            members.append(member)
            tenant_users_by_unit[unit.id] = user
        return members, tenant_users_by_unit

    def _ensure_ownership_and_occupancy(
        self,
        *,
        units,
        owner_users_by_unit,
        tenant_users_by_unit,
        owner_start_date,
        tenant_start_date,
    ):
        ownership_created = 0
        ownership_closed = 0
        occupancy_created = 0
        occupancy_closed = 0

        for unit in units:
            owner_user = owner_users_by_unit[unit.id]
            active_primary = list(
                UnitOwnership.objects.filter(
                    unit=unit,
                    role=UnitOwnership.OwnershipRole.PRIMARY,
                    end_date__isnull=True,
                ).order_by("id"),
            )
            desired_primary = next(
                (item for item in active_primary if item.owner_id == owner_user.id),
                None,
            )
            if desired_primary and len(active_primary) == 1:
                if desired_primary.start_date != owner_start_date:
                    desired_primary.start_date = owner_start_date
                    desired_primary.save(update_fields=["start_date"])
            else:
                for record in active_primary:
                    if record.end_date is None:
                        record.end_date = owner_start_date - timedelta(days=1)
                        record.save(update_fields=["end_date"])
                        ownership_closed += 1
                UnitOwnership.objects.create(
                    unit=unit,
                    owner=owner_user,
                    role=UnitOwnership.OwnershipRole.PRIMARY,
                    start_date=owner_start_date,
                )
                ownership_created += 1

            tenant_user = tenant_users_by_unit.get(unit.id)
            desired_occupancy_type = (
                UnitOccupancy.OccupancyType.TENANT
                if tenant_user
                else UnitOccupancy.OccupancyType.OWNER
            )
            desired_occupant = tenant_user or owner_user
            desired_start_date = tenant_start_date if tenant_user else owner_start_date

            active_occupancies = list(
                UnitOccupancy.objects.filter(
                    unit=unit,
                    end_date__isnull=True,
                ).order_by("id"),
            )
            desired_active = next(
                (
                    item
                    for item in active_occupancies
                    if item.occupancy_type == desired_occupancy_type
                    and item.occupant_id == desired_occupant.id
                ),
                None,
            )
            if desired_active and len(active_occupancies) == 1:
                if desired_active.start_date != desired_start_date:
                    desired_active.start_date = desired_start_date
                    desired_active.save(update_fields=["start_date"])
                continue

            for occupancy in active_occupancies:
                if occupancy.end_date is None:
                    occupancy.end_date = desired_start_date - timedelta(days=1)
                    occupancy.save(update_fields=["end_date"])
                    occupancy_closed += 1

            UnitOccupancy.objects.create(
                unit=unit,
                occupant=desired_occupant,
                occupancy_type=desired_occupancy_type,
                start_date=desired_start_date,
            )
            occupancy_created += 1

        return {
            "ownership_created": ownership_created,
            "ownership_closed": ownership_closed,
            "occupancy_created": occupancy_created,
            "occupancy_closed": occupancy_closed,
        }

    def _periods_to_seed(self, *, society, financial_year, open_period):
        previous_period = (
            AccountingPeriod.objects.filter(
                society=society,
                financial_year=financial_year,
                end_date__lt=open_period.start_date,
            )
            .order_by("-start_date")
            .first()
        )
        periods = []
        if previous_period:
            periods.append(previous_period)
        periods.append(open_period)
        return periods

    def _bill_date_for_period(self, *, period_start, period_end, today):
        bill_date = period_start + timedelta(days=4)
        bill_date = min(bill_date, today)
        return max(bill_date, period_start)

    def _format_periods(self, periods):
        return ", ".join(
            f"{period.start_date} to {period.end_date}" for period in periods
        )

    def _ensure_charge_templates(
        self,
        *,
        society,
        income_account,
        sinking_account,
        receivable_account,
        seed_effective_from,
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
            active_template = (
                ChargeTemplate.objects.filter(
                    society=society,
                    name=item["name"],
                    effective_to__isnull=True,
                )
                .order_by("-effective_from", "-version_no")
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
            if (
                active_template
                and not active_template.bill_lines.exists()
                and active_template.effective_from == today
            ):
                update_fields = []
                for key, value in expected.items():
                    if getattr(active_template, key) != value:
                        setattr(active_template, key, value)
                        update_fields.append(key)
                if update_fields:
                    active_template.save(update_fields=update_fields)
            elif active_template is None:
                latest_version = (
                    ChargeTemplate.objects.filter(
                        society=society,
                        name=item["name"],
                    )
                    .order_by("-version_no")
                    .first()
                )
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
                    previous_version=latest_version,
                )
            self._ensure_template_period_coverage(
                society=society,
                template_name=item["name"],
                seed_effective_from=seed_effective_from,
                expected=expected,
            )

    def _ensure_template_period_coverage(
        self,
        *,
        society,
        template_name,
        seed_effective_from,
        expected,
    ):
        covers_seed_period = ChargeTemplate.objects.filter(
            society=society,
            name=template_name,
            effective_from__lte=seed_effective_from,
        ).filter(
            Q(effective_to__isnull=True) | Q(effective_to__gte=seed_effective_from),
        ).exists()
        if covers_seed_period:
            return

        next_template = (
            ChargeTemplate.objects.filter(
                society=society,
                name=template_name,
                effective_from__gt=seed_effective_from,
            )
            .order_by("effective_from", "version_no")
            .first()
        )
        effective_to = (
            next_template.effective_from - timedelta(days=1)
            if next_template
            else None
        )
        ChargeTemplate.objects.create(
            society=society,
            name=template_name,
            description=expected["description"],
            charge_type=expected["charge_type"],
            rate=expected["rate"],
            frequency=expected["frequency"],
            due_days=expected["due_days"],
            late_fee_percent=expected["late_fee_percent"],
            income_account=expected["income_account"],
            receivable_account=expected["receivable_account"],
            is_active=effective_to is None,
            effective_from=seed_effective_from,
            effective_to=effective_to,
        )

    def _create_sample_receipts(
        self,
        *,
        society,
        bills,
        bank_account,
        receipt_date,
        mode,
    ):
        created = 0
        for index, bill in enumerate(bills):
            if bill.outstanding_amount <= 0:
                continue
            if bill.receipt_allocations.exists():
                continue

            if mode == "historical":
                cycle = index % HISTORICAL_RECEIPT_CYCLE
                if cycle < HISTORICAL_FULLY_PAID_MAX:
                    amount = bill.outstanding_amount
                    payment_mode = "CASH"
                    reference = ""
                elif cycle < HISTORICAL_PARTIAL_MAX:
                    amount = (bill.total_amount * Decimal("0.45")).quantize(
                        DECIMAL_CENTS,
                    )
                    payment_mode = "BANK_TRANSFER"
                    reference = f"DEEP-H-{bill.bill_number}"
                else:
                    continue
            else:
                cycle = index % CURRENT_RECEIPT_CYCLE
                if cycle in {0, 1, 2}:
                    amount = (bill.total_amount * Decimal("0.60")).quantize(
                        DECIMAL_CENTS,
                    )
                    payment_mode = "UPI"
                    reference = f"DEEP-C-{bill.bill_number}"
                elif cycle == CURRENT_FULL_RECEIPT_SLOT:
                    amount = bill.outstanding_amount
                    payment_mode = "CASH"
                    reference = ""
                else:
                    continue

            if amount <= 0:
                continue
            amount = min(amount, bill.outstanding_amount)

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
