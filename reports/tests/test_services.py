from decimal import Decimal

import pytest
from django.utils import timezone

from accounting.models import AccountingPeriod
from accounting.models import LedgerEntry
from accounting.models import Voucher
from housing.models import Society
from members.models import Structure
from members.models import Unit
from reports.services import build_gst_reports


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
