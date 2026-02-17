from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from accounting.models import Account
from accounting.models import AccountCategory
from accounting.models import AccountingPeriod
from accounting.models import FinancialYear
from housing.models import Bill
from housing.models import ChargeTemplate
from housing.models import Member
from housing.models import Society
from housing.models import Structure
from housing.models import Unit
from housing.models import ReminderLog
from housing.services.billing import apply_late_fees
from housing.services.billing import generate_bills_for_period
from housing.services.outstanding import build_member_outstanding
from housing.services.receipts import post_receipt_for_bill
from housing.services.reminders import schedule_payment_reminders


pytestmark = pytest.mark.django_db


def _create_society_context():
    society = Society.objects.create(name="Phase 2 Society")
    fy = FinancialYear.objects.get(
        society=society,
        start_date=date(2025, 4, 1),
        end_date=date(2026, 3, 31),
    )
    AccountingPeriod.objects.filter(
        society=society,
        financial_year=fy,
        start_date=date(2025, 4, 1),
        end_date=date(2025, 4, 30),
    ).update(is_open=True)

    asset_cat = AccountCategory.objects.create(
        society=society,
        name="Cash and Bank",
        account_type=AccountCategory.AccountType.ASSET,
    )
    income_cat = AccountCategory.objects.create(
        society=society,
        name="Income",
        account_type=AccountCategory.AccountType.INCOME,
    )

    receivable = Account.objects.create(
        society=society,
        name="Maintenance Receivable",
        category=asset_cat,
    )
    cash_bank = Account.objects.create(
        society=society,
        name="Bank Account",
        category=asset_cat,
    )
    income_main = Account.objects.create(
        society=society,
        name="Maintenance Income",
        category=income_cat,
    )
    income_sinking = Account.objects.create(
        society=society,
        name="Sinking Fund Income",
        category=income_cat,
    )

    structure = Structure.objects.create(
        society=society,
        structure_type=Structure.StructureType.BUILDING,
        name="A",
    )
    unit = Unit.objects.create(
        structure=structure,
        unit_type=Unit.UnitType.FLAT,
        identifier="101",
    )
    member = Member.objects.create(
        society=society,
        unit=unit,
        full_name="Member One",
        role=Member.MemberRole.OWNER,
        status=Member.MemberStatus.ACTIVE,
        start_date=date(2025, 4, 1),
        receivable_account=receivable,
    )
    return {
        "society": society,
        "asset_cat": asset_cat,
        "income_cat": income_cat,
        "receivable": receivable,
        "cash_bank": cash_bank,
        "income_main": income_main,
        "income_sinking": income_sinking,
        "member": member,
    }


def _create_templates(ctx):
    t1 = ChargeTemplate.objects.create(
        society=ctx["society"],
        name="Maintenance",
        amount=Decimal("1000.00"),
        frequency=ChargeTemplate.Frequency.MONTHLY,
        due_days=10,
        late_fee_percent=Decimal("10.00"),
        income_account=ctx["income_main"],
        receivable_account=ctx["receivable"],
    )
    t2 = ChargeTemplate.objects.create(
        society=ctx["society"],
        name="Sinking Fund",
        amount=Decimal("300.00"),
        frequency=ChargeTemplate.Frequency.MONTHLY,
        due_days=15,
        late_fee_percent=Decimal("0.00"),
        income_account=ctx["income_sinking"],
        receivable_account=ctx["receivable"],
    )
    return t1, t2


def test_billing_generation_creates_one_bill_with_multiple_lines_and_posted_voucher():
    ctx = _create_society_context()
    _create_templates(ctx)

    bills = generate_bills_for_period(
        society=ctx["society"],
        period_start=date(2025, 4, 1),
        period_end=date(2025, 4, 30),
        bill_date=date(2025, 4, 5),
    )

    assert len(bills) == 1
    bill = bills[0]
    assert bill.lines.count() == 2
    assert bill.total_amount == Decimal("1300.00")
    assert bill.voucher is not None
    assert bill.voucher.posted_at is not None
    assert bill.voucher.entries.count() == 4


def test_billing_generation_is_idempotent_for_same_member_period():
    ctx = _create_society_context()
    _create_templates(ctx)

    first = generate_bills_for_period(
        society=ctx["society"],
        period_start=date(2025, 4, 1),
        period_end=date(2025, 4, 30),
        bill_date=date(2025, 4, 5),
    )
    second = generate_bills_for_period(
        society=ctx["society"],
        period_start=date(2025, 4, 1),
        period_end=date(2025, 4, 30),
        bill_date=date(2025, 4, 5),
    )

    assert len(first) == 1
    assert second == []
    assert Bill.objects.filter(society=ctx["society"]).count() == 1


