from datetime import date
from datetime import timedelta
from decimal import Decimal
import hashlib
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounting.models import Account
from accounting.models import AccountCategory
from accounting.models import AccountingPeriod
from accounting.models import FinancialYear
from accounting.models import LedgerEntry
from accounting.models import Voucher
from accounting.services.gst_vouchers import AccountCodes
from accounting.services.gst_vouchers import create_vendor_payment
from accounting.services.standard_accounts import create_default_accounts_for_society
from accounting.services.standard_accounts import ensure_standard_categories
from auditlog.models import AuditLog
from billing.models import Bill
from billing.models import ChargeTemplate
from billing.services import apply_late_fees
from billing.services import generate_bills_for_period
from members.models import Member
from members.models import Nominee
from members.models import Structure
from members.models import Unit
from members.models import UnitOccupancy
from members.models import UnitOwnership
from notifications.models import ReminderLog
from notifications.models import SocietyEmailSettings
from notifications.services import ensure_default_email_templates
from notifications.services import schedule_payment_reminders
from parking.models import ParkingPermit
from parking.models import ParkingSlot
from parking.models import Vehicle
from receipts.models import PaymentReceipt
from receipts.models import ReceiptAllocation
from receipts.services import post_receipt_for_bill
from reconciliation.models import BankStatementImport
from reconciliation.models import BankTransaction
from reconciliation.services.matcher import MatchingEngine
from reconciliation.services.normalizer import NormalizerService
from shares.models import EventLog
from shares.models import ShareCertificate
from shares.models import ShareLedger
from shares.services import ShareTransactionService
from societies.models import Membership
from societies.models import Society
from societies.models import SocietyConfig

FINANCIAL_YEAR_START_MONTH = 4
DEFAULT_OWNER_COUNT = 40
DEFAULT_TENANT_COUNT = 18
SEED_TAG = "[MANGALMURTI-SEED]"
DECIMAL_CENTS = Decimal("0.01")
HISTORICAL_RECEIPT_CYCLE = 10
HISTORICAL_FULLY_PAID_MAX = 4
HISTORICAL_PARTIAL_MAX = 7
CURRENT_RECEIPT_CYCLE = 6
CURRENT_FULL_RECEIPT_SLOT = 3
SHARE_ALLOTMENT_COUNT = 10
SHARE_TRANSFER_COUNT = 5
SUCCESSION_OLD_NAME_INDEX = 90

LAYOUT = [
    (
        "Building A",
        [
            ("Wing A1", "A1", 4, 6),
            ("Wing A2", "A2", 4, 6),
        ],
    ),
    (
        "Building B",
        [
            ("Wing B1", "B1", 4, 5),
        ],
    ),
]

FIRST_NAMES = [
    "Rahul", "Sneha", "Amit", "Priya", "Vikram", "Anjali", "Sagar", "Meera",
    "Nikhil", "Pooja", "Rohan", "Kavita", "Sandeep", "Neha", "Arjun", "Shweta",
    "Manish", "Ritu", "Prasad", "Swati", "Ganesh", "Vaishali", "Suresh",
    "Archana", "Deepak", "Madhuri", "Kiran", "Varsha", "Sanjay", "Rupali",
    "Yogesh", "Pallavi", "Nitin", "Aarti", "Rajesh", "Smita", "Mahesh",
    "Reshma", "Abhijit", "Manisha", "Prashant", "Tejaswini", "Sachin",
    "Sonali", "Aniket", "Ashwini", "Girish", "Bhavana", "Vishal", "Shubhangi",
    "Harshal", "Snehal", "Mangesh", "Kalpana", "Sunil", "Ujwala", "Dinesh",
    "Vaishnavi", "Pravin", "Mrunal",
]

LAST_NAMES = [
    "Deshmukh", "Kulkarni", "Joshi", "Patil", "Shinde", "Jadhav", "Gaikwad",
    "More", "Pawar", "Chavan", "Kadam", "Bhosale", "Sawant", "Mane", "Kale",
    "Naik", "Salunkhe", "Thorat", "Chougule", "Pathak", "Bhandari", "Apte",
    "Kelkar", "Gokhale", "Phadnis", "Bapat", "Ranade", "Marathe", "Limaye",
    "Thatte",
]

VEHICLE_SERIES = ["AB", "CD", "EF", "GH", "JK", "LM", "NP", "QR", "ST", "UV"]
VEHICLE_COLORS = [
    "Pearl White", "Midnight Black", "Silver", "Metallic Grey", "Maroon",
    "Deep Blue", "Bronze", "Red", "Graphite", "Champagne Gold",
]
RELATIONSHIPS = ["Spouse", "Son", "Daughter", "Father", "Mother"]


