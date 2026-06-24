from datetime import date
from decimal import Decimal

import pytest
from django.utils import timezone

from accounting.models import AccountingPeriod
from accounting.models import LedgerEntry
from accounting.models import Voucher
from housing.models import Society
from members.models import Member
from members.models import Structure
from members.models import Nominee
from members.models import Unit
from reconciliation.models import BankStatementImport
from reconciliation.models import BankTransaction
from reconciliation.models import ReconciliationLink
from reconciliation.tests.factories import BankAccountFactory
from reconciliation.tests.factories import BankStatementImportFactory
from reconciliation.tests.factories import BankTransactionFactory
from reconciliation.tests.factories import ReconciliationLinkFactory
from reports.services import build_active_member_list_report
from reports.services import build_gst_reports
from reports.services import build_bank_reconciliation_statement
from reports.services import build_member_register_report
from shares.models import ShareCertificate


pytestmark = pytest.mark.django_db


def _open_period(society):
    today = timezone.localdate()
    AccountingPeriod.objects.filter(
        society=society,
        start_date__lte=today,
        end_date__gte=today,
    ).update(is_open=True)


def _make_unit(society):
    structure = Structure.objects.create(
        society=society,
        structure_type=Structure.StructureType.BUILDING,
        name="GST Service Tower",
    )
    return Unit.objects.create(
        structure=structure,
        unit_type=Unit.UnitType.FLAT,
        identifier="801",
    )


def test_build_gst_reports_uses_only_gst_tagged_accounts():
    society = Society.objects.create(name="GST Report Service Society")
    _open_period(society)
    unit = _make_unit(society)
    today = timezone.localdate()

    receivable = society.accounts.get(name="Maintenance Receivable")
    income = society.accounts.get(name="Maintenance Charges")
    output_cgst = society.accounts.get(name="Output CGST")
    output_sgst = society.accounts.get(name="Output SGST")
    input_cgst = society.accounts.get(name="Input CGST")
    input_sgst = society.accounts.get(name="Input SGST")
    expense = society.accounts.get(name="Lift Maintenance")
    payable = society.accounts.get(name="Vendor Payable")

    billing = Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.BILL,
        voucher_date=today,
        narration="Maintenance billing with GST",
    )
    LedgerEntry.objects.create(voucher=billing, account=receivable, unit=unit, debit=Decimal("1180.00"))
    LedgerEntry.objects.create(voucher=billing, account=income, credit=Decimal("1000.00"))
    LedgerEntry.objects.create(voucher=billing, account=output_cgst, credit=Decimal("90.00"))
    LedgerEntry.objects.create(voucher=billing, account=output_sgst, credit=Decimal("90.00"))
    billing.post()

    purchase = Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.BILL,
        voucher_date=today,
        narration="Expense booked with GST",
    )
    LedgerEntry.objects.create(voucher=purchase, account=expense, debit=Decimal("1000.00"))
    LedgerEntry.objects.create(voucher=purchase, account=input_cgst, debit=Decimal("90.00"))
    LedgerEntry.objects.create(voucher=purchase, account=input_sgst, debit=Decimal("90.00"))
    LedgerEntry.objects.create(voucher=purchase, account=payable, credit=Decimal("1180.00"))
    purchase.post()

    report = build_gst_reports(society=society, to_date=today)

    assert report["summary"]["gstr3b_output"] == Decimal("180.00")
    assert report["summary"]["input_tax_credit"] == Decimal("180.00")
    assert report["summary"]["net_payable"] == Decimal("0.00")
    assert report["summary"]["unmapped_total"] == Decimal("0.00")
    assert report["status_note"] == ""


def test_build_gst_reports_returns_empty_note_when_no_gst_entries():
    society = Society.objects.create(name="GST Empty Society")
    _open_period(society)
    today = timezone.localdate()

    cash = society.accounts.get(name="Cash in Hand")
    income = society.accounts.get(name="Maintenance Charges")

    voucher = Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.GENERAL,
        voucher_date=today,
        narration="Non GST voucher",
    )
    LedgerEntry.objects.create(voucher=voucher, account=cash, debit=Decimal("100.00"))
    LedgerEntry.objects.create(voucher=voucher, account=income, credit=Decimal("100.00"))
    voucher.post()

    report = build_gst_reports(society=society, to_date=today)
    assert report["rows"] == []
    assert report["status_note"] == "No GST-tagged accounts found in posted vouchers."


