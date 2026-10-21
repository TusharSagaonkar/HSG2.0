"""Seed a complete, realistic demo society named *Jagruti*.

The command builds:
  * society profile, memberships and share configuration
  * the standard chart of accounts and account mapping
  * two financial years (previous year + current year) with open periods
  * buildings / wings / floors / units (flats, shops, offices)
  * owners, tenants, succession, vacancy, nominees and occupancy history
  * charge templates, monthly bills for both years and payment receipts
  * posted vouchers covering every voucher type (opening, bill, receipt,
    payment, journal, adjustment, general) plus a reversal and a draft
  * shares, certificates, parking slots, vehicles and permits
  * bank reconciliation demo rows, reminders, audit trail and voucher templates

It is safe to re-run: existing seed data is detected and skipped.  Pass
``--reset`` to hard-delete and rebuild the Jagruti society from scratch.
"""

from __future__ import annotations

from datetime import date
from datetime import timedelta
from decimal import Decimal
from decimal import ROUND_HALF_UP
import hashlib
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import transaction
from django.db.models import Q
from django.db.models import Sum
from django.utils import timezone

from accounting.models import Account
from accounting.models import AccountCategory
from accounting.models import AccountMapping
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
from billing.models import BillLine
from billing.models import ChargeTemplate
from billing.services import apply_late_fees
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
DEFAULT_OWNER_COUNT = 44
DEFAULT_TENANT_COUNT = 14
SEED_TAG = "[JAGRUTI-SEED]"
SEED_DOMAIN = "jagruti.in"
DECIMAL_CENTS = Decimal("0.01")
SHARE_ALLOTMENT_COUNT = 10
SHARE_TRANSFER_COUNT = 5

MONEY = Decimal("0.01")
SNAPSHOT = Decimal("0.0001")

