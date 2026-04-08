from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from accounting.models import Account
from accounting.models import AccountCategory
from accounting.models import AccountingPeriod
from accounting.models import FinancialYear
from accounting.models import LedgerEntry
from accounting.models import Voucher
from accounting.services.standard_accounts import create_default_accounts_for_society
from accounting.services.standard_accounts import ensure_standard_categories
from members.models import Member
from members.models import Structure
from members.models import Unit
from societies.models import Society

SEED_PREFIX = "[TEST-SOCIETY-SEED]"


class Command(BaseCommand):
    help = "Seed Test Society with report-ready accounting data for phased rollout validation."

    def add_arguments(self, parser):
        parser.add_argument(
            "--society",
            default="Test Society",
            help="Society name to seed. Default: Test Society",
        )
        parser.add_argument(
            "--target-posted",
            type=int,
            default=100,
            help="Target number of posted seed vouchers (SEED_PREFIX scoped). Default: 100",
        )

    def handle(self, *args, **options):
        society_name = (options["society"] or "").strip() or "Test Society"
        target_posted = max(int(options.get("target_posted") or 100), 1)

        society, _ = Society.objects.get_or_create(name=society_name)
        ensure_standard_categories(society)
        create_default_accounts_for_society(society)

        structure = self._ensure_structure(society)
        unit_101 = self._ensure_unit(structure, "101")
        unit_102 = self._ensure_unit(structure, "102")
        unit_103 = self._ensure_unit(structure, "103")
        unit_104 = self._ensure_unit(structure, "104")

        fy = self._ensure_financial_year(
            society=society,
            name="FY 2025-26",
            start_date=date(2025, 4, 1),
            end_date=date(2026, 3, 31),
        )

        for dt in (
            date(2025, 12, 10),
            date(2026, 1, 20),
            date(2026, 2, 10),
            date(2026, 3, 5),
            date(2026, 3, 12),
            date(2026, 3, 18),
            date(2026, 3, 22),
            date(2026, 3, 25),
            date(2026, 3, 26),
            date(2026, 3, 27),
            date(2026, 2, 25),
            date(2026, 3, 28),
            date(2026, 3, 29),
            date(2026, 3, 30),
            date(2026, 3, 31),
        ):
            self._ensure_period_open(society=society, financial_year=fy, target_date=dt)

        accounts = self._resolve_accounts(society)
        self._ensure_report_members(
            society=society,
            receivable_account=accounts["maintenance_receivable"],
            units={
                "101": unit_101,
                "102": unit_102,
                "103": unit_103,
                "104": unit_104,
            },
        )

        seeded = []
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-001",
                voucher_type=Voucher.VoucherType.OPENING,
                voucher_date=date(2025, 12, 10),
                narration="Opening balances for reporting baseline",
                rows=[
                    {"account": accounts["cash"], "debit": Decimal("50000.00"), "credit": Decimal("0.00")},
                    {"account": accounts["bank"], "debit": Decimal("300000.00"), "credit": Decimal("0.00")},
                    {"account": accounts["opening_equity"], "debit": Decimal("0.00"), "credit": Decimal("350000.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-002",
                voucher_type=Voucher.VoucherType.GENERAL,
                voucher_date=date(2025, 12, 10),
                narration="Maintenance billed to Unit 101 (aged 90+ candidate)",
                rows=[
                    {
                        "account": accounts["maintenance_receivable"],
                        "unit": unit_101,
                        "debit": Decimal("20000.00"),
                        "credit": Decimal("0.00"),
                    },
                    {"account": accounts["maintenance_income"], "debit": Decimal("0.00"), "credit": Decimal("20000.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-003",
                voucher_type=Voucher.VoucherType.GENERAL,
                voucher_date=date(2026, 2, 10),
                narration="Maintenance billed to Unit 102 (aged 30-60 candidate)",
                rows=[
                    {
                        "account": accounts["maintenance_receivable"],
                        "unit": unit_102,
                        "debit": Decimal("15000.00"),
                        "credit": Decimal("0.00"),
                    },
                    {"account": accounts["maintenance_income"], "debit": Decimal("0.00"), "credit": Decimal("15000.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-004",
                voucher_type=Voucher.VoucherType.RECEIPT,
                voucher_date=date(2026, 3, 5),
                narration="Partial maintenance collection from Unit 101",
                payment_mode=Voucher.PaymentMode.UPI,
                reference_number="TSR-UPI-101-A",
                rows=[
                    {"account": accounts["bank"], "debit": Decimal("10000.00"), "credit": Decimal("0.00")},
                    {
                        "account": accounts["maintenance_receivable"],
                        "unit": unit_101,
                        "debit": Decimal("0.00"),
                        "credit": Decimal("10000.00"),
                    },
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-005",
                voucher_type=Voucher.VoucherType.GENERAL,
                voucher_date=date(2026, 1, 20),
                narration="Electricity vendor bill booked (AP aging base)",
                rows=[
                    {"account": accounts["electricity_expense"], "debit": Decimal("12000.00"), "credit": Decimal("0.00")},
                    {"account": accounts["vendor_payable"], "debit": Decimal("0.00"), "credit": Decimal("12000.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-006",
                voucher_type=Voucher.VoucherType.PAYMENT,
                voucher_date=date(2026, 3, 12),
                narration="Partial payment to electricity vendor",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number="TSR-BANK-AP-001",
                rows=[
                    {"account": accounts["vendor_payable"], "debit": Decimal("5000.00"), "credit": Decimal("0.00")},
                    {"account": accounts["bank"], "debit": Decimal("0.00"), "credit": Decimal("5000.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-007",
                voucher_type=Voucher.VoucherType.PAYMENT,
                voucher_date=date(2026, 3, 18),
                narration="Monthly salary payout",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number="TSR-BANK-PAYROLL-001",
                rows=[
                    {"account": accounts["salary_expense"], "debit": Decimal("8000.00"), "credit": Decimal("0.00")},
                    {"account": accounts["bank"], "debit": Decimal("0.00"), "credit": Decimal("8000.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-008",
                voucher_type=Voucher.VoucherType.RECEIPT,
                voucher_date=date(2026, 3, 22),
                narration="Maintenance collection from Unit 102 (bank matched)",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number="TSR-BANK-AR-102",
                rows=[
                    {"account": accounts["bank"], "debit": Decimal("7000.00"), "credit": Decimal("0.00")},
                    {
                        "account": accounts["maintenance_receivable"],
                        "unit": unit_102,
                        "debit": Decimal("0.00"),
                        "credit": Decimal("7000.00"),
                    },
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-009",
                voucher_type=Voucher.VoucherType.GENERAL,
                voucher_date=date(2026, 3, 25),
                narration="Bank reconciliation exception: unmatched debit moved to suspense",
                rows=[
                    {"account": accounts["bank_suspense"], "debit": Decimal("2500.00"), "credit": Decimal("0.00")},
                    {"account": accounts["bank"], "debit": Decimal("0.00"), "credit": Decimal("2500.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-010",
                voucher_type=Voucher.VoucherType.RECEIPT,
                voucher_date=date(2026, 3, 26),
                narration="Potential duplicate collection A for testing exception reporting",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number="TSR-DUP-UTR-9001",
                rows=[
                    {"account": accounts["bank"], "debit": Decimal("4000.00"), "credit": Decimal("0.00")},
                    {
                        "account": accounts["maintenance_receivable"],
                        "unit": unit_101,
                        "debit": Decimal("0.00"),
                        "credit": Decimal("4000.00"),
                    },
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-011",
                voucher_type=Voucher.VoucherType.RECEIPT,
                voucher_date=date(2026, 3, 27),
                narration="Potential duplicate collection B for testing exception reporting",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number="TSR-DUP-UTR-9001",
                rows=[
                    {"account": accounts["bank"], "debit": Decimal("4000.00"), "credit": Decimal("0.00")},
                    {
                        "account": accounts["maintenance_receivable"],
                        "unit": unit_101,
                        "debit": Decimal("0.00"),
                        "credit": Decimal("4000.00"),
                    },
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-012",
                voucher_type=Voucher.VoucherType.GENERAL,
                voucher_date=date(2026, 1, 5),
                narration="Maintenance billed to Unit 104 (aged 61-90 candidate)",
                rows=[
                    {
                        "account": accounts["maintenance_receivable"],
                        "unit": unit_104,
                        "debit": Decimal("18000.00"),
                        "credit": Decimal("0.00"),
                    },
                    {
                        "account": accounts["maintenance_income"],
                        "debit": Decimal("0.00"),
                        "credit": Decimal("18000.00"),
                    },
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-013",
                voucher_type=Voucher.VoucherType.GENERAL,
                voucher_date=date(2026, 3, 28),
                narration="Maintenance billed to Unit 103 (current aging candidate)",
                rows=[
                    {
                        "account": accounts["maintenance_receivable"],
                        "unit": unit_103,
                        "debit": Decimal("9000.00"),
                        "credit": Decimal("0.00"),
                    },
                    {
                        "account": accounts["maintenance_income"],
                        "debit": Decimal("0.00"),
                        "credit": Decimal("9000.00"),
                    },
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-014",
                voucher_type=Voucher.VoucherType.RECEIPT,
                voucher_date=date(2026, 3, 29),
                narration="Advance maintenance received from Unit 103 owner",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number="TSR-ADV-103-001",
                rows=[
                    {"account": accounts["bank"], "debit": Decimal("5000.00"), "credit": Decimal("0.00")},
                    {"account": accounts["advance_maintenance"], "debit": Decimal("0.00"), "credit": Decimal("5000.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-015",
                voucher_type=Voucher.VoucherType.ADJUSTMENT,
                voucher_date=date(2026, 3, 30),
                narration="Advance maintenance adjusted against Unit 103 receivable",
                rows=[
                    {"account": accounts["advance_maintenance"], "debit": Decimal("5000.00"), "credit": Decimal("0.00")},
                    {
                        "account": accounts["maintenance_receivable"],
                        "unit": unit_103,
                        "debit": Decimal("0.00"),
                        "credit": Decimal("5000.00"),
                    },
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-016",
                voucher_type=Voucher.VoucherType.PAYMENT,
                voucher_date=date(2026, 2, 25),
                narration="Building structure capital addition paid from bank",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number="TSR-CAPEX-001",
                rows=[
                    {"account": accounts["building_structure"], "debit": Decimal("60000.00"), "credit": Decimal("0.00")},
                    {"account": accounts["bank"], "debit": Decimal("0.00"), "credit": Decimal("60000.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-017",
                voucher_type=Voucher.VoucherType.RECEIPT,
                voucher_date=date(2026, 3, 24),
                narration="Bank interest received",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number="TSR-INT-001",
                rows=[
                    {"account": accounts["bank"], "debit": Decimal("1200.00"), "credit": Decimal("0.00")},
                    {"account": accounts["interest_income"], "debit": Decimal("0.00"), "credit": Decimal("1200.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-018",
                voucher_type=Voucher.VoucherType.PAYMENT,
                voucher_date=date(2026, 3, 24),
                narration="Bank charges debited by bank",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number="TSR-BCHG-001",
                rows=[
                    {"account": accounts["bank_charges"], "debit": Decimal("350.00"), "credit": Decimal("0.00")},
                    {"account": accounts["bank"], "debit": Decimal("0.00"), "credit": Decimal("350.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-019",
                voucher_type=Voucher.VoucherType.GENERAL,
                voucher_date=date(2025, 12, 20),
                narration="Lift maintenance vendor bill booked (aged 90+ AP candidate)",
                rows=[
                    {"account": accounts["lift_maintenance"], "debit": Decimal("4500.00"), "credit": Decimal("0.00")},
                    {"account": accounts["vendor_payable"], "debit": Decimal("0.00"), "credit": Decimal("4500.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-020",
                voucher_type=Voucher.VoucherType.PAYMENT,
                voucher_date=date(2026, 3, 31),
                narration="Partial settlement of lift maintenance payable",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number="TSR-BANK-AP-002",
                rows=[
                    {"account": accounts["vendor_payable"], "debit": Decimal("2000.00"), "credit": Decimal("0.00")},
                    {"account": accounts["bank"], "debit": Decimal("0.00"), "credit": Decimal("2000.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-021",
                voucher_type=Voucher.VoucherType.RECEIPT,
                voucher_date=date(2026, 3, 30),
                narration="Tenant security deposit received for Unit 104",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number="TSR-DEP-104-001",
                rows=[
                    {"account": accounts["bank"], "debit": Decimal("15000.00"), "credit": Decimal("0.00")},
                    {"account": accounts["security_deposit_members"], "debit": Decimal("0.00"), "credit": Decimal("15000.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-022",
                voucher_type=Voucher.VoucherType.RECEIPT,
                voucher_date=date(2026, 3, 31),
                narration="Cash collection from Unit 104 tenant",
                payment_mode=Voucher.PaymentMode.CASH,
                rows=[
                    {"account": accounts["cash"], "debit": Decimal("3000.00"), "credit": Decimal("0.00")},
                    {
                        "account": accounts["maintenance_receivable"],
                        "unit": unit_104,
                        "debit": Decimal("0.00"),
                        "credit": Decimal("3000.00"),
                    },
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-023",
                voucher_type=Voucher.VoucherType.RECEIPT,
                voucher_date=date(2026, 3, 31),
                narration="Cheque receipt from Unit 102 owner",
                payment_mode=Voucher.PaymentMode.CHEQUE,
                reference_number="TSR-CHQ-102-001",
                rows=[
                    {"account": accounts["bank"], "debit": Decimal("2500.00"), "credit": Decimal("0.00")},
                    {
                        "account": accounts["maintenance_receivable"],
                        "unit": unit_102,
                        "debit": Decimal("0.00"),
                        "credit": Decimal("2500.00"),
                    },
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-024",
                voucher_type=Voucher.VoucherType.PAYMENT,
                voucher_date=date(2026, 3, 31),
                narration="Card payment for software expense",
                payment_mode=Voucher.PaymentMode.CARD,
                reference_number="TSR-CARD-001",
                rows=[
                    {"account": accounts["software_expense"], "debit": Decimal("1800.00"), "credit": Decimal("0.00")},
                    {"account": accounts["bank"], "debit": Decimal("0.00"), "credit": Decimal("1800.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-025",
                voucher_type=Voucher.VoucherType.PAYMENT,
                voucher_date=date(2026, 3, 31),
                narration="Other mode payment for legal fees",
                payment_mode=Voucher.PaymentMode.OTHER,
                reference_number="TSR-OTH-001",
                rows=[
                    {"account": accounts["legal_fees"], "debit": Decimal("2200.00"), "credit": Decimal("0.00")},
                    {"account": accounts["bank"], "debit": Decimal("0.00"), "credit": Decimal("2200.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher_with_reversal(
                society=society,
                code="TSR-026",
                voucher_type=Voucher.VoucherType.GENERAL,
                voucher_date=date(2026, 3, 31),
                narration="Miscellaneous expense later reversed",
                rows=[
                    {"account": accounts["misc_expense"], "debit": Decimal("999.00"), "credit": Decimal("0.00")},
                    {"account": accounts["cash"], "debit": Decimal("0.00"), "credit": Decimal("999.00")},
                ],
                reversal_code="TSR-026-R",
                reversal_narration="Auto reversal of miscellaneous expense test voucher",
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-027",
                voucher_type=Voucher.VoucherType.PAYMENT,
                voucher_date=date(2026, 3, 31),
                narration="Tenant security deposit refund for Unit 104",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number="TSR-DEP-REF-104",
                rows=[
                    {"account": accounts["security_deposit_members"], "debit": Decimal("5000.00"), "credit": Decimal("0.00")},
                    {"account": accounts["bank"], "debit": Decimal("0.00"), "credit": Decimal("5000.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-028",
                voucher_type=Voucher.VoucherType.GENERAL,
                voucher_date=date(2026, 3, 31),
                narration="Audit fees accrued but not yet paid",
                rows=[
                    {"account": accounts["audit_fees"], "debit": Decimal("6000.00"), "credit": Decimal("0.00")},
                    {"account": accounts["vendor_payable"], "debit": Decimal("0.00"), "credit": Decimal("6000.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-029",
                voucher_type=Voucher.VoucherType.GENERAL,
                voucher_date=date(2026, 3, 31),
                narration="Cash transferred and deposited into main bank",
                rows=[
                    {"account": accounts["bank"], "debit": Decimal("10000.00"), "credit": Decimal("0.00")},
                    {"account": accounts["cash"], "debit": Decimal("0.00"), "credit": Decimal("10000.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-030",
                voucher_type=Voucher.VoucherType.ADJUSTMENT,
                voucher_date=date(2026, 3, 31),
                narration="Small receivable written off for Unit 103",
                rows=[
                    {"account": accounts["misc_expense"], "debit": Decimal("250.00"), "credit": Decimal("0.00")},
                    {
                        "account": accounts["maintenance_receivable"],
                        "unit": unit_103,
                        "debit": Decimal("0.00"),
                        "credit": Decimal("250.00"),
                    },
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-031",
                voucher_type=Voucher.VoucherType.RECEIPT,
                voucher_date=date(2026, 3, 31),
                narration="Additional maintenance collection from Unit 104 via UPI",
                payment_mode=Voucher.PaymentMode.UPI,
                reference_number="TSR-UPI-104-B",
                rows=[
                    {"account": accounts["bank"], "debit": Decimal("6200.00"), "credit": Decimal("0.00")},
                    {
                        "account": accounts["maintenance_receivable"],
                        "unit": unit_104,
                        "debit": Decimal("0.00"),
                        "credit": Decimal("6200.00"),
                    },
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-032",
                voucher_type=Voucher.VoucherType.PAYMENT,
                voucher_date=date(2026, 3, 31),
                narration="Additional vendor settlement from bank",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number="TSR-BANK-AP-003",
                rows=[
                    {"account": accounts["vendor_payable"], "debit": Decimal("3200.00"), "credit": Decimal("0.00")},
                    {"account": accounts["bank"], "debit": Decimal("0.00"), "credit": Decimal("3200.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-033",
                voucher_type=Voucher.VoucherType.GENERAL,
                voucher_date=date(2026, 3, 31),
                narration="Bank reconciliation exception: unmatched credit parked in suspense",
                rows=[
                    {"account": accounts["bank"], "debit": Decimal("900.00"), "credit": Decimal("0.00")},
                    {"account": accounts["bank_suspense"], "debit": Decimal("0.00"), "credit": Decimal("900.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-034",
                voucher_type=Voucher.VoucherType.GENERAL,
                voucher_date=date(2026, 3, 31),
                narration="Bank reconciliation exception: unmatched debit parked in suspense",
                rows=[
                    {"account": accounts["bank_suspense"], "debit": Decimal("1400.00"), "credit": Decimal("0.00")},
                    {"account": accounts["bank"], "debit": Decimal("0.00"), "credit": Decimal("1400.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-035",
                voucher_type=Voucher.VoucherType.ADJUSTMENT,
                voucher_date=date(2026, 3, 31),
                narration="Partial suspense resolution after bank confirmation",
                rows=[
                    {"account": accounts["bank"], "debit": Decimal("700.00"), "credit": Decimal("0.00")},
                    {"account": accounts["bank_suspense"], "debit": Decimal("0.00"), "credit": Decimal("700.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-036",
                voucher_type=Voucher.VoucherType.PAYMENT,
                voucher_date=date(2026, 3, 31),
                narration="Additional bank charges for month-end reconciliation",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number="TSR-BCHG-002",
                rows=[
                    {"account": accounts["bank_charges"], "debit": Decimal("125.00"), "credit": Decimal("0.00")},
                    {"account": accounts["bank"], "debit": Decimal("0.00"), "credit": Decimal("125.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-037",
                voucher_type=Voucher.VoucherType.RECEIPT,
                voucher_date=date(2026, 3, 31),
                narration="Additional bank interest credit",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number="TSR-INT-002",
                rows=[
                    {"account": accounts["bank"], "debit": Decimal("480.00"), "credit": Decimal("0.00")},
                    {"account": accounts["interest_income"], "debit": Decimal("0.00"), "credit": Decimal("480.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-038",
                voucher_type=Voucher.VoucherType.BILL,
                voucher_date=date(2026, 3, 31),
                narration="GST maintenance billing for Unit 102",
                rows=[
                    {
                        "account": accounts["maintenance_receivable"],
                        "unit": unit_102,
                        "debit": Decimal("1180.00"),
                        "credit": Decimal("0.00"),
                    },
                    {
                        "account": accounts["maintenance_income"],
                        "debit": Decimal("0.00"),
                        "credit": Decimal("1000.00"),
                    },
                    {
                        "account": accounts["output_cgst"],
                        "debit": Decimal("0.00"),
                        "credit": Decimal("90.00"),
                    },
                    {
                        "account": accounts["output_sgst"],
                        "debit": Decimal("0.00"),
                        "credit": Decimal("90.00"),
                    },
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-039",
                voucher_type=Voucher.VoucherType.BILL,
                voucher_date=date(2026, 3, 31),
                narration="GST expense booking for lift maintenance",
                rows=[
                    {"account": accounts["lift_maintenance"], "debit": Decimal("1000.00"), "credit": Decimal("0.00")},
                    {"account": accounts["input_cgst"], "debit": Decimal("90.00"), "credit": Decimal("0.00")},
                    {"account": accounts["input_sgst"], "debit": Decimal("90.00"), "credit": Decimal("0.00")},
                    {"account": accounts["vendor_payable"], "debit": Decimal("0.00"), "credit": Decimal("1180.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-040",
                voucher_type=Voucher.VoucherType.GENERAL,
                voucher_date=date(2026, 3, 31),
                narration="TDS deducted on professional fees",
                rows=[
                    {"account": accounts["audit_fees"], "debit": Decimal("300.00"), "credit": Decimal("0.00")},
                    {"account": accounts["tds_payable"], "debit": Decimal("0.00"), "credit": Decimal("300.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-041",
                voucher_type=Voucher.VoucherType.PAYMENT,
                voucher_date=date(2026, 3, 31),
                narration="TDS payable remitted to authority",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number="TSR-TDS-PAY-001",
                rows=[
                    {"account": accounts["tds_payable"], "debit": Decimal("120.00"), "credit": Decimal("0.00")},
                    {"account": accounts["bank"], "debit": Decimal("0.00"), "credit": Decimal("120.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-042",
                voucher_type=Voucher.VoucherType.GENERAL,
                voucher_date=date(2026, 3, 31),
                narration="TDS receivable recognized on bank interest",
                rows=[
                    {"account": accounts["tds_receivable"], "debit": Decimal("75.00"), "credit": Decimal("0.00")},
                    {"account": accounts["interest_income"], "debit": Decimal("0.00"), "credit": Decimal("75.00")},
                ],
            )
        )
        seeded.append(
            self._ensure_seed_voucher(
                society=society,
                code="TSR-043",
                voucher_type=Voucher.VoucherType.RECEIPT,
                voucher_date=date(2026, 3, 31),
                narration="TDS receivable adjusted via bank credit",
                payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                reference_number="TSR-TDS-ADJ-001",
                rows=[
                    {"account": accounts["bank"], "debit": Decimal("75.00"), "credit": Decimal("0.00")},
                    {"account": accounts["tds_receivable"], "debit": Decimal("0.00"), "credit": Decimal("75.00")},
                ],
            )
        )
        bulk_seeded = self._ensure_bulk_seed_vouchers(
            society=society,
            target_posted=target_posted,
            accounts=accounts,
            units=[unit_101, unit_102, unit_103, unit_104],
        )
        seeded.extend(bulk_seeded)
        draft_voucher, draft_created = self._ensure_seed_draft_voucher(
            society=society,
            code="TSR-DRAFT-001",
            voucher_type=Voucher.VoucherType.GENERAL,
            voucher_date=date(2026, 3, 31),
            narration="Draft voucher should be excluded from reports",
            rows=[
                {"account": accounts["misc_expense"], "debit": Decimal("1234.00"), "credit": Decimal("0.00")},
                {"account": accounts["cash"], "debit": Decimal("0.00"), "credit": Decimal("1234.00")},
            ],
        )

        created_count = sum(1 for item in seeded if item[1])
        reused_count = len(seeded) - created_count
        vouchers_total = Voucher.objects.filter(society=society, narration__startswith=SEED_PREFIX).count()
        draft_total = Voucher.objects.filter(
            society=society,
            narration__startswith=f"{SEED_PREFIX} TSR-DRAFT",
            posted_at__isnull=True,
        ).count()

        self.stdout.write(self.style.SUCCESS("Test Society report dataset is ready."))
        self.stdout.write(f"Society: {society.name} (id={society.id})")
        self.stdout.write(f"Financial Year: {fy.name} ({fy.start_date} to {fy.end_date})")
        self.stdout.write(f"Target posted vouchers: {target_posted}")
        self.stdout.write(f"Seed vouchers created now: {created_count}")
        self.stdout.write(f"Seed vouchers reused: {reused_count}")
        self.stdout.write(f"Seed vouchers total: {vouchers_total}")
        self.stdout.write(f"Draft report-seed vouchers: {draft_total}")

    def _ensure_structure(self, society):
        structure, _ = Structure.objects.get_or_create(
            society=society,
            parent=None,
            structure_type=Structure.StructureType.BUILDING,
            name="Building A",
            defaults={"display_order": 1},
        )
        return structure

    def _ensure_unit(self, structure, identifier):
        unit, _ = Unit.objects.get_or_create(
            structure=structure,
            identifier=identifier,
            defaults={
                "unit_type": Unit.UnitType.FLAT,
                "area_sqft": Decimal("900.00"),
                "chargeable_area_sqft": Decimal("900.00"),
                "is_active": True,
            },
        )
        return unit

    def _ensure_report_members(self, *, society, receivable_account, units):
        owner_specs = [
            ("reports.owner101@example.com", "Report Owner 101", "101"),
            ("reports.owner102@example.com", "Report Owner 102", "102"),
            ("reports.owner103@example.com", "Report Owner 103", "103"),
            ("reports.owner104@example.com", "Report Owner 104", "104"),
        ]
        tenant_specs = [
            ("reports.tenant104@example.com", "Report Tenant 104", "104"),
        ]

        for email, full_name, unit_key in owner_specs:
            self._ensure_member(
                society=society,
                unit=units[unit_key],
                email=email,
                full_name=full_name,
                role=Member.MemberRole.OWNER,
                receivable_account=receivable_account,
            )

        for email, full_name, unit_key in tenant_specs:
            self._ensure_member(
                society=society,
                unit=units[unit_key],
                email=email,
                full_name=full_name,
                role=Member.MemberRole.TENANT,
                receivable_account=receivable_account,
            )

    def _ensure_member(
        self,
        *,
        society,
        unit,
        email,
        full_name,
        role,
        receivable_account,
    ):
        user_model = get_user_model()
        user, _ = user_model.objects.get_or_create(
            email=email,
            defaults={"name": full_name, "is_active": True},
        )

        user_update_fields = []
        if getattr(user, "name", "") != full_name:
            user.name = full_name
            user_update_fields.append("name")
        if not user.is_active:
            user.is_active = True
            user_update_fields.append("is_active")
        if user_update_fields:
            user.save(update_fields=user_update_fields)

        member, _ = Member.objects.get_or_create(
            society=society,
            unit=unit,
            full_name=full_name,
            role=role,
            defaults={
                "user": user,
                "email": email,
                "phone": self._member_phone(unit.identifier),
                "status": Member.MemberStatus.ACTIVE,
                "start_date": date(2025, 4, 1),
                "receivable_account": receivable_account,
            },
        )

        member_update_fields = []
        if member.user_id != user.id:
            member.user = user
            member_update_fields.append("user")
        if member.email != email:
            member.email = email
            member_update_fields.append("email")
        phone = self._member_phone(unit.identifier)
        if member.phone != phone:
            member.phone = phone
            member_update_fields.append("phone")
        if member.status != Member.MemberStatus.ACTIVE:
            member.status = Member.MemberStatus.ACTIVE
            member_update_fields.append("status")
        if member.start_date != date(2025, 4, 1):
            member.start_date = date(2025, 4, 1)
            member_update_fields.append("start_date")
        if member.end_date is not None:
            member.end_date = None
            member_update_fields.append("end_date")
        if member.receivable_account_id != receivable_account.id:
            member.receivable_account = receivable_account
            member_update_fields.append("receivable_account")
        if member_update_fields:
            member.save(update_fields=member_update_fields)

    def _member_phone(self, identifier):
        digits = "".join(ch for ch in identifier if ch.isdigit())[-3:].rjust(3, "0")
        return f"9100000{digits}"

    def _ensure_financial_year(self, *, society, name, start_date, end_date):
        fy, created = FinancialYear.objects.get_or_create(
            society=society,
            start_date=start_date,
            end_date=end_date,
            defaults={"name": name, "is_open": True},
        )
        if not created:
            changed = False
            if fy.name != name:
                fy.name = name
                changed = True
            if not fy.is_open:
                fy.is_open = True
                changed = True
            if changed:
                fy.save(update_fields=["name", "is_open"])
        return fy

    def _ensure_period_open(self, *, society, financial_year, target_date):
        period = AccountingPeriod.objects.filter(
            society=society,
            financial_year=financial_year,
            start_date__lte=target_date,
            end_date__gte=target_date,
        ).first()
        if period and not period.is_open:
            period.is_open = True
            period.save(update_fields=["is_open"])

    def _resolve_accounts(self, society):
        accounts = {
            "cash": self._get_or_create_account(
                society=society,
                preferred_names=["Cash in Hand", "Cash"],
                fallback_name="Cash in Hand",
                category_name="Bank & Cash",
                account_type=AccountCategory.AccountType.ASSET,
            ),
            "bank": self._get_or_create_account(
                society=society,
                preferred_names=["Bank Account – Main", "Bank Account - Main", "Bank"],
                fallback_name="Bank Account - Main",
                category_name="Bank & Cash",
                account_type=AccountCategory.AccountType.ASSET,
            ),
            "maintenance_receivable": self._get_or_create_account(
                society=society,
                preferred_names=["Maintenance Receivable"],
                fallback_name="Maintenance Receivable",
                category_name="Member Receivables",
                account_type=AccountCategory.AccountType.ASSET,
            ),
            "maintenance_income": self._get_or_create_account(
                society=society,
                preferred_names=["Maintenance Charges", "Maintenance Income"],
                fallback_name="Maintenance Charges",
                category_name="Maintenance Charges",
                account_type=AccountCategory.AccountType.INCOME,
            ),
            "electricity_expense": self._get_or_create_account(
                society=society,
                preferred_names=["Electricity Expense"],
                fallback_name="Electricity Expense",
                category_name="Utility Expenses",
                account_type=AccountCategory.AccountType.EXPENSE,
            ),
            "salary_expense": self._get_or_create_account(
                society=society,
                preferred_names=["Salary Expense"],
                fallback_name="Salary Expense",
                category_name="Staff Salary & Wages",
                account_type=AccountCategory.AccountType.EXPENSE,
            ),
            "opening_equity": self._get_or_create_account(
                society=society,
                preferred_names=["Opening Balance Fund", "Opening Balance Equity"],
                fallback_name="Opening Balance Fund",
                category_name="Opening Balance Fund",
                account_type=AccountCategory.AccountType.EQUITY,
            ),
            "vendor_payable": self._get_or_create_account(
                society=society,
                preferred_names=["Vendor Payable"],
                fallback_name="Vendor Payable",
                category_name="Vendor Payables",
                account_type=AccountCategory.AccountType.LIABILITY,
            ),
            "advance_maintenance": self._get_or_create_account(
                society=society,
                preferred_names=["Advance Maintenance"],
                fallback_name="Advance Maintenance",
                category_name="Member Deposits",
                account_type=AccountCategory.AccountType.LIABILITY,
            ),
            "building_structure": self._get_or_create_account(
                society=society,
                preferred_names=["Building Structure"],
                fallback_name="Building Structure",
                category_name="Fixed Assets",
                account_type=AccountCategory.AccountType.ASSET,
            ),
            "interest_income": self._get_or_create_account(
                society=society,
                preferred_names=["Interest Income – Bank", "Interest Income - Bank"],
                fallback_name="Interest Income - Bank",
                category_name="Interest Income",
                account_type=AccountCategory.AccountType.INCOME,
            ),
            "bank_charges": self._get_or_create_account(
                society=society,
                preferred_names=["Bank Charges"],
                fallback_name="Bank Charges",
                category_name="Bank Charges",
                account_type=AccountCategory.AccountType.EXPENSE,
            ),
            "software_expense": self._get_or_create_account(
                society=society,
                preferred_names=["Software Expense"],
                fallback_name="Software Expense",
                category_name="Administrative Expenses",
                account_type=AccountCategory.AccountType.EXPENSE,
            ),
            "legal_fees": self._get_or_create_account(
                society=society,
                preferred_names=["Legal Fees"],
                fallback_name="Legal Fees",
                category_name="Professional Charges",
                account_type=AccountCategory.AccountType.EXPENSE,
            ),
            "audit_fees": self._get_or_create_account(
                society=society,
                preferred_names=["Audit Fees"],
                fallback_name="Audit Fees",
                category_name="Professional Charges",
                account_type=AccountCategory.AccountType.EXPENSE,
            ),
            "lift_maintenance": self._get_or_create_account(
                society=society,
                preferred_names=["Lift Maintenance"],
                fallback_name="Lift Maintenance",
                category_name="Repair & Maintenance Expenses",
                account_type=AccountCategory.AccountType.EXPENSE,
            ),
            "misc_expense": self._get_or_create_account(
                society=society,
                preferred_names=["Miscellaneous Expense"],
                fallback_name="Miscellaneous Expense",
                category_name="Miscellaneous",
                account_type=AccountCategory.AccountType.EXPENSE,
            ),
            "security_deposit_members": self._get_or_create_account(
                society=society,
                preferred_names=["Security Deposit – Members", "Security Deposit - Members"],
                fallback_name="Security Deposit - Members",
                category_name="Member Deposits",
                account_type=AccountCategory.AccountType.LIABILITY,
            ),
            "bank_suspense": self._get_or_create_account(
                society=society,
                preferred_names=["Bank Suspense - Unreconciled"],
                fallback_name="Bank Suspense - Unreconciled",
                category_name="Current Assets",
                account_type=AccountCategory.AccountType.ASSET,
            ),
            "output_cgst": self._get_or_create_account(
                society=society,
                preferred_names=["Output CGST"],
                fallback_name="Output CGST",
                category_name="Statutory Liabilities",
                account_type=AccountCategory.AccountType.LIABILITY,
            ),
            "output_sgst": self._get_or_create_account(
                society=society,
                preferred_names=["Output SGST"],
                fallback_name="Output SGST",
                category_name="Statutory Liabilities",
                account_type=AccountCategory.AccountType.LIABILITY,
            ),
            "input_cgst": self._get_or_create_account(
                society=society,
                preferred_names=["Input CGST"],
                fallback_name="Input CGST",
                category_name="Current Assets",
                account_type=AccountCategory.AccountType.ASSET,
            ),
            "input_sgst": self._get_or_create_account(
                society=society,
                preferred_names=["Input SGST"],
                fallback_name="Input SGST",
                category_name="Current Assets",
                account_type=AccountCategory.AccountType.ASSET,
            ),
            "tds_payable": self._get_or_create_account(
                society=society,
                preferred_names=["TDS Payable"],
                fallback_name="TDS Payable",
                category_name="Statutory Liabilities",
                account_type=AccountCategory.AccountType.LIABILITY,
            ),
            "tds_receivable": self._get_or_create_account(
                society=society,
                preferred_names=["TDS Receivable"],
                fallback_name="TDS Receivable",
                category_name="Current Assets",
                account_type=AccountCategory.AccountType.ASSET,
            ),
        }
        self._ensure_gst_account_metadata(accounts["output_cgst"], "OUTPUT")
        self._ensure_gst_account_metadata(accounts["output_sgst"], "OUTPUT")
        self._ensure_gst_account_metadata(accounts["input_cgst"], "INPUT")
        self._ensure_gst_account_metadata(accounts["input_sgst"], "INPUT")
        return accounts

    def _ensure_gst_account_metadata(self, account, gst_type):
        update_fields = []
        if not account.is_gst:
            account.is_gst = True
            update_fields.append("is_gst")
        if account.gst_type != gst_type:
            account.gst_type = gst_type
            update_fields.append("gst_type")
        if account.sub_type != "GST":
            account.sub_type = "GST"
            update_fields.append("sub_type")
        if update_fields:
            account.save(update_fields=update_fields)

    def _get_or_create_account(
        self,
        *,
        society,
        preferred_names,
        fallback_name,
        category_name,
        account_type,
    ):
        for candidate in preferred_names:
            account = Account.objects.filter(society=society, name=candidate).first()
            if account:
                return account

        category = AccountCategory.objects.get(
            society=society,
            name=category_name,
            account_type=account_type,
        )
        account, _ = Account.objects.get_or_create(
            society=society,
            name=fallback_name,
            category=category,
            parent=None,
            defaults={"is_active": True},
        )
        return account

    def _ensure_seed_voucher(
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
        full_narration = f"{SEED_PREFIX} {code} {narration}"
        existing = Voucher.objects.filter(
            society=society,
            voucher_type=voucher_type,
            voucher_date=voucher_date,
            narration=full_narration,
        ).order_by("-id").first()

        if existing and existing.posted_at:
            return existing, False

        if existing and not existing.posted_at:
            existing.delete()

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
                    debit=row["debit"],
                    credit=row["credit"],
                )
            voucher.post()

        return voucher, True

    def _ensure_seed_voucher_with_reversal(
        self,
        *,
        society,
        code,
        voucher_type,
        voucher_date,
        narration,
        rows,
        reversal_code,
        reversal_narration,
    ):
        original, original_created = self._ensure_seed_voucher(
            society=society,
            code=code,
            voucher_type=voucher_type,
            voucher_date=voucher_date,
            narration=narration,
            rows=rows,
        )

        reversal_full_narration = f"{SEED_PREFIX} {reversal_code} {reversal_narration}"
        reversal = Voucher.objects.filter(
            society=society,
            reversal_of=original,
            narration=reversal_full_narration,
        ).first()
        if reversal and reversal.posted_at:
            return reversal, original_created
        if reversal and not reversal.posted_at:
            reversal.delete()

        with transaction.atomic():
            reversal = Voucher.objects.create(
                society=society,
                voucher_type=original.voucher_type,
                voucher_date=original.voucher_date,
                narration=reversal_full_narration,
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

    def _ensure_seed_draft_voucher(
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
        full_narration = f"{SEED_PREFIX} {code} {narration}"
        existing = Voucher.objects.filter(
            society=society,
            voucher_type=voucher_type,
            voucher_date=voucher_date,
            narration=full_narration,
            posted_at__isnull=True,
        ).order_by("-id").first()
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
                    debit=row["debit"],
                    credit=row["credit"],
                )
        return voucher, True

    def _ensure_bulk_seed_vouchers(self, *, society, target_posted, accounts, units):
        current_posted = Voucher.objects.filter(
            society=society,
            narration__startswith=SEED_PREFIX,
            posted_at__isnull=False,
        ).count()
        if current_posted >= target_posted:
            return []

        seeded = []
        for index in range(current_posted + 1, target_posted + 1):
            unit = units[(index - 1) % len(units)]
            amount = Decimal(str(900 + ((index * 37) % 1200)))
            code = f"TSR-BULK-{index:03d}"
            voucher_date = date(2026, 3, 31)
            pattern = index % 5

            if pattern == 0:
                seeded.append(
                    self._ensure_seed_voucher(
                        society=society,
                        code=code,
                        voucher_type=Voucher.VoucherType.GENERAL,
                        voucher_date=voucher_date,
                        narration=f"Bulk maintenance billing for Unit {unit.identifier}",
                        rows=[
                            {
                                "account": accounts["maintenance_receivable"],
                                "unit": unit,
                                "debit": amount,
                                "credit": Decimal("0.00"),
                            },
                            {
                                "account": accounts["maintenance_income"],
                                "debit": Decimal("0.00"),
                                "credit": amount,
                            },
                        ],
                    )
                )
            elif pattern == 1:
                seeded.append(
                    self._ensure_seed_voucher(
                        society=society,
                        code=code,
                        voucher_type=Voucher.VoucherType.RECEIPT,
                        voucher_date=voucher_date,
                        narration=f"Bulk member collection for Unit {unit.identifier}",
                        payment_mode=Voucher.PaymentMode.UPI,
                        reference_number=f"TSR-BULK-UTR-{index:03d}",
                        rows=[
                            {
                                "account": accounts["bank"],
                                "debit": amount,
                                "credit": Decimal("0.00"),
                            },
                            {
                                "account": accounts["maintenance_receivable"],
                                "unit": unit,
                                "debit": Decimal("0.00"),
                                "credit": amount,
                            },
                        ],
                    )
                )
            elif pattern == 2:
                seeded.append(
                    self._ensure_seed_voucher(
                        society=society,
                        code=code,
                        voucher_type=Voucher.VoucherType.PAYMENT,
                        voucher_date=voucher_date,
                        narration="Bulk utility payment",
                        payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                        reference_number=f"TSR-BULK-PAY-{index:03d}",
                        rows=[
                            {
                                "account": accounts["electricity_expense"],
                                "debit": amount,
                                "credit": Decimal("0.00"),
                            },
                            {
                                "account": accounts["bank"],
                                "debit": Decimal("0.00"),
                                "credit": amount,
                            },
                        ],
                    )
                )
            elif pattern == 3:
                seeded.append(
                    self._ensure_seed_voucher(
                        society=society,
                        code=code,
                        voucher_type=Voucher.VoucherType.GENERAL,
                        voucher_date=voucher_date,
                        narration="Bulk vendor bill booking",
                        rows=[
                            {
                                "account": accounts["lift_maintenance"],
                                "debit": amount,
                                "credit": Decimal("0.00"),
                            },
                            {
                                "account": accounts["vendor_payable"],
                                "debit": Decimal("0.00"),
                                "credit": amount,
                            },
                        ],
                    )
                )
            else:
                seeded.append(
                    self._ensure_seed_voucher(
                        society=society,
                        code=code,
                        voucher_type=Voucher.VoucherType.PAYMENT,
                        voucher_date=voucher_date,
                        narration="Bulk vendor settlement",
                        payment_mode=Voucher.PaymentMode.BANK_TRANSFER,
                        reference_number=f"TSR-BULK-VND-{index:03d}",
                        rows=[
                            {
                                "account": accounts["vendor_payable"],
                                "debit": amount,
                                "credit": Decimal("0.00"),
                            },
                            {
                                "account": accounts["bank"],
                                "debit": Decimal("0.00"),
                                "credit": amount,
                            },
                        ],
                    )
                )
        return seeded