def test_build_member_register_report_includes_share_and_nominee_details():
    society = Society.objects.create(name="Form I Society")
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
        full_name="Tushar Sagaonkar",
        role=Member.MemberRole.OWNER,
        status=Member.MemberStatus.ACTIVE,
        join_date=date(2024, 1, 1),
        start_date=date(2024, 1, 1),
        share_balance=10,
    )
    Nominee.objects.create(
        member=member,
        name="Priya Sagaonkar",
        relationship="Spouse",
        percentage=100,
        priority_order=1,
    )
    ShareCertificate.objects.create(
        member=member,
        certificate_no="45",
        share_count=Decimal("10.00"),
        issued_date=date(2024, 1, 5),
        status=ShareCertificate.Status.ACTIVE,
    )

    report = build_member_register_report(society=society)

    assert report["total_members"] == 1
    assert report["active_members"] == 1
    row = report["rows"][0]
    assert row["full_name"] == "Tushar Sagaonkar"
    assert row["share_certificate_no"] == "45"
    assert row["nominee_name"] == "Priya Sagaonkar"
    assert row["no_of_shares"] == Decimal("10.00")


def test_build_member_register_report_excludes_tenant_members():
    society = Society.objects.create(name="Form I Tenant Filter Society")
    structure = Structure.objects.create(
        society=society,
        structure_type=Structure.StructureType.BUILDING,
        name="A",
    )
    unit = Unit.objects.create(
        structure=structure,
        unit_type=Unit.UnitType.FLAT,
        identifier="103",
    )
    Member.objects.create(
        society=society,
        unit=unit,
        full_name="Owner Member",
        role=Member.MemberRole.OWNER,
        status=Member.MemberStatus.ACTIVE,
        join_date=date(2024, 1, 1),
        start_date=date(2024, 1, 1),
        share_balance=10,
    )
    Member.objects.create(
        society=society,
        unit=unit,
        full_name="Tenant Member",
        role=Member.MemberRole.TENANT,
        status=Member.MemberStatus.ACTIVE,
        join_date=date(2024, 1, 2),
        start_date=date(2024, 1, 2),
        share_balance=2,
    )

    report = build_member_register_report(society=society)

    assert report["total_members"] == 1
    assert [row["full_name"] for row in report["rows"]] == ["Owner Member"]


