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
from housing.models import Society
from housing_accounting.selection import SESSION_SELECTED_FINANCIAL_YEAR_ID
from housing_accounting.selection import SESSION_SELECTED_SOCIETY_ID


pytestmark = pytest.mark.django_db


def _set_scope(client, society, financial_year):
    session = client.session
    session[SESSION_SELECTED_SOCIETY_ID] = society.id
    session[SESSION_SELECTED_FINANCIAL_YEAR_ID] = financial_year.id
    session.save()


def _seed_basic_posted_data(society, financial_year):
    AccountingPeriod.objects.filter(
        society=society,
        financial_year=financial_year,
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
    LedgerEntry.objects.create(voucher=voucher, account=cash, debit=Decimal("150.00"))
    LedgerEntry.objects.create(voucher=voucher, account=income, credit=Decimal("150.00"))
    voucher.post()


def test_reports_index_renders(client, user):
    client.force_login(user)
    response = client.get(reverse("reports:index"))

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "Trial Balance" in content
    assert "Profit and Loss" in content


def test_accounting_trial_balance_url_uses_reports_view(client, user):
    society = Society.objects.create(name="Reports Trial Society")
    financial_year = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    _seed_basic_posted_data(society, financial_year)

    client.force_login(user)
    _set_scope(client, society, financial_year)
    response = client.get(reverse("accounting:trial-balance"))

    assert response.status_code == HTTPStatus.OK
    assert "Integrity check passed" in response.content.decode()


def test_reports_pages_render_with_selected_scope(client, user):
    society = Society.objects.create(name="Reports Scope Society")
    financial_year = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    _seed_basic_posted_data(society, financial_year)

    client.force_login(user)
    _set_scope(client, society, financial_year)

    urls = [
        reverse("reports:trial-balance"),
        reverse("reports:profit-and-loss"),
        reverse("reports:balance-sheet"),
        reverse("reports:cash-flow-statement"),
        reverse("reports:fixed-assets-register"),
        reverse("reports:accounts-receivable-aging"),
        reverse("reports:accounts-payable-aging"),
        reverse("reports:bank-reconciliation-statement"),
        reverse("reports:transaction-reconciliation"),
        reverse("reports:exception-report"),
        reverse("reports:gst-reports"),
        reverse("reports:tds-reports"),
        reverse("reports:inventory-costing-reports"),
        reverse("reports:management-analytics-reports"),
        reverse("reports:control-risk-reports"),
        reverse("reports:advanced-regulatory-reports"),
    ]

    for url in urls:
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK


def test_cash_flow_report_renders_sections(client, user):
    society = Society.objects.create(name="Cash Flow Society")
    financial_year = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    _seed_basic_posted_data(society, financial_year)

    client.force_login(user)
    _set_scope(client, society, financial_year)
    response = client.get(reverse("reports:cash-flow-statement"))

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "Operating Activities" in content
    assert "Net Change in Cash and Cash Equivalents" in content


def test_bank_reconciliation_report_renders_summary(client, user):
    society = Society.objects.create(name="BRS Society")
    financial_year = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    _seed_basic_posted_data(society, financial_year)

    client.force_login(user)
    _set_scope(client, society, financial_year)
    response = client.get(reverse("reports:bank-reconciliation-statement"))

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "Bank Reconciliation Statement" in content
    assert "Adjusted Bank Balance" in content


def test_fixed_assets_report_renders_register(client, user):
    society = Society.objects.create(name="Fixed Assets Society")
    financial_year = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    _seed_basic_posted_data(society, financial_year)

    client.force_login(user)
    _set_scope(client, society, financial_year)
    response = client.get(reverse("reports:fixed-assets-register"))

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "Fixed Assets Register" in content
    assert "Asset Movement" in content


def test_transaction_reconciliation_report_renders_lifecycle(client, user):
    society = Society.objects.create(name="Transaction Reconciliation Society")
    financial_year = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    _seed_basic_posted_data(society, financial_year)

    client.force_login(user)
    _set_scope(client, society, financial_year)
    response = client.get(reverse("reports:transaction-reconciliation"))

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "Lifecycle Summary" in content
    assert "Reference Lifecycle" in content


def test_phase_3_to_5_reports_render_sections(client, user):
    society = Society.objects.create(name="Phase 3-5 Society")
    financial_year = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    _seed_basic_posted_data(society, financial_year)

    client.force_login(user)
    _set_scope(client, society, financial_year)

    gst = client.get(reverse("reports:gst-reports"))
    tds = client.get(reverse("reports:tds-reports"))
    inventory = client.get(reverse("reports:inventory-costing-reports"))
    management = client.get(reverse("reports:management-analytics-reports"))
    control = client.get(reverse("reports:control-risk-reports"))
    advanced = client.get(reverse("reports:advanced-regulatory-reports"))

    assert gst.status_code == HTTPStatus.OK
    assert "GST Summary" in gst.content.decode()
    assert tds.status_code == HTTPStatus.OK
    assert "TDS Summary" in tds.content.decode()
    assert inventory.status_code == HTTPStatus.OK
    assert "Inventory Valuation" in inventory.content.decode()
    assert management.status_code == HTTPStatus.OK
    assert "KPI Summary" in management.content.decode()
    assert control.status_code == HTTPStatus.OK
    assert "Risk Summary" in control.content.decode()
    assert advanced.status_code == HTTPStatus.OK
    assert "Advanced Regulatory Summary" in advanced.content.decode()
