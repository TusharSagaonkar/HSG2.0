from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from django.db.models import Count

from accounting.models import Account
from accounting.models import LedgerEntry
from accounting.models import Voucher
from accounting.services.reporting import build_trial_balance


ZERO = Decimal("0.00")

FIXED_ASSET_KEYWORDS = ("fixed", "building", "plant", "machinery", "equipment", "vehicle", "furniture", "asset")
INVENTORY_KEYWORDS = ("inventory", "stock", "material")


def _text_contains_any(value: str, keywords: tuple[str, ...]) -> bool:
    value_lower = (value or "").lower()
    return any(keyword in value_lower for keyword in keywords)


def _is_fixed_asset_account(account: Account) -> bool:
    return (
        account.category.account_type == "ASSET"
        and _text_contains_any(account.name, FIXED_ASSET_KEYWORDS)
        and not _is_cash_bank_account(account)
        and "receivable" not in (account.name or "").lower()
        and "suspense" not in (account.name or "").lower()
    )


def _is_inventory_account(account: Account) -> bool:
    return (
        account.category.account_type == "ASSET"
        and (
            _text_contains_any(account.name, INVENTORY_KEYWORDS)
            or _text_contains_any(account.category.name if account.category else "", INVENTORY_KEYWORDS)
        )
    )


def _bucket_label(age_days: int) -> str:
    if age_days <= 30:
        return "0-30"
    if age_days <= 60:
        return "31-60"
    if age_days <= 90:
        return "61-90"
    return "90+"


def _to_date_or_year_end(financial_year, to_date):
    if to_date is not None:
        return to_date
    if financial_year is not None:
        return financial_year.end_date
    return None


def _is_cash_bank_account(account) -> bool:
    account_name = (account.name or "").lower()
    category_name = (account.category.name if account.category else "").lower()
    return (
        "cash" in account_name
        or "bank" in account_name
        or "cash" in category_name
        or "bank" in category_name
    )


def build_cash_flow_statement(*, society, financial_year=None, to_date=None):
    cutoff = _to_date_or_year_end(financial_year, to_date)
    if cutoff is None:
        return {
            "operating_rows": [],
            "investing_rows": [],
            "financing_rows": [],
            "operating_totals": {"inflow": ZERO, "outflow": ZERO, "net": ZERO},
            "investing_totals": {"inflow": ZERO, "outflow": ZERO, "net": ZERO},
            "financing_totals": {"inflow": ZERO, "outflow": ZERO, "net": ZERO},
            "opening_cash_equivalent": ZERO,
            "closing_cash_equivalent": ZERO,
            "net_change_in_cash": ZERO,
            "is_reconciled": True,
            "status_note": "Reporting date is required.",
        }

    queryset = Voucher.objects.select_related("society").prefetch_related(
        "entries__account__category",
    ).filter(
        society=society,
        posted_at__isnull=False,
        voucher_date__lte=cutoff,
    )
    if financial_year is not None:
        queryset = queryset.filter(
            voucher_date__gte=financial_year.start_date,
            voucher_date__lte=financial_year.end_date,
        )

    operating_rows = []
    investing_rows = []
    financing_rows = []
    opening_cash_equivalent = ZERO
    if financial_year is not None:
        opening_cash_entries = LedgerEntry.objects.select_related("voucher", "account").filter(
            voucher__society=society,
            voucher__posted_at__isnull=False,
            voucher__voucher_date__lt=financial_year.start_date,
            account__category__account_type="ASSET",
        )
        for entry in opening_cash_entries:
            if _is_cash_bank_account(entry.account):
                opening_cash_equivalent += (entry.debit - entry.credit)

    def _target_section_for_voucher(voucher):
        if voucher.voucher_type == Voucher.VoucherType.OPENING:
            return "financing"
        if voucher.voucher_type == Voucher.VoucherType.PAYMENT:
            has_fixed_asset = any(_is_fixed_asset_account(entry.account) for entry in voucher.entries.all())
            return "investing" if has_fixed_asset else "operating"
        if voucher.voucher_type == Voucher.VoucherType.RECEIPT:
            has_liability = any(entry.account.category.account_type == "LIABILITY" for entry in voucher.entries.all())
            return "financing" if has_liability else "operating"

        non_cash_asset_seen = False
        non_cash_equity_or_liability_seen = False
        for entry in voucher.entries.all():
            if _is_cash_bank_account(entry.account):
                continue
            if entry.account.category.account_type == "ASSET":
                non_cash_asset_seen = True
            if entry.account.category.account_type in {"EQUITY", "LIABILITY"}:
                non_cash_equity_or_liability_seen = True
        if voucher.voucher_type == Voucher.VoucherType.OPENING or non_cash_equity_or_liability_seen:
            return "financing"
        if non_cash_asset_seen:
            return "investing"
        return "operating"

    def _to_row(voucher, cash_delta):
        return {
            "voucher_id": voucher.id,
            "voucher_date": voucher.voucher_date,
            "voucher_number": voucher.display_number,
            "voucher_type": voucher.voucher_type,
            "narration": voucher.narration,
            "amount": abs(cash_delta),
            "direction": "inflow" if cash_delta >= ZERO else "outflow",
            "cash_delta": cash_delta,
        }

    vouchers = queryset.order_by("voucher_date", "id")
    for voucher in vouchers:
        cash_delta = ZERO
        for entry in voucher.entries.all():
            if _is_cash_bank_account(entry.account):
                cash_delta += (entry.debit - entry.credit)
        if cash_delta == ZERO:
            continue

        section = _target_section_for_voucher(voucher)
        row = _to_row(voucher, cash_delta)
        if section == "financing":
            financing_rows.append(row)
        elif section == "investing":
            investing_rows.append(row)
        else:
            operating_rows.append(row)

    def _section_totals(rows):
        inflow = sum((item["amount"] for item in rows if item["direction"] == "inflow"), ZERO)
        outflow = sum((item["amount"] for item in rows if item["direction"] == "outflow"), ZERO)
        net = inflow - outflow
        return {
            "inflow": inflow,
            "outflow": outflow,
            "net": net,
        }

    operating_totals = _section_totals(operating_rows)
    investing_totals = _section_totals(investing_rows)
    financing_totals = _section_totals(financing_rows)
    net_change = operating_totals["net"] + investing_totals["net"] + financing_totals["net"]
    closing_cash_equivalent = opening_cash_equivalent + net_change

    return {
        "operating_rows": operating_rows,
        "investing_rows": investing_rows,
        "financing_rows": financing_rows,
        "operating_totals": operating_totals,
        "investing_totals": investing_totals,
        "financing_totals": financing_totals,
        "opening_cash_equivalent": opening_cash_equivalent,
        "closing_cash_equivalent": closing_cash_equivalent,
        "net_change_in_cash": net_change,
        "is_reconciled": (closing_cash_equivalent - opening_cash_equivalent) == net_change,
        "status_note": "",
    }


