from datetime import date
from decimal import Decimal

import pytest

from accounting.models import Account
from accounting.models import AccountCategory
from accounting.models import AccountingPeriod
from accounting.models import FinancialYear
from accounting.models import LedgerEntry
from accounting.models import Voucher
from accounting.services.reporting import build_account_ledger
from accounting.services.reporting import build_trial_balance
from housing.models import Society


def _ensure_open_period(society, fy, month_start, month_end):
    AccountingPeriod.objects.filter(
        society=society,
        financial_year=fy,
        start_date=month_start,
        end_date=month_end,
    ).update(is_open=True)


def _post_voucher(*, society, voucher_date, rows, voucher_type=Voucher.VoucherType.GENERAL):
    voucher = Voucher.objects.create(
        society=society,
        voucher_type=voucher_type,
        voucher_date=voucher_date,
        narration="Reporting test voucher",
    )
    for account, debit, credit in rows:
        LedgerEntry.objects.create(voucher=voucher, account=account, debit=debit, credit=credit)
    voucher.post()
    return voucher


@pytest.mark.django_db
def test_ledger_uses_only_posted_entries_and_running_balance_for_asset():
    society = Society.objects.create(name="Ledger Society")
    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    _ensure_open_period(society, fy, date(2024, 8, 1), date(2024, 8, 31))

    asset_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Current Assets",
        account_type="ASSET",
    )
    income_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Income",
        account_type="INCOME",
    )
    cash = Account.objects.create(society=society, name="Cash", category=asset_cat)
    income = Account.objects.create(society=society, name="Maintenance Income", category=income_cat)

    draft = Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.GENERAL,
        voucher_date=date(2024, 8, 5),
        narration="Draft",
    )
    LedgerEntry.objects.create(voucher=draft, account=cash, debit=999, credit=0)
    LedgerEntry.objects.create(voucher=draft, account=income, debit=0, credit=999)

    _post_voucher(
        society=society,
        voucher_date=date(2024, 8, 6),
        rows=[(cash, Decimal("100.00"), Decimal("0.00")), (income, Decimal("0.00"), Decimal("100.00"))],
    )
    _post_voucher(
        society=society,
        voucher_date=date(2024, 8, 7),
        rows=[(cash, Decimal("0.00"), Decimal("30.00")), (income, Decimal("30.00"), Decimal("0.00"))],
    )

    ledger = build_account_ledger(cash, society=society, financial_year=fy)
    assert len(ledger) == 2
    assert [line.running_balance for line in ledger] == [Decimal("100.00"), Decimal("70.00")]
    assert [line.balance_side for line in ledger] == ["DR", "DR"]


@pytest.mark.django_db
def test_ledger_running_balance_for_credit_normal_account():
    society = Society.objects.create(name="Income Ledger Society")
    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    _ensure_open_period(society, fy, date(2024, 8, 1), date(2024, 8, 31))

    asset_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Current Assets",
        account_type="ASSET",
    )
    income_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Income",
        account_type="INCOME",
    )
    cash = Account.objects.create(society=society, name="Cash", category=asset_cat)
    income = Account.objects.create(society=society, name="Maintenance Income", category=income_cat)

    _post_voucher(
        society=society,
        voucher_date=date(2024, 8, 6),
        rows=[(cash, Decimal("1000.00"), Decimal("0.00")), (income, Decimal("0.00"), Decimal("1000.00"))],
    )
    _post_voucher(
        society=society,
        voucher_date=date(2024, 8, 7),
        rows=[(cash, Decimal("0.00"), Decimal("200.00")), (income, Decimal("200.00"), Decimal("0.00"))],
    )

    ledger = build_account_ledger(income, society=society, financial_year=fy)
    assert [line.running_balance for line in ledger] == [Decimal("1000.00"), Decimal("800.00")]
    assert [line.balance_side for line in ledger] == ["CR", "CR"]


