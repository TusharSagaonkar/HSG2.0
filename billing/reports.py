from decimal import Decimal

from django.db.models import DecimalField
from django.db.models import ExpressionWrapper
from django.db.models import F
from django.db.models import Sum
from django.db.models import Value
from django.db.models.functions import Coalesce

from billing.models import Bill

BUCKET_1_30_MAX = 30
BUCKET_31_60_MAX = 60
BUCKET_61_90_MAX = 90


def _bucket_for_days(days_overdue):
    if days_overdue <= 0:
        return "current"
    if days_overdue <= BUCKET_1_30_MAX:
        return "bucket_1_30"
    if days_overdue <= BUCKET_31_60_MAX:
        return "bucket_31_60"
    if days_overdue <= BUCKET_61_90_MAX:
        return "bucket_61_90"
    return "bucket_90_plus"


def build_member_outstanding(*, society, as_of_date):
    rows = {}
    money_field = DecimalField(max_digits=12, decimal_places=2)
    zero = Value(0, output_field=money_field)
    allocated_amount = Coalesce(Sum("receipt_allocations__amount"), zero)
    outstanding_amount = ExpressionWrapper(
        F("total_amount") - allocated_amount,
        output_field=money_field,
    )
    bills = (
        Bill.objects.filter(society=society)
        .select_related(
            "member",
            "member__society",
            "unit",
            "unit__structure",
            "unit__structure__society",
        )
        .annotate(outstanding_amount_value=outstanding_amount)
        .filter(outstanding_amount_value__gt=0)
        .order_by("member__full_name", "due_date", "id")
    )

    for bill in bills:
        outstanding = bill.outstanding_amount_value
        member_id = bill.member_id
        row = rows.setdefault(
            member_id,
            {
                "member": bill.member,
                "unit": bill.unit,
                "total_outstanding": Decimal("0.00"),
                "current": Decimal("0.00"),
                "bucket_1_30": Decimal("0.00"),
                "bucket_31_60": Decimal("0.00"),
                "bucket_61_90": Decimal("0.00"),
                "bucket_90_plus": Decimal("0.00"),
            },
        )
        row["total_outstanding"] += outstanding
        days_overdue = (as_of_date - bill.due_date).days
        bucket = _bucket_for_days(days_overdue)
        row[bucket] += outstanding

    ordered = sorted(
        rows.values(),
        key=lambda item: (item["member"].full_name, item["member"].id),
    )
    totals = {
        "total_outstanding": Decimal("0.00"),
        "current": Decimal("0.00"),
        "bucket_1_30": Decimal("0.00"),
        "bucket_31_60": Decimal("0.00"),
        "bucket_61_90": Decimal("0.00"),
        "bucket_90_plus": Decimal("0.00"),
    }
    for row in ordered:
        for key in totals:
            totals[key] += row[key]

    return {
        "rows": ordered,
        "totals": totals,
    }