def _select_primary_bank_account(*, society):
    exact = Account.objects.filter(
        society=society,
        is_active=True,
        category__account_type="ASSET",
        name__in=["Bank Account", "Bank"],
    ).order_by("name", "id").first()
    if exact:
        return exact

    return Account.objects.filter(
        society=society,
        is_active=True,
        category__account_type="ASSET",
        name__icontains="bank",
    ).exclude(
        name__icontains="suspense",
    ).order_by("name", "id").first()


def build_bank_reconciliation_statement(*, society, financial_year=None, to_date=None):
    cutoff = _to_date_or_year_end(financial_year, to_date)
    if cutoff is None:
        return {
            "bank_account_name": None,
            "book_balance": ZERO,
            "add_total": ZERO,
            "less_total": ZERO,
            "adjusted_bank_balance": ZERO,
            "reconciling_rows": [],
            "as_of_date": None,
            "status_note": "Reporting date is required.",
        }

    bank_account = _select_primary_bank_account(society=society)
    if bank_account is None:
        return {
            "bank_account_name": None,
            "book_balance": ZERO,
            "add_total": ZERO,
            "less_total": ZERO,
            "adjusted_bank_balance": ZERO,
            "reconciling_rows": [],
            "as_of_date": cutoff,
            "status_note": "No active bank account found for this society.",
        }

    bank_entries = LedgerEntry.objects.select_related("voucher").filter(
        account=bank_account,
        voucher__posted_at__isnull=False,
        voucher__society=society,
        voucher__voucher_date__lte=cutoff,
    ).order_by("voucher__voucher_date", "voucher_id", "id")
    if financial_year is not None:
        bank_entries = bank_entries.filter(
            voucher__voucher_date__gte=financial_year.start_date,
            voucher__voucher_date__lte=financial_year.end_date,
        )

    book_balance = sum((entry.debit - entry.credit for entry in bank_entries), ZERO)

    suspense_account = Account.objects.filter(
        society=society,
        is_active=True,
        name__icontains="bank suspense",
    ).order_by("id").first()

    reconciling_rows = []
    add_total = ZERO
    less_total = ZERO
    if suspense_account is not None:
        suspense_entries = LedgerEntry.objects.select_related("voucher").filter(
            account=suspense_account,
            voucher__posted_at__isnull=False,
            voucher__society=society,
            voucher__voucher_date__lte=cutoff,
        ).order_by("voucher__voucher_date", "voucher_id", "id")
        if financial_year is not None:
            suspense_entries = suspense_entries.filter(
                voucher__voucher_date__gte=financial_year.start_date,
                voucher__voucher_date__lte=financial_year.end_date,
            )

        for entry in suspense_entries:
            impact = entry.debit - entry.credit
            if impact == ZERO:
                continue
            if impact >= ZERO:
                direction = "Add"
                add_total += impact
            else:
                direction = "Less"
                less_total += abs(impact)
            reconciling_rows.append(
                {
                    "voucher_id": entry.voucher_id,
                    "voucher_date": entry.voucher.voucher_date,
                    "voucher_number": entry.voucher.display_number,
                    "reference": entry.voucher.reference_number,
                    "narration": entry.voucher.narration,
                    "direction": direction,
                    "amount": abs(impact),
                }
            )

    adjusted_bank_balance = book_balance + add_total - less_total
    return {
        "bank_account_name": bank_account.name,
        "book_balance": book_balance,
        "add_total": add_total,
        "less_total": less_total,
        "adjusted_bank_balance": adjusted_bank_balance,
        "reconciling_rows": reconciling_rows,
        "reconciling_count": len(reconciling_rows),
        "is_fully_reconciled": len(reconciling_rows) == 0,
        "as_of_date": cutoff,
        "status_note": "",
    }