def test_build_active_member_list_report_only_returns_active_members():
    society = Society.objects.create(name="Form J Society")
    structure = Structure.objects.create(
        society=society,
        structure_type=Structure.StructureType.BUILDING,
        name="A",
    )
    unit = Unit.objects.create(
        structure=structure,
        unit_type=Unit.UnitType.FLAT,
        identifier="102",
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
    Member.objects.create(
        society=society,
        unit=unit,
        full_name="Inactive Member",
        role=Member.MemberRole.OWNER,
        status=Member.MemberStatus.INACTIVE,
        join_date=date(2024, 1, 1),
        start_date=date(2024, 1, 1),
        share_balance=5,
    )

    report = build_active_member_list_report(society=society)

    assert report["total_active_members"] == 1
    assert [row["member_name"] for row in report["rows"]] == ["Active Member"]


def test_build_active_member_list_report_excludes_tenant_members():
    society = Society.objects.create(name="Form J Tenant Filter Society")
    structure = Structure.objects.create(
        society=society,
        structure_type=Structure.StructureType.BUILDING,
        name="A",
    )
    unit = Unit.objects.create(
        structure=structure,
        unit_type=Unit.UnitType.FLAT,
        identifier="104",
    )
    Member.objects.create(
        society=society,
        unit=unit,
        full_name="Owner Member",
        role=Member.MemberRole.OWNER,
        status=Member.MemberStatus.ACTIVE,
        join_date=date(2024, 1, 1),
        start_date=date(2024, 1, 1),
        share_balance=10,
    )
    Member.objects.create(
        society=society,
        unit=unit,
        full_name="Tenant Member",
        role=Member.MemberRole.TENANT,
        status=Member.MemberStatus.ACTIVE,
        join_date=date(2024, 1, 2),
        start_date=date(2024, 1, 2),
        share_balance=2,
    )

    report = build_active_member_list_report(society=society)

    assert report["total_active_members"] == 1
    assert [row["member_name"] for row in report["rows"]] == ["Owner Member"]


def test_build_bank_reconciliation_statement_reflects_live_links():
    society = Society.objects.create(name="BRS Service Society")
    BankAccountFactory(society=society, name="Bank Account", code="1.4.2.1")
    imp = BankStatementImportFactory(society=society)
    linked_bt = BankTransactionFactory(
        bank_statement_import=imp,
        amount=Decimal("500.00"),
        dr_cr=BankTransaction.DrCr.CREDIT,
        reference_no="LINKED-001",
    )
    BankTransactionFactory(
        bank_statement_import=imp,
        amount=Decimal("750.00"),
        dr_cr=BankTransaction.DrCr.CREDIT,
        reference_no="UNLINKED-001",
    )
    ReconciliationLinkFactory(
        society=society,
        bank_transaction=linked_bt,
        status=ReconciliationLink.Status.SUGGESTED,
        match_type=ReconciliationLink.MatchType.PARTIAL,
    )

    report = build_bank_reconciliation_statement(society=society, to_date=date.today())

    assert report["bank_account_name"]
    assert report["reconciling_count"] == 1
    assert [row["reference"] for row in report["reconciling_rows"]] == ["UNLINKED-001"]
    assert report["add_total"] == Decimal("750.00")
    assert report["bank_balance"] == Decimal("1250.00")
    assert report["bank_statement_count"] == 2
    assert {row["reference"] for row in report["bank_statement_rows"]} == {
        "LINKED-001",
        "UNLINKED-001",
    }
    unmatched_row = next(row for row in report["bank_statement_rows"] if row["reference"] == "UNLINKED-001")
    assert unmatched_row["match_status"] == "UNMATCHED"
    assert unmatched_row["credit"] == Decimal("750.00")


def test_build_bank_reconciliation_statement_includes_all_bank_accounts():
    society = Society.objects.create(name="BRS Multi Bank Society")
    maintenance_bank = BankAccountFactory(
        society=society,
        name="Maintenance Bank",
        code="1.4.2.1",
    )
    sinking_bank = BankAccountFactory(
        society=society,
        name="Sinking Fund Bank",
        code="1.4.2.2",
    )

    BankTransactionFactory(
        bank_statement_import=BankStatementImportFactory(
            society=society,
            bank_account=maintenance_bank,
        ),
        amount=Decimal("750.00"),
        dr_cr=BankTransaction.DrCr.CREDIT,
        reference_no="MAINT-UNLINKED",
    )
    BankTransactionFactory(
        bank_statement_import=BankStatementImportFactory(
            society=society,
            bank_account=sinking_bank,
        ),
        amount=Decimal("1250.00"),
        dr_cr=BankTransaction.DrCr.CREDIT,
        reference_no="SINK-UNLINKED",
    )

    report = build_bank_reconciliation_statement(society=society, to_date=date.today())

    reported_account_names = {row["account_name"] for row in report["bank_account_rows"]}

    assert report["bank_account_count"] == len(report["bank_account_rows"])
    assert {maintenance_bank.name, sinking_bank.name}.issubset(reported_account_names)
    assert "Bank & Cash" not in reported_account_names
    assert "Bank Accounts" not in reported_account_names
    assert "Cash-in-Hand" not in reported_account_names
    assert "Fund Transfer Account" not in reported_account_names
    assert report["reconciling_count"] == 2
    assert {row["reference"] for row in report["reconciling_rows"]} == {
        "MAINT-UNLINKED",
        "SINK-UNLINKED",
    }
    assert {row["account_name"] for row in report["reconciling_rows"]} == {
        maintenance_bank.name,
        sinking_bank.name,
    }
    assert report["add_total"] == Decimal("2000.00")