class Command(BaseCommand):
    help = "Create or refresh realistic sample data for the Mangalmurti demo society."

    def add_arguments(self, parser):
        parser.add_argument(
            "--society",
            default="Mangalmurti",
            help="Society name (case-insensitive). Default: Mangalmurti",
        )
        parser.add_argument(
            "--owners",
            type=int,
            default=DEFAULT_OWNER_COUNT,
            help=f"Number of owner units/members to seed. Default: {DEFAULT_OWNER_COUNT}",
        )
        parser.add_argument(
            "--tenants",
            type=int,
            default=DEFAULT_TENANT_COUNT,
            help=f"Number of tenant members to seed. Default: {DEFAULT_TENANT_COUNT}",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Hard-delete the existing Mangalmurti society and rebuild from scratch.",
        )

    def handle(self, *args, **options):  # noqa: PLR0915
        society_name = (options["society"] or "").strip() or "Mangalmurti"
        owners_requested = int(options["owners"])
        tenants_requested = int(options["tenants"])
        reset = bool(options["reset"])

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

        with transaction.atomic():
            if reset:
                existing = Society.objects.filter(name__iexact=society_name).first()
                if existing is not None:
                    self._reset_society(existing)
                    self.stdout.write(f"Reset existing society: {existing.name}")

            society = Society.objects.filter(name__iexact=society_name).first()
            if society is None:
                society = Society.objects.create(name=society_name)
                self.stdout.write(f"Created society: {society.name}")
            else:
                self.stdout.write(f"Using existing society: {society.name}")
            self._apply_society_profile(society)

            ensure_standard_categories(society)
            create_default_accounts_for_society(society)

            admin_user = self._ensure_staff_user(
                email="admin@mangalmurti.in",
                full_name="Mangalmurti Admin",
                is_superuser=True,
            )
            accountant_user = self._ensure_staff_user(
                email="accounts@mangalmurti.in",
                full_name="Mangalmurti Accountant",
                is_superuser=False,
            )
            self._ensure_membership(
                society=society, user=admin_user, role=Membership.Role.ADMIN
            )
            self._ensure_membership(
                society=society, user=accountant_user, role=Membership.Role.ACCOUNTANT
            )

            config = self._apply_society_config(society)

            receivable = Account.objects.get(
                society=society, code=AccountCodes.MAINTENANCE_DUE
            )
            main_bank = Account.objects.get(
                society=society, code=AccountCodes.BANK_MAINTENANCE
            )

            fy = self._ensure_open_financial_year(society=society, today=today)
            period = self._ensure_open_period(
                society=society, financial_year=fy, today=today
            )
            periods_to_seed = self._periods_to_seed(
                society=society, financial_year=fy, open_period=period
            )

            units = self._ensure_structures_and_units(society=society)
            if len(units) < owners_requested:
                msg = (
                    f"Only {len(units)} units are available, "
                    f"but {owners_requested} owners were requested."
                )
                raise CommandError(msg)

            owner_units = units[:owners_requested]
            tenant_units = owner_units[:tenants_requested]
            succession_index, vacancy_index = self._special_unit_indexes(
                owners=owners_requested, tenants=tenants_requested
            )

            owner_members, owner_members_by_unit, owner_users_by_unit = (
                self._ensure_owner_members(
                    society=society,
                    owner_units=owner_units,
                    receivable_account=receivable,
                    start_date=fy.start_date,
                    succession_index=succession_index,
                    name_offset=0,
                )
            )
            tenant_members, tenant_users_by_unit = self._ensure_tenant_members(
                society=society,
                tenant_units=tenant_units,
                receivable_account=receivable,
                start_date=fy.start_date,
                name_offset=owners_requested,
            )

            ownership_stats = self._rebuild_ownership_and_occupancy(
                society=society,
                owner_units=owner_units,
                owner_users_by_unit=owner_users_by_unit,
                tenant_users_by_unit=tenant_users_by_unit,
                base_start_date=fy.start_date,
                succession_index=succession_index,
                vacancy_index=vacancy_index,
            )

            nominees_created = self._ensure_nominees(owner_members)

            share_stats = self._seed_shares(
                society=society,
                owner_members=owner_members,
                created_by=admin_user,
                transaction_date=fy.start_date,
            )

            self._ensure_charge_templates(
                society=society,
                receivable_account=receivable,
                seed_effective_from=periods_to_seed[0].start_date,
            )

            bills_in_seed_periods = 0
            generated_bills_count = 0
            receipts_created = 0
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
                    .order_by("id")
                )
                bills_in_seed_periods += len(period_bills)
                receipt_mode = "historical" if index == 0 else "current"
                receipts_created += self._create_sample_receipts(
                    society=society,
                    bills=period_bills,
                    bank_account=main_bank,
                    receipt_date=min(today, target_period.end_date),
                    mode=receipt_mode,
                )

            vouchers_created = self._ensure_accounting_vouchers(
                society=society,
                financial_year=fy,
                today=today,
                unit=owner_units[0] if owner_units else None,
            )

            reconciliation_stats = self._ensure_reconciliation_demo(
                society=society,
                bank_account=main_bank,
                today=today,
                uploaded_by=admin_user,
            )

            late_fees_updated = apply_late_fees(society=society, as_of_date=today)
            reminders_scheduled = schedule_payment_reminders(
                society=society,
                as_of_date=today,
                channels=[ReminderLog.Channel.EMAIL, ReminderLog.Channel.SMS],
                upcoming_days=30,
            )

            templates_created = len(ensure_default_email_templates())
            email_settings_created = self._ensure_email_settings(society)

            call_command(
                "seed_voucher_templates", society=society.name, stdout=StringIO()
            )

            parking_stats = self._ensure_parking(
                society=society,
                owner_units=owner_units,
                owner_members_by_unit=owner_members_by_unit,
                created_by=admin_user,
            )

            audit_created = self._ensure_audit_logs(society=society, actor=admin_user)

            self._print_summary(
                society=society,
                fy=fy,
                periods=periods_to_seed,
                owner_members=owner_members,
                tenant_members=tenant_members,
                ownership_stats=ownership_stats,
                nominees_created=nominees_created,
                share_stats=share_stats,
                bills_in_seed_periods=bills_in_seed_periods,
                generated_bills_count=generated_bills_count,
                receipts_created=receipts_created,
                vouchers_created=vouchers_created,
                reconciliation_stats=reconciliation_stats,
                late_fees_updated=late_fees_updated,
                reminders_scheduled=reminders_scheduled,
                templates_created=templates_created,
                email_settings_created=email_settings_created,
                parking_stats=parking_stats,
                audit_created=audit_created,
                config=config,
            )

    def _apply_society_profile(self, society):
        society.registration_number = "MH/PUN/HSG/2011/0784"
        society.address = "Mangalmurti Society, Survey No. 128, Baner Road, Pune 411045"
        society.save(update_fields=["registration_number", "address"])

    def _apply_society_config(self, society):
        config, _ = SocietyConfig.objects.get_or_create(society=society)
        config.share_value = Decimal("100.00")
        config.default_share_count = 1
        config.entrance_fee = Decimal("5000.00")
        config.transfer_fee = Decimal("2500.00")
        config.premium_amount = Decimal("0.00")
        config.allow_multiple_nominees = True
        config.require_approval = False
        config.auto_generate_vouchers = True
        config.save()
        return config

    def _ensure_staff_user(self, *, email, full_name, is_superuser):
        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(
            email=email,
            defaults={
                "name": full_name,
                "is_active": True,
                "is_staff": True,
                "is_superuser": is_superuser,
            },
        )
        update_fields = []
        for field, value in (
            ("name", full_name),
            ("is_active", True),
            ("is_staff", True),
            ("is_superuser", is_superuser),
        ):
            if getattr(user, field) != value:
                setattr(user, field, value)
                update_fields.append(field)
        if update_fields:
            user.save(update_fields=update_fields)
        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])
        return user

    def _ensure_membership(self, *, society, user, role):
        Membership.objects.get_or_create(
            user=user,
            society=society,
            defaults={"role": role, "is_active": True},
        )

    def _ensure_user(self, *, email, full_name):
        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(
            email=email,
            defaults={"name": full_name, "is_active": True},
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

    def _person_name(self, index):
        first = FIRST_NAMES[index % len(FIRST_NAMES)]
        last = LAST_NAMES[(index * 11 + 5) % len(LAST_NAMES)]
        return f"{first} {last}"

    def _email_for(self, full_name, serial):
        slug = full_name.lower().replace(" ", ".")
        base = f"{slug}@mangalmurti.in"
        user_model = get_user_model()
        if not user_model.objects.filter(email=base).exists():
            return base
        return f"{slug}.{serial}@mangalmurti.in"

    def _phone_for(self, serial):
        return f"98{(10000000 + serial * 137) % 100000000:08d}"

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
            year=start_year, month=FINANCIAL_YEAR_START_MONTH, day=1
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
                    society=society, financial_year=financial_year
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

    def _ensure_structures_and_units(self, *, society):
        units = []
        building_order = 0
        wing_order = 0
        for building_name, wings in LAYOUT:
            building_order += 1
            building = self._upsert_structure(
                society=society,
                parent=None,
                structure_type=Structure.StructureType.BUILDING,
                name=building_name,
                display_order=building_order,
            )
            for wing_name, wing_code, floors, units_per_floor in wings:
                wing_order += 1
                wing = self._upsert_structure(
                    society=society,
                    parent=building,
                    structure_type=Structure.StructureType.WING,
                    name=wing_name,
                    display_order=wing_order,
                )
                for floor in range(1, floors + 1):
                    floor_node = self._upsert_structure(
                        society=society,
                        parent=wing,
                        structure_type=Structure.StructureType.FLOOR,
                        name=f"Floor {floor}",
                        display_order=floor,
                    )
                    for sequence in range(1, units_per_floor + 1):
                        identifier = f"{wing_code}-{floor}{sequence:02d}"
                        unit_type = self._unit_type_for(wing_code, floor, sequence)
                        area = self._area_for(unit_type, len(units))
                        units.append(
                            self._upsert_unit(
                                floor_node, identifier, unit_type, area
                            )
                        )
        return units

    def _unit_type_for(self, wing_code, floor, sequence):
        if wing_code == "B1" and floor == 1 and sequence == 1:
            return Unit.UnitType.SHOP
        if wing_code == "B1" and floor == 1 and sequence == 2:
            return Unit.UnitType.OFFICE
        return Unit.UnitType.FLAT

    def _area_for(self, unit_type, index):
        if unit_type == Unit.UnitType.SHOP:
            return Decimal("320.00") + Decimal(index % 4) * Decimal("20.00")
        if unit_type == Unit.UnitType.OFFICE:
            return Decimal("480.00") + Decimal(index % 4) * Decimal("25.00")
        return Decimal("640.00") + Decimal(index % 9) * Decimal("65.00")

    def _upsert_structure(
        self, *, society, parent, structure_type, name, display_order
    ):
        structure, _ = Structure.objects.get_or_create(
            society=society,
            parent=parent,
            name=name,
            defaults={
                "structure_type": structure_type,
                "display_order": display_order,
            },
        )
        update_fields = []
        if structure.structure_type != structure_type:
            structure.structure_type = structure_type
            update_fields.append("structure_type")
        if structure.display_order != display_order:
            structure.display_order = display_order
            update_fields.append("display_order")
        if update_fields:
            structure.save(update_fields=update_fields)
        return structure

    def _upsert_unit(self, structure, identifier, unit_type, area):
        chargeable = (area - Decimal("10.00")).quantize(DECIMAL_CENTS)
        unit, _ = Unit.objects.get_or_create(
            structure=structure,
            identifier=identifier,
            defaults={
                "unit_type": unit_type,
                "area_sqft": area,
                "chargeable_area_sqft": chargeable,
                "is_active": True,
            },
        )
        update_fields = []
        if unit.unit_type != unit_type:
            unit.unit_type = unit_type
            update_fields.append("unit_type")
        if unit.area_sqft != area:
            unit.area_sqft = area
            update_fields.append("area_sqft")
        if unit.chargeable_area_sqft != chargeable:
            unit.chargeable_area_sqft = chargeable
            update_fields.append("chargeable_area_sqft")
        if not unit.is_active:
            unit.is_active = True
            update_fields.append("is_active")
        if update_fields:
            unit.save(update_fields=update_fields)
        return unit

    def _special_unit_indexes(self, *, owners, tenants):
        if owners >= tenants + 2:
            return owners - 1, owners - 2
        if owners > 1:
            return 0, 1
        return 0, 0

    def _upsert_member(
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
        end_date,
    ):
        status = (
            Member.MemberStatus.INACTIVE if end_date else Member.MemberStatus.ACTIVE
        )
        member, _ = Member.objects.get_or_create(
            society=society,
            unit=unit,
            full_name=full_name,
            role=role,
            defaults={
                "user": user,
                "email": email,
                "phone": phone,
                "status": status,
                "start_date": start_date,
                "end_date": end_date,
                "join_date": start_date,
                "exit_date": end_date,
                "receivable_account": receivable_account,
            },
        )
        member.user = user
        member.email = email
        member.phone = phone
        member.status = status
        member.start_date = start_date
        member.end_date = end_date
        member.join_date = start_date
        member.exit_date = end_date
        member.receivable_account = receivable_account
        member.save(
            update_fields=[
                "user",
                "email",
                "phone",
                "status",
                "start_date",
                "end_date",
                "join_date",
                "exit_date",
                "receivable_account",
            ]
        )
        return member

    def _ensure_owner_members(
        self,
        *,
        society,
        owner_units,
        receivable_account,
        start_date,
        succession_index,
        name_offset,
    ):
        members = []
        members_by_unit = {}
        users_by_unit = {}
        for index, unit in enumerate(owner_units):
            serial = name_offset + index + 1
            member_start = start_date

            if index == succession_index:
                old_name = self._person_name(SUCCESSION_OLD_NAME_INDEX)
                old_email = self._email_for(old_name, 900)
                old_user = self._ensure_user(email=old_email, full_name=old_name)
                self._upsert_member(
                    society=society,
                    unit=unit,
                    full_name=old_name,
                    email=old_email,
                    phone=self._phone_for(900),
                    role=Member.MemberRole.OWNER,
                    user=old_user,
                    receivable_account=receivable_account,
                    start_date=date(start_date.year - 2, 4, 1),
                    end_date=date(start_date.year - 1, 3, 31),
                )
                member_start = date(start_date.year - 1, 4, 1)

            full_name = self._person_name(name_offset + index)
            email = self._email_for(full_name, serial)
            user = self._ensure_user(email=email, full_name=full_name)
            member = self._upsert_member(
                society=society,
                unit=unit,
                full_name=full_name,
                email=email,
                phone=self._phone_for(serial),
                role=Member.MemberRole.OWNER,
                user=user,
                receivable_account=receivable_account,
                start_date=member_start,
                end_date=None,
            )
            members.append(member)
            members_by_unit[unit.id] = member
            users_by_unit[unit.id] = user
        return members, members_by_unit, users_by_unit

    def _ensure_tenant_members(
        self,
        *,
        society,
        tenant_units,
        receivable_account,
        start_date,
        name_offset,
    ):
        members = []
        tenant_users_by_unit = {}
        for index, unit in enumerate(tenant_units):
            serial = name_offset + index + 1
            full_name = self._person_name(name_offset + index)
            email = self._email_for(full_name, serial)
            user = self._ensure_user(email=email, full_name=full_name)
            member = self._upsert_member(
                society=society,
                unit=unit,
                full_name=full_name,
                email=email,
                phone=self._phone_for(serial + 200),
                role=Member.MemberRole.TENANT,
                user=user,
                receivable_account=receivable_account,
                start_date=start_date,
                end_date=None,
            )
            members.append(member)
            tenant_users_by_unit[unit.id] = user
        return members, tenant_users_by_unit

    def _rebuild_ownership_and_occupancy(
        self,
        *,
        society,
        owner_units,
        owner_users_by_unit,
        tenant_users_by_unit,
        base_start_date,
        succession_index,
        vacancy_index,
    ):
        unit_ids = [unit.id for unit in owner_units]
        UnitOwnership.objects.filter(unit_id__in=unit_ids).delete()
        UnitOccupancy.objects.filter(unit_id__in=unit_ids).delete()

        ownership_created = 0
        occupancy_created = 0
        succession_unit_id = owner_units[succession_index].id if owner_units else None
        vacancy_unit_id = owner_units[vacancy_index].id if owner_units else None

        for index, unit in enumerate(owner_units):
            current_owner_user = owner_users_by_unit[unit.id]

            if unit.id == succession_unit_id:
                old_name = self._person_name(SUCCESSION_OLD_NAME_INDEX)
                old_email = self._email_for(old_name, 900)
                old_user = self._ensure_user(email=old_email, full_name=old_name)
                UnitOwnership.objects.create(
                    unit=unit,
                    owner=old_user,
                    role=UnitOwnership.OwnershipRole.PRIMARY,
                    start_date=date(base_start_date.year - 2, 4, 1),
                    end_date=date(base_start_date.year - 1, 3, 31),
                )
                UnitOwnership.objects.create(
                    unit=unit,
                    owner=current_owner_user,
                    role=UnitOwnership.OwnershipRole.PRIMARY,
                    start_date=date(base_start_date.year - 1, 4, 1),
                )
                ownership_created += 2
                UnitOccupancy.objects.create(
                    unit=unit,
                    occupant=old_user,
                    occupancy_type=UnitOccupancy.OccupancyType.OWNER,
                    start_date=date(base_start_date.year - 2, 4, 1),
                    end_date=date(base_start_date.year - 1, 3, 31),
                )
                UnitOccupancy.objects.create(
                    unit=unit,
                    occupant=current_owner_user,
                    occupancy_type=UnitOccupancy.OccupancyType.OWNER,
                    start_date=date(base_start_date.year - 1, 4, 1),
                )
                occupancy_created += 2
                continue

            UnitOwnership.objects.create(
                unit=unit,
                owner=current_owner_user,
                role=UnitOwnership.OwnershipRole.PRIMARY,
                start_date=base_start_date,
            )
            ownership_created += 1

            tenant_user = tenant_users_by_unit.get(unit.id)
            if unit.id == vacancy_unit_id:
                UnitOccupancy.objects.create(
                    unit=unit,
                    occupant=None,
                    occupancy_type=UnitOccupancy.OccupancyType.VACANT,
                    start_date=base_start_date,
                )
            elif tenant_user is not None:
                UnitOccupancy.objects.create(
                    unit=unit,
                    occupant=tenant_user,
                    occupancy_type=UnitOccupancy.OccupancyType.TENANT,
                    start_date=base_start_date,
                )
            else:
                UnitOccupancy.objects.create(
                    unit=unit,
                    occupant=current_owner_user,
                    occupancy_type=UnitOccupancy.OccupancyType.OWNER,
                    start_date=base_start_date,
                )
            occupancy_created += 1

        return {
            "ownership_created": ownership_created,
            "occupancy_created": occupancy_created,
        }

    def _ensure_nominees(self, owner_members):
        created = 0
        for position, member in enumerate(owner_members[:2]):
            nominee_name = self._person_name(SUCCESSION_OLD_NAME_INDEX + 1 + position)
            _, was_created = Nominee.objects.get_or_create(
                member=member,
                name=nominee_name,
                priority_order=1,
                defaults={
                    "relationship": RELATIONSHIPS[position % len(RELATIONSHIPS)],
                    "percentage": Decimal("100.00"),
                    "is_active": True,
                },
            )
            if was_created:
                created += 1
        return created

    def _seed_shares(
        self, *, society, owner_members, created_by, transaction_date
    ):
        allotted = 0
        for member in owner_members:
            if ShareLedger.objects.filter(
                society=society,
                member=member,
                transaction_type=ShareLedger.TransactionType.ALLOTMENT,
            ).exists():
                continue
            ShareTransactionService.allot_shares_to_member(
                member=member,
                share_count=Decimal(str(SHARE_ALLOTMENT_COUNT)),
                transaction_date=transaction_date,
                reference=f"MG-ALLOT-{member.id}",
                created_by=created_by,
            )
            allotted += 1

        certificates = 0
        for member in owner_members:
            if ShareCertificate.objects.filter(member=member).exists():
                continue
            ShareTransactionService.generate_share_certificate(
                member=member,
                share_count=Decimal(str(SHARE_ALLOTMENT_COUNT)),
                transaction_date=transaction_date,
                certificate_no=f"MG-CERT-{member.id:05d}",
                issued_by=created_by,
            )
            certificates += 1

        transfers = 0
        if len(owner_members) >= 2:
            from_member = owner_members[0]
            to_member = owner_members[1]
            if not ShareLedger.objects.filter(
                society=society,
                member=from_member,
                transaction_type=ShareLedger.TransactionType.TRANSFER_OUT,
            ).exists():
                from_member.refresh_from_db()
                to_member.refresh_from_db()
                ShareTransactionService.transfer_shares(
                    from_member=from_member,
                    to_member=to_member,
                    share_count=Decimal(str(SHARE_TRANSFER_COUNT)),
                    transaction_date=transaction_date,
                    reference="MG-TRF-001",
                    created_by=created_by,
                )
                transfers += 1

        corrections = 0
        if owner_members:
            target = owner_members[min(2, len(owner_members) - 1)]
            manual_balance = Decimal(str(SHARE_ALLOTMENT_COUNT + 3))
            if not ShareLedger.objects.filter(
                society=society,
                member=target,
                reference_id="MG-COR-001",
            ).exists():
                ShareTransactionService.record_share_correction(
                    member=target,
                    new_balance=manual_balance,
                    transaction_date=transaction_date,
                    reference="MG-COR-001",
                    reason="Reconciliation with physical share register",
                    created_by=created_by,
                )
                corrections += 1

        return {
            "allotted": allotted,
            "certificates": certificates,
            "transfers": transfers,
            "corrections": corrections,
        }

    def _ensure_charge_templates(
        self, *, society, receivable_account, seed_effective_from
    ):
        templates = [
            {
                "name": "Maintenance",
                "rate": Decimal("2500.00"),
                "due_days": 10,
                "late_fee_percent": Decimal("10.00"),
                "income_code": AccountCodes.MAINTENANCE_CHARGES,
            },
            {
                "name": "Sinking Fund",
                "rate": Decimal("500.00"),
                "due_days": 15,
                "late_fee_percent": Decimal("0.00"),
                "income_code": AccountCodes.SINKING_FUND,
            },
            {
                "name": "Parking",
                "rate": Decimal("300.00"),
                "due_days": 15,
                "late_fee_percent": Decimal("0.00"),
                "income_code": AccountCodes.PARKING_CHARGES,
            },
        ]
        for item in templates:
            income_account = Account.objects.get(
                society=society, code=item["income_code"]
            )
            covers = (
                ChargeTemplate.objects.filter(
                    society=society,
                    name=item["name"],
                    effective_from__lte=seed_effective_from,
                )
                .filter(
                    Q(effective_to__isnull=True)
                    | Q(effective_to__gte=seed_effective_from)
                )
                .exists()
            )
            if covers:
                continue
            ChargeTemplate.objects.create(
                society=society,
                name=item["name"],
                description=f"Sample template: {item['name']}",
                charge_type=ChargeTemplate.ChargeType.FIXED,
                rate=item["rate"],
                frequency=ChargeTemplate.Frequency.MONTHLY,
                due_days=item["due_days"],
                late_fee_percent=item["late_fee_percent"],
                income_account=income_account,
                receivable_account=receivable_account,
                is_active=True,
                effective_from=seed_effective_from,
            )

    def _create_sample_receipts(
        self, *, society, bills, bank_account, receipt_date, mode
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
                        DECIMAL_CENTS
                    )
                    payment_mode = "BANK_TRANSFER"
                    reference = f"MG-H-{bill.bill_number}"
                else:
                    continue
            else:
                cycle = index % CURRENT_RECEIPT_CYCLE
                if cycle in {0, 1, 2}:
                    amount = (bill.total_amount * Decimal("0.60")).quantize(
                        DECIMAL_CENTS
                    )
                    payment_mode = "UPI"
                    reference = f"MG-C-{bill.bill_number}"
                elif cycle == CURRENT_FULL_RECEIPT_SLOT:
                    amount = bill.outstanding_amount
                    payment_mode = "CASH"
                    reference = ""
                else:
                    continue

            if amount <= 0:
                continue
            amount = min(amount, bill.outstanding_amount)
            if amount <= 0:
                continue

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

    def _seed_date(self, *, financial_year, today, days):
        candidate = financial_year.start_date + timedelta(days=days)
        candidate = min(candidate, today)
        return max(candidate, financial_year.start_date)

    def _ensure_voucher(
        self,
        *,
        society,
        code,
        voucher_type,
        voucher_date,
        narration,
        rows,
        payment_mode="",
        reference_number="",
    ):
        full_narration = f"{SEED_TAG} {code} {narration}"
        existing = (
            Voucher.objects.filter(
                society=society,
                voucher_type=voucher_type,
                voucher_date=voucher_date,
                narration=full_narration,
            )
            .order_by("-id")
            .first()
        )
        if existing and existing.posted_at:
            return existing, False
        if existing and not existing.posted_at:
            existing.delete()

        voucher = Voucher.objects.create(
            society=society,
            voucher_type=voucher_type,
            voucher_date=voucher_date,
            narration=full_narration,
            payment_mode=payment_mode,
            reference_number=reference_number,
        )
        for row in rows:
            LedgerEntry.objects.create(
                voucher=voucher,
                account=row["account"],
                unit=row.get("unit"),
                debit=row.get("debit", Decimal("0.00")),
                credit=row.get("credit", Decimal("0.00")),
            )
        voucher.post()
        return voucher, True

    def _ensure_accounting_vouchers(self, *, society, financial_year, today, unit):
        cash = Account.objects.get(society=society, code=AccountCodes.CASH_IN_HAND)
        bank = Account.objects.get(society=society, code=AccountCodes.BANK_MAINTENANCE)
        opening_equity = Account.objects.get(
            society=society, code=AccountCodes.OPENING_BALANCE_FUND
        )
        electricity = Account.objects.get(society=society, code="4.2.1")
        housekeeping = Account.objects.get(society=society, code="4.5.2")
        lift = Account.objects.get(society=society, code="4.3.3")
        bank_charges = Account.objects.get(society=society, code="4.6.1")
        maintenance_due = Account.objects.get(
            society=society, code=AccountCodes.MAINTENANCE_DUE
        )
        maintenance_income = Account.objects.get(
            society=society, code=AccountCodes.MAINTENANCE_CHARGES
        )
        input_cgst = Account.objects.get(society=society, code=AccountCodes.INPUT_CGST)
        input_sgst = Account.objects.get(society=society, code=AccountCodes.INPUT_SGST)
        output_cgst = Account.objects.get(society=society, code=AccountCodes.OUTPUT_CGST)
        output_sgst = Account.objects.get(society=society, code=AccountCodes.OUTPUT_SGST)
        vendor_payable = Account.objects.get(
            society=society, code=AccountCodes.VENDOR_PAYABLE
        )
        interest_income = Account.objects.get(
            society=society, code=AccountCodes.INTEREST_INCOME_BANK
        )

        def seed_date(days):
            return self._seed_date(
                financial_year=financial_year, today=today, days=days
            )

        specs = [
            {
                "code": "MG-OB-001",
                "voucher_type": Voucher.VoucherType.OPENING,
                "voucher_date": seed_date(0),
                "narration": "Opening balances for the society bank and cash",
                "rows": [
                    {"account": cash, "debit": Decimal("50000.00")},
                    {"account": bank, "debit": Decimal("300000.00")},
                    {"account": opening_equity, "credit": Decimal("350000.00")},
                ],
            },
            {
                "code": "MG-ELEC-001",
                "voucher_type": Voucher.VoucherType.BILL,
                "voucher_date": seed_date(30),
                "narration": "Electricity vendor bill booked with GST",
                "rows": [
                    {"account": electricity, "debit": Decimal("12000.00")},
                    {"account": input_cgst, "debit": Decimal("1080.00")},
                    {"account": input_sgst, "debit": Decimal("1080.00")},
                    {"account": vendor_payable, "credit": Decimal("14160.00")},
                ],
            },
            {
                "code": "MG-SAL-001",
                "voucher_type": Voucher.VoucherType.PAYMENT,
                "voucher_date": seed_date(45),
                "narration": "Housekeeping staff salary paid",
                "payment_mode": Voucher.PaymentMode.BANK_TRANSFER,
                "reference_number": "MG-SAL-001",
                "rows": [
                    {"account": housekeeping, "debit": Decimal("18000.00")},
                    {"account": bank, "credit": Decimal("18000.00")},
                ],
            },
            {
                "code": "MG-LIFT-001",
                "voucher_type": Voucher.VoucherType.BILL,
                "voucher_date": seed_date(60),
                "narration": "Lift maintenance vendor bill booked with GST",
                "rows": [
                    {"account": lift, "debit": Decimal("4500.00")},
                    {"account": input_cgst, "debit": Decimal("405.00")},
                    {"account": input_sgst, "debit": Decimal("405.00")},
                    {"account": vendor_payable, "credit": Decimal("5310.00")},
                ],
            },
            {
                "code": "MG-GST-001",
                "voucher_type": Voucher.VoucherType.BILL,
                "voucher_date": seed_date(75),
                "narration": "GST split maintenance billing for a member unit",
                "rows": [
                    {
                        "account": maintenance_due,
                        "unit": unit,
                        "debit": Decimal("11800.00"),
                    },
                    {"account": maintenance_income, "credit": Decimal("10000.00")},
                    {"account": output_cgst, "credit": Decimal("900.00")},
                    {"account": output_sgst, "credit": Decimal("900.00")},
                ],
            },
            {
                "code": "MG-INT-001",
                "voucher_type": Voucher.VoucherType.RECEIPT,
                "voucher_date": seed_date(90),
                "narration": "Bank interest credited by bank",
                "payment_mode": Voucher.PaymentMode.BANK_TRANSFER,
                "reference_number": "MG-INT-001",
                "rows": [
                    {"account": bank, "debit": Decimal("1200.00")},
                    {"account": interest_income, "credit": Decimal("1200.00")},
                ],
            },
            {
                "code": "MG-BCHG-001",
                "voucher_type": Voucher.VoucherType.PAYMENT,
                "voucher_date": seed_date(95),
                "narration": "Bank charges debited by bank",
                "payment_mode": Voucher.PaymentMode.BANK_TRANSFER,
                "reference_number": "MG-BCHG-001",
                "rows": [
                    {"account": bank_charges, "debit": Decimal("350.00")},
                    {"account": bank, "credit": Decimal("350.00")},
                ],
            },
        ]

        created = 0
        for spec in specs:
            _, was_created = self._ensure_voucher(
                society=society,
                code=spec["code"],
                voucher_type=spec["voucher_type"],
                voucher_date=spec["voucher_date"],
                narration=spec["narration"],
                rows=spec["rows"],
                payment_mode=spec.get("payment_mode", ""),
                reference_number=spec.get("reference_number", ""),
            )
            if was_created:
                created += 1

        if not Voucher.objects.filter(
            society=society, reference_number="MG-VND-001"
        ).exists():
            create_vendor_payment(
                society=society,
                voucher_date=seed_date(100),
                amount=Decimal("2000.00"),
                bank_account_code=AccountCodes.BANK_MAINTENANCE,
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number="MG-VND-001",
            )
            created += 1

        return created

    def _ensure_reconciliation_demo(
        self, *, society, bank_account, today, uploaded_by
    ):
        demo_file_name = "mangalmurti_reconciliation_demo.csv"
        demo_marker = "MG-DEMO"

        if BankStatementImport.objects.filter(
            society=society, file_name=demo_file_name
        ).exists():
            self.stdout.write("Reconciliation demo statement already exists; skipping.")
            return {"transactions_created": 0, "suggestions_created": 0}

        bills = [
            bill
            for bill in Bill.objects.filter(society=society)
            .select_related("member", "unit")
            .order_by("bill_date", "id")
            if bill.outstanding_amount > 0
        ][:2]
        if len(bills) < 2:
            self.stdout.write(
                "Not enough open bills for reconciliation demo; skipping."
            )
            return {"transactions_created": 0, "suggestions_created": 0}

        receipt_1_ref = f"{demo_marker}-REC-001"
        receipt_2_ref = f"{demo_marker}-REC-002"
        payment_ref = f"{demo_marker}-PAY-001"

        receipt_1 = self._get_or_create_demo_receipt(
            society=society,
            bill=bills[0],
            amount=min(bills[0].outstanding_amount, Decimal("7500.00")),
            receipt_date=min(today, bills[0].bill_date + timedelta(days=5)),
            bank_account=bank_account,
            reference_number=receipt_1_ref,
        )
        receipt_2 = self._get_or_create_demo_receipt(
            society=society,
            bill=bills[1],
            amount=min(bills[1].outstanding_amount, Decimal("6200.00")),
            receipt_date=min(today, bills[1].bill_date + timedelta(days=7)),
            bank_account=bank_account,
            reference_number=receipt_2_ref,
        )
        payment_voucher = self._get_or_create_demo_vendor_payment(
            society=society,
            amount=Decimal("4200.00"),
            voucher_date=max(today - timedelta(days=2), date(today.year, 4, 1)),
            reference_number=payment_ref,
            bank_account=bank_account,
        )

        demo_entries = [
            {
                "transaction_date": receipt_1.receipt_date,
                "narration": receipt_1.voucher.narration,
                "reference_no": receipt_1.reference_number,
                "amount": receipt_1.amount,
                "dr_cr": BankTransaction.DrCr.CREDIT,
                "balance": Decimal("0.00"),
            },
            {
                "transaction_date": receipt_2.receipt_date,
                "narration": receipt_2.voucher.narration,
                "reference_no": receipt_2.reference_number,
                "amount": receipt_2.amount,
                "dr_cr": BankTransaction.DrCr.CREDIT,
                "balance": Decimal("0.00"),
            },
            {
                "transaction_date": payment_voucher.voucher_date,
                "narration": payment_voucher.narration,
                "reference_no": payment_voucher.reference_number,
                "amount": Decimal("4200.00"),
                "dr_cr": BankTransaction.DrCr.DEBIT,
                "balance": Decimal("0.00"),
            },
        ]

        csv_lines = ["Date,Narration,Ref No,Debit,Credit,Balance"]
        running_balance = Decimal("250000.00")
        bank_transactions = []
        statement_hash_input = []
        for index, entry in enumerate(demo_entries, start=1):
            debit = (
                entry["amount"]
                if entry["dr_cr"] == BankTransaction.DrCr.DEBIT
                else Decimal("0.00")
            )
            credit = (
                entry["amount"]
                if entry["dr_cr"] == BankTransaction.DrCr.CREDIT
                else Decimal("0.00")
            )
            running_balance = running_balance - debit + credit
            balance = running_balance.quantize(Decimal("0.01"))
            entry["balance"] = balance
            csv_lines.append(
                ",".join(
                    [
                        entry["transaction_date"].isoformat(),
                        entry["narration"].replace(",", " "),
                        entry["reference_no"],
                        f"{debit:.2f}" if debit else "",
                        f"{credit:.2f}" if credit else "",
                        f"{balance:.2f}",
                    ]
                )
            )
            statement_hash_input.append(
                f"{entry['transaction_date']}|{entry['narration']}|"
                f"{entry['reference_no']}|{debit}|{credit}|{balance}"
            )

        file_content = "\n".join(csv_lines) + "\n"
        file_hash = hashlib.sha256(
            "\n".join(statement_hash_input).encode("utf-8")
        ).hexdigest()
        statement_import = BankStatementImport.objects.create(
            society=society,
            bank_account=bank_account,
            file_name=demo_file_name,
            file_hash=file_hash,
            raw_file=ContentFile(file_content.encode("utf-8"), name=demo_file_name),
            uploaded_by=uploaded_by,
            import_status=BankStatementImport.ImportStatus.COMPLETED,
            source_type="DEMO_RECONCILIATION",
            row_count=len(demo_entries),
            statement_start_date=demo_entries[0]["transaction_date"],
            statement_end_date=demo_entries[-1]["transaction_date"],
        )

        for index, entry in enumerate(demo_entries, start=1):
            bank_transaction = BankTransaction.objects.create(
                bank_statement_import=statement_import,
                source_row_index=index,
                transaction_date=entry["transaction_date"],
                narration=entry["narration"],
                reference_no=entry["reference_no"],
                cheque_no="",
                amount=entry["amount"],
                dr_cr=entry["dr_cr"],
                balance=entry["balance"],
                raw_row_data={
                    "Date": entry["transaction_date"].isoformat(),
                    "Narration": entry["narration"],
                    "Ref No": entry["reference_no"],
                    "Debit": f"{entry['amount']:.2f}"
                    if entry["dr_cr"] == BankTransaction.DrCr.DEBIT
                    else "",
                    "Credit": f"{entry['amount']:.2f}"
                    if entry["dr_cr"] == BankTransaction.DrCr.CREDIT
                    else "",
                    "Balance": f"{entry['balance']:.2f}",
                },
                duplicate_hash=BankTransaction.compute_duplicate_hash(
                    entry["transaction_date"],
                    entry["amount"],
                    entry["narration"],
                    entry["reference_no"],
                ),
                is_duplicate=False,
            )
            NormalizerService(society).normalize_transaction(bank_transaction)
            bank_transactions.append(bank_transaction)

        results = MatchingEngine(society).run_matching(
            bank_transactions=bank_transactions,
            auto_confirm=False,
            create_suggestions=True,
        )

        self.stdout.write(
            f"Created demo statement import {statement_import.file_name} "
            f"with {len(bank_transactions)} bank rows for reconciliation checking."
        )
        return {
            "transactions_created": len(bank_transactions),
            "suggestions_created": len(results.get("suggested", [])),
        }

    def _get_or_create_demo_receipt(
        self, *, society, bill, amount, receipt_date, bank_account, reference_number
    ):
        existing = (
            PaymentReceipt.objects.filter(
                society=society, reference_number=reference_number
            )
            .select_related("voucher")
            .first()
        )
        if existing and existing.voucher_id:
            return existing
        return post_receipt_for_bill(
            society=society,
            member=bill.member,
            bill=bill,
            amount=amount,
            receipt_date=receipt_date,
            payment_mode="BANK_TRANSFER",
            deposited_account=bank_account,
            reference_number=reference_number,
        )

    def _get_or_create_demo_vendor_payment(
        self, *, society, amount, voucher_date, reference_number, bank_account
    ):
        existing = (
            Voucher.objects.filter(
                society=society, reference_number=reference_number
            )
            .order_by("-id")
            .first()
        )
        if existing:
            return existing
        return create_vendor_payment(
            society=society,
            voucher_date=voucher_date,
            amount=amount,
            bank_account_code=bank_account.code,
            payment_mode="BANK_TRANSFER",
            reference_number=reference_number,
        )

    def _ensure_email_settings(self, society):
        _, created = SocietyEmailSettings.objects.get_or_create(
            society=society,
            defaults={
                "provider_type": "SMTP",
                "smtp_host": "smtp.mangalmurti.in",
                "smtp_port": 587,
                "smtp_username": "notifications@mangalmurti.in",
                "use_tls": True,
                "use_ssl": False,
                "default_from_email": "noreply@mangalmurti.in",
                "is_active": False,
            },
        )
        return 1 if created else 0

    def _ensure_parking(
        self, *, society, owner_units, owner_members_by_unit, created_by
    ):
        slots_created = 0
        vehicles_created = 0
        permits_created = 0

        slot_specs = [
            (f"B-{index:02d}", ParkingSlot.SlotType.BASEMENT) for index in range(1, 11)
        ] + [(f"P-{index:02d}", ParkingSlot.SlotType.OPEN) for index in range(1, 6)]
        sold_slot_numbers = {f"B-{i:02d}" for i in range(1, 6)} | {
            f"P-{i:02d}" for i in range(1, 6)
        }

        sold_units = list(owner_units[: len(sold_slot_numbers)])
        sold_index = 0
        for slot_number, slot_type in slot_specs:
            is_sold = slot_number in sold_slot_numbers
            owner_unit = sold_units[sold_index] if is_sold and sold_units else None
            if is_sold:
                sold_index += 1

            slot, slot_created = ParkingSlot.objects.get_or_create(
                society=society,
                slot_number=slot_number,
                defaults={
                    "parking_model": ParkingSlot.ParkingModel.SOLD
                    if is_sold
                    else ParkingSlot.ParkingModel.COMMON,
                    "slot_type": slot_type,
                    "owned_unit": owner_unit,
                    "is_active": True,
                },
            )
            if slot_created:
                slots_created += 1

            if not is_sold or owner_unit is None:
                continue

            vehicle_number = self._vehicle_number(sold_index - 1)
            vehicle, vehicle_created = Vehicle.objects.get_or_create(
                society=society,
                vehicle_number=vehicle_number,
                defaults={
                    "unit": owner_unit,
                    "member": owner_members_by_unit.get(owner_unit.id),
                    "vehicle_type": Vehicle.VehicleType.CAR,
                    "color": VEHICLE_COLORS[(sold_index - 1) % len(VEHICLE_COLORS)],
                    "is_active": True,
                },
            )
            if vehicle_created:
                vehicles_created += 1

            _, permit_created = ParkingPermit.objects.get_or_create(
                society=society,
                slot=slot,
                status=ParkingPermit.Status.ACTIVE,
                defaults={
                    "vehicle": vehicle,
                    "unit": owner_unit,
                    "permit_type": ParkingPermit.PermitType.SOLD,
                    "created_by": created_by,
                },
            )
            if permit_created:
                permits_created += 1

        return {
            "slots_created": slots_created,
            "vehicles_created": vehicles_created,
            "permits_created": permits_created,
        }

    def _vehicle_number(self, index):
        series = VEHICLE_SERIES[index % len(VEHICLE_SERIES)]
        return f"MH-12-{series}-{1000 + index:04d}"

    def _ensure_audit_logs(self, *, society, actor):
        created = 0
        entries = [
            (AuditLog.Action.CREATE, "member", "members"),
            (AuditLog.Action.CREATE, "unit", "units"),
            (AuditLog.Action.CREATE, "voucher", "accounting"),
            (AuditLog.Action.POST, "voucher", "accounting"),
            (AuditLog.Action.CREATE, "bill", "billing"),
            (AuditLog.Action.CREATE, "payment_receipt", "receipts"),
            (AuditLog.Action.CREATE, "share_ledger", "shares"),
            (AuditLog.Action.CREATE, "parking_permit", "parking"),
        ]
        for action, entity_type, module in entries:
            entity_id = f"MANGALMURTI-{entity_type}"
            if AuditLog.objects.filter(
                society=society,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
            ).exists():
                continue
            AuditLog.log(
                society=society,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                actor=actor,
                module=module,
                reason="Seeded demo data for Mangalmurti society",
            )
            created += 1
        return created

    def _reset_society(self, society):
        from accounting.models import AccountMapping
        from accounting.models import VoucherTemplate
        from accounting.models import VoucherTemplateRow
        from reconciliation.models import BankTransactionNormalized
        from reconciliation.models import ReconciliationLink

        ReconciliationLink.objects.filter(society=society).delete()
        BankTransactionNormalized.objects.filter(
            bank_transaction__bank_statement_import__society=society
        ).delete()
        BankTransaction.objects.filter(
            bank_statement_import__society=society
        ).delete()
        BankStatementImport.objects.filter(society=society).delete()
        ReminderLog.objects.filter(society=society).delete()
        ReceiptAllocation.objects.filter(receipt__society=society).delete()
        PaymentReceipt.objects.filter(society=society).delete()
        Bill.objects.filter(society=society).delete()
        AuditLog.objects.filter(society=society).delete()
        EventLog.objects.filter(society=society).delete()
        ShareCertificate.objects.filter(member__society=society).delete()
        ShareLedger.objects.filter(society=society).delete()
        Nominee.objects.filter(member__society=society).delete()
        ParkingPermit.objects.filter(society=society).delete()
        Vehicle.objects.filter(society=society).delete()
        ParkingSlot.objects.filter(society=society).delete()
        Member.objects.filter(society=society).delete()
        UnitOccupancy.objects.filter(unit__structure__society=society).delete()
        UnitOwnership.objects.filter(unit__structure__society=society).delete()
        LedgerEntry.objects.filter(voucher__society=society).delete()
        Voucher.objects.filter(society=society).delete()
        Unit.objects.filter(structure__society=society).delete()
        Structure.objects.filter(society=society).delete()
        ChargeTemplate.objects.filter(society=society).delete()
        AccountingPeriod.objects.filter(society=society).delete()
        FinancialYear.objects.filter(society=society).delete()
        VoucherTemplateRow.objects.filter(template__society=society).delete()
        VoucherTemplate.objects.filter(society=society).delete()
        AccountMapping.objects.filter(society=society).delete()
        Account.objects.filter(society=society).delete()
        AccountCategory.objects.filter(society=society).delete()
        Membership.objects.filter(society=society).delete()
        SocietyConfig.objects.filter(society=society).delete()
        society.delete()

    def _print_summary(self, **stats):  # noqa: PLR0913
        society = stats["society"]
        owner_members = stats["owner_members"]
        tenant_members = stats["tenant_members"]

        def count(model, **filters):
            return model.objects.filter(society=society, **filters).count()

        lines = [
            self.style.SUCCESS("Mangalmurti demo data loaded."),
            f"Society: {society.name} (id={society.id})",
            f"Registration: {society.registration_number}",
            f"Address: {society.address}",
            f"Share config: value={stats['config'].share_value}, "
            f"entrance_fee={stats['config'].entrance_fee}, "
            f"transfer_fee={stats['config'].transfer_fee}, "
            f"auto_generate_vouchers={stats['config'].auto_generate_vouchers}",
            f"Financial year: {stats['fy'].name} "
            f"({stats['fy'].start_date} to {stats['fy'].end_date})",
            f"Periods seeded: {self._format_periods(stats['periods'])}",
            f"Structures: {Structure.objects.filter(society=society).count()}",
            f"Units: {Unit.objects.filter(structure__society=society).count()}",
            f"Owner members: {len(owner_members)} "
            f"(total: {count(Member, role=Member.MemberRole.OWNER)})",
            f"Tenant members: {len(tenant_members)} "
            f"(total: {count(Member, role=Member.MemberRole.TENANT)})",
            f"Ownership records: {stats['ownership_stats']['ownership_created']}",
            f"Occupancy records: {stats['ownership_stats']['occupancy_created']}",
            f"Nominees created now: {stats['nominees_created']} "
            f"(total: {Nominee.objects.filter(member__society=society).count()})",
            f"Shares allotted now: {stats['share_stats']['allotted']} "
            f"(ledger rows: {count(ShareLedger)})",
            f"Share certificates created now: {stats['share_stats']['certificates']} "
            f"(total: {ShareCertificate.objects.filter(member__society=society).count()})",
            f"Share transfers now: {stats['share_stats']['transfers']}, "
            f"corrections now: {stats['share_stats']['corrections']}",
            f"Share events (EventLog): {count(EventLog)}",
            f"Charge templates: {count(ChargeTemplate)}",
            f"Bills seeded (two periods): {stats['bills_in_seed_periods']} "
            f"(new now: {stats['generated_bills_count']}, total: {count(Bill)})",
            f"Receipts created now: {stats['receipts_created']} "
            f"(total: {count(PaymentReceipt)})",
            f"Vouchers created now: {stats['vouchers_created']} "
            f"(total: {count(Voucher)})",
            f"Ledger entries: "
            f"{LedgerEntry.objects.filter(voucher__society=society).count()}",
            f"Accounts: {count(Account)}, "
            f"Account categories: {count(AccountCategory)}",
            f"Reconciliation rows created now: "
            f"{stats['reconciliation_stats']['transactions_created']}, "
            f"suggestions: {stats['reconciliation_stats']['suggestions_created']}",
            f"Late fees updated now: {stats['late_fees_updated']}",
            f"Reminders scheduled now: {stats['reminders_scheduled']} "
            f"(total: {count(ReminderLog)})",
            f"Email templates ensured: {stats['templates_created']}, "
            f"society email settings created: {stats['email_settings_created']}",
            f"Parking slots created now: {stats['parking_stats']['slots_created']} "
            f"(total: {count(ParkingSlot)})",
            f"Vehicles created now: {stats['parking_stats']['vehicles_created']} "
            f"(total: {count(Vehicle)})",
            f"Parking permits created now: {stats['parking_stats']['permits_created']} "
            f"(total: {count(ParkingPermit)})",
            f"Audit log rows created now: {stats['audit_created']} "
            f"(total: {count(AuditLog)})",
        ]
        self.stdout.write("\n".join(lines))

    def _format_periods(self, periods):
        return ", ".join(
            f"{period.start_date} to {period.end_date}" for period in periods
        )