def _sum_debit_credit_delta(queryset):
    debit_total = sum((entry.debit for entry in queryset), ZERO)
    credit_total = sum((entry.credit for entry in queryset), ZERO)
    return debit_total - credit_total


def build_fixed_assets_register(*, society, financial_year=None, to_date=None):
    cutoff = _to_date_or_year_end(financial_year, to_date)
    if cutoff is None:
        return {
            "rows": [],
            "totals": {
                "opening": ZERO,
                "additions": ZERO,
                "reductions": ZERO,
                "closing": ZERO,
            },
            "status_note": "Reporting date is required.",
        }

    asset_accounts = Account.objects.filter(
        society=society,
        is_active=True,
        category__account_type="ASSET",
    ).order_by("name", "id")

    rows = []
    total_opening = ZERO
    total_additions = ZERO
    total_reductions = ZERO
    total_closing = ZERO

    negative_closing_count = 0

    for account in asset_accounts:
        if not _is_fixed_asset_account(account):
            continue
        base_entries = LedgerEntry.objects.select_related("voucher").filter(
            account=account,
            voucher__posted_at__isnull=False,
            voucher__society=society,
            voucher__voucher_date__lte=cutoff,
        )

        if financial_year is not None:
            opening_entries = base_entries.filter(voucher__voucher_date__lt=financial_year.start_date)
            period_entries = base_entries.filter(
                voucher__voucher_date__gte=financial_year.start_date,
                voucher__voucher_date__lte=cutoff,
            )
        else:
            opening_entries = LedgerEntry.objects.none()
            period_entries = base_entries

        opening_balance = _sum_debit_credit_delta(opening_entries)
        additions = sum((entry.debit for entry in period_entries), ZERO)
        reductions = sum((entry.credit for entry in period_entries), ZERO)
        closing_balance = opening_balance + additions - reductions
        if closing_balance < ZERO:
            negative_closing_count += 1

        if opening_balance == ZERO and additions == ZERO and reductions == ZERO and closing_balance == ZERO:
            continue

        latest_entry = period_entries.order_by("-voucher__voucher_date", "-voucher_id", "-id").first()
        rows.append(
            {
                "account_id": account.id,
                "account_name": account.name,
                "opening_balance": opening_balance,
                "additions": additions,
                "reductions": reductions,
                "closing_balance": closing_balance,
                "latest_voucher_id": latest_entry.voucher_id if latest_entry else None,
                "latest_voucher_number": latest_entry.voucher.display_number if latest_entry else "",
            }
        )

        total_opening += opening_balance
        total_additions += additions
        total_reductions += reductions
        total_closing += closing_balance

    return {
        "rows": rows,
        "totals": {
            "opening": total_opening,
            "additions": total_additions,
            "reductions": total_reductions,
            "closing": total_closing,
        },
        "negative_closing_count": negative_closing_count,
        "status_note": (
            "Some fixed assets have negative closing balances; check posting and disposals."
            if negative_closing_count > 0
            else ""
        ),
    }