@pytest.mark.django_db
def test_ledger_ordering_is_deterministic_for_same_date():
    society = Society.objects.create(name="Order Society")
    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    _ensure_open_period(society, fy, date(2024, 8, 1), date(2024, 8, 31))

    asset_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Current Assets",
        account_type="ASSET",
    )
    income_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Income",
        account_type="INCOME",
    )
    cash = Account.objects.create(society=society, name="Cash", category=asset_cat)
    income = Account.objects.create(society=society, name="Maintenance Income", category=income_cat)

    v1 = _post_voucher(
        society=society,
        voucher_date=date(2024, 8, 6),
        rows=[(cash, Decimal("10.00"), Decimal("0.00")), (income, Decimal("0.00"), Decimal("10.00"))],
    )
    v2 = _post_voucher(
        society=society,
        voucher_date=date(2024, 8, 6),
        rows=[(cash, Decimal("20.00"), Decimal("0.00")), (income, Decimal("0.00"), Decimal("20.00"))],
    )

    ledger = build_account_ledger(cash, society=society, financial_year=fy)
    voucher_ids = [line.entry.voucher_id for line in ledger]
    assert voucher_ids == [v1.id, v2.id]


@pytest.mark.django_db
def test_trial_balance_integrity_and_side_only_net_display():
    society = Society.objects.create(name="Trial Society")
    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    _ensure_open_period(society, fy, date(2024, 8, 1), date(2024, 8, 31))

    asset_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Current Assets",
        account_type="ASSET",
    )
    income_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Income",
        account_type="INCOME",
    )
    expense_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Expenses",
        account_type="EXPENSE",
    )
    cash = Account.objects.create(society=society, name="Cash", category=asset_cat)
    income = Account.objects.create(society=society, name="Maintenance Income", category=income_cat)
    expense = Account.objects.create(society=society, name="Electricity Expense", category=expense_cat)

    _post_voucher(
        society=society,
        voucher_date=date(2024, 8, 6),
        rows=[(cash, Decimal("100.00"), Decimal("0.00")), (income, Decimal("0.00"), Decimal("100.00"))],
    )
    _post_voucher(
        society=society,
        voucher_date=date(2024, 8, 8),
        rows=[(expense, Decimal("40.00"), Decimal("0.00")), (cash, Decimal("0.00"), Decimal("40.00"))],
    )

    result = build_trial_balance(society=society, financial_year=fy)
    assert result["is_balanced"] is True
    assert result["grand_total_debit"] == result["grand_total_credit"] == Decimal("140.00")
    assert result["total_balance_debit"] == result["total_balance_credit"] == Decimal("100.00")

    rows = {row["account_name"]: row for row in result["rows"]}
    assert rows["Cash"]["balance_debit"] == Decimal("60.00")
    assert rows["Cash"]["balance_credit"] == Decimal("0.00")
    assert rows["Maintenance Income"]["balance_debit"] == Decimal("0.00")
    assert rows["Maintenance Income"]["balance_credit"] == Decimal("100.00")


@pytest.mark.django_db
def test_trial_balance_respects_to_date_filter():
    society = Society.objects.create(name="To Date Society")
    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    _ensure_open_period(society, fy, date(2024, 8, 1), date(2024, 8, 31))

    asset_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Current Assets",
        account_type="ASSET",
    )
    income_cat, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Income",
        account_type="INCOME",
    )
    cash = Account.objects.create(society=society, name="Cash", category=asset_cat)
    income = Account.objects.create(society=society, name="Maintenance Income", category=income_cat)

    _post_voucher(
        society=society,
        voucher_date=date(2024, 8, 6),
        rows=[(cash, Decimal("50.00"), Decimal("0.00")), (income, Decimal("0.00"), Decimal("50.00"))],
    )
    _post_voucher(
        society=society,
        voucher_date=date(2024, 8, 20),
        rows=[(cash, Decimal("70.00"), Decimal("0.00")), (income, Decimal("0.00"), Decimal("70.00"))],
    )

    as_of_10 = build_trial_balance(society=society, financial_year=fy, to_date=date(2024, 8, 10))
    as_of_31 = build_trial_balance(society=society, financial_year=fy, to_date=date(2024, 8, 31))

    assert as_of_10["grand_total_debit"] == Decimal("50.00")
    assert as_of_31["grand_total_debit"] == Decimal("120.00")
