from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Sum

from accounting.models import LedgerEntry


DR_ACCOUNT_TYPES = {"ASSET", "EXPENSE"}
CR_ACCOUNT_TYPES = {"LIABILITY", "INCOME", "EQUITY"}


def normal_side_for_account_type(account_type: str) -> str:
    if account_type in DR_ACCOUNT_TYPES:
        return "DR"
    if account_type in CR_ACCOUNT_TYPES:
        return "CR"
    raise ValueError(f"Unsupported account type: {account_type}")


def running_balance_delta(normal_side: str, debit: Decimal, credit: Decimal) -> Decimal:
    if normal_side == "DR":
        return debit - credit
    if normal_side == "CR":
        return credit - debit
    raise ValueError(f"Unsupported normal side: {normal_side}")


@dataclass(frozen=True)
class LedgerLine:
    entry: LedgerEntry
    running_balance: Decimal
    balance_side: str


def build_account_ledger(account, *, society=None, financial_year=None, to_date=None):
    target_society = society or account.society
    normal_side = normal_side_for_account_type(account.category.account_type)

    queryset = (
        LedgerEntry.objects.select_related("voucher", "voucher__society", "account")
        .filter(
            account=account,
            voucher__posted_at__isnull=False,
            voucher__society=target_society,
        )
        .order_by("voucher__voucher_date", "voucher_id", "id")
    )

    if financial_year is not None:
        queryset = queryset.filter(
            voucher__voucher_date__gte=financial_year.start_date,
            voucher__voucher_date__lte=financial_year.end_date,
        )

    if to_date is not None:
        queryset = queryset.filter(voucher__voucher_date__lte=to_date)

    running = Decimal("0.00")
    lines = []
    for entry in queryset:
        running += running_balance_delta(normal_side, entry.debit, entry.credit)
        side = normal_side if running >= 0 else ("CR" if normal_side == "DR" else "DR")
        lines.append(
            LedgerLine(
                entry=entry,
                running_balance=abs(running),
                balance_side=side,
            )
        )
    return lines


def build_trial_balance(*, society, financial_year=None, to_date=None):
    queryset = LedgerEntry.objects.filter(
        voucher__posted_at__isnull=False,
        voucher__society=society,
    )

    if financial_year is not None:
        queryset = queryset.filter(
            voucher__voucher_date__gte=financial_year.start_date,
            voucher__voucher_date__lte=financial_year.end_date,
        )

    if to_date is not None:
        queryset = queryset.filter(voucher__voucher_date__lte=to_date)

    grouped = (
        queryset.values(
            "account_id",
            "account__name",
            "account__category__account_type",
        )
        .annotate(
            total_debit=Sum("debit"),
            total_credit=Sum("credit"),
        )
        .order_by("account__name", "account_id")
    )

    rows = []
    grand_total_debit = Decimal("0.00")
    grand_total_credit = Decimal("0.00")
    total_balance_debit = Decimal("0.00")
    total_balance_credit = Decimal("0.00")

    for item in grouped:
        total_debit = item["total_debit"] or Decimal("0.00")
        total_credit = item["total_credit"] or Decimal("0.00")
        account_type = item["account__category__account_type"]
        normal_side = normal_side_for_account_type(account_type)

        grand_total_debit += total_debit
        grand_total_credit += total_credit

        natural_net = running_balance_delta(normal_side, total_debit, total_credit)
        balance_side = normal_side if natural_net >= 0 else ("CR" if normal_side == "DR" else "DR")
        balance_amount = abs(natural_net)
        balance_debit = balance_amount if balance_side == "DR" else Decimal("0.00")
        balance_credit = balance_amount if balance_side == "CR" else Decimal("0.00")

        total_balance_debit += balance_debit
        total_balance_credit += balance_credit

        rows.append(
            {
                "account_id": item["account_id"],
                "account_name": item["account__name"],
                "account_type": account_type,
                "normal_side": normal_side,
                "total_debit": total_debit,
                "total_credit": total_credit,
                "balance_side": balance_side,
                "balance_amount": balance_amount,
                "balance_debit": balance_debit,
                "balance_credit": balance_credit,
            }
        )

    return {
        "rows": rows,
        "grand_total_debit": grand_total_debit,
        "grand_total_credit": grand_total_credit,
        "total_balance_debit": total_balance_debit,
        "total_balance_credit": total_balance_credit,
        "is_balanced": grand_total_debit == grand_total_credit,
    }