def build_transaction_reconciliation(*, society, financial_year=None, to_date=None):
    cutoff = _to_date_or_year_end(financial_year, to_date)
    if cutoff is None:
        return {
            "rows": [],
            "summary": {
                "matched_count": 0,
                "unmatched_count": 0,
                "exception_count": 0,
            },
            "status_note": "Reporting date is required.",
        }

    bank_account = _select_primary_bank_account(society=society)
    suspense_account = Account.objects.filter(
        society=society,
        is_active=True,
        name__icontains="bank suspense",
    ).order_by("id").first()

    if bank_account is None:
        return {
            "rows": [],
            "summary": {
                "matched_count": 0,
                "unmatched_count": 0,
                "exception_count": 0,
            },
            "status_note": "No active bank account found for reconciliation.",
        }

    vouchers = Voucher.objects.prefetch_related("entries__account").filter(
        society=society,
        posted_at__isnull=False,
    ).exclude(reference_number="")
    vouchers_with_missing_reference = Voucher.objects.prefetch_related("entries__account").filter(
        society=society,
        posted_at__isnull=False,
        voucher_type__in=[Voucher.VoucherType.RECEIPT, Voucher.VoucherType.PAYMENT],
        reference_number="",
    )
    if financial_year is not None:
        vouchers = vouchers.filter(
            voucher_date__gte=financial_year.start_date,
            voucher_date__lte=financial_year.end_date,
        )
        vouchers_with_missing_reference = vouchers_with_missing_reference.filter(
            voucher_date__gte=financial_year.start_date,
            voucher_date__lte=financial_year.end_date,
        )
    if cutoff is not None:
        vouchers = vouchers.filter(voucher_date__lte=cutoff)
        vouchers_with_missing_reference = vouchers_with_missing_reference.filter(voucher_date__lte=cutoff)

    grouped = defaultdict(list)
    for voucher in vouchers.order_by("reference_number", "voucher_date", "id"):
        grouped[voucher.reference_number].append(voucher)

    rows = []
    matched_count = 0
    unmatched_count = 0
    exception_count = 0

    for reference, ref_vouchers in grouped.items():
        bank_debit = ZERO
        bank_credit = ZERO
        suspense_impact = ZERO
        for voucher in ref_vouchers:
            for entry in voucher.entries.all():
                if entry.account_id == bank_account.id:
                    bank_debit += entry.debit
                    bank_credit += entry.credit
                if suspense_account and entry.account_id == suspense_account.id:
                    suspense_impact += (entry.debit - entry.credit)

        if suspense_impact != ZERO:
            status = "Exception"
            status_reason = "Suspense impact exists."
            exception_count += 1
        elif bank_debit > ZERO and bank_credit > ZERO:
            if bank_debit == bank_credit and len(ref_vouchers) >= 2:
                status = "Matched"
                status_reason = "Debit and credit legs are aligned."
                matched_count += 1
            else:
                status = "Exception"
                status_reason = "Debit and credit legs are not equal."
                exception_count += 1
        else:
            status = "Unmatched"
            status_reason = "Only one-sided bank leg found."
            unmatched_count += 1

        latest_voucher = ref_vouchers[-1]
        rows.append(
            {
                "reference": reference,
                "status": status,
                "status_reason": status_reason,
                "voucher_count": len(ref_vouchers),
                "voucher_id": latest_voucher.id,
                "voucher_number": latest_voucher.display_number,
                "voucher_date": latest_voucher.voucher_date,
                "bank_debit": bank_debit,
                "bank_credit": bank_credit,
                "difference": abs(bank_debit - bank_credit),
            }
        )

    for voucher in vouchers_with_missing_reference.order_by("voucher_date", "id"):
        rows.append(
            {
                "reference": f"(missing) {voucher.display_number}",
                "status": "Exception",
                "status_reason": "Settlement voucher missing reference number.",
                "voucher_count": 1,
                "voucher_id": voucher.id,
                "voucher_number": voucher.display_number,
                "voucher_date": voucher.voucher_date,
                "bank_debit": ZERO,
                "bank_credit": ZERO,
                "difference": ZERO,
            }
        )
        exception_count += 1

    return {
        "rows": sorted(
            rows,
            key=lambda item: (item["status"], item["reference"], item["voucher_date"] or cutoff),
        ),
        "summary": {
            "matched_count": matched_count,
            "unmatched_count": unmatched_count,
            "exception_count": exception_count,
        },
        "status_note": "",
    }


def _voucher_scope(*, society, financial_year=None, to_date=None):
    queryset = Voucher.objects.filter(
        society=society,
        posted_at__isnull=False,
    )
    if financial_year is not None:
        queryset = queryset.filter(
            voucher_date__gte=financial_year.start_date,
            voucher_date__lte=financial_year.end_date,
        )
    if to_date is not None:
        queryset = queryset.filter(voucher_date__lte=to_date)
    return queryset


def build_gst_reports(*, society, financial_year=None, to_date=None):
    vouchers = _voucher_scope(society=society, financial_year=financial_year, to_date=to_date)
    configured_gst_accounts = Account.objects.filter(
        society=society,
        is_gst=True,
        gst_type__in=("INPUT", "OUTPUT"),
    )
    legacy_named_gst_accounts = Account.objects.filter(
        society=society,
        name__in=[
            "Output CGST",
            "Output SGST",
            "Output IGST",
            "Input CGST",
            "Input SGST",
            "Input IGST",
        ],
    )
    gst_entries = LedgerEntry.objects.select_related("voucher", "account").filter(
        voucher__in=vouchers,
        account__is_gst=True,
        account__gst_type__in=("INPUT", "OUTPUT"),
    ).order_by("voucher__voucher_date", "voucher_id", "id")

    output_gst = ZERO
    input_tax_credit = ZERO
    rows = []

    for entry in gst_entries:
        if entry.account.gst_type == "INPUT":
            input_tax_credit += entry.debit
            section = "Input Tax Credit"
            amount = entry.debit
        else:
            line_output = max(entry.credit - entry.debit, ZERO)
            output_gst += line_output
            section = "Output GST"
            amount = line_output
        rows.append(
            {
                "voucher_id": entry.voucher_id,
                "voucher_number": entry.voucher.display_number,
                "voucher_date": entry.voucher.voucher_date,
                "section": section,
                "account_name": entry.account.name,
                "amount": amount,
            }
        )

    return {
        "rows": rows,
        "summary": {
            "gstr1_sales": output_gst,
            "gstr3b_output": output_gst,
            "input_tax_credit": input_tax_credit,
            "net_payable": output_gst - input_tax_credit,
            "unmapped_total": ZERO,
        },
        "status_note": (
            ""
            if rows
            else (
                "GST accounts are not configured. Configure Input/Output GST accounts first."
                if not configured_gst_accounts.exists() and not legacy_named_gst_accounts.exists()
                else (
                    "GST account names exist but are not GST-tagged. Set is_gst=True and gst_type on GST accounts."
                    if not configured_gst_accounts.exists() and legacy_named_gst_accounts.exists()
                    else "No posted GST vouchers found in the selected period/society."
                )
            )
        ),
    }


