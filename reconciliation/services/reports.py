"""
report.service.py — Bank Reconciliation Reporting Engine.

Provides the ReportService class with static methods for:
  - Bank Reconciliation Statement (BRS)
  - Unmatched Transactions Report
  - Duplicate Detection Report
  - Exception Summary

All methods are scoped to a single Society instance.
Monetary computations use Decimal exclusively for precision.
"""

import logging
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

from accounting.models.model_Account import Account
from accounting.models.model_LedgerEntry import LedgerEntry
from accounting.models.model_Voucher import Voucher
from reconciliation.models import (
    BankTransaction,
    ReconciliationLink,
)

logger = logging.getLogger(__name__)


class ReportService:
    """Stateless reporting service for reconciliation data."""

    # ------------------------------------------------------------------
    # 7.1.1  Bank Reconciliation Statement
    # ------------------------------------------------------------------

    @staticmethod
    def get_brs_data(society, as_of_date=None):
        """
        Compute a full Bank Reconciliation Statement.

        Returns a dict with book balance, bank balance, reconciling items,
        adjusted balances, and match counts.
        """
        if as_of_date is None:
            as_of_date = timezone.localdate()

        # -- leaf bank accounts for the society --
        # Parent grouping accounts (Bank & Cash, Bank Accounts) and cash/fund-transfer
        # accounts are not reconciliation bank accounts.
        bank_accounts = (
            Account.objects.filter(
                society=society,
                is_bank=True,
                is_active=True,
                sub_type=Account.SubType.BANK,
            )
            .annotate(active_child_count=Count("children", filter=Q(children__is_active=True)))
            .filter(active_child_count=0)
            .exclude(name__icontains="cash")
            .exclude(name__icontains="fund transfer")
            .order_by("name", "id")
        )
        bank_account_ids = list(bank_accounts.values_list("id", flat=True))

        # ------------------------------------------------------------------
        # Book balance: sum of posted ledger entries hitting bank accounts.
        # Bank is an ASSET account → DR increases, CR decreases.
        # book_balance = Σ debit - Σ credit
        # ------------------------------------------------------------------
        book_entries = LedgerEntry.objects.filter(
            account_id__in=bank_account_ids,
            voucher__posted_at__isnull=False,
        )
        if as_of_date:
            book_entries = book_entries.filter(
                voucher__voucher_date__lte=as_of_date,
            )

        book_aggregates = book_entries.aggregate(
            total_debit=Sum("debit"),
            total_credit=Sum("credit"),
        )
        book_balance = (
            (book_aggregates["total_debit"] or Decimal("0.00"))
            - (book_aggregates["total_credit"] or Decimal("0.00"))
        )

        book_totals_by_account = {
            row["account_id"]: row
            for row in book_entries.values("account_id").annotate(
                total_debit=Sum("debit"),
                total_credit=Sum("credit"),
            )
        }
        book_balance_by_account = []
        for account in bank_accounts:
            totals = book_totals_by_account.get(account.id, {})
            account_debit = totals.get("total_debit") or Decimal("0.00")
            account_credit = totals.get("total_credit") or Decimal("0.00")
            book_balance_by_account.append(
                {
                    "account_id": account.id,
                    "account_name": account.name,
                    "account_code": account.code or "",
                    "debit_total": account_debit,
                    "credit_total": account_credit,
                    "balance": account_debit - account_credit,
                }
            )

        # ------------------------------------------------------------------
        # Bank balance: Σ credit - Σ debit (from bank's perspective)
        # ------------------------------------------------------------------
        bank_tx_qs = BankTransaction.objects.filter(
            bank_statement_import__society=society,
            bank_statement_import__bank_account_id__in=bank_account_ids,
            is_duplicate=False,
        )
        if as_of_date:
            bank_tx_qs = bank_tx_qs.filter(
                transaction_date__lte=as_of_date,
            )

        bank_aggregates = bank_tx_qs.aggregate(
            total_credit=Sum(
                "amount",
                filter=Q(dr_cr=BankTransaction.DrCr.CREDIT),
            ),
            total_debit=Sum(
                "amount",
                filter=Q(dr_cr=BankTransaction.DrCr.DEBIT),
            ),
        )
        bank_balance = (
            (bank_aggregates["total_credit"] or Decimal("0.00"))
            - (bank_aggregates["total_debit"] or Decimal("0.00"))
        )

        # ------------------------------------------------------------------
        # Reconciling items — use ReconciliationLink to classify
        # ------------------------------------------------------------------
        matched_statuses = [
            ReconciliationLink.Status.MATCHED,
            ReconciliationLink.Status.FORCE_MATCHED,
        ]

        # IDs of ledger entries already linked (matched)
        matched_ledger_ids = set(
            ReconciliationLink.objects.filter(
                society=society,
                status__in=matched_statuses,
                voucher_entry__isnull=False,
            ).values_list("voucher_entry_id", flat=True)
        )

        # IDs of bank transactions already linked (matched)
        matched_bank_ids = set(
            ReconciliationLink.objects.filter(
                society=society,
                status__in=matched_statuses,
                bank_transaction__isnull=False,
            ).values_list("bank_transaction_id", flat=True)
        )

        # All linked bank IDs (any status) for finding truly unmatched items
        any_linked_bank_ids = set(
            ReconciliationLink.objects.filter(
                society=society,
                bank_transaction__isnull=False,
            ).exclude(
                status__in=[
                    ReconciliationLink.Status.REVERSED,
                    ReconciliationLink.Status.IGNORED,
                ],
            ).values_list("bank_transaction_id", flat=True)
        )

        any_linked_ledger_ids = set(
            ReconciliationLink.objects.filter(
                society=society,
                voucher_entry__isnull=False,
            ).exclude(
                status__in=[
                    ReconciliationLink.Status.REVERSED,
                    ReconciliationLink.Status.IGNORED,
                ],
            ).values_list("voucher_entry_id", flat=True)
        )

        # -- Unpresented debits: book debits to bank with no bank match --
        # These are payments recorded in books but not yet reflected in bank.
        unpresented_debits_qs = LedgerEntry.objects.filter(
            account_id__in=bank_account_ids,
            voucher__posted_at__isnull=False,
            debit__gt=0,
        ).exclude(
            id__in=any_linked_ledger_ids,
        ).select_related("voucher", "account").order_by("-voucher__voucher_date")

        if as_of_date:
            unpresented_debits_qs = unpresented_debits_qs.filter(
                voucher__voucher_date__lte=as_of_date,
            )

        unpresented_debits = []
        unpresented_debits_total = Decimal("0.00")
        for entry in unpresented_debits_qs[:500]:
            unpresented_debits.append({
                "date": entry.voucher.voucher_date,
                "narration": entry.voucher.narration or "",
                "voucher": entry.voucher.display_number,
                "amount": entry.debit,
                "account_name": entry.account.name,
                "account_code": entry.account.code or "",
            })
            unpresented_debits_total += entry.debit

        # -- Unpresented credits: book credits to bank with no bank match --
        # These are deposits recorded in books but not yet in bank.
        unpresented_credits_qs = LedgerEntry.objects.filter(
            account_id__in=bank_account_ids,
            voucher__posted_at__isnull=False,
            credit__gt=0,
        ).exclude(
            id__in=any_linked_ledger_ids,
        ).select_related("voucher", "account").order_by("-voucher__voucher_date")

        if as_of_date:
            unpresented_credits_qs = unpresented_credits_qs.filter(
                voucher__voucher_date__lte=as_of_date,
            )

        unpresented_credits = []
        unpresented_credits_total = Decimal("0.00")
        for entry in unpresented_credits_qs[:500]:
            unpresented_credits.append({
                "date": entry.voucher.voucher_date,
                "narration": entry.voucher.narration or "",
                "voucher": entry.voucher.display_number,
                "amount": entry.credit,
                "account_name": entry.account.name,
                "account_code": entry.account.code or "",
            })
            unpresented_credits_total += entry.credit

        # -- Outstanding cheques: bank debits not in books --
        outstanding_qs = BankTransaction.objects.filter(
            bank_statement_import__society=society,
            bank_statement_import__bank_account_id__in=bank_account_ids,
            dr_cr=BankTransaction.DrCr.DEBIT,
            is_duplicate=False,
        ).exclude(
            id__in=any_linked_bank_ids,
        ).select_related("bank_statement_import__bank_account").order_by("-transaction_date")

        if as_of_date:
            outstanding_qs = outstanding_qs.filter(
                transaction_date__lte=as_of_date,
            )

        outstanding_cheques = []
        outstanding_total = Decimal("0.00")
        for tx in outstanding_qs[:500]:
            bank_account = tx.bank_statement_import.bank_account
            outstanding_cheques.append({
                "date": tx.transaction_date,
                "narration": tx.narration,
                "reference": tx.reference_no or "",
                "amount": tx.amount,
                "account_name": bank_account.name,
                "account_code": bank_account.code or "",
            })
            outstanding_total += tx.amount

        # -- Uncredited items: bank credits not in books --
        uncredited_qs = BankTransaction.objects.filter(
            bank_statement_import__society=society,
            bank_statement_import__bank_account_id__in=bank_account_ids,
            dr_cr=BankTransaction.DrCr.CREDIT,
            is_duplicate=False,
        ).exclude(
            id__in=any_linked_bank_ids,
        ).select_related("bank_statement_import__bank_account").order_by("-transaction_date")

        if as_of_date:
            uncredited_qs = uncredited_qs.filter(
                transaction_date__lte=as_of_date,
            )

        uncredited_items = []
        uncredited_total = Decimal("0.00")
        for tx in uncredited_qs[:500]:
            bank_account = tx.bank_statement_import.bank_account
            uncredited_items.append({
                "date": tx.transaction_date,
                "narration": tx.narration,
                "reference": tx.reference_no or "",
                "amount": tx.amount,
                "account_name": bank_account.name,
                "account_code": bank_account.code or "",
            })
            uncredited_total += tx.amount

        # ------------------------------------------------------------------
        # Adjusted balances
        # ------------------------------------------------------------------
        adjusted_book_balance = (
            book_balance
            + unpresented_credits_total
            - unpresented_debits_total
        )
        adjusted_bank_balance = (
            bank_balance
            + uncredited_total
            - outstanding_total
        )
        difference = adjusted_book_balance - adjusted_bank_balance
        is_balanced = difference == Decimal("0.00")

        # ------------------------------------------------------------------
        # Counts
        # ------------------------------------------------------------------
        link_status_counts = dict(
            ReconciliationLink.objects.filter(society=society)
            .values("status")
            .annotate(count=Count("id"))
            .values_list("status", "count")
        )

        matched_count = sum(
            link_status_counts.get(s, 0) for s in matched_statuses
        )
        exception_count = link_status_counts.get(
            ReconciliationLink.Status.EXCEPTION, 0
        )
        unmatched_count = (
            link_status_counts.get(ReconciliationLink.Status.PENDING, 0)
            + link_status_counts.get(ReconciliationLink.Status.SUGGESTED, 0)
            + unpresented_debits_qs.count()
            + unpresented_credits_qs.count()
            + outstanding_qs.count()
            + uncredited_qs.count()
        )

        active_links = (
            ReconciliationLink.objects.filter(
                society=society,
                bank_transaction_id__in=bank_tx_qs.values("id"),
            )
            .exclude(
                status__in=[
                    ReconciliationLink.Status.REVERSED,
                    ReconciliationLink.Status.IGNORED,
                ],
            )
            .select_related("voucher_entry__voucher")
            .order_by("-matched_at", "-id")
        )
        bank_link_by_transaction_id = {}
        for link in active_links:
            bank_link_by_transaction_id.setdefault(link.bank_transaction_id, link)

        bank_statement_rows = []
        for tx in bank_tx_qs.select_related("bank_statement_import__bank_account").order_by("-transaction_date", "-id")[:500]:
            link = bank_link_by_transaction_id.get(tx.id)
            voucher = link.voucher_entry.voucher if link and link.voucher_entry_id else None
            bank_account = tx.bank_statement_import.bank_account
            bank_statement_rows.append({
                "transaction_id": tx.id,
                "date": tx.transaction_date,
                "value_date": tx.value_date,
                "account_name": bank_account.name if bank_account else "",
                "account_code": bank_account.code if bank_account else "",
                "narration": tx.narration,
                "reference": tx.reference_no or "",
                "cheque_no": tx.cheque_no or "",
                "debit": tx.amount if tx.dr_cr == BankTransaction.DrCr.DEBIT else Decimal("0.00"),
                "credit": tx.amount if tx.dr_cr == BankTransaction.DrCr.CREDIT else Decimal("0.00"),
                "balance": tx.balance,
                "is_matched": link is not None and link.status in matched_statuses,
                "match_status": link.status if link else "UNMATCHED",
                "match_type": link.match_type if link else "",
                "matched_amount": link.matched_amount if link else Decimal("0.00"),
                "voucher_id": voucher.id if voucher else None,
                "voucher_number": voucher.display_number if voucher else "",
            })

        return {
            "book_balance": book_balance,
            "book_balance_by_account": book_balance_by_account,
            "bank_balance": bank_balance,
            "bank_statement_rows": bank_statement_rows,
            "bank_statement_count": bank_tx_qs.count(),
            "unpresented_credits": unpresented_credits,
            "unpresented_credits_total": unpresented_credits_total,
            "unpresented_debits": unpresented_debits,
            "unpresented_debits_total": unpresented_debits_total,
            "uncredited_items": uncredited_items,
            "uncredited_total": uncredited_total,
            "outstanding_cheques": outstanding_cheques,
            "outstanding_total": outstanding_total,
            "adjusted_book_balance": adjusted_book_balance,
            "adjusted_bank_balance": adjusted_bank_balance,
            "is_balanced": is_balanced,
            "difference": difference,
            "matched_count": matched_count,
            "unmatched_count": unmatched_count,
            "exception_count": exception_count,
            "bank_accounts": list(
                bank_accounts.values("id", "name", "code")
            ),
        }

    # ------------------------------------------------------------------
    # 7.1.2  Unmatched Report
    # ------------------------------------------------------------------

    @staticmethod
    def get_unmatched_report(society):
        """
        Return book entries and bank transactions that have no
        reconciliation link at all.
        """
        bank_account_ids = list(
            Account.objects.filter(
                society=society,
                is_bank=True,
                is_active=True,
            ).values_list("id", flat=True)
        )

        # Ledger entry IDs that already have at least one active link
        linked_ledger_ids = set(
            ReconciliationLink.objects.filter(
                society=society,
                voucher_entry__isnull=False,
            ).exclude(
                status__in=[
                    ReconciliationLink.Status.REVERSED,
                    ReconciliationLink.Status.IGNORED,
                ],
            ).values_list("voucher_entry_id", flat=True)
        )

        # Bank transaction IDs that already have at least one active link
        linked_bank_ids = set(
            ReconciliationLink.objects.filter(
                society=society,
                bank_transaction__isnull=False,
            ).exclude(
                status__in=[
                    ReconciliationLink.Status.REVERSED,
                    ReconciliationLink.Status.IGNORED,
                ],
            ).values_list("bank_transaction_id", flat=True)
        )

        # -- Book-only: ledger entries in bank accounts with no link --
        book_only_qs = LedgerEntry.objects.filter(
            account_id__in=bank_account_ids,
            voucher__posted_at__isnull=False,
        ).exclude(
            id__in=linked_ledger_ids,
        ).select_related("voucher", "account", "unit").order_by(
            "-voucher__voucher_date",
        )

        book_only = []
        book_only_total = Decimal("0.00")
        for entry in book_only_qs[:500]:
            book_only.append({
                "date": entry.voucher.voucher_date,
                "account": entry.account.name,
                "narration": entry.voucher.narration or "",
                "voucher": entry.voucher.display_number,
                "voucher_id": entry.voucher_id,
                "debit": entry.debit,
                "credit": entry.credit,
            })
            book_only_total += entry.debit + entry.credit

        # -- Bank-only: bank transactions with no link --
        bank_only_qs = BankTransaction.objects.filter(
            bank_statement_import__society=society,
            is_duplicate=False,
        ).exclude(
            id__in=linked_bank_ids,
        ).select_related(
            "bank_statement_import", "normalized",
        ).order_by("-transaction_date")

        bank_only = []
        bank_only_total = Decimal("0.00")
        for tx in bank_only_qs[:500]:
            extracted_info = {}
            if hasattr(tx, "normalized") and tx.normalized:
                normalized = tx.normalized
                extracted_info = {
                    "extracted_utr": getattr(normalized, "extracted_utr", "") or "",
                    "extracted_flat_no": getattr(normalized, "extracted_flat_no", "") or "",
                    "extracted_member_name": getattr(normalized, "extracted_member_name", "") or "",
                    "predicted_account_code": getattr(normalized, "predicted_account_code", "") or "",
                }
            bank_only.append({
                "date": tx.transaction_date,
                "narration": tx.narration,
                "reference": tx.reference_no or "",
                "dr_cr": tx.dr_cr,
                "amount": tx.amount,
                "id": tx.id,
                "extracted_info": extracted_info,
            })
            bank_only_total += tx.amount

        return {
            "book_only": book_only,
            "bank_only": bank_only,
            "book_only_total": book_only_total,
            "bank_only_total": bank_only_total,
        }

    # ------------------------------------------------------------------
    # 7.1.3  Duplicates Report
    # ------------------------------------------------------------------

    @staticmethod
    def get_duplicates_report(society):
        """
        Return duplicate reconciliation links, duplicate-flagged bank
        transactions, and suspected book duplicates.
        """
        # Duplicate links
        duplicate_links = ReconciliationLink.objects.filter(
            society=society,
            status=ReconciliationLink.Status.DUPLICATE,
        ).select_related(
            "bank_transaction",
            "bank_transaction__bank_statement_import",
            "voucher_entry",
            "voucher_entry__voucher",
        ).order_by("-id")

        # Duplicate-flagged bank transactions
        duplicate_bank = BankTransaction.objects.filter(
            bank_statement_import__society=society,
            is_duplicate=True,
        ).select_related(
            "bank_statement_import",
        ).order_by("-transaction_date")

        duplicate_bank_list = []
        for tx in duplicate_bank[:500]:
            duplicate_bank_list.append({
                "date": tx.transaction_date,
                "narration": tx.narration,
                "amount": tx.amount,
                "hash": tx.duplicate_hash or "",
                "dr_cr": tx.dr_cr,
                "reference": tx.reference_no or "",
                "import_filename": (
                    tx.bank_statement_import.original_filename
                    if tx.bank_statement_import else ""
                ),
                "import_id": tx.bank_statement_import_id,
            })

        # Suspected book duplicates: same date + same amount ledger entries
        # hitting bank accounts. Use raw aggregation.
        bank_account_ids = list(
            Account.objects.filter(
                society=society,
                is_bank=True,
                is_active=True,
            ).values_list("id", flat=True)
        )

        suspected_dups = (
            LedgerEntry.objects.filter(
                account_id__in=bank_account_ids,
                voucher__posted_at__isnull=False,
            )
            .values(
                "voucher__voucher_date",
                "account__name",
                "debit",
                "credit",
            )
            .annotate(
                count=Count("id"),
                total_amount=Sum("debit") + Sum("credit"),
            )
            .filter(count__gt=1)
            .order_by("-voucher__voucher_date")
        )

        suspected_book_duplicates = []
        for row in suspected_dups[:200]:
            suspected_book_duplicates.append({
                "date": row["voucher__voucher_date"],
                "account": row["account__name"],
                "amount": row["total_amount"] or Decimal("0.00"),
                "count": row["count"],
            })

        return {
            "duplicate_links": duplicate_links,
            "duplicate_bank_transactions": duplicate_bank_list,
            "suspected_book_duplicates": suspected_book_duplicates,
        }

    # ------------------------------------------------------------------
    # 7.1.4  Exception Summary
    # ------------------------------------------------------------------

    @staticmethod
    def get_exception_summary(society):
        """
        Return exception breakdown by type with totals.
        """
        exception_qs = ReconciliationLink.objects.filter(
            society=society,
            status__in=[
                ReconciliationLink.Status.EXCEPTION,
                ReconciliationLink.Status.DUPLICATE,
                ReconciliationLink.Status.PARTIAL,
            ],
        ).select_related(
            "bank_transaction",
            "bank_transaction__bank_statement_import",
            "voucher_entry",
            "voucher_entry__voucher",
            "voucher_entry__account",
        )

        by_type = defaultdict(int)
        total_amount = Decimal("0.00")

        for link in exception_qs:
            exc_type = link.exception_type or "UNCATEGORISED"
            by_type[exc_type] += 1
            if link.bank_transaction:
                total_amount += link.bank_transaction.amount

        return {
            "by_type": dict(by_type),
            "total_exceptions": exception_qs.count(),
            "total_amount": total_amount,
            "exceptions": exception_qs,
        }