def test_receipt_posting_auto_creates_voucher_and_updates_bill_status():
    ctx = _create_society_context()
    _create_templates(ctx)
    bill = generate_bills_for_period(
        society=ctx["society"],
        period_start=date(2025, 4, 1),
        period_end=date(2025, 4, 30),
        bill_date=date(2025, 4, 5),
    )[0]

    r1 = post_receipt_for_bill(
        society=ctx["society"],
        member=ctx["member"],
        bill=bill,
        amount=Decimal("400.00"),
        receipt_date=date(2025, 4, 10),
        payment_mode="BANK_TRANSFER",
        deposited_account=ctx["cash_bank"],
        reference_number="TXN-001",
    )
    bill.refresh_from_db()
    assert r1.voucher is not None
    assert r1.voucher.posted_at is not None
    assert bill.status == Bill.BillStatus.PARTIAL
    assert bill.outstanding_amount == Decimal("900.00")

    post_receipt_for_bill(
        society=ctx["society"],
        member=ctx["member"],
        bill=bill,
        amount=Decimal("900.00"),
        receipt_date=date(2025, 4, 12),
        payment_mode="CASH",
        deposited_account=ctx["cash_bank"],
        reference_number="",
    )
    bill.refresh_from_db()
    assert bill.status == Bill.BillStatus.PAID
    assert bill.outstanding_amount == Decimal("0.00")


def test_receipt_posting_rejects_cross_society_deposit_account():
    ctx = _create_society_context()
    _create_templates(ctx)
    bill = generate_bills_for_period(
        society=ctx["society"],
        period_start=date(2025, 4, 1),
        period_end=date(2025, 4, 30),
        bill_date=date(2025, 4, 5),
    )[0]

    other_society = Society.objects.create(name="Other Society")
    other_asset_cat = AccountCategory.objects.create(
        society=other_society,
        name="Cash and Bank",
        account_type=AccountCategory.AccountType.ASSET,
    )
    foreign_bank = Account.objects.create(
        society=other_society,
        name="Foreign Bank",
        category=other_asset_cat,
    )

    with pytest.raises(ValidationError):
        post_receipt_for_bill(
            society=ctx["society"],
            member=ctx["member"],
            bill=bill,
            amount=Decimal("100.00"),
            receipt_date=date(2025, 4, 10),
            payment_mode="BANK_TRANSFER",
            deposited_account=foreign_bank,
            reference_number="X-1",
        )


def test_apply_late_fees_applies_once_for_overdue_bill():
    ctx = _create_society_context()
    _create_templates(ctx)
    bill = generate_bills_for_period(
        society=ctx["society"],
        period_start=date(2025, 4, 1),
        period_end=date(2025, 4, 30),
        bill_date=date(2025, 4, 5),
    )[0]

    updated_first = apply_late_fees(
        society=ctx["society"],
        as_of_date=date(2025, 6, 1),
    )
    bill.refresh_from_db()
    assert updated_first == 1
    assert bill.penalty_amount == Decimal("130.00")
    assert bill.total_amount == Decimal("1430.00")

    updated_second = apply_late_fees(
        society=ctx["society"],
        as_of_date=date(2025, 6, 2),
    )
    bill.refresh_from_db()
    assert updated_second == 0
    assert bill.penalty_amount == Decimal("130.00")
    assert bill.total_amount == Decimal("1430.00")


def test_outstanding_aging_and_reminder_scheduling_is_idempotent():
    ctx = _create_society_context()
    _create_templates(ctx)
    bill = generate_bills_for_period(
        society=ctx["society"],
        period_start=date(2025, 4, 1),
        period_end=date(2025, 4, 30),
        bill_date=date(2025, 4, 5),
    )[0]

    bill.due_date = date(2025, 4, 20)
    bill.save(update_fields=["due_date"])
    as_of_date = date(2025, 5, 30)

    outstanding = build_member_outstanding(society=ctx["society"], as_of_date=as_of_date)
    assert len(outstanding["rows"]) == 1
    row = outstanding["rows"][0]
    assert row["current"] == Decimal("0.00")
    assert row["bucket_31_60"] == Decimal("1300.00")
    assert row["bucket_1_30"] == Decimal("0.00")

    first = schedule_payment_reminders(
        society=ctx["society"],
        as_of_date=as_of_date,
        channels=[ReminderLog.Channel.EMAIL],
    )
    second = schedule_payment_reminders(
        society=ctx["society"],
        as_of_date=as_of_date,
        channels=[ReminderLog.Channel.EMAIL],
    )
    assert first == 1
    assert second == 0
    assert ReminderLog.objects.filter(society=ctx["society"]).count() == 1