def build_tds_reports(*, society, financial_year=None, to_date=None):
    vouchers = _voucher_scope(society=society, financial_year=financial_year, to_date=to_date)
    tds_entries = LedgerEntry.objects.select_related("voucher", "account").filter(
        voucher__in=vouchers,
        account__name__icontains="tds",
    ).order_by("voucher__voucher_date", "voucher_id", "id")

    deducted = ZERO
    paid_or_adjusted = ZERO
    unmapped_total = ZERO
    rows = []
    for entry in tds_entries:
        account_name = entry.account.name.lower()
        account_type = entry.account.category.account_type
        if "receivable" in account_name and account_type == "ASSET":
            deducted += entry.debit
            paid_or_adjusted += entry.credit
            amount = entry.debit if entry.debit > ZERO else entry.credit
            mapping = "TDS Receivable"
        elif account_type == "LIABILITY":
            deducted += entry.credit
            paid_or_adjusted += entry.debit
            amount = entry.credit if entry.credit > ZERO else entry.debit
            mapping = "TDS Payable"
        else:
            amount = max(entry.debit, entry.credit)
            unmapped_total += amount
            mapping = "Unmapped TDS"

        rows.append(
            {
                "voucher_id": entry.voucher_id,
                "voucher_number": entry.voucher.display_number,
                "voucher_date": entry.voucher.voucher_date,
                "account_name": entry.account.name,
                "debit": entry.debit,
                "credit": entry.credit,
                "amount": amount,
                "mapping": mapping,
            }
        )

    return {
        "rows": rows,
        "summary": {
            "deducted": deducted,
            "paid_or_adjusted": paid_or_adjusted,
            "payable": deducted - paid_or_adjusted,
            "unmapped_total": unmapped_total,
        },
        "status_note": (
            "TDS rows include unmapped accounts; verify TDS account setup."
            if unmapped_total > ZERO
            else ("" if rows else "No TDS-tagged accounts found in posted vouchers.")
        ),
    }


def build_inventory_costing_reports(*, society, financial_year=None, to_date=None):
    cutoff = _to_date_or_year_end(financial_year, to_date)
    if cutoff is None:
        return {
            "rows": [],
            "summary": {"valuation_total": ZERO},
            "status_note": "Reporting date is required.",
        }

    inventory_accounts = Account.objects.filter(
        society=society,
        is_active=True,
        category__account_type="ASSET",
    ).order_by("name", "id")

    rows = []
    opening_total = ZERO
    inward_total = ZERO
    outward_total = ZERO
    valuation_total = ZERO
    for account in inventory_accounts:
        if not _is_inventory_account(account):
            continue

        entries = LedgerEntry.objects.filter(
            account=account,
            voucher__society=society,
            voucher__posted_at__isnull=False,
            voucher__voucher_date__lte=cutoff,
        )
        if financial_year is not None:
            opening_entries = entries.filter(voucher__voucher_date__lt=financial_year.start_date)
            period_entries = entries.filter(
                voucher__voucher_date__gte=financial_year.start_date,
                voucher__voucher_date__lte=financial_year.end_date,
            )
        else:
            opening_entries = LedgerEntry.objects.none()
            period_entries = entries

        opening = _sum_debit_credit_delta(opening_entries)
        inward = sum((entry.debit for entry in period_entries), ZERO)
        outward = sum((entry.credit for entry in period_entries), ZERO)
        valuation = opening + inward - outward
        opening_total += opening
        inward_total += inward
        outward_total += outward
        valuation_total += valuation
        rows.append(
            {
                "account_id": account.id,
                "account_name": account.name,
                "opening": opening,
                "inward": inward,
                "outward": outward,
                "valuation": valuation,
            }
        )

    return {
        "rows": rows,
        "summary": {
            "opening_total": opening_total,
            "inward_total": inward_total,
            "outward_total": outward_total,
            "valuation_total": valuation_total,
        },
        "status_note": "" if rows else "No inventory/stock accounts configured yet.",
    }