LAYOUT = [
    (
        "Building A",
        [
            ("Wing A1", "A1", 4, 4),
            ("Wing A2", "A2", 4, 4),
        ],
    ),
    (
        "Building B",
        [
            ("Wing B1", "B1", 4, 3),
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
    "Vaishnavi", "Pravin", "Mrunal", "Sameer", "Ruchita", "Onkar", "Sayali",
]

LAST_NAMES = [
    "Deshmukh", "Kulkarni", "Joshi", "Patil", "Shinde", "Jadhav", "Gaikwad",
    "More", "Pawar", "Chavan", "Kadam", "Bhosale", "Sawant", "Mane", "Kale",
    "Naik", "Salunkhe", "Thorat", "Chougule", "Pathak", "Bhandari", "Apte",
    "Kelkar", "Gokhale", "Phadnis", "Bapat", "Ranade", "Marathe", "Limaye",
    "Thatte", "Bhat", "Kulkarni", "Vaidya", "Deshpande", "Godbole",
]

VEHICLE_SERIES = ["AB", "CD", "EF", "GH", "JK", "LM", "NP", "QR", "ST", "UV"]
VEHICLE_COLORS = [
    "Pearl White", "Midnight Black", "Silver", "Metallic Grey", "Maroon",
    "Deep Blue", "Bronze", "Red", "Graphite", "Champagne Gold",
]
RELATIONSHIPS = ["Spouse", "Son", "Daughter", "Father", "Mother"]


class Command(BaseCommand):
    help = "Create or refresh a realistic demo society named Jagruti."

    def add_arguments(self, parser):
        parser.add_argument(
            "--society",
            default="Jagruti",
            help="Society name (case-insensitive). Default: Jagruti",
        )
        parser.add_argument(
            "--owners",
            type=int,
            default=DEFAULT_OWNER_COUNT,
            help=f"Number of owner units/members. Default: {DEFAULT_OWNER_COUNT}",
        )
        parser.add_argument(
            "--tenants",
            type=int,
            default=DEFAULT_TENANT_COUNT,
            help=f"Number of tenant members. Default: {DEFAULT_TENANT_COUNT}",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Hard-delete the existing Jagruti society and rebuild from scratch.",
        )
        parser.add_argument(
            "--admin-password",
            default="",
            help="Optional password to set on the seeded admin user (for demo logins).",
        )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    def handle(self, *args, **options):  # noqa: PLR0915
        society_name = (options["society"] or "").strip() or "Jagruti"
        owners_requested = int(options["owners"])
        tenants_requested = int(options["tenants"])
        reset = bool(options["reset"])
        admin_password = (options.get("admin_password") or "").strip()

        if owners_requested <= 0:
            raise CommandError("Owners count must be greater than 0.")
        if tenants_requested < 0:
            raise CommandError("Tenants count cannot be negative.")
        if tenants_requested > owners_requested:
            raise CommandError("Tenants count cannot exceed owners count.")

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
            AccountMapping.ensure_for_society(society)

            admin_user = self._ensure_staff_user(
                email=f"admin@{SEED_DOMAIN}",
                full_name="Jagruti Society Admin",
                is_superuser=True,
            )
            accountant_user = self._ensure_staff_user(
                email=f"accounts@{SEED_DOMAIN}",
                full_name="Jagruti Society Accountant",
                is_superuser=False,
            )
            self._ensure_membership(
                society=society, user=admin_user, role=Membership.Role.ADMIN
            )
            self._ensure_membership(
                society=society, user=accountant_user, role=Membership.Role.ACCOUNTANT
            )
            if admin_password:
                admin_user.set_password(admin_password)
                admin_user.save(update_fields=["password"])
                self.stdout.write("Admin password updated.")

            config = self._apply_society_config(society)

            previous_fy, current_fy = self._ensure_financial_years(
                society=society, today=today
            )

            receivable = Account.objects.get(
                society=society, code=AccountCodes.MAINTENANCE_DUE
            )
            main_bank = Account.objects.get(
                society=society, code=AccountCodes.BANK_MAINTENANCE
            )

            units = self._ensure_structures_and_units(society=society)
            if len(units) < owners_requested:
                raise CommandError(
                    f"Only {len(units)} units are available, "
                    f"but {owners_requested} owners were requested."
                )
            owner_units = units[:owners_requested]
            tenant_units = owner_units[:tenants_requested]

            succession_index, vacancy_index, tenant_turnover_index = (
                self._special_unit_indexes(
                    owners=owners_requested, tenants=tenants_requested
                )
            )

            (
                owner_members,
                owner_members_by_unit,
                owner_users_by_unit,
            ) = self._ensure_owner_members(
                society=society,
                owner_units=owner_units,
                receivable_account=receivable,
                start_date=previous_fy.start_date,
                succession_index=succession_index,
                name_offset=0,
            )
            (
                tenant_members,
                tenant_users_by_unit,
                historical_tenant,
            ) = self._ensure_tenant_members(
                society=society,
                tenant_units=tenant_units,
                receivable_account=receivable,
                previous_fy=previous_fy,
                current_fy=current_fy,
                turnover_index=tenant_turnover_index,
                name_offset=owners_requested,
            )

            ownership_stats = self._rebuild_ownership_and_occupancy(
                society=society,
                owner_units=owner_units,
                owner_users_by_unit=owner_users_by_unit,
                tenant_users_by_unit=tenant_users_by_unit,
                previous_fy=previous_fy,
                current_fy=current_fy,
                succession_index=succession_index,
                vacancy_index=vacancy_index,
                turnover_index=tenant_turnover_index,
                historical_tenant=historical_tenant,
            )

            nominees_created = self._ensure_nominees(owner_members)
            share_stats = self._seed_shares(
                society=society,
                owner_members=owner_members,
                created_by=admin_user,
                transaction_date=previous_fy.start_date,
            )

            self._ensure_charge_templates(
                society=society,
                receivable_account=receivable,
                seed_effective_from=previous_fy.start_date,
            )

            # ----- Previous financial year --------------------------------
            previous_billing = self._seed_billing_for_year(
                society=society,
                financial_year=previous_fy,
                today=today,
                mode="historical",
                bank_account=main_bank,
            )
            self._seed_monthly_accounting(
                society=society,
                financial_year=previous_fy,
                today=today,
                member_unit=owner_units[0] if owner_units else None,
            )
            special_previous = self._seed_special_vouchers(
                society=society,
                financial_year=previous_fy,
                today=today,
                units=owner_units,
                history=True,
            )

            # ----- Carry forward balance sheet into the current year ------
            opening_created = self._ensure_current_year_opening(
                society=society,
                previous_fy=previous_fy,
                current_fy=current_fy,
            )

            # ----- Current financial year ---------------------------------
            current_billing = self._seed_billing_for_year(
                society=society,
                financial_year=current_fy,
                today=today,
                mode="current",
                bank_account=main_bank,
            )
            self._seed_monthly_accounting(
                society=society,
                financial_year=current_fy,
                today=today,
                member_unit=owner_units[min(1, len(owner_units) - 1)]
                if owner_units
                else None,
            )
            special_current = self._seed_special_vouchers(
                society=society,
                financial_year=current_fy,
                today=today,
                units=owner_units,
                history=False,
            )

            late_fee_stats = self._apply_late_fees_with_vouchers(
                society=society, today=today
            )

            reconciliation_stats = self._ensure_reconciliation_demo(
                society=society,
                bank_account=main_bank,
                today=today,
                uploaded_by=admin_user,
            )

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
                previous_fy=previous_fy,
                current_fy=current_fy,
                config=config,
                owner_members=owner_members,
                tenant_members=tenant_members,
                ownership_stats=ownership_stats,
                nominees_created=nominees_created,
                share_stats=share_stats,
                previous_billing=previous_billing,
                current_billing=current_billing,
                special_previous=special_previous,
                special_current=special_current,
                opening_created=opening_created,
                late_fee_stats=late_fee_stats,
                reconciliation_stats=reconciliation_stats,
                reminders_scheduled=reminders_scheduled,
                templates_created=templates_created,
                email_settings_created=email_settings_created,
                parking_stats=parking_stats,
                audit_created=audit_created,
            )

    # ------------------------------------------------------------------
    # Society profile / configuration
    # ------------------------------------------------------------------
    def _apply_society_profile(self, society):
        society.registration_number = "PNA/PUN/HSG/2009/0457"
        society.address = (
            "Jagruti Co-operative Housing Society Ltd., Plot No. 27, "
            "Sinhagad Road, Parvati, Pune 411030"
        )
        society.save(update_fields=["registration_number", "address"])

    def _apply_society_config(self, society):
        config, _ = SocietyConfig.objects.get_or_create(society=society)
        config.share_value = Decimal("100.00")
        config.default_share_count = SHARE_ALLOTMENT_COUNT
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
        base = f"{slug}@{SEED_DOMAIN}"
        user_model = get_user_model()

        # Deterministic across re-runs: reuse the base address when it belongs
        # to this person, otherwise fall back to a serial-suffixed address.
        existing = user_model.objects.filter(email=base).first()
        if existing is None or existing.name == full_name:
            return base

        candidate = f"{slug}.{serial}@{SEED_DOMAIN}"
        existing = user_model.objects.filter(email=candidate).first()
        if existing is None or existing.name == full_name:
            return candidate

        return f"{slug}.{serial}.{existing.pk}@{SEED_DOMAIN}"

    def _phone_for(self, serial):
        return f"98{(10000000 + serial * 137) % 100000000:08d}"

    # ------------------------------------------------------------------
    # Financial years
    # ------------------------------------------------------------------
    def _financial_year_dates(self, start_year):
        return date(start_year, FINANCIAL_YEAR_START_MONTH, 1), date(
            start_year + 1, 3, 31
        )

    def _ensure_financial_year(self, *, society, start_year):
        start_date, end_date = self._financial_year_dates(start_year)
        name = f"FY {start_year}-{str(start_year + 1)[-2:]}"
        fy, created = FinancialYear.objects.get_or_create(
            society=society,
            start_date=start_date,
            end_date=end_date,
            defaults={"name": name, "is_open": True},
        )
        update_fields = []
        if fy.name != name:
            fy.name = name
            update_fields.append("name")
        if not fy.is_open:
            fy.is_open = True
            update_fields.append("is_open")
        if update_fields:
            fy.save(update_fields=update_fields)

        if not fy.periods.exists():
            fy._create_accounting_periods()

        # Make sure every period that has already started is open so seeded
        # vouchers can post regardless of when the command is run.
        AccountingPeriod.objects.filter(
            society=society,
            financial_year=fy,
            start_date__lte=timezone.localdate(),
        ).update(is_open=True)
        return fy

    def _ensure_financial_years(self, *, society, today):
        current_start_year = (
            today.year
            if today.month >= FINANCIAL_YEAR_START_MONTH
            else today.year - 1
        )
        previous_fy = self._ensure_financial_year(
            society=society, start_year=current_start_year - 1
        )
        current_fy = self._ensure_financial_year(
            society=society, start_year=current_start_year
        )
        return previous_fy, current_fy

    def _open_periods_until(self, *, society, financial_year, until):
        AccountingPeriod.objects.filter(
            society=society,
            financial_year=financial_year,
            start_date__lte=until,
        ).update(is_open=True)

    # ------------------------------------------------------------------
    # Structure and units
    # ------------------------------------------------------------------
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
                        unit_type = self._unit_type_for(
                            wing_code, floor, sequence
                        )
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
            return Decimal("360.00") + Decimal(index % 4) * Decimal("20.00")
        if unit_type == Unit.UnitType.OFFICE:
            return Decimal("520.00") + Decimal(index % 4) * Decimal("25.00")
        return Decimal("680.00") + Decimal(index % 9) * Decimal("55.00")

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
        succession = min(owners - 1, max(owners - 2, 0)) if owners else 0
        vacancy = owners - 1 if owners else 0
        turnover = tenants - 1 if tenants > 0 else None
        return succession, vacancy, turnover

    # ------------------------------------------------------------------
    # Members
    # ------------------------------------------------------------------
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

            if index == succession_index and index > 0:
                old_name = self._person_name(90)
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
                    end_date=start_date - timedelta(days=1),
                )

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
                start_date=start_date,
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
        previous_fy,
        current_fy,
        turnover_index,
        name_offset,
    ):
        members = []
        tenant_users_by_unit = {}
        historical_tenant = None

        for index, unit in enumerate(tenant_units):
            serial = name_offset + index + 1
            full_name = self._person_name(name_offset + index)
            email = self._email_for(full_name, serial)
            user = self._ensure_user(email=email, full_name=full_name)
            tenant_start = previous_fy.start_date + timedelta(
                days=45 + (index % 6) * 30
            )
            if index == turnover_index:
                tenant_start = current_fy.start_date + timedelta(days=20)

            member = self._upsert_member(
                society=society,
                unit=unit,
                full_name=full_name,
                email=email,
                phone=self._phone_for(serial + 200),
                role=Member.MemberRole.TENANT,
                user=user,
                receivable_account=receivable_account,
                start_date=tenant_start,
                end_date=None,
            )
            members.append(member)
            tenant_users_by_unit[unit.id] = user

            if index == turnover_index:
                old_name = self._person_name(120)
                old_email = self._email_for(old_name, 950)
                old_user = self._ensure_user(email=old_email, full_name=old_name)
                historical_tenant = self._upsert_member(
                    society=society,
                    unit=unit,
                    full_name=old_name,
                    email=old_email,
                    phone=self._phone_for(950),
                    role=Member.MemberRole.TENANT,
                    user=old_user,
                    receivable_account=receivable_account,
                    start_date=previous_fy.start_date,
                    end_date=current_fy.start_date + timedelta(days=19),
                )
                tenant_users_by_unit.setdefault("historical", old_user)

        return members, tenant_users_by_unit, historical_tenant

    def _rebuild_ownership_and_occupancy(
        self,
        *,
        society,
        owner_units,
        owner_users_by_unit,
        tenant_users_by_unit,
        previous_fy,
        current_fy,
        succession_index,
        vacancy_index,
        turnover_index,
        historical_tenant,
    ):
        unit_ids = [unit.id for unit in owner_units]
        UnitOwnership.objects.filter(unit_id__in=unit_ids).delete()
        UnitOccupancy.objects.filter(unit_id__in=unit_ids).delete()

        ownership_created = 0
        occupancy_created = 0
        base_start = previous_fy.start_date

        for index, unit in enumerate(owner_units):
            current_owner_user = owner_users_by_unit[unit.id]

            if index == succession_index and index > 0:
                old_member = (
                    Member.objects.filter(
                        society=society,
                        unit=unit,
                        role=Member.MemberRole.OWNER,
                        status=Member.MemberStatus.INACTIVE,
                    )
                    .order_by("-end_date", "-id")
                    .first()
                )
                old_user = old_member.user if old_member else None
                if old_user is not None:
                    UnitOwnership.objects.create(
                        unit=unit,
                        owner=old_user,
                        role=UnitOwnership.OwnershipRole.PRIMARY,
                        start_date=date(base_start.year - 2, 4, 1),
                        end_date=base_start - timedelta(days=1),
                    )
                    UnitOccupancy.objects.create(
                        unit=unit,
                        occupant=old_user,
                        occupancy_type=UnitOccupancy.OccupancyType.OWNER,
                        start_date=date(base_start.year - 2, 4, 1),
                        end_date=base_start - timedelta(days=1),
                    )
                    ownership_created += 1
                    occupancy_created += 1
                UnitOwnership.objects.create(
                    unit=unit,
                    owner=current_owner_user,
                    role=UnitOwnership.OwnershipRole.PRIMARY,
                    start_date=base_start,
                )
                ownership_created += 1
                UnitOccupancy.objects.create(
                    unit=unit,
                    occupant=current_owner_user,
                    occupancy_type=UnitOccupancy.OccupancyType.OWNER,
                    start_date=base_start,
                )
                occupancy_created += 1
                continue

            UnitOwnership.objects.create(
                unit=unit,
                owner=current_owner_user,
                role=UnitOwnership.OwnershipRole.PRIMARY,
                start_date=base_start,
            )
            ownership_created += 1

            if index == vacancy_index:
                UnitOccupancy.objects.create(
                    unit=unit,
                    occupant=None,
                    occupancy_type=UnitOccupancy.OccupancyType.VACANT,
                    start_date=base_start,
                )
                occupancy_created += 1
                continue

            tenant_user = tenant_users_by_unit.get(unit.id)
            if tenant_user is None:
                UnitOccupancy.objects.create(
                    unit=unit,
                    occupant=current_owner_user,
                    occupancy_type=UnitOccupancy.OccupancyType.OWNER,
                    start_date=base_start,
                )
                occupancy_created += 1
                continue

            if index == turnover_index and historical_tenant is not None:
                old_start = base_start
                old_end = current_fy.start_date + timedelta(days=19)
                UnitOccupancy.objects.create(
                    unit=unit,
                    occupant=historical_tenant.user,
                    occupancy_type=UnitOccupancy.OccupancyType.TENANT,
                    start_date=old_start,
                    end_date=old_end,
                )
                UnitOccupancy.objects.create(
                    unit=unit,
                    occupant=tenant_user,
                    occupancy_type=UnitOccupancy.OccupancyType.TENANT,
                    start_date=old_end + timedelta(days=1),
                )
                occupancy_created += 2
                continue

            tenant_start = base_start + timedelta(days=45 + (index % 6) * 30)
            if tenant_start > base_start:
                UnitOccupancy.objects.create(
                    unit=unit,
                    occupant=current_owner_user,
                    occupancy_type=UnitOccupancy.OccupancyType.OWNER,
                    start_date=base_start,
                    end_date=tenant_start - timedelta(days=1),
                )
                occupancy_created += 1
            UnitOccupancy.objects.create(
                unit=unit,
                occupant=tenant_user,
                occupancy_type=UnitOccupancy.OccupancyType.TENANT,
                start_date=tenant_start,
            )
            occupancy_created += 1

        return {
            "ownership_created": ownership_created,
            "occupancy_created": occupancy_created,
        }

    def _ensure_nominees(self, owner_members):
        created = 0
        for position, member in enumerate(owner_members[:18]):
            if position < 4:
                shares = [Decimal("60.00"), Decimal("40.00")]
                for slot, percentage in enumerate(shares, start=1):
                    nominee_name = self._person_name(130 + position * 2 + slot)
                    _, was_created = Nominee.objects.get_or_create(
                        member=member,
                        priority_order=slot,
                        defaults={
                            "name": nominee_name,
                            "relationship": RELATIONSHIPS[
                                (position + slot) % len(RELATIONSHIPS)
                            ],
                            "percentage": percentage,
                            "is_active": True,
                        },
                    )
                    if was_created:
                        created += 1
                continue

            nominee_name = self._person_name(130 + position)
            _, was_created = Nominee.objects.get_or_create(
                member=member,
                priority_order=1,
                defaults={
                    "name": nominee_name,
                    "relationship": RELATIONSHIPS[position % len(RELATIONSHIPS)],
                    "percentage": Decimal("100.00"),
                    "is_active": True,
                },
            )
            if was_created:
                created += 1
        return created

    # ------------------------------------------------------------------
    # Shares
    # ------------------------------------------------------------------
    def _seed_shares(self, *, society, owner_members, created_by, transaction_date):
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
                reference=f"JG-ALLOT-{member.id}",
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
                certificate_no=f"JG-CERT-{member.id:05d}",
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
                    reference="JG-TRF-001",
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
                reference_id="JG-COR-001",
            ).exists():
                ShareTransactionService.record_share_correction(
                    member=target,
                    new_balance=manual_balance,
                    transaction_date=transaction_date,
                    reference="JG-COR-001",
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

    # ------------------------------------------------------------------
    # Billing and receipts
    # ------------------------------------------------------------------
    def _ensure_charge_templates(
        self, *, society, receivable_account, seed_effective_from
    ):
        templates = [
            {
                "name": "Maintenance",
                "rate": Decimal("3.2500"),
                "charge_type": ChargeTemplate.ChargeType.PER_SQFT,
                "due_days": 10,
                "late_fee_percent": Decimal("10.00"),
                "income_code": AccountCodes.MAINTENANCE_CHARGES,
            },
            {
                "name": "Sinking Fund",
                "rate": Decimal("500.0000"),
                "charge_type": ChargeTemplate.ChargeType.FIXED,
                "due_days": 15,
                "late_fee_percent": Decimal("0.00"),
                "income_code": AccountCodes.SINKING_FUND,
            },
            {
                "name": "Water Charges",
                "rate": Decimal("250.0000"),
                "charge_type": ChargeTemplate.ChargeType.FIXED,
                "due_days": 15,
                "late_fee_percent": Decimal("0.00"),
                "income_code": AccountCodes.SERVICE_CHARGES,
            },
            {
                "name": "Parking Charges",
                "rate": Decimal("300.0000"),
                "charge_type": ChargeTemplate.ChargeType.FIXED,
                "due_days": 15,
                "late_fee_percent": Decimal("0.00"),
                "income_code": AccountCodes.PARKING_CHARGES,
            },
        ]

        # Sinking fund is an equity/fund account; fall back to misc income when
        # the standard tree does not expose it as a posting income account.
        for item in templates:
            try:
                income_account = Account.objects.get(
                    society=society, code=item["income_code"], is_posting=True
                )
            except Account.DoesNotExist:
                income_account = Account.objects.get(
                    society=society, code=AccountCodes.MAINTENANCE_CHARGES
                )
            item["income_account"] = income_account

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
                description=f"Standard {item['name'].lower()} for Jagruti society",
                charge_type=item["charge_type"],
                rate=item["rate"],
                frequency=ChargeTemplate.Frequency.MONTHLY,
                due_days=item["due_days"],
                late_fee_percent=item["late_fee_percent"],
                income_account=income_account,
                receivable_account=receivable_account,
                is_active=True,
                effective_from=seed_effective_from,
            )

    def _due_templates(self, *, society, period_start):
        templates = list(
            ChargeTemplate.objects.filter(
                society=society,
                effective_from__lte=period_start,
            )
            .filter(
                Q(effective_to__isnull=True) | Q(effective_to__gte=period_start)
            )
            .select_related("income_account", "receivable_account")
        )
        return [
            template
            for template in templates
            if template.frequency == ChargeTemplate.Frequency.MONTHLY
            or (
                template.frequency == ChargeTemplate.Frequency.QUARTERLY
                and period_start.month in {1, 4, 7, 10}
            )
            or (
                template.frequency == ChargeTemplate.Frequency.YEARLY
                and period_start.month == 1
            )
        ]

    def _month_starts(self, *, start, end):
        current = date(start.year, start.month, 1)
        while current <= end:
            yield current
            current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)

    def _month_end(self, month_start):
        return (
            (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
            - timedelta(days=1)
        )

    def _seed_billing_for_year(
        self, *, society, financial_year, today, mode, bank_account
    ):
        if financial_year.start_date > today:
            return {"bills": 0, "receipts": 0}

        cash_account = Account.objects.get(
            society=society, code=AccountCodes.CASH_IN_HAND
        )
        lease_end = min(today, financial_year.end_date)
        created_bills = 0
        created_receipts = 0

        next_bill_number = (
            Bill.objects.filter(society=society)
            .order_by("-bill_number")
            .values_list("bill_number", flat=True)
            .first()
            or 0
        ) + 1

        for month_start in self._month_starts(
            start=financial_year.start_date, end=lease_end
        ):
            period_end = min(self._month_end(month_start), financial_year.end_date)
            if period_end < month_start:
                continue

            # Bills are raised a few days into the month. Skip the current
            # month until that date has arrived so voucher dates never move
            # into the future (and re-runs stay idempotent).
            bill_date = month_start + timedelta(days=4)
            if bill_date > today:
                continue
            due_templates = self._due_templates(
                society=society, period_start=month_start
            )
            if not due_templates:
                continue

            active_owners = list(
                Member.objects.filter(
                    society=society,
                    role=Member.MemberRole.OWNER,
                    status=Member.MemberStatus.ACTIVE,
                    start_date__lte=period_end,
                )
                .filter(Q(end_date__isnull=True) | Q(end_date__gte=month_start))
                .select_related("unit")
                .order_by("id")
            )

            period_bills = []
            for owner in active_owners:
                if Bill.objects.filter(
                    society=society,
                    member=owner,
                    bill_period_start=month_start,
                    bill_period_end=period_end,
                ).exists():
                    continue

                receivable_account = (
                    owner.receivable_account
                    or due_templates[0].receivable_account
                )
                due_days = max(template.due_days for template in due_templates)
                with transaction.atomic():
                    bill = Bill.objects.create(
                        society=society,
                        member=owner,
                        unit=owner.unit,
                        receivable_account=receivable_account,
                        bill_number=next_bill_number,
                        bill_period_start=month_start,
                        bill_period_end=period_end,
                        bill_date=bill_date,
                        due_date=bill_date + timedelta(days=due_days),
                        total_amount=Decimal("0.00"),
                    )
                    next_bill_number += 1

                    voucher = Voucher.objects.create(
                        society=society,
                        voucher_type=Voucher.VoucherType.BILL,
                        voucher_date=bill_date,
                        narration=(
                            f"Bill #{bill.bill_number} for {owner.full_name}"
                        ),
                    )
                    total = Decimal("0.00")
                    for template in due_templates:
                        rate = Decimal(template.rate).quantize(SNAPSHOT)
                        if template.charge_type == ChargeTemplate.ChargeType.FIXED:
                            quantity = Decimal("1.0000")
                            basis = "Fixed amount"
                        else:
                            quantity = Decimal(
                                owner.unit.billing_area_sqft or 0
                            ).quantize(SNAPSHOT)
                            basis = "Per sqft using unit chargeable area"
                        amount = (rate * quantity).quantize(
                            MONEY, rounding=ROUND_HALF_UP
                        )
                        if amount <= 0:
                            continue

                        BillLine.objects.create(
                            bill=bill,
                            charge_template=template,
                            description=template.name,
                            amount=amount,
                            charge_type_snapshot=template.charge_type,
                            rate_snapshot=rate,
                            quantity_snapshot=quantity,
                            late_fee_percent_snapshot=template.late_fee_percent,
                            template_version_snapshot=template.version_no,
                            calculation_basis=basis,
                            income_account=template.income_account,
                        )
                        LedgerEntry.objects.create(
                            voucher=voucher,
                            account=receivable_account,
                            unit=bill.unit,
                            debit=amount,
                        )
                        LedgerEntry.objects.create(
                            voucher=voucher,
                            account=template.income_account,
                            credit=amount,
                        )
                        total += amount

                    voucher.post()
                    bill.voucher = voucher
                    bill.total_amount = total
                    bill.save(update_fields=["voucher", "total_amount"])
                    bill.refresh_status(as_of_date=bill_date)

                created_bills += 1
                period_bills.append(bill)

            # A stable subset of the period's bills gets receipts.
            for index, bill in enumerate(period_bills):
                if bill.outstanding_amount <= 0:
                    continue
                amount = self._receipt_amount(
                    bill=bill, index=index, mode=mode
                )
                if amount <= 0:
                    continue
                payment_mode = ["CASH", "UPI", "BANK_TRANSFER", "CHEQUE", "CARD"][
                    index % 5
                ]
                reference = (
                    "" if payment_mode == "CASH" else f"JG-{mode[:4].upper()}-{bill.bill_number}"
                )
                receipt_date = min(
                    bill.bill_date + timedelta(days=6 + (index % 10)),
                    today,
                )
                if receipt_date < bill.bill_date:
                    receipt_date = bill.bill_date
                deposited = cash_account if payment_mode == "CASH" else bank_account
                post_receipt_for_bill(
                    society=society,
                    member=bill.member,
                    bill=bill,
                    amount=amount,
                    receipt_date=receipt_date,
                    payment_mode=payment_mode,
                    deposited_account=deposited,
                    reference_number=reference,
                )
                created_receipts += 1

        return {"bills": created_bills, "receipts": created_receipts}

    def _receipt_amount(self, *, bill, index, mode):
        cycle = index % 10
        if mode == "historical":
            if cycle <= 5:
                return bill.outstanding_amount
            if cycle <= 7:
                return (bill.total_amount * Decimal("0.50")).quantize(
                    MONEY, rounding=ROUND_HALF_UP
                )
            return Decimal("0.00")

        if cycle <= 5:
            return (bill.total_amount * Decimal("0.60")).quantize(
                MONEY, rounding=ROUND_HALF_UP
            )
        if cycle <= 7:
            return bill.outstanding_amount
        return Decimal("0.00")

    # ------------------------------------------------------------------
    # Voucher helpers
    # ------------------------------------------------------------------
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
        if voucher_date > timezone.localdate():
            return None, False
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

    def _ensure_reversal_voucher(
        self,
        *,
        society,
        original,
        code,
        narration,
    ):
        full_narration = f"{SEED_TAG} {code} {narration}"
        reversal = Voucher.objects.filter(
            society=society,
            reversal_of=original,
            narration=full_narration,
        ).first()
        if reversal and reversal.posted_at:
            return reversal, False
        if reversal and not reversal.posted_at:
            reversal.delete()

        with transaction.atomic():
            reversal = Voucher.objects.create(
                society=society,
                voucher_type=original.voucher_type,
                voucher_date=original.voucher_date,
                narration=full_narration,
                reversal_of=original,
            )
            for entry in original.entries.all():
                LedgerEntry.objects.create(
                    voucher=reversal,
                    account=entry.account,
                    unit=entry.unit,
                    debit=entry.credit,
                    credit=entry.debit,
                )
            reversal.post()
        return reversal, True

    def _ensure_draft_voucher(
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
        existing = Voucher.objects.filter(
            society=society,
            voucher_type=voucher_type,
            voucher_date=voucher_date,
            narration=full_narration,
            posted_at__isnull=True,
        ).first()
        if existing:
            return existing, False

        with transaction.atomic():
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
        return voucher, True

    def _account(self, society, code):
        return Account.objects.get(society=society, code=code)

    # ------------------------------------------------------------------
    # Recurring accounting entries
    # ------------------------------------------------------------------
    def _seed_monthly_accounting(self, *, society, financial_year, today, member_unit):
        if financial_year.start_date > today:
            return

        accounts = {
            "cash": self._account(society, AccountCodes.CASH_IN_HAND),
            "bank": self._account(society, AccountCodes.BANK_MAINTENANCE),
            "bank_sinking": self._account(society, AccountCodes.BANK_SINKING_FUND),
            "fd": self._account(society, "1.3.1"),
            "electricity": self._account(society, "4.2.1"),
            "water": self._account(society, "4.2.2"),
            "security": self._account(society, "4.5.1"),
            "housekeeping": self._account(society, "4.5.2"),
            "pest": self._account(society, "4.5.3"),
            "garden": self._account(society, "4.3.6"),
            "lift": self._account(society, "4.3.3"),
            "civil": self._account(society, "4.3.1"),
            "salary": self._account(society, "4.4.1"),
            "bank_charges": self._account(society, "4.6.1"),
            "printing": self._account(society, "4.1.2"),
            "office": self._account(society, "4.1.5"),
            "audit_fees": self._account(society, "4.1.1"),
            "provision_audit": self._account(society, "2.5.2"),
            "vendor_payable": self._account(society, AccountCodes.VENDOR_PAYABLE),
            "input_cgst": self._account(society, AccountCodes.INPUT_CGST),
            "input_sgst": self._account(society, AccountCodes.INPUT_SGST),
            "rental_income": self._account(society, "3.3.1"),
            "advertisement": self._account(society, "3.3.2"),
            "interest_bank": self._account(society, AccountCodes.INTEREST_INCOME_BANK),
            "interest_fd": self._account(society, "3.2.2"),
            "misc_income": self._account(society, "3.4.1"),
            "member_advance": self._account(
                society, AccountCodes.MEMBER_ADVANCE
            ),
            "security_deposit": self._account(
                society, AccountCodes.SECURITY_DEPOSIT_MEMBERS
            ),
        }

        year_tag = financial_year.name.replace(" ", "")
        end = min(today, financial_year.end_date)
        month_index = 0
        for month_start in self._month_starts(
            start=financial_year.start_date, end=end
        ):
            month_index += 1
            prefix = f"JG-{year_tag}-{month_start:%Y%m}"

            def mday(day):
                month_last = self._month_end(month_start)
                return month_start.replace(day=min(day, month_last.day))

            electricity = Decimal("9000.00") + Decimal(
                (month_index % 6) * 600
            )
            elec_gst = (electricity * Decimal("0.09")).quantize(
                MONEY, rounding=ROUND_HALF_UP
            )
            self._ensure_voucher(
                society=society,
                code=f"{prefix}-ELEC",
                voucher_type=Voucher.VoucherType.BILL,
                voucher_date=mday(8),
                narration="Electricity bill booked with input GST",
                rows=[
                    {"account": accounts["electricity"], "debit": electricity},
                    {"account": accounts["input_cgst"], "debit": elec_gst},
                    {"account": accounts["input_sgst"], "debit": elec_gst},
                    {
                        "account": accounts["vendor_payable"],
                        "credit": electricity + elec_gst * 2,
                    },
                ],
            )

            water = Decimal("5200.00") + Decimal((month_index % 4) * 300)
            self._ensure_voucher(
                society=society,
                code=f"{prefix}-WATER",
                voucher_type=Voucher.VoucherType.BILL,
                voucher_date=mday(10),
                narration="Water tanker and municipal water charges",
                rows=[
                    {"account": accounts["water"], "debit": water},
                    {"account": accounts["vendor_payable"], "credit": water},
                ],
            )

            security_amount = Decimal("42000.00") + Decimal(
                (month_index % 3) * 1500
            )
            security_gst = (security_amount * Decimal("0.09")).quantize(
                MONEY, rounding=ROUND_HALF_UP
            )
            self._ensure_voucher(
                society=society,
                code=f"{prefix}-SEC",
                voucher_type=Voucher.VoucherType.BILL,
                voucher_date=mday(5),
                narration="Security agency invoice booked with GST",
                rows=[
                    {"account": accounts["security"], "debit": security_amount},
                    {"account": accounts["input_cgst"], "debit": security_gst},
                    {"account": accounts["input_sgst"], "debit": security_gst},
                    {
                        "account": accounts["vendor_payable"],
                        "credit": security_amount + security_gst * 2,
                    },
                ],
            )

            garden = Decimal("3500.00") + Decimal((month_index % 2) * 400)
            self._ensure_voucher(
                society=society,
                code=f"{prefix}-GARDEN",
                voucher_type=Voucher.VoucherType.BILL,
                voucher_date=mday(14),
                narration="Garden maintenance contractor invoice",
                rows=[
                    {"account": accounts["garden"], "debit": garden},
                    {"account": accounts["vendor_payable"], "credit": garden},
                ],
            )

            housekeeping = Decimal("18000.00") + Decimal(
                (month_index % 2) * 1000
            )
            self._ensure_voucher(
                society=society,
                code=f"{prefix}-HK",
                voucher_type=Voucher.VoucherType.PAYMENT,
                voucher_date=mday(7),
                narration="Housekeeping staff wages paid",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number=f"{prefix}-HK",
                rows=[
                    {"account": accounts["housekeeping"], "debit": housekeeping},
                    {"account": accounts["bank"], "credit": housekeeping},
                ],
            )

            salary = Decimal("68000.00") + Decimal((month_index % 3) * 1200)
            self._ensure_voucher(
                society=society,
                code=f"{prefix}-SAL",
                voucher_type=Voucher.VoucherType.PAYMENT,
                voucher_date=mday(28),
                narration="Staff salary and statutory payments",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number=f"{prefix}-SAL",
                rows=[
                    {"account": accounts["salary"], "debit": salary},
                    {"account": accounts["bank"], "credit": salary},
                ],
            )

            bank_charges = Decimal("295.00") + Decimal((month_index % 4) * 25)
            self._ensure_voucher(
                society=society,
                code=f"{prefix}-BCHG",
                voucher_type=Voucher.VoucherType.PAYMENT,
                voucher_date=mday(26),
                narration="Bank charges debited by bank",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number=f"{prefix}-BCHG",
                rows=[
                    {"account": accounts["bank_charges"], "debit": bank_charges},
                    {"account": accounts["bank"], "credit": bank_charges},
                ],
            )

            stationery = Decimal("1200.00") + Decimal((month_index % 3) * 150)
            self._ensure_voucher(
                society=society,
                code=f"{prefix}-STAT",
                voucher_type=Voucher.VoucherType.BILL,
                voucher_date=mday(15),
                narration="Printing and stationery purchased",
                rows=[
                    {"account": accounts["printing"], "debit": stationery},
                    {"account": accounts["vendor_payable"], "credit": stationery},
                ],
            )

            office = Decimal("2200.00") + Decimal((month_index % 4) * 200)
            self._ensure_voucher(
                society=society,
                code=f"{prefix}-OFFICE",
                voucher_type=Voucher.VoucherType.PAYMENT,
                voucher_date=mday(18),
                narration="Office consumables and miscellaneous expenses",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number=f"{prefix}-OFFICE",
                rows=[
                    {"account": accounts["office"], "debit": office},
                    {"account": accounts["bank"], "credit": office},
                ],
            )

            rental = Decimal("15000.00") + Decimal((month_index % 2) * 1000)
            self._ensure_voucher(
                society=society,
                code=f"{prefix}-RENT",
                voucher_type=Voucher.VoucherType.RECEIPT,
                voucher_date=mday(3),
                narration="Rent received for common area tenancy",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number=f"{prefix}-RENT",
                rows=[
                    {"account": accounts["bank"], "debit": rental},
                    {"account": accounts["rental_income"], "credit": rental},
                ],
            )

            vendor_payment = Decimal("60000.00") + Decimal(
                (month_index % 5) * 2500
            )
            self._ensure_voucher(
                society=society,
                code=f"{prefix}-VNDPAY",
                voucher_type=Voucher.VoucherType.PAYMENT,
                voucher_date=mday(25),
                narration="Monthly vendor payments released",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number=f"{prefix}-VNDPAY",
                rows=[
                    {"account": accounts["vendor_payable"], "debit": vendor_payment},
                    {"account": accounts["bank"], "credit": vendor_payment},
                ],
            )

            cash_deposit = Decimal("15000.00") + Decimal(
                (month_index % 3) * 1000
            )
            self._ensure_voucher(
                society=society,
                code=f"{prefix}-CASHDEP",
                voucher_type=Voucher.VoucherType.JOURNAL,
                voucher_date=mday(27),
                narration="Cash collected deposited into bank",
                rows=[
                    {"account": accounts["bank"], "debit": cash_deposit},
                    {"account": accounts["cash"], "credit": cash_deposit},
                ],
            )

            if month_start.month in {1, 4, 7, 10}:
                lift = Decimal("5500.00") + Decimal(
                    ((month_index // 3) % 2) * 500
                )
                lift_gst = (lift * Decimal("0.09")).quantize(
                    MONEY, rounding=ROUND_HALF_UP
                )
                self._ensure_voucher(
                    society=society,
                    code=f"{prefix}-LIFT",
                    voucher_type=Voucher.VoucherType.BILL,
                    voucher_date=mday(12),
                    narration="Lift annual maintenance contract invoice",
                    rows=[
                        {"account": accounts["lift"], "debit": lift},
                        {"account": accounts["input_cgst"], "debit": lift_gst},
                        {"account": accounts["input_sgst"], "debit": lift_gst},
                        {
                            "account": accounts["vendor_payable"],
                            "credit": lift + lift_gst * 2,
                        },
                    ],
                )

                pest = Decimal("4000.00")
                self._ensure_voucher(
                    society=society,
                    code=f"{prefix}-PEST",
                    voucher_type=Voucher.VoucherType.BILL,
                    voucher_date=mday(20),
                    narration="Pest control treatment invoice",
                    rows=[
                        {"account": accounts["pest"], "debit": pest},
                        {"account": accounts["vendor_payable"], "credit": pest},
                    ],
                )

                self._ensure_voucher(
                    society=society,
                    code=f"{prefix}-AUDITPROV",
                    voucher_type=Voucher.VoucherType.JOURNAL,
                    voucher_date=mday(22),
                    narration="Audit fee provision created",
                    rows=[
                        {"account": accounts["audit_fees"], "debit": Decimal("15000.00")},
                        {
                            "account": accounts["provision_audit"],
                            "credit": Decimal("15000.00"),
                        },
                    ],
                )

                self._ensure_voucher(
                    society=society,
                    code=f"{prefix}-AUDITPAY",
                    voucher_type=Voucher.VoucherType.PAYMENT,
                    voucher_date=mday(24),
                    narration="Auditor remuneration paid",
                    payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                    reference_number=f"{prefix}-AUDITPAY",
                    rows=[
                        {
                            "account": accounts["provision_audit"],
                            "debit": Decimal("15000.00"),
                        },
                        {"account": accounts["bank"], "credit": Decimal("15000.00")},
                    ],
                )

                self._ensure_voucher(
                    society=society,
                    code=f"{prefix}-ADVERT",
                    voucher_type=Voucher.VoucherType.RECEIPT,
                    voucher_date=mday(6),
                    narration="Hoarding advertisement income received",
                    payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                    reference_number=f"{prefix}-ADVERT",
                    rows=[
                        {"account": accounts["bank"], "debit": Decimal("25000.00")},
                        {"account": accounts["advertisement"], "credit": Decimal("25000.00")},
                    ],
                )

                self._ensure_voucher(
                    society=society,
                    code=f"{prefix}-FUNDTRF",
                    voucher_type=Voucher.VoucherType.JOURNAL,
                    voucher_date=mday(16),
                    narration="Funds transferred to sinking fund bank account",
                    rows=[
                        {
                            "account": accounts["bank_sinking"],
                            "debit": Decimal("50000.00"),
                        },
                        {"account": accounts["bank"], "credit": Decimal("50000.00")},
                    ],
                )

            if month_start.month in {3, 6, 9, 12}:
                self._ensure_voucher(
                    society=society,
                    code=f"{prefix}-BANKINT",
                    voucher_type=Voucher.VoucherType.RECEIPT,
                    voucher_date=mday(29),
                    narration="Quarterly savings bank interest credited",
                    payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                    reference_number=f"{prefix}-BANKINT",
                    rows=[
                        {"account": accounts["bank"], "debit": Decimal("4250.00")},
                        {"account": accounts["interest_bank"], "credit": Decimal("4250.00")},
                    ],
                )

                self._ensure_voucher(
                    society=society,
                    code=f"{prefix}-FDINT",
                    voucher_type=Voucher.VoucherType.JOURNAL,
                    voucher_date=mday(30),
                    narration="Fixed deposit interest accrued and reinvested",
                    rows=[
                        {"account": accounts["fd"], "debit": Decimal("11250.00")},
                        {"account": accounts["interest_fd"], "credit": Decimal("11250.00")},
                    ],
                )

            if month_index % 4 == 2:
                civil = Decimal("7500.00") + Decimal((month_index % 3) * 750)
                self._ensure_voucher(
                    society=society,
                    code=f"{prefix}-CIVIL",
                    voucher_type=Voucher.VoucherType.BILL,
                    voucher_date=mday(21),
                    narration="Civil repair work invoice",
                    rows=[
                        {"account": accounts["civil"], "debit": civil},
                        {"account": accounts["vendor_payable"], "credit": civil},
                    ],
                )

            if month_index % 5 == 0:
                self._ensure_voucher(
                    society=society,
                    code=f"{prefix}-MISC",
                    voucher_type=Voucher.VoucherType.RECEIPT,
                    voucher_date=mday(9),
                    narration="Miscellaneous society income received",
                    payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                    reference_number=f"{prefix}-MISC",
                    rows=[
                        {"account": accounts["bank"], "debit": Decimal("2500.00")},
                        {"account": accounts["misc_income"], "credit": Decimal("2500.00")},
                    ],
                )

                if member_unit is not None:
                    self._ensure_voucher(
                        society=society,
                        code=f"{prefix}-ADV",
                        voucher_type=Voucher.VoucherType.RECEIPT,
                        voucher_date=mday(11),
                        narration="Member advance received for future dues",
                        payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                        reference_number=f"{prefix}-ADV",
                        rows=[
                            {
                                "account": accounts["bank"],
                                "debit": Decimal("10000.00"),
                            },
                            {
                                "account": accounts["member_advance"],
                                "unit": member_unit,
                                "credit": Decimal("10000.00"),
                            },
                        ],
                    )

    # ------------------------------------------------------------------
    # One-off / special vouchers
    # ------------------------------------------------------------------
    def _seed_special_vouchers(
        self, *, society, financial_year, today, units, history
    ):
        if financial_year.start_date > today:
            return 0

        start = financial_year.start_date
        year_tag = financial_year.name.replace(" ", "")
        created = 0

        cash = self._account(society, AccountCodes.CASH_IN_HAND)
        bank = self._account(society, AccountCodes.BANK_MAINTENANCE)
        bank_sinking = self._account(society, AccountCodes.BANK_SINKING_FUND)
        fd = self._account(society, "1.3.1")
        opening_equity = self._account(
            society, AccountCodes.OPENING_BALANCE_FUND
        )
        maintenance_due = self._account(society, AccountCodes.MAINTENANCE_DUE)
        late_fee_income = self._account(
            society, AccountCodes.LATE_PAYMENT_PENALTY
        )
        maintenance_income = self._account(
            society, AccountCodes.MAINTENANCE_CHARGES
        )
        output_cgst = self._account(society, AccountCodes.OUTPUT_CGST)
        output_sgst = self._account(society, AccountCodes.OUTPUT_SGST)
        member_advance = self._account(society, AccountCodes.MEMBER_ADVANCE)
        security_deposit = self._account(
            society, AccountCodes.SECURITY_DEPOSIT_MEMBERS
        )
        donation = self._account(society, "3.4.3")
        scrap = self._account(society, "3.4.2")
        misc_expense = self._account(society, "4.8.1")
        electricity = self._account(society, "4.2.1")
        input_cgst = self._account(society, AccountCodes.INPUT_CGST)
        input_sgst = self._account(society, AccountCodes.INPUT_SGST)
        vendor_payable = self._account(society, AccountCodes.VENDOR_PAYABLE)
        office_expense = self._account(society, "4.1.5")

        if history:
            # Opening balances at the start of the previous financial year.
            voucher, was_created = self._ensure_voucher(
                society=society,
                code=f"JG-{year_tag}-OPEN",
                voucher_type=Voucher.VoucherType.OPENING,
                voucher_date=start,
                narration="Opening balances brought forward",
                rows=[
                    {"account": bank, "debit": Decimal("1500000.00")},
                    {"account": bank_sinking, "debit": Decimal("500000.00")},
                    {"account": fd, "debit": Decimal("500000.00")},
                    {"account": cash, "debit": Decimal("60000.00")},
                    {
                        "account": opening_equity,
                        "credit": Decimal("2560000.00"),
                    },
                ],
            )
            created += int(was_created)

            # Security deposits received from two members.
            for position, unit in enumerate(units[:2], start=1):
                voucher, was_created = self._ensure_voucher(
                    society=society,
                    code=f"JG-{year_tag}-SECDEP-{position}",
                    voucher_type=Voucher.VoucherType.RECEIPT,
                    voucher_date=min(start + timedelta(days=10 + position), today),
                    narration="Security deposit received from member",
                    payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                    reference_number=f"JG-SECDEP-{position}",
                    rows=[
                        {"account": bank, "debit": Decimal("25000.00")},
                        {
                            "account": security_deposit,
                            "unit": unit,
                            "credit": Decimal("25000.00"),
                        },
                    ],
                )
                created += int(was_created)

            # Member advance receipts.
            for position, unit in enumerate(units[2:5], start=1):
                voucher, was_created = self._ensure_voucher(
                    society=society,
                    code=f"JG-{year_tag}-MADV-{position}",
                    voucher_type=Voucher.VoucherType.RECEIPT,
                    voucher_date=min(start + timedelta(days=20 + position), today),
                    narration="Advance maintenance collected from member",
                    payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                    reference_number=f"JG-MADV-{position}",
                    rows=[
                        {"account": bank, "debit": Decimal("15000.00")},
                        {
                            "account": member_advance,
                            "unit": unit,
                            "credit": Decimal("15000.00"),
                        },
                    ],
                )
                created += int(was_created)

            # Donation and scrap sale.
            voucher, was_created = self._ensure_voucher(
                society=society,
                code=f"JG-{year_tag}-DONATION",
                voucher_type=Voucher.VoucherType.RECEIPT,
                voucher_date=min(start + timedelta(days=75), today),
                narration="Donation received for community hall renovation",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number=f"JG-DONATION-{year_tag}",
                rows=[
                    {"account": bank, "debit": Decimal("11000.00")},
                    {"account": donation, "credit": Decimal("11000.00")},
                ],
            )
            created += int(was_created)

            voucher, was_created = self._ensure_voucher(
                society=society,
                code=f"JG-{year_tag}-SCRAP",
                voucher_type=Voucher.VoucherType.RECEIPT,
                voucher_date=min(start + timedelta(days=120), today),
                narration="Scrap material sold to local vendor",
                payment_mode=Voucher.PaymentMode.CASH,
                rows=[
                    {"account": cash, "debit": Decimal("1500.00")},
                    {"account": scrap, "credit": Decimal("1500.00")},
                ],
            )
            created += int(was_created)

            # Write-off adjustment against a member due.  Target a member in
            # the never-paid cycle so the unit keeps a net debit balance for
            # the year-end carry forward (a net credit would force the same
            # receivable account onto both sides of the opening voucher).
            if units:
                adjustment_unit = units[min(8, len(units) - 1)]
                voucher, was_created = self._ensure_voucher(
                    society=society,
                    code=f"JG-{year_tag}-ADJ",
                    voucher_type=Voucher.VoucherType.ADJUSTMENT,
                    voucher_date=min(start + timedelta(days=200), today),
                    narration="Irrecoverable member due written off",
                    rows=[
                        {
                            "account": misc_expense,
                            "debit": Decimal("2500.00"),
                        },
                        {
                            "account": maintenance_due,
                            "unit": adjustment_unit,
                            "credit": Decimal("2500.00"),
                        },
                    ],
                )
                created += int(was_created)

            # A maintenance billing example with explicit output GST split.
            if units:
                gst_unit = units[min(4, len(units) - 1)]
                voucher, was_created = self._ensure_voucher(
                    society=society,
                    code=f"JG-{year_tag}-GSTBILL",
                    voucher_type=Voucher.VoucherType.BILL,
                    voucher_date=min(start + timedelta(days=60), today),
                    narration="Maintenance billing with GST split for member",
                    rows=[
                        {
                            "account": maintenance_due,
                            "unit": gst_unit,
                            "debit": Decimal("11800.00"),
                        },
                        {
                            "account": maintenance_income,
                            "credit": Decimal("10000.00"),
                        },
                        {"account": output_cgst, "credit": Decimal("900.00")},
                        {"account": output_sgst, "credit": Decimal("900.00")},
                    ],
                )
                created += int(was_created)

            # An erroneous invoice and its reversal.
            original, was_created = self._ensure_voucher(
                society=society,
                code=f"JG-{year_tag}-REVORIG",
                voucher_type=Voucher.VoucherType.BILL,
                voucher_date=min(start + timedelta(days=135), today),
                narration="Duplicate electricity invoice booked in error",
                rows=[
                    {"account": electricity, "debit": Decimal("4000.00")},
                    {"account": vendor_payable, "credit": Decimal("4000.00")},
                ],
            )
            created += int(was_created)
            if original is not None:
                _, reversal_created = self._ensure_reversal_voucher(
                    society=society,
                    original=original,
                    code=f"JG-{year_tag}-REVERSAL",
                    narration="Reversal of duplicate electricity invoice",
                )
                created += int(reversal_created)

            # Accrued office expense (expense payable).
            expense_payable = self._account(society, AccountCodes.EXPENSE_PAYABLE)
            voucher, was_created = self._ensure_voucher(
                society=society,
                code=f"JG-{year_tag}-ACCRUAL",
                voucher_type=Voucher.VoucherType.JOURNAL,
                voucher_date=min(start + timedelta(days=150), today),
                narration="Office expenses accrued at year end",
                rows=[
                    {"account": office_expense, "debit": Decimal("3200.00")},
                    {"account": expense_payable, "credit": Decimal("3200.00")},
                ],
            )
            created += int(was_created)

            # Late payment penalty income (separate from bill penalties).
            if units:
                penalty_unit = units[min(5, len(units) - 1)]
                voucher, was_created = self._ensure_voucher(
                    society=society,
                    code=f"JG-{year_tag}-LATEFEE",
                    voucher_type=Voucher.VoucherType.BILL,
                    voucher_date=min(start + timedelta(days=210), today),
                    narration="Late payment penalty raised on overdue dues",
                    rows=[
                        {
                            "account": maintenance_due,
                            "unit": penalty_unit,
                            "debit": Decimal("850.00"),
                        },
                        {
                            "account": late_fee_income,
                            "credit": Decimal("850.00"),
                        },
                    ],
                )
                created += int(was_created)
        else:
            # Current-year extras: a member advance and a draft voucher.
            if units:
                voucher, was_created = self._ensure_voucher(
                    society=society,
                    code=f"JG-{year_tag}-MADV-CUR",
                    voucher_type=Voucher.VoucherType.RECEIPT,
                    voucher_date=min(start + timedelta(days=12), today),
                    narration="Advance maintenance collected for current year",
                    payment_mode=Voucher.PaymentMode.UPI,
                    reference_number=f"JG-MADV-CUR-{year_tag}",
                    rows=[
                        {"account": bank, "debit": Decimal("12000.00")},
                        {
                            "account": member_advance,
                            "unit": units[min(6, len(units) - 1)],
                            "credit": Decimal("12000.00"),
                        },
                    ],
                )
                created += int(was_created)

            draft_date = max(start, today - timedelta(days=2))
            _, was_created = self._ensure_draft_voucher(
                society=society,
                code=f"JG-{year_tag}-DRAFT",
                voucher_type=Voucher.VoucherType.GENERAL,
                voucher_date=draft_date,
                narration="Draft entry awaiting review before posting",
                rows=[
                    {"account": office_expense, "debit": Decimal("1850.00")},
                    {"account": vendor_payable, "credit": Decimal("1850.00")},
                ],
            )
            created += int(was_created)

        return created

    # ------------------------------------------------------------------
    # Current-year opening carry forward
    # ------------------------------------------------------------------
    def _ensure_current_year_opening(self, *, society, previous_fy, current_fy):
        narration = (
            f"{SEED_TAG} JG-{current_fy.name.replace(' ', '')}-OPEN-CF "
            f"Balance sheet carried forward from {previous_fy.name}"
        )
        existing = Voucher.objects.filter(
            society=society,
            voucher_type=Voucher.VoucherType.OPENING,
            voucher_date=current_fy.start_date,
            narration=narration,
        ).first()
        if existing and existing.posted_at:
            return 0

        rows = list(
            LedgerEntry.objects.filter(
                voucher__society=society,
                voucher__posted_at__isnull=False,
                voucher__voucher_date__gte=previous_fy.start_date,
                voucher__voucher_date__lte=previous_fy.end_date,
                account__account_type__in=("ASSET", "LIABILITY", "EQUITY"),
            )
            .values(
                "account_id",
                "unit_id",
                "account__account_type",
            )
            .annotate(
                total_debit=Sum("debit"),
                total_credit=Sum("credit"),
            )
        )

        debit_total = Decimal("0.00")
        credit_total = Decimal("0.00")
        opening_rows = []

        for item in rows:
            if item["account__account_type"] == "ASSET":
                net = (item["total_debit"] or Decimal("0.00")) - (
                    item["total_credit"] or Decimal("0.00")
                )
                is_debit_side = net > 0
            else:
                net = (item["total_credit"] or Decimal("0.00")) - (
                    item["total_debit"] or Decimal("0.00")
                )
                is_debit_side = net < 0

            amount = abs(net)
            if amount == 0:
                continue

            account = Account.objects.get(pk=item["account_id"])
            unit = (
                Unit.objects.filter(pk=item["unit_id"]).first()
                if item["unit_id"]
                else None
            )

            if is_debit_side:
                opening_rows.append(
                    {"account": account, "unit": unit, "debit": amount, "credit": Decimal("0.00")}
                )
                debit_total += amount
            else:
                opening_rows.append(
                    {"account": account, "unit": unit, "debit": Decimal("0.00"), "credit": amount}
                )
                credit_total += amount

        difference = debit_total - credit_total
        surplus = self._account(society, AccountCodes.SURPLUS_DEFICIT)
        if difference > 0:
            opening_rows.append(
                {"account": surplus, "debit": Decimal("0.00"), "credit": difference}
            )
        elif difference < 0:
            opening_rows.append(
                {"account": surplus, "debit": abs(difference), "credit": Decimal("0.00")}
            )

        if not opening_rows:
            return 0

        voucher = Voucher.objects.create(
            society=society,
            voucher_type=Voucher.VoucherType.OPENING,
            voucher_date=current_fy.start_date,
            narration=narration,
        )
        for row in opening_rows:
            LedgerEntry.objects.create(
                voucher=voucher,
                account=row["account"],
                unit=row.get("unit"),
                debit=row["debit"],
                credit=row["credit"],
            )
        voucher.post()
        return 1

    # ------------------------------------------------------------------
    # Late fees (with matching ledger vouchers)
    # ------------------------------------------------------------------
    def _apply_late_fees_with_vouchers(self, *, society, today):
        bills_before = {
            bill.id: bill.penalty_amount
            for bill in Bill.objects.filter(
                society=society,
                due_date__lt=today,
                status__in=(
                    Bill.BillStatus.OPEN,
                    Bill.BillStatus.PARTIAL,
                    Bill.BillStatus.OVERDUE,
                ),
            )
        }
        updated = apply_late_fees(society=society, as_of_date=today)

        late_fee_income = self._account(
            society, AccountCodes.LATE_PAYMENT_PENALTY
        )
        vouchers_created = 0
        for bill in Bill.objects.filter(
            society=society, id__in=list(bills_before.keys())
        ).select_related("unit"):
            before = bills_before.get(bill.id, Decimal("0.00"))
            delta = bill.penalty_amount - before
            if delta <= 0:
                continue
            _, was_created = self._ensure_voucher(
                society=society,
                code=f"JG-LATE-{bill.bill_number}",
                voucher_type=Voucher.VoucherType.BILL,
                voucher_date=today,
                narration=(
                    f"Late payment penalty on bill #{bill.bill_number} "
                    f"for {bill.member.full_name}"
                ),
                rows=[
                    {
                        "account": bill.receivable_account,
                        "unit": bill.unit,
                        "debit": delta,
                    },
                    {"account": late_fee_income, "credit": delta},
                ],
            )
            vouchers_created += int(was_created)

        return {"bills_updated": updated, "vouchers_created": vouchers_created}

    # ------------------------------------------------------------------
    # Reconciliation demo
    # ------------------------------------------------------------------
    def _ensure_reconciliation_demo(self, *, society, bank_account, today, uploaded_by):
        demo_file_name = "jagruti_reconciliation_demo.csv"
        demo_marker = "JG-DEMO"

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
            self.stdout.write("Not enough open bills for reconciliation demo; skipping.")
            return {"transactions_created": 0, "suggestions_created": 0}

        receipt_1_ref = f"{demo_marker}-REC-001"
        receipt_2_ref = f"{demo_marker}-REC-002"
        payment_ref = f"{demo_marker}-PAY-001"

        # Keep the demo receipts inside the current financial year and close
        # to today so the statement rows line up with the current bank ledger.
        current_fy_start_year = (
            today.year
            if today.month >= FINANCIAL_YEAR_START_MONTH
            else today.year - 1
        )
        current_fy_start = date(
            current_fy_start_year, FINANCIAL_YEAR_START_MONTH, 1
        )
        receipt_1_date = max(today - timedelta(days=6), current_fy_start)
        receipt_2_date = max(today - timedelta(days=3), current_fy_start)

        receipt_1 = self._get_or_create_demo_receipt(
            society=society,
            bill=bills[0],
            amount=min(bills[0].outstanding_amount, Decimal("7500.00")),
            receipt_date=receipt_1_date,
            bank_account=bank_account,
            reference_number=receipt_1_ref,
        )
        receipt_2 = self._get_or_create_demo_receipt(
            society=society,
            bill=bills[1],
            amount=min(bills[1].outstanding_amount, Decimal("6200.00")),
            receipt_date=receipt_2_date,
            bank_account=bank_account,
            reference_number=receipt_2_ref,
        )
        payment_date = max(today - timedelta(days=2), current_fy_start)
        payment_voucher = self._get_or_create_demo_vendor_payment(
            society=society,
            amount=Decimal("4200.00"),
            voucher_date=payment_date,
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

    # ------------------------------------------------------------------
    # Misc demo data
    # ------------------------------------------------------------------
    def _ensure_email_settings(self, society):
        _, created = SocietyEmailSettings.objects.get_or_create(
            society=society,
            defaults={
                "provider_type": "SMTP",
                "smtp_host": f"smtp.{SEED_DOMAIN}",
                "smtp_port": 587,
                "smtp_username": f"notifications@{SEED_DOMAIN}",
                "use_tls": True,
                "use_ssl": False,
                "default_from_email": f"noreply@{SEED_DOMAIN}",
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
            (f"B-{index:02d}", ParkingSlot.SlotType.BASEMENT) for index in range(1, 21)
        ] + [(f"P-{index:02d}", ParkingSlot.SlotType.OPEN) for index in range(1, 11)]
        sold_slot_numbers = {f"B-{i:02d}" for i in range(1, 16)}

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
        return f"MH-12-{series}-{2000 + index:04d}"

    def _ensure_audit_logs(self, *, society, actor):
        created = 0
        entries = [
            (AuditLog.Action.CREATE, "member", "members"),
            (AuditLog.Action.CREATE, "unit", "units"),
            (AuditLog.Action.CREATE, "voucher", "accounting"),
            (AuditLog.Action.POST, "voucher", "accounting"),
            (AuditLog.Action.REVERSE, "voucher", "accounting"),
            (AuditLog.Action.CREATE, "bill", "billing"),
            (AuditLog.Action.CREATE, "payment_receipt", "receipts"),
            (AuditLog.Action.CREATE, "share_ledger", "shares"),
            (AuditLog.Action.CREATE, "parking_permit", "parking"),
            (AuditLog.Action.EXPORT, "trial_balance", "accounting"),
        ]
        for action, entity_type, module in entries:
            entity_id = f"JAGRUTI-{entity_type}"
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
                reason="Seeded demo data for Jagruti society",
            )
            created += 1
        return created

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------
    def _reset_society(self, society):
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

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    def _print_summary(self, **stats):  # noqa: PLR0913
        society = stats["society"]

        def count(model, **filters):
            return model.objects.filter(society=society, **filters).count()

        lines = [
            self.style.SUCCESS("Jagruti demo data loaded."),
            f"Society: {society.name} (id={society.id})",
            f"Registration: {society.registration_number}",
            f"Address: {society.address}",
            f"Share config: value={stats['config'].share_value}, "
            f"entrance_fee={stats['config'].entrance_fee}, "
            f"transfer_fee={stats['config'].transfer_fee}",
            f"Financial years: {stats['previous_fy'].name} "
            f"({stats['previous_fy'].start_date} to {stats['previous_fy'].end_date}), "
            f"{stats['current_fy'].name} "
            f"({stats['current_fy'].start_date} to {stats['current_fy'].end_date})",
            f"Structures: {Structure.objects.filter(society=society).count()}",
            f"Units: {Unit.objects.filter(structure__society=society).count()}",
            f"Owner members: {len(stats['owner_members'])} "
            f"(total: {count(Member, role=Member.MemberRole.OWNER)})",
            f"Tenant members: {len(stats['tenant_members'])} "
            f"(total: {count(Member, role=Member.MemberRole.TENANT)})",
            f"Ownership records: {stats['ownership_stats']['ownership_created']}",
            f"Occupancy records: {stats['ownership_stats']['occupancy_created']}",
            f"Nominees created now: {stats['nominees_created']} "
            f"(total: {Nominee.objects.filter(member__society=society).count()})",
            f"Shares allotted now: {stats['share_stats']['allotted']} "
            f"(ledger rows: {count(ShareLedger)})",
            f"Share certificates created now: {stats['share_stats']['certificates']} "
            f"(total: {ShareCertificate.objects.filter(member__society=society).count()})",
            f"Share transfers: {stats['share_stats']['transfers']}, "
            f"corrections: {stats['share_stats']['corrections']}",
            f"Share events (EventLog): {count(EventLog)}",
            f"Charge templates: {count(ChargeTemplate)}",
            f"Previous-year bills/receipts: "
            f"{stats['previous_billing']['bills']}/{stats['previous_billing']['receipts']}",
            f"Current-year bills/receipts: "
            f"{stats['current_billing']['bills']}/{stats['current_billing']['receipts']}",
            f"Total bills: {count(Bill)}",
            f"Total receipts: {count(PaymentReceipt)}",
            f"Special vouchers created now: "
            f"{stats['special_previous']} previous, "
            f"{stats['special_current']} current",
            f"Current-year opening voucher created: {stats['opening_created']}",
            f"Late fees: bills updated {stats['late_fee_stats']['bills_updated']}, "
            f"vouchers created {stats['late_fee_stats']['vouchers_created']}",
            f"Vouchers total: {count(Voucher)}",
            f"Ledger entries: "
            f"{LedgerEntry.objects.filter(voucher__society=society).count()}",
            f"Accounts: {count(Account)}, "
            f"Account categories: {count(AccountCategory)}",
            f"Reconciliation rows created now: "
            f"{stats['reconciliation_stats']['transactions_created']}, "
            f"suggestions: {stats['reconciliation_stats']['suggestions_created']}",
            f"Reminders scheduled now: {stats['reminders_scheduled']} "
            f"(total: {count(ReminderLog)})",
            f"Email templates ensured: {stats['templates_created']}, "
            f"email settings created: {stats['email_settings_created']}",
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
