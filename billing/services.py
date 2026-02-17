from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from accounting.models import LedgerEntry
from accounting.models import Voucher
from billing.models import Bill
from billing.models import BillLine
from billing.models import ChargeTemplate
from members.models import Member


def _is_template_due(template, period_start):
    month = period_start.month
    if template.frequency == ChargeTemplate.Frequency.MONTHLY:
        return True
    if template.frequency == ChargeTemplate.Frequency.QUARTERLY:
        return month in {1, 4, 7, 10}
    if template.frequency == ChargeTemplate.Frequency.YEARLY:
        return month == 1
    return False


def _next_bill_number(society):
    latest = (
        Bill.objects.select_for_update()
        .filter(society=society)
        .order_by("-bill_number")
        .first()
    )
    return 1 if latest is None else latest.bill_number + 1


def _post_bill_voucher(bill):
    if not bill.lines.exists():
        raise ValidationError("Cannot post bill without bill lines.")

    voucher = Voucher.objects.create(
        society=bill.society,
        voucher_type=Voucher.VoucherType.GENERAL,
        voucher_date=bill.bill_date,
        narration=f"Bill #{bill.bill_number} for {bill.member.full_name}",
    )
    total = Decimal("0.00")
    for line in bill.lines.select_related("income_account"):
        total += line.amount
        LedgerEntry.objects.create(
            voucher=voucher,
            account=bill.receivable_account,
            unit=bill.unit,
            debit=line.amount,
            credit=Decimal("0.00"),
        )
        LedgerEntry.objects.create(
            voucher=voucher,
            account=line.income_account,
            unit=None,
            debit=Decimal("0.00"),
            credit=line.amount,
        )
    voucher.post()
    bill.voucher = voucher
    bill.total_amount = total + bill.penalty_amount
    bill.save(update_fields=["voucher", "total_amount"])
    bill.refresh_status(as_of_date=bill.bill_date)


def generate_bills_for_period(*, society, period_start, period_end, bill_date):
    if period_end < period_start:
        raise ValidationError("Billing period end cannot be before period start.")
    if bill_date < period_start:
        raise ValidationError("Bill date cannot be before period start.")

    templates = list(
        ChargeTemplate.objects.filter(society=society, is_active=True).select_related(
            "income_account",
            "receivable_account",
        )
    )
    due_templates = [template for template in templates if _is_template_due(template, period_start)]
    if not due_templates:
        return []

    active_members = list(
        Member.objects.filter(
            society=society,
            status=Member.MemberStatus.ACTIVE,
            start_date__lte=period_end,
        ).filter(
            Q(end_date__isnull=True) | Q(end_date__gte=period_start)
        )
    )

    created_bills = []
    for member in active_members:
        if Bill.objects.filter(
            society=society,
            member=member,
            bill_period_start=period_start,
            bill_period_end=period_end,
        ).exists():
            continue

        # One bill per member/period; all due templates become bill lines.
        receivable_account = member.receivable_account or due_templates[0].receivable_account
        if receivable_account is None:
            raise ValidationError(
                f"No receivable account configured for member {member.full_name}."
            )

        due_days = max(template.due_days for template in due_templates)
        due_date = bill_date + timedelta(days=due_days)

        with transaction.atomic():
            bill = Bill.objects.create(
                society=society,
                member=member,
                unit=member.unit,
                receivable_account=receivable_account,
                bill_number=_next_bill_number(society),
                bill_period_start=period_start,
                bill_period_end=period_end,
                bill_date=bill_date,
                due_date=due_date,
                total_amount=Decimal("0.00"),
            )
            for template in due_templates:
                BillLine.objects.create(
                    bill=bill,
                    charge_template=template,
                    description=template.name,
                    amount=template.amount,
                    income_account=template.income_account,
                )
            _post_bill_voucher(bill)
            created_bills.append(bill)
    return created_bills


def apply_late_fees(*, society, as_of_date):
    updated = 0
    bills = (
        Bill.objects.filter(
            society=society,
            due_date__lt=as_of_date,
            status__in=(Bill.BillStatus.OPEN, Bill.BillStatus.PARTIAL, Bill.BillStatus.OVERDUE),
        )
        .prefetch_related("lines__charge_template")
        .order_by("id")
    )
    for bill in bills:
        max_fee_percent = Decimal("0.00")
        for line in bill.lines.all():
            if line.charge_template and line.charge_template.late_fee_percent > max_fee_percent:
                max_fee_percent = line.charge_template.late_fee_percent
        if max_fee_percent <= 0:
            bill.refresh_status(as_of_date=as_of_date)
            continue
        base_amount = bill.total_amount - bill.penalty_amount
        penalty = (base_amount * max_fee_percent) / Decimal("100.00")
        if penalty <= bill.penalty_amount:
            bill.refresh_status(as_of_date=as_of_date)
            continue
        delta = penalty - bill.penalty_amount
        bill.penalty_amount = penalty
        bill.total_amount += delta
        bill.save(update_fields=["penalty_amount", "total_amount"])
        bill.refresh_status(as_of_date=as_of_date)
        updated += 1
    return updated