def build_management_analytics_reports(*, society, financial_year=None, to_date=None):
    trial_balance = build_trial_balance(
        society=society,
        financial_year=financial_year,
        to_date=to_date,
    )
    pnl = build_profit_and_loss(society=society, financial_year=financial_year, to_date=to_date)

    total_revenue = pnl["total_income"]
    total_expenses = pnl["total_expenses"]
    net_profit = pnl["net_profit"]
    expense_ratio = (total_expenses / total_revenue * Decimal("100.00")) if total_revenue > ZERO else ZERO
    profit_margin = (net_profit / total_revenue * Decimal("100.00")) if total_revenue > ZERO else ZERO

    income_rows = [row for row in trial_balance["rows"] if row["account_type"] == "INCOME"]
    expense_rows = [row for row in trial_balance["rows"] if row["account_type"] == "EXPENSE"]
    top_income = sorted(income_rows, key=lambda row: row["balance_amount"], reverse=True)[:5]
    top_expense = sorted(expense_rows, key=lambda row: row["balance_amount"], reverse=True)[:5]

    return {
        "summary": {
            "revenue": total_revenue,
            "expenses": total_expenses,
            "net_profit": net_profit,
            "expense_ratio": expense_ratio,
            "profit_margin": profit_margin,
            "budget": ZERO,
            "variance": net_profit - ZERO,
        },
        "top_income": top_income,
        "top_expense": top_expense,
        "status_note": "Budget master is not configured yet; variance is currently against zero baseline.",
    }


def build_control_risk_reports(*, society, financial_year=None, to_date=None):
    vouchers = _voucher_scope(society=society, financial_year=financial_year, to_date=to_date).exclude(reference_number="")
    duplicate_groups = (
        vouchers.values("reference_number")
        .annotate(ref_count=Count("id"))
        .filter(ref_count__gt=1)
        .order_by("-ref_count", "reference_number")
    )

    duplicate_rows = []
    for item in duplicate_groups:
        refs = vouchers.filter(reference_number=item["reference_number"]).order_by("voucher_date", "id")
        latest = refs.last()
        duplicate_rows.append(
            {
                "reference": item["reference_number"],
                "count": item["ref_count"],
                "latest_voucher_id": latest.id if latest else None,
                "latest_voucher_number": latest.display_number if latest else "",
            }
        )

    suspense_account = Account.objects.filter(
        society=society,
        is_active=True,
        name__icontains="suspense",
    ).order_by("id").first()
    suspense_balance = ZERO
    if suspense_account:
        suspense_entries = LedgerEntry.objects.filter(
            account=suspense_account,
            voucher__society=society,
            voucher__posted_at__isnull=False,
        )
        if financial_year is not None:
            suspense_entries = suspense_entries.filter(
                voucher__voucher_date__gte=financial_year.start_date,
                voucher__voucher_date__lte=financial_year.end_date,
            )
        if to_date is not None:
            suspense_entries = suspense_entries.filter(voucher__voucher_date__lte=to_date)
        suspense_balance = _sum_debit_credit_delta(suspense_entries)

    unreferenced_settlement_count = _voucher_scope(
        society=society,
        financial_year=financial_year,
        to_date=to_date,
    ).filter(
        voucher_type__in=[Voucher.VoucherType.RECEIPT, Voucher.VoucherType.PAYMENT],
        reference_number="",
    ).count()

    return {
        "duplicate_rows": duplicate_rows,
        "summary": {
            "duplicate_reference_count": len(duplicate_rows),
            "suspense_balance": suspense_balance,
            "unreferenced_settlement_count": unreferenced_settlement_count,
        },
    }


def build_advanced_regulatory_reports(*, society, financial_year=None, to_date=None):
    vouchers = _voucher_scope(society=society, financial_year=financial_year, to_date=to_date)
    total_posted = vouchers.count()
    settlement_vouchers = vouchers.filter(
        voucher_type__in=[Voucher.VoucherType.RECEIPT, Voucher.VoucherType.PAYMENT]
    )
    settlement_count = settlement_vouchers.count()
    unsettled_suspense = ZERO

    suspense_account = Account.objects.filter(
        society=society,
        is_active=True,
        name__icontains="suspense",
    ).first()
    if suspense_account:
        suspense_entries = LedgerEntry.objects.filter(
            account=suspense_account,
            voucher__in=vouchers,
        )
        unsettled_suspense = _sum_debit_credit_delta(suspense_entries)

    trial_balance = build_trial_balance(
        society=society,
        financial_year=financial_year,
        to_date=to_date,
    )
    liquid_balance = ZERO
    for row in trial_balance["rows"]:
        if _text_contains_any(row["account_name"], ("bank", "cash")):
            if row["balance_side"] == "DR":
                liquid_balance += row["balance_amount"]
            else:
                liquid_balance -= row["balance_amount"]

    return {
        "summary": {
            "total_posted_vouchers": total_posted,
            "settlement_voucher_count": settlement_count,
            "unsettled_suspense": unsettled_suspense,
            "liquid_balance": liquid_balance,
        },
        "status_note": "Regulatory exports are currently summarized from accounting books; filing-format exports can be added next.",
    }


