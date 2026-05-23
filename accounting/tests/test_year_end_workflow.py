from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from accounting.models import Account
from accounting.models import AccountCategory
from accounting.models import AccountingPeriod
from accounting.models import FinancialYear
from accounting.models import LedgerEntry
from accounting.models import PeriodStatusLog
from accounting.models import Voucher
from accounting.models import YearEndCloseLog
from accounting.services.period_workflow import close_period
from accounting.services.period_workflow import reopen_period
from accounting.services.year_end import close_financial_year_with_carry_forward
from housing.models import Society


@pytest.mark.django_db
def test_close_period_blocks_when_draft_exists():
    society = Society.objects.create(name="Draft Block Society")
    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    period = AccountingPeriod.objects.get(
        society=society,
        financial_year=fy,
        start_date=date(2024, 4, 1),
        end_date=date(2024, 4, 30),
    )
    Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.GENERAL,
        voucher_date=date(2024, 4, 10),
        narration="Draft in period",
    )

    with pytest.raises(ValidationError):
        close_period(period)


@pytest.mark.django_db
def test_close_and_reopen_period_creates_audit_logs():
    society = Society.objects.create(name="Period Audit Society")
    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    p1 = AccountingPeriod.objects.get(
        society=society,
        financial_year=fy,
        start_date=date(2024, 4, 1),
        end_date=date(2024, 4, 30),
    )
    p2 = AccountingPeriod.objects.get(
        society=society,
        financial_year=fy,
        start_date=date(2024, 5, 1),
        end_date=date(2024, 5, 31),
    )

    # Under cumulative multi-open, both p1 and p2 start open.
    # close_period no longer auto-opens the next period.
    result = close_period(p1, reason="Close for month-end")
    assert result is None
    p1.refresh_from_db()
    p2.refresh_from_db()
    assert p1.is_open is False
    # p2 remains open (multi-open paradigm).
    assert p2.is_open is True
    assert PeriodStatusLog.objects.filter(period=p1, action=PeriodStatusLog.Action.CLOSED).exists()
    # No auto-open log for p2.
    assert not PeriodStatusLog.objects.filter(period=p2, action=PeriodStatusLog.Action.OPENED, reason__startswith="Auto-open").exists()

    # reopen_period no longer auto-closes later periods.
    reopen_period(p1, reason="Reopen for adjustment")
    p1.refresh_from_db()
    p2.refresh_from_db()
    assert p1.is_open is True
    # p2 remains open.
    assert p2.is_open is True
    assert PeriodStatusLog.objects.filter(period=p1, action=PeriodStatusLog.Action.OPENED).exists()
    # No auto-close log for p2.
    assert not PeriodStatusLog.objects.filter(period=p2, action=PeriodStatusLog.Action.CLOSED, reason__startswith="Auto-close").exists()


@pytest.mark.django_db
def test_close_last_period_closes_financial_year():
    society = Society.objects.create(name="Last Period FY Close")
    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    # Close all but the last period.
    AccountingPeriod.objects.filter(
        society=society,
        financial_year=fy,
        start_date__lt=date(2025, 3, 1),
    ).update(is_open=False)

    last_period = AccountingPeriod.objects.get(
        society=society,
        financial_year=fy,
        start_date=date(2025, 3, 1),
        end_date=date(2025, 3, 31),
    )
    assert last_period.is_open is True
    assert fy.is_open is True

    close_period(last_period, reason="Last period")
    last_period.refresh_from_db()
    fy.refresh_from_db()
    assert last_period.is_open is False
    assert fy.is_open is False


@pytest.mark.django_db
def test_close_non_last_period_keeps_fy_open():
    society = Society.objects.create(name="Mid Period FY")
    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    mid_period = AccountingPeriod.objects.get(
        society=society,
        financial_year=fy,
        start_date=date(2024, 6, 1),
        end_date=date(2024, 6, 30),
    )
    close_period(mid_period, reason="Mid-year close")
    mid_period.refresh_from_db()
    fy.refresh_from_db()
    assert mid_period.is_open is False
    # FY stays open because later periods remain open.
    assert fy.is_open is True


@pytest.mark.django_db
def test_year_end_carry_forward_creates_opening_voucher_and_log():
    society = Society.objects.create(name="Year End Society")
    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    AccountingPeriod.objects.filter(
        society=society,
        financial_year=fy,
        start_date=date(2024, 4, 1),
        end_date=date(2024, 4, 30),
    ).update(is_open=True)

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
    cash = Account.objects.create(
        society=society, name="Cash", category=asset_cat,
        account_type=Account.AccountType.ASSET,
    )
    income = Account.objects.create(
        society=society, name="Maintenance Income", category=income_cat,
        account_type=Account.AccountType.INCOME,
    )

    voucher = Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.GENERAL,
        voucher_date=date(2024, 4, 10),
        narration="Year activity",
    )
    LedgerEntry.objects.create(voucher=voucher, account=cash, debit=Decimal("1000.00"))
    LedgerEntry.objects.create(voucher=voucher, account=income, credit=Decimal("1000.00"))
    voucher.post()

    next_fy, opening_voucher = close_financial_year_with_carry_forward(fy, notes="FY close test")

    fy.refresh_from_db()
    assert fy.is_open is False
    assert next_fy.start_date == date(2025, 4, 1)
    assert opening_voucher.voucher_type == Voucher.VoucherType.OPENING
    assert opening_voucher.posted_at is not None
    assert opening_voucher.voucher_date == next_fy.start_date
    assert YearEndCloseLog.objects.filter(source_financial_year=fy, opening_voucher=opening_voucher).exists()

    lines = opening_voucher.entries.order_by("id")
    assert lines.count() == 2
    assert lines.filter(account=cash, debit=Decimal("1000.00"), credit=Decimal("0.00")).exists()
    assert lines.filter(account=income, debit=Decimal("0.00"), credit=Decimal("1000.00")).exists()


