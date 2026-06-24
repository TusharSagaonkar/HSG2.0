from datetime import date
from decimal import Decimal
from http import HTTPStatus

import pytest
from django.urls import reverse

from accounting.models import Account
from accounting.models import AccountCategory
from accounting.models import AccountingPeriod
from accounting.models import FinancialYear
from accounting.models import LedgerEntry
from accounting.models import Voucher
from housing.models import Society
from members.models import Member
from members.models import Structure
from members.models import Unit
from reconciliation.models import BankStatementImport
from reconciliation.models import BankTransaction
from reconciliation.models import ReconciliationLink
from reconciliation.tests.factories import BankStatementImportFactory
from societies.models import Membership
from reconciliation.tests.factories import BankTransactionFactory
from reconciliation.tests.factories import ReconciliationLinkFactory
from housing_accounting.selection import SESSION_SELECTED_FINANCIAL_YEAR_ID
from housing_accounting.selection import SESSION_SELECTED_SOCIETY_ID


pytestmark = pytest.mark.django_db


def _set_scope(client, society, financial_year, user=None):
    if user is not None:
        Membership.objects.get_or_create(
            user=user,
            society=society,
            defaults={"role": Membership.Role.OWNER, "is_active": True},
        )
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

    def _ensure_account(*, code, name, account_type, sub_type, is_bank=False):
        account = Account.objects.filter(society=society, code=code).first()
        if account is None:
            account = Account.objects.filter(society=society, name=name).first()
        if account is not None:
            return account

        category = AccountCategory.objects.filter(
            society=society,
            account_type=account_type,
        ).first()
        return Account.objects.create(
            society=society,
            name=name,
            code=code,
            category=category,
            account_type=account_type,
            sub_type=sub_type,
            is_active=True,
            is_bank=is_bank,
        )

    cash = _ensure_account(
        code="1.4.1",
        name="Cash in Hand",
        account_type=Account.AccountType.ASSET,
        sub_type=Account.SubType.BANK,
        is_bank=True,
    )
    income = _ensure_account(
        code="3.1.1",
        name="Maintenance Charges",
        account_type=Account.AccountType.INCOME,
        sub_type=Account.SubType.INCOME,
    )

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
    assert "Form I - Register of Members" in content
    assert "Form J - List of Members" in content


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
        reverse("reports:form-i-register-of-members"),
        reverse("reports:form-j-list-of-members"),
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
    _set_scope(client, society, financial_year, user=user)
    response = client.get(reverse("reports:bank-reconciliation-statement"))

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "Bank Reconciliation Statement" in content
    assert "Bank Statement Entries" in content
    assert "Bank Balance as per Statement" in content


def test_bank_reconciliation_report_reflects_links(client, user):
    society = Society.objects.create(name="BRS Link Society")
    financial_year = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    _seed_basic_posted_data(society, financial_year)

    imp = BankStatementImportFactory(society=society)
    linked_bt = BankTransactionFactory(
        bank_statement_import=imp,
        transaction_date=date(2024, 4, 12),
        amount=Decimal("500.00"),
        dr_cr=BankTransaction.DrCr.CREDIT,
        reference_no="LINKED-001",
    )
    unlinked_bt = BankTransactionFactory(
        bank_statement_import=imp,
        transaction_date=date(2024, 4, 13),
        amount=Decimal("750.00"),
        dr_cr=BankTransaction.DrCr.CREDIT,
        reference_no="UNLINKED-001",
        narration="Unlinked maintenance receipt",
    )
    ReconciliationLinkFactory(
        society=society,
        bank_transaction=linked_bt,
        status=ReconciliationLink.Status.SUGGESTED,
        match_type=ReconciliationLink.MatchType.PARTIAL,
    )

    client.force_login(user)
    _set_scope(client, society, financial_year, user=user)
    response = client.get(reverse("reports:bank-reconciliation-statement"))

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "Bank Statement Entries" in content
    assert "LINKED-001" in content
    assert "UNLINKED-001" in content
    assert "Unlinked maintenance receipt" in content
    assert "UNMATCHED" in content


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


def test_form_i_and_form_j_reports_render(client, user):
    society = Society.objects.create(name="Membership Reports Society")
    financial_year = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
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
    Member.objects.create(
        society=society,
        unit=unit,
        full_name="Active Member",
        role=Member.MemberRole.OWNER,
        status=Member.MemberStatus.ACTIVE,
        join_date=date(2024, 1, 1),
        start_date=date(2024, 1, 1),
        share_balance=10,
    )

    client.force_login(user)
    _set_scope(client, society, financial_year)

    form_i = client.get(reverse("reports:form-i-register-of-members"))
    form_j = client.get(reverse("reports:form-j-list-of-members"))

    assert form_i.status_code == HTTPStatus.OK
    assert "Register of Members" in form_i.content.decode()
    assert form_j.status_code == HTTPStatus.OK
    assert "List of Members" in form_j.content.decode()