def build_profit_and_loss(*, society, financial_year=None, to_date=None):
    trial_balance = build_trial_balance(
        society=society,
        financial_year=financial_year,
        to_date=to_date,
    )
    income_rows = []
    expense_rows = []
    total_income = ZERO
    total_expenses = ZERO

    for row in trial_balance["rows"]:
        amount = row["balance_amount"]
        if row["account_type"] == "INCOME":
            income_rows.append(
                {
                    "account_name": row["account_name"],
                    "amount": amount,
                }
            )
            total_income += amount
        elif row["account_type"] == "EXPENSE":
            expense_rows.append(
                {
                    "account_name": row["account_name"],
                    "amount": amount,
                }
            )
            total_expenses += amount

    net_profit = total_income - total_expenses
    return {
        "income_rows": income_rows,
        "expense_rows": expense_rows,
        "total_income": total_income,
        "total_expenses": total_expenses,
        "net_profit": net_profit,
    }


def build_balance_sheet(*, society, financial_year=None, to_date=None):
    trial_balance = build_trial_balance(
        society=society,
        financial_year=financial_year,
        to_date=to_date,
    )
    pnl = build_profit_and_loss(
        society=society,
        financial_year=financial_year,
        to_date=to_date,
    )

    asset_rows = []
    liability_rows = []
    equity_rows = []
    total_assets = ZERO
    total_liabilities = ZERO
    total_equity = ZERO

    for row in trial_balance["rows"]:
        amount = row["balance_amount"]
        if row["account_type"] == "ASSET":
            asset_rows.append({"account_name": row["account_name"], "amount": amount})
            total_assets += amount
        elif row["account_type"] == "LIABILITY":
            liability_rows.append({"account_name": row["account_name"], "amount": amount})
            total_liabilities += amount
        elif row["account_type"] == "EQUITY":
            equity_rows.append({"account_name": row["account_name"], "amount": amount})
            total_equity += amount

    current_earnings = pnl["net_profit"]
    equity_rows.append(
        {
            "account_name": "Current Period Surplus / Deficit",
            "amount": current_earnings,
        }
    )
    total_equity += current_earnings

    total_liabilities_and_equity = total_liabilities + total_equity
    return {
        "asset_rows": asset_rows,
        "liability_rows": liability_rows,
        "equity_rows": equity_rows,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "total_equity": total_equity,
        "total_liabilities_and_equity": total_liabilities_and_equity,
        "is_balanced": total_assets == total_liabilities_and_equity,
    }


@dataclass
class OpenItem:
    voucher_date: object
    amount: Decimal
    label: str


def _allocate_open_items(items, settlement_amount):
    remaining = settlement_amount
    open_items = []
    for item in items:
        if remaining <= ZERO:
            open_items.append(item)
            continue
        if item.amount <= remaining:
            remaining -= item.amount
            continue
        item.amount -= remaining
        remaining = ZERO
        open_items.append(item)
    return open_items


def build_receivable_aging(*, society, financial_year=None, to_date=None):
    cutoff = _to_date_or_year_end(financial_year, to_date)
    account = Account.objects.filter(society=society, name="Maintenance Receivable").first()
    if account is None or cutoff is None:
        return {
            "rows": [],
            "bucket_totals": {
                "bucket_0_30": ZERO,
                "bucket_31_60": ZERO,
                "bucket_61_90": ZERO,
                "bucket_90_plus": ZERO,
            },
            "grand_total": ZERO,
        }

    queryset = (
        LedgerEntry.objects.select_related("voucher", "unit")
        .filter(
            account=account,
            voucher__posted_at__isnull=False,
            voucher__society=society,
            voucher__voucher_date__lte=cutoff,
        )
        .order_by("unit__identifier", "voucher__voucher_date", "voucher_id", "id")
    )
    if financial_year is not None:
        queryset = queryset.filter(
            voucher__voucher_date__gte=financial_year.start_date,
            voucher__voucher_date__lte=financial_year.end_date,
        )

    by_unit = defaultdict(list)
    for entry in queryset:
        if entry.unit_id:
            by_unit[entry.unit].append(entry)

    rows = []
    bucket_totals = {
        "bucket_0_30": ZERO,
        "bucket_31_60": ZERO,
        "bucket_61_90": ZERO,
        "bucket_90_plus": ZERO,
    }
    grand_total = ZERO

    for unit, entries in by_unit.items():
        open_items = []
        for entry in entries:
            if entry.debit > ZERO:
                open_items.append(
                    OpenItem(
                        voucher_date=entry.voucher.voucher_date,
                        amount=entry.debit,
                        label=entry.voucher.narration,
                    )
                )
            if entry.credit > ZERO:
                open_items = _allocate_open_items(open_items, entry.credit)

        buckets = {"0-30": ZERO, "31-60": ZERO, "61-90": ZERO, "90+": ZERO}
        for item in open_items:
            age_days = (cutoff - item.voucher_date).days
            bucket = _bucket_label(age_days)
            buckets[bucket] += item.amount

        total_outstanding = sum(buckets.values(), ZERO)
        if total_outstanding <= ZERO:
            continue

        bucket_totals["bucket_0_30"] += buckets["0-30"]
        bucket_totals["bucket_31_60"] += buckets["31-60"]
        bucket_totals["bucket_61_90"] += buckets["61-90"]
        bucket_totals["bucket_90_plus"] += buckets["90+"]
        grand_total += total_outstanding
        rows.append(
            {
                "party_name": f"Unit {unit.identifier}",
                "reference": unit.identifier,
                "bucket_0_30": buckets["0-30"],
                "bucket_31_60": buckets["31-60"],
                "bucket_61_90": buckets["61-90"],
                "bucket_90_plus": buckets["90+"],
                "total": total_outstanding,
            }
        )

    return {
        "rows": rows,
        "bucket_totals": bucket_totals,
        "grand_total": grand_total,
    }