@pytest.mark.django_db
def test_year_end_carry_forward_cannot_run_twice():
    society = Society.objects.create(name="Year End Idempotency Society")
    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    AccountingPeriod.objects.filter(
        society=society,
        financial_year=fy,
        start_date=date(2024, 4, 1),
        end_date=date(2024, 4, 30),
    ).update(is_open=True)

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
    cash = Account.objects.create(
        society=society, name="Cash", category=asset_cat,
        account_type=Account.AccountType.ASSET,
    )
    income = Account.objects.create(
        society=society, name="Maintenance Income", category=income_cat,
        account_type=Account.AccountType.INCOME,
    )
    voucher = Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.GENERAL,
        voucher_date=date(2024, 4, 10),
        narration="Year activity",
    )
    LedgerEntry.objects.create(voucher=voucher, account=cash, debit=Decimal("1.00"))
    LedgerEntry.objects.create(voucher=voucher, account=income, credit=Decimal("1.00"))
    voucher.post()

    close_financial_year_with_carry_forward(fy)
    with pytest.raises(ValidationError):
        close_financial_year_with_carry_forward(fy)


@pytest.mark.django_db
def test_financial_year_creation_opens_periods_up_to_today(monkeypatch):
    """Under cumulative multi-open, all periods up to today are open on creation."""
    from django.utils.timezone import localdate

    society = Society.objects.create(name="Cumulative Open Society")
    # Freeze today to a known date so the test is deterministic.
    fake_today = date(2025, 9, 15)
    monkeypatch.setattr(
        "accounting.models.model_FinancialYear.localdate",
        lambda: fake_today,
    )

    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2025-26",
        start_date=date(2025, 4, 1),
        end_date=date(2026, 3, 31),
        is_open=True,
    )

    periods = AccountingPeriod.objects.filter(financial_year=fy).order_by("start_date")
    assert periods.count() == 12

    for p in periods:
        if p.start_date <= fake_today:
            assert p.is_open is True, f"Period {p.start_date} should be open"
        else:
            assert p.is_open is False, f"Period {p.start_date} should be closed"


@pytest.mark.django_db
def test_auto_open_period_command_idempotent(monkeypatch):
    """The auto_open_period command opens the period containing today."""
    from django.core.management import call_command
    from io import StringIO

    society = Society.objects.create(name="Auto Open Society")
    fake_today = date(2025, 6, 15)
    monkeypatch.setattr(
        "accounting.management.commands.auto_open_period.localdate",
        lambda: fake_today,
    )

    # Create FY with cumulative open: periods from Apr to today (Jun) open.
    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2025-26",
        start_date=date(2025, 4, 1),
        end_date=date(2026, 3, 31),
        is_open=True,
    )

    # Manually close the June period to simulate a stale period.
    june = AccountingPeriod.objects.get(
        society=society,
        financial_year=fy,
        start_date=date(2025, 6, 1),
    )
    june.is_open = False
    june.save(update_fields=["is_open"])

    out = StringIO()
    # First run: should open June.
    call_command("auto_open_period", stdout=out)
    june.refresh_from_db()
    assert june.is_open is True
    log_count = PeriodStatusLog.objects.filter(period=june, action=PeriodStatusLog.Action.OPENED).count()
    assert log_count == 1

    # Second run: idempotent — June already open, should skip.
    out2 = StringIO()
    call_command("auto_open_period", stdout=out2)
    june.refresh_from_db()
    assert june.is_open is True
    # No additional OPENED log.
    assert PeriodStatusLog.objects.filter(period=june, action=PeriodStatusLog.Action.OPENED).count() == log_count


@pytest.mark.django_db
def test_reopen_closed_financial_year():
    """Reopening a period in a closed FY reopens the FY."""
    society = Society.objects.create(name="Reopen FY Society")
    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=False,
    )
    period = AccountingPeriod.objects.get(
        society=society,
        financial_year=fy,
        start_date=date(2024, 4, 1),
    )
    period.is_open = False
    period.save(update_fields=["is_open"])

    reopen_period(period, reason="Reopen FY")
    period.refresh_from_db()
    fy.refresh_from_db()
    assert period.is_open is True
    assert fy.is_open is True
