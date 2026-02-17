from datetime import date
from decimal import Decimal
from http import HTTPStatus

import pytest
from django.urls import reverse

from accounting.models import Account
from accounting.models import AccountingPeriod
from accounting.models import FinancialYear
from accounting.models import LedgerEntry
from accounting.models import Voucher
from housing_accounting.selection import SESSION_SELECTED_FINANCIAL_YEAR_ID
from housing_accounting.selection import SESSION_SELECTED_SOCIETY_ID
from housing.models import Society

pytestmark = pytest.mark.django_db


def _set_scope(client, society, financial_year):
    session = client.session
    session[SESSION_SELECTED_SOCIETY_ID] = society.id
    session[SESSION_SELECTED_FINANCIAL_YEAR_ID] = financial_year.id
    session.save()


def test_account_ledger_export_csv_contains_posted_rows_only(client, user):
    society = Society.objects.create(name="Export Ledger Society")
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
    cash = Account.objects.get(society=society, name="Cash in Hand")
    income = Account.objects.get(society=society, name="Maintenance Charges")

    posted = Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.GENERAL,
        voucher_date=date(2024, 4, 10),
        narration="Posted voucher",
    )
    LedgerEntry.objects.create(voucher=posted, account=cash, debit=Decimal("50.00"))
    LedgerEntry.objects.create(voucher=posted, account=income, credit=Decimal("50.00"))
    posted.post()

    draft = Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.GENERAL,
        voucher_date=date(2024, 4, 12),
        narration="Draft voucher",
    )
    LedgerEntry.objects.create(voucher=draft, account=cash, debit=Decimal("999.00"))
    LedgerEntry.objects.create(voucher=draft, account=income, credit=Decimal("999.00"))

    client.force_login(user)
    _set_scope(client, society, fy)
    response = client.get(
        reverse("accounting:account-ledger-export-csv", kwargs={"pk": cash.pk})
    )

    assert response.status_code == HTTPStatus.OK
    body = response.content.decode()
    assert "Posted voucher" in body
    assert "Draft voucher" not in body
    assert "Running Balance" in body


def test_trial_balance_export_csv_returns_totals(client, user):
    society = Society.objects.create(name="Export Trial Society")
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
    cash = Account.objects.get(society=society, name="Cash in Hand")
    income = Account.objects.get(society=society, name="Maintenance Charges")

    voucher = Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.GENERAL,
        voucher_date=date(2024, 4, 10),
        narration="Posted voucher",
    )
    LedgerEntry.objects.create(voucher=voucher, account=cash, debit=Decimal("75.00"))
    LedgerEntry.objects.create(voucher=voucher, account=income, credit=Decimal("75.00"))
    voucher.post()

    client.force_login(user)
    _set_scope(client, society, fy)
    response = client.get(reverse("accounting:trial-balance-export-csv"))

    assert response.status_code == HTTPStatus.OK
    body = response.content.decode()
    assert "TOTALS" in body
    assert "75.00" in body
