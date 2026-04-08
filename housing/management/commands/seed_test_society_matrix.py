from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from accounting.models import Account
from accounting.models import AccountCategory
from accounting.models import FinancialYear
from accounting.services.standard_accounts import create_default_accounts_for_society
from accounting.services.standard_accounts import ensure_standard_categories
from members.models import Member
from members.models import Structure
from members.models import Unit
from members.models import UnitOccupancy
from members.models import UnitOwnership
from societies.models import Society


class Command(BaseCommand):
    help = (
        "Seed Test Society with a rich ownership/tenancy matrix "
        "covering primary/secondary/history/vacancy combinations."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--society",
            default="Test Society",
            help="Society name to seed. Default: Test Society",
        )

    def handle(self, *args, **options):
        society_name = (options["society"] or "").strip() or "Test Society"
        society, _ = Society.objects.get_or_create(name=society_name)

        ensure_standard_categories(society)
        create_default_accounts_for_society(society)
        receivable = self._get_or_create_account(
            society=society,
            name="Maintenance Receivable",
            category_name="Member Receivables",
            account_type=AccountCategory.AccountType.ASSET,
        )

        self._ensure_financial_year(
            society=society,
            name="FY 2025-26",
            start=date(2025, 4, 1),
            end=date(2026, 3, 31),
        )

        structures = self._seed_structures(society)
        units = self._seed_units(structures)

        self._rebuild_member_matrix(society=society, units=units, receivable_account=receivable)
        self._rebuild_ownership_matrix(units)
        self._rebuild_occupancy_matrix(units)

        members_total = Member.objects.filter(
            society=society,
            full_name__startswith="Matrix ",
        ).count()
        owners_total = Member.objects.filter(
            society=society,
            full_name__startswith="Matrix ",
            role=Member.MemberRole.OWNER,
        ).count()
        tenants_total = Member.objects.filter(
            society=society,
            full_name__startswith="Matrix ",
            role=Member.MemberRole.TENANT,
        ).count()
        nominees_total = Member.objects.filter(
            society=society,
            full_name__startswith="Matrix ",
            role=Member.MemberRole.NOMINEE,
        ).count()

        ownership_total = UnitOwnership.objects.filter(
            unit__in=units.values(),
        ).count()
        occupancy_total = UnitOccupancy.objects.filter(
            unit__in=units.values(),
        ).count()

        self.stdout.write(self.style.SUCCESS("Test Society matrix dataset is ready."))
        self.stdout.write(f"Society: {society.name} (id={society.id})")
        self.stdout.write(f"Structures seeded: {len(structures)}")
        self.stdout.write(f"Units seeded: {len(units)}")
        self.stdout.write(
            f"Matrix members: {members_total} (owners={owners_total}, tenants={tenants_total}, nominees={nominees_total})"
        )
        self.stdout.write(f"Ownership records on matrix units: {ownership_total}")
        self.stdout.write(f"Occupancy records on matrix units: {occupancy_total}")

    def _seed_structures(self, society):
        structures = {}
        structures["building_a"] = self._upsert_structure(
            society=society,
            parent=None,
            structure_type=Structure.StructureType.BUILDING,
            name="Building A",
            display_order=1,
        )
        structures["wing_a1"] = self._upsert_structure(
            society=society,
            parent=structures["building_a"],
            structure_type=Structure.StructureType.WING,
            name="Wing A1",
            display_order=1,
        )
        structures["wing_a2"] = self._upsert_structure(
            society=society,
            parent=structures["building_a"],
            structure_type=Structure.StructureType.WING,
            name="Wing A2",
            display_order=2,
        )
        structures["floor_a1_1"] = self._upsert_structure(
            society=society,
            parent=structures["wing_a1"],
            structure_type=Structure.StructureType.FLOOR,
            name="Floor 1",
            display_order=1,
        )
        structures["floor_a1_2"] = self._upsert_structure(
            society=society,
            parent=structures["wing_a1"],
            structure_type=Structure.StructureType.FLOOR,
            name="Floor 2",
            display_order=2,
        )
        structures["floor_a2_2"] = self._upsert_structure(
            society=society,
            parent=structures["wing_a2"],
            structure_type=Structure.StructureType.FLOOR,
            name="Floor 2",
            display_order=1,
        )
        structures["building_b"] = self._upsert_structure(
            society=society,
            parent=None,
            structure_type=Structure.StructureType.BUILDING,
            name="Building B",
            display_order=2,
        )
        structures["floor_b1"] = self._upsert_structure(
            society=society,
            parent=structures["building_b"],
            structure_type=Structure.StructureType.FLOOR,
            name="Floor 3",
            display_order=1,
        )
        return structures

    def _seed_units(self, structures):
        units = {}
        units["A1-101"] = self._upsert_unit(structures["floor_a1_1"], "A1-101", Unit.UnitType.FLAT)
        units["A1-102"] = self._upsert_unit(structures["floor_a1_1"], "A1-102", Unit.UnitType.FLAT)
        units["A1-103"] = self._upsert_unit(structures["floor_a1_1"], "A1-103", Unit.UnitType.FLAT)
        units["A1-104"] = self._upsert_unit(structures["floor_a1_1"], "A1-104", Unit.UnitType.FLAT)
        units["A2-201"] = self._upsert_unit(structures["floor_a2_2"], "A2-201", Unit.UnitType.FLAT)
        units["A2-202"] = self._upsert_unit(structures["floor_a2_2"], "A2-202", Unit.UnitType.FLAT)
        units["A2-203"] = self._upsert_unit(structures["floor_a2_2"], "A2-203", Unit.UnitType.FLAT)
        units["B1-301"] = self._upsert_unit(structures["floor_b1"], "B1-301", Unit.UnitType.FLAT)
        units["B1-302"] = self._upsert_unit(structures["floor_b1"], "B1-302", Unit.UnitType.FLAT)
        units["B1-303"] = self._upsert_unit(structures["floor_b1"], "B1-303", Unit.UnitType.FLAT)
        units["B1-304"] = self._upsert_unit(structures["floor_b1"], "B1-304", Unit.UnitType.FLAT)
        units["B1-SH1"] = self._upsert_unit(structures["floor_b1"], "B1-SH1", Unit.UnitType.SHOP)
        return units

    def _rebuild_member_matrix(self, *, society, units, receivable_account):
        User = get_user_model()

        matrix_members = [
            ("owner01@example.com", "Matrix Owner 01", "A1-101", Member.MemberRole.OWNER, date(2023, 4, 1), None),
            ("owner02@example.com", "Matrix Owner 02", "A1-102", Member.MemberRole.OWNER, date(2023, 4, 1), None),
            ("owner03@example.com", "Matrix Owner 03", "A1-102", Member.MemberRole.OWNER, date(2023, 4, 1), None),
            ("owner04@example.com", "Matrix Owner 04", "A1-103", Member.MemberRole.OWNER, date(2023, 4, 1), date(2024, 12, 31)),
            ("owner05@example.com", "Matrix Owner 05", "A1-103", Member.MemberRole.OWNER, date(2025, 1, 1), None),
            ("owner06@example.com", "Matrix Owner 06", "A1-103", Member.MemberRole.OWNER, date(2025, 1, 1), None),
            ("owner07@example.com", "Matrix Owner 07", "A1-104", Member.MemberRole.OWNER, date(2023, 4, 1), None),
            ("owner08@example.com", "Matrix Owner 08", "A2-201", Member.MemberRole.OWNER, date(2023, 4, 1), None),
            ("owner09@example.com", "Matrix Owner 09", "A2-201", Member.MemberRole.OWNER, date(2023, 4, 1), None),
            ("owner10@example.com", "Matrix Owner 10", "A2-201", Member.MemberRole.OWNER, date(2023, 4, 1), None),
            ("owner11@example.com", "Matrix Owner 11", "A2-202", Member.MemberRole.OWNER, date(2023, 4, 1), None),
            ("owner12@example.com", "Matrix Owner 12", "A2-203", Member.MemberRole.OWNER, date(2023, 4, 1), None),
            ("owner13@example.com", "Matrix Owner 13", "B1-301", Member.MemberRole.OWNER, date(2023, 4, 1), date(2024, 3, 31)),
            ("owner14@example.com", "Matrix Owner 14", "B1-301", Member.MemberRole.OWNER, date(2024, 4, 1), None),
            ("owner15@example.com", "Matrix Owner 15", "B1-302", Member.MemberRole.OWNER, date(2023, 4, 1), None),
            ("owner16@example.com", "Matrix Owner 16", "B1-303", Member.MemberRole.OWNER, date(2023, 4, 1), None),
            ("owner17@example.com", "Matrix Owner 17", "B1-304", Member.MemberRole.OWNER, date(2023, 4, 1), None),
            ("owner18@example.com", "Matrix Owner 18", "B1-304", Member.MemberRole.OWNER, date(2023, 4, 1), date(2024, 3, 31)),
            ("owner19@example.com", "Matrix Owner 19", "B1-304", Member.MemberRole.OWNER, date(2024, 4, 1), None),
            ("owner20@example.com", "Matrix Owner 20", "B1-SH1", Member.MemberRole.OWNER, date(2023, 4, 1), None),
            ("tenant01@example.com", "Matrix Tenant 01", "A1-104", Member.MemberRole.TENANT, date(2024, 4, 1), date(2025, 3, 31)),
            ("tenant02@example.com", "Matrix Tenant 02", "A1-104", Member.MemberRole.TENANT, date(2025, 4, 1), None),
            ("tenant03@example.com", "Matrix Tenant 03", "A2-201", Member.MemberRole.TENANT, date(2025, 1, 1), None),
            ("tenant04@example.com", "Matrix Tenant 04", "A2-202", Member.MemberRole.TENANT, date(2024, 7, 1), date(2026, 1, 31)),
            ("tenant05@example.com", "Matrix Tenant 05", "A2-203", Member.MemberRole.TENANT, date(2025, 5, 1), None),
            ("tenant06@example.com", "Matrix Tenant 06", "B1-303", Member.MemberRole.TENANT, date(2024, 1, 1), date(2024, 12, 31)),
            ("tenant07@example.com", "Matrix Tenant 07", "B1-303", Member.MemberRole.TENANT, date(2025, 7, 1), None),
            ("tenant08@example.com", "Matrix Tenant 08", "B1-304", Member.MemberRole.TENANT, date(2024, 5, 1), None),
            ("tenant09@example.com", "Matrix Tenant 09", "B1-SH1", Member.MemberRole.TENANT, date(2024, 6, 1), None),
            ("nominee01@example.com", "Matrix Nominee 01", "B1-302", Member.MemberRole.NOMINEE, date(2023, 4, 1), None),
        ]

        for email, full_name, unit_key, role, start_date, end_date in matrix_members:
            user, _ = User.objects.get_or_create(
                email=f"matrix.{email}",
                defaults={"name": full_name, "is_active": True},
            )
            if user.name != full_name:
                user.name = full_name
                user.save(update_fields=["name"])

            member, _ = Member.objects.get_or_create(
                society=society,
                unit=units[unit_key],
                full_name=full_name,
                role=role,
                defaults={
                    "user": user,
                    "email": user.email,
                    "phone": self._matrix_phone(full_name),
                    "status": Member.MemberStatus.ACTIVE,
                    "start_date": start_date,
                    "end_date": end_date,
                    "receivable_account": receivable_account,
                },
            )
            status = (
                Member.MemberStatus.INACTIVE
                if end_date is not None
                else Member.MemberStatus.ACTIVE
            )
            update_fields = []
            if member.user_id != user.id:
                member.user = user
                update_fields.append("user")
            if member.email != user.email:
                member.email = user.email
                update_fields.append("email")
            phone = self._matrix_phone(full_name)
            if member.phone != phone:
                member.phone = phone
                update_fields.append("phone")
            if member.start_date != start_date:
                member.start_date = start_date
                update_fields.append("start_date")
            if member.end_date != end_date:
                member.end_date = end_date
                update_fields.append("end_date")
            if member.status != status:
                member.status = status
                update_fields.append("status")
            if member.receivable_account_id != receivable_account.id:
                member.receivable_account = receivable_account
                update_fields.append("receivable_account")
            if update_fields:
                member.save(update_fields=update_fields)

    def _rebuild_ownership_matrix(self, units):
        UnitOwnership.objects.filter(unit__in=units.values()).delete()

        specs = [
            ("A1-101", "matrix.owner01@example.com", UnitOwnership.OwnershipRole.PRIMARY, date(2023, 4, 1), None),
            ("A1-102", "matrix.owner02@example.com", UnitOwnership.OwnershipRole.PRIMARY, date(2023, 4, 1), None),
            ("A1-102", "matrix.owner03@example.com", UnitOwnership.OwnershipRole.SECONDARY, date(2023, 4, 1), None),
            ("A1-103", "matrix.owner04@example.com", UnitOwnership.OwnershipRole.PRIMARY, date(2023, 4, 1), date(2024, 12, 31)),
            ("A1-103", "matrix.owner05@example.com", UnitOwnership.OwnershipRole.PRIMARY, date(2025, 1, 1), None),
            ("A1-103", "matrix.owner06@example.com", UnitOwnership.OwnershipRole.SECONDARY, date(2025, 1, 1), None),
            ("A1-104", "matrix.owner07@example.com", UnitOwnership.OwnershipRole.PRIMARY, date(2023, 4, 1), None),
            ("A2-201", "matrix.owner08@example.com", UnitOwnership.OwnershipRole.PRIMARY, date(2023, 4, 1), None),
            ("A2-201", "matrix.owner09@example.com", UnitOwnership.OwnershipRole.SECONDARY, date(2023, 4, 1), None),
            ("A2-201", "matrix.owner10@example.com", UnitOwnership.OwnershipRole.SECONDARY, date(2023, 4, 1), None),
            ("A2-202", "matrix.owner11@example.com", UnitOwnership.OwnershipRole.PRIMARY, date(2023, 4, 1), None),
            ("A2-203", "matrix.owner12@example.com", UnitOwnership.OwnershipRole.PRIMARY, date(2023, 4, 1), None),
            ("B1-301", "matrix.owner13@example.com", UnitOwnership.OwnershipRole.PRIMARY, date(2023, 4, 1), date(2024, 3, 31)),
            ("B1-301", "matrix.owner14@example.com", UnitOwnership.OwnershipRole.PRIMARY, date(2024, 4, 1), None),
            ("B1-302", "matrix.owner15@example.com", UnitOwnership.OwnershipRole.PRIMARY, date(2023, 4, 1), None),
            ("B1-303", "matrix.owner16@example.com", UnitOwnership.OwnershipRole.PRIMARY, date(2023, 4, 1), None),
            ("B1-304", "matrix.owner17@example.com", UnitOwnership.OwnershipRole.PRIMARY, date(2023, 4, 1), None),
            ("B1-304", "matrix.owner18@example.com", UnitOwnership.OwnershipRole.SECONDARY, date(2023, 4, 1), date(2024, 3, 31)),
            ("B1-304", "matrix.owner19@example.com", UnitOwnership.OwnershipRole.SECONDARY, date(2024, 4, 1), None),
            ("B1-SH1", "matrix.owner20@example.com", UnitOwnership.OwnershipRole.PRIMARY, date(2023, 4, 1), None),
        ]

        User = get_user_model()
        for unit_key, owner_email, role, start_date, end_date in specs:
            owner = User.objects.get(email=owner_email)
            record = UnitOwnership(
                unit=units[unit_key],
                owner=owner,
                role=role,
                start_date=start_date,
                end_date=end_date,
            )
            record.full_clean()
            record.save()

    def _rebuild_occupancy_matrix(self, units):
        UnitOccupancy.objects.filter(unit__in=units.values()).delete()

        specs = [
            ("A1-101", "OWNER", "matrix.owner01@example.com", date(2023, 4, 1), None),
            ("A1-102", "OWNER", "matrix.owner02@example.com", date(2023, 4, 1), None),
            ("A1-103", "OWNER", "matrix.owner04@example.com", date(2023, 4, 1), date(2024, 12, 31)),
            ("A1-103", "OWNER", "matrix.owner05@example.com", date(2025, 1, 1), None),
            ("A1-104", "TENANT", "matrix.tenant01@example.com", date(2024, 4, 1), date(2025, 3, 31)),
            ("A1-104", "TENANT", "matrix.tenant02@example.com", date(2025, 4, 1), None),
            ("A2-201", "TENANT", "matrix.tenant03@example.com", date(2025, 1, 1), None),
            ("A2-202", "OWNER", "matrix.owner11@example.com", date(2023, 4, 1), date(2024, 6, 30)),
            ("A2-202", "TENANT", "matrix.tenant04@example.com", date(2024, 7, 1), date(2026, 1, 31)),
            ("A2-202", "VACANT", None, date(2026, 2, 1), None),
            ("A2-203", "TENANT", "matrix.tenant05@example.com", date(2025, 5, 1), None),
            ("B1-301", "OWNER", "matrix.owner13@example.com", date(2023, 4, 1), date(2024, 3, 31)),
            ("B1-301", "OWNER", "matrix.owner14@example.com", date(2024, 4, 1), None),
            ("B1-302", "OWNER", "matrix.owner15@example.com", date(2023, 4, 1), None),
            ("B1-303", "TENANT", "matrix.tenant06@example.com", date(2024, 1, 1), date(2024, 12, 31)),
            ("B1-303", "OWNER", "matrix.owner16@example.com", date(2025, 1, 1), date(2025, 6, 30)),
            ("B1-303", "TENANT", "matrix.tenant07@example.com", date(2025, 7, 1), None),
            ("B1-304", "TENANT", "matrix.tenant08@example.com", date(2024, 5, 1), None),
            ("B1-SH1", "TENANT", "matrix.tenant09@example.com", date(2024, 6, 1), None),
        ]

        User = get_user_model()
        for unit_key, occupancy_type, occupant_email, start_date, end_date in specs:
            occupant = None
            if occupant_email:
                occupant = User.objects.get(email=occupant_email)

            record = UnitOccupancy(
                unit=units[unit_key],
                occupant=occupant,
                occupancy_type=occupancy_type,
                start_date=start_date,
                end_date=end_date,
            )
            record.full_clean()
            record.save()

    def _upsert_structure(self, *, society, parent, structure_type, name, display_order):
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

    def _upsert_unit(self, structure, identifier, unit_type):
        unit, _ = Unit.objects.get_or_create(
            structure=structure,
            identifier=identifier,
            defaults={
                "unit_type": unit_type,
                "area_sqft": "1000.00",
                "chargeable_area_sqft": "1000.00",
                "is_active": True,
            },
        )
        update_fields = []
        if unit.unit_type != unit_type:
            unit.unit_type = unit_type
            update_fields.append("unit_type")
        if not unit.is_active:
            unit.is_active = True
            update_fields.append("is_active")
        if update_fields:
            unit.save(update_fields=update_fields)
        return unit

    def _matrix_phone(self, full_name):
        suffix = "".join(ch for ch in full_name if ch.isdigit())[-2:].rjust(2, "0")
        return f"90000000{suffix}"

    def _ensure_financial_year(self, *, society, name, start, end):
        fy, created = FinancialYear.objects.get_or_create(
            society=society,
            start_date=start,
            end_date=end,
            defaults={"name": name, "is_open": True},
        )
        if not created and (fy.name != name or not fy.is_open):
            fy.name = name
            fy.is_open = True
            fy.save(update_fields=["name", "is_open"])

    def _get_or_create_account(self, *, society, name, category_name, account_type):
        account = Account.objects.filter(society=society, name=name).first()
        if account:
            return account
        category = AccountCategory.objects.get(
            society=society,
            name=category_name,
            account_type=account_type,
        )
        account, _ = Account.objects.get_or_create(
            society=society,
            name=name,
            category=category,
            parent=None,
            defaults={"is_active": True},
        )
        return account
