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
from reports.services import build_active_member_list_report
from reports.services import build_gst_reports
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