def build_payable_aging(*, society, financial_year=None, to_date=None):
    cutoff = _to_date_or_year_end(financial_year, to_date)
    account = Account.objects.filter(society=society, name="Vendor Payable").first()
    if account is None or cutoff is None:
        return {
            "rows": [],
            "bucket_totals": {
                "bucket_0_30": ZERO,
                "bucket_31_60": ZERO,
                "bucket_61_90": ZERO,
                "bucket_90_plus": ZERO,
            },
            "grand_total": ZERO,
        }

    queryset = (
        LedgerEntry.objects.select_related("voucher")
        .filter(
            account=account,
            voucher__posted_at__isnull=False,
            voucher__society=society,
            voucher__voucher_date__lte=cutoff,
        )
        .order_by("voucher__voucher_date", "voucher_id", "id")
    )
    if financial_year is not None:
        queryset = queryset.filter(
            voucher__voucher_date__gte=financial_year.start_date,
            voucher__voucher_date__lte=financial_year.end_date,
        )

    open_items = []
    for entry in queryset:
        if entry.credit > ZERO:
            open_items.append(
                OpenItem(
                    voucher_date=entry.voucher.voucher_date,
                    amount=entry.credit,
                    label=entry.voucher.narration,
                )
            )
        if entry.debit > ZERO:
            open_items = _allocate_open_items(open_items, entry.debit)

    rows = []
    bucket_totals = {
        "bucket_0_30": ZERO,
        "bucket_31_60": ZERO,
        "bucket_61_90": ZERO,
        "bucket_90_plus": ZERO,
    }
    grand_total = ZERO

    for index, item in enumerate(open_items, start=1):
        age_days = (cutoff - item.voucher_date).days
        bucket = _bucket_label(age_days)
        buckets = {"0-30": ZERO, "31-60": ZERO, "61-90": ZERO, "90+": ZERO}
        buckets[bucket] = item.amount
        bucket_totals["bucket_0_30"] += buckets["0-30"]
        bucket_totals["bucket_31_60"] += buckets["31-60"]
        bucket_totals["bucket_61_90"] += buckets["61-90"]
        bucket_totals["bucket_90_plus"] += buckets["90+"]
        grand_total += item.amount
        rows.append(
            {
                "party_name": f"Vendor Item {index}",
                "reference": item.label,
                "bucket_0_30": buckets["0-30"],
                "bucket_31_60": buckets["31-60"],
                "bucket_61_90": buckets["61-90"],
                "bucket_90_plus": buckets["90+"],
                "total": item.amount,
            }
        )

    return {
        "rows": rows,
        "bucket_totals": bucket_totals,
        "grand_total": grand_total,
    }


def build_exception_report(*, society, financial_year=None, to_date=None):
    queryset = Voucher.objects.filter(
        society=society,
        posted_at__isnull=False,
    ).exclude(reference_number="")
    if financial_year is not None:
        queryset = queryset.filter(
            voucher_date__gte=financial_year.start_date,
            voucher_date__lte=financial_year.end_date,
        )
    if to_date is not None:
        queryset = queryset.filter(voucher_date__lte=to_date)

    duplicates = []
    seen = defaultdict(list)
    for voucher in queryset.order_by("reference_number", "voucher_date", "id"):
        seen[voucher.reference_number].append(voucher)
    for reference_number, vouchers in seen.items():
        if len(vouchers) > 1:
            duplicates.append(
                {
                    "exception_type": "Duplicate Reference",
                    "reference": reference_number,
                    "date": vouchers[-1].voucher_date,
                    "amount": sum(
                        (entry.debit for voucher in vouchers for entry in voucher.entries.all() if entry.debit > ZERO),
                        ZERO,
                    ),
                    "details": f"{len(vouchers)} vouchers share the same reference number.",
                }
            )

    suspense_balance = ZERO
    suspense_account = Account.objects.filter(society=society, name="Bank Suspense - Unreconciled").first()
    if suspense_account is not None:
        suspense_trial = build_trial_balance(
            society=society,
            financial_year=financial_year,
            to_date=to_date,
        )
        for row in suspense_trial["rows"]:
            if row["account_name"] == suspense_account.name:
                suspense_balance = row["balance_amount"]
                break

    suspense_rows = []
    if suspense_balance > ZERO:
        suspense_rows.append(
            {
                "exception_type": "Suspense Balance",
                "reference": suspense_account.name,
                "date": to_date or (financial_year.end_date if financial_year else None),
                "amount": suspense_balance,
                "details": "Suspense account has an unreconciled balance.",
            }
        )

    rows = sorted(
        duplicates + suspense_rows,
        key=lambda item: (item["date"] is None, item["date"], item["exception_type"]),
    )
    return {"rows": rows}
