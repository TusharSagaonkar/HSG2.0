from decimal import Decimal

from billing.models import Bill


def _bucket_for_days(days_overdue):
    if days_overdue <= 0:
        return "current"
    if days_overdue <= 30:
        return "bucket_1_30"
    if days_overdue <= 60:
        return "bucket_31_60"
    if days_overdue <= 90:
        return "bucket_61_90"
    return "bucket_90_plus"


def build_member_outstanding(*, society, as_of_date):
    rows = {}
    bills = (
        Bill.objects.filter(society=society)
        .select_related("member", "unit")
        .order_by("member__full_name", "due_date", "id")
    )

    for bill in bills:
        outstanding = bill.outstanding_amount
        if outstanding <= 0:
            continue
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

    ordered = sorted(rows.values(), key=lambda item: (item["member"].full_name, item["member"].id))
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
