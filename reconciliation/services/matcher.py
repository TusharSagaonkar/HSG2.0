"""
Matching Engine — Core Reconciliation Logic.

Implements a 6-tier priority-based matching system that compares
bank transactions against accounting ledger entries.

Matching Rules (highest to lowest priority):
  1. UTR Exact Match          → Confidence: 99
  2. Cheque Number Match       → Confidence: 98
  3. Date + Amount + Direction → Confidence: 85
  4. Flat Number + Amount      → Confidence: 75
  5. Narration Similarity      → Confidence: 70
  6. Amount Only (within window)→ Confidence: 50

Auto-match threshold: 85 (rules 1-3)
Suggestion threshold: 50 (rules 4-6)
"""

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from django.db import models as dm
from django.db.models import Q, Sum
from django.utils import timezone

from accounting.models.model_LedgerEntry import LedgerEntry
from accounting.models.model_Voucher import Voucher
from reconciliation.models import (
    BankTransaction,
    BankTransactionNormalized,
    ReconciliationLink,
)

logger = logging.getLogger(__name__)


@dataclass
class MatchCandidate:
    """A potential match between a bank transaction and a ledger entry."""

    bank_transaction: BankTransaction
    ledger_entry: LedgerEntry
    confidence: int
    rule_name: str
    match_type: str = "EXACT"  # EXACT, PARTIAL, SPLIT
    matched_amount: Decimal = field(default=Decimal("0.00"))
    details: dict = field(default_factory=dict)


class MatchingEngine:
    """
    Core reconciliation matching engine.

    Usage:
        engine = MatchingEngine(society)
        results = engine.run_matching(
            bank_transactions=unmatched_bank_txs,
            auto_confirm=True,         # Create MATCHED links for ≥85
            create_suggestions=True,   # Create SUGGESTED links for ≥50
        )
    """

    # Confidence thresholds
    UTR_MATCH_CONFIDENCE = 99
    CHEQUE_MATCH_CONFIDENCE = 98
    DATE_AMOUNT_MATCH_CONFIDENCE = 85
    FLAT_AMOUNT_MATCH_CONFIDENCE = 75
    NARRATION_MATCH_CONFIDENCE = 70
    AMOUNT_ONLY_CONFIDENCE = 50

    AUTO_MATCH_THRESHOLD = 85
    SUGGESTION_THRESHOLD = 50

    # Date window for amount-only matching (days before/after)
    AMOUNT_ONLY_DATE_WINDOW = 7

    # Maximum number of candidates to return per bank transaction
    MAX_CANDIDATES_PER_TX = 10

    def __init__(self, society):
        """
        Args:
            society: The Society instance to reconcile against.
        """
        self.society = society

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_matching(
        self,
        bank_transactions: Optional[list[BankTransaction]] = None,
        *,
        auto_confirm: bool = True,
        create_suggestions: bool = True,
    ) -> dict:
        """
        Execute the full matching pipeline.

        Args:
            bank_transactions: Specific bank transactions to match.
                               If None, matches all unreconciled transactions.
            auto_confirm: If True, auto-creates MATCHED ReconciliationLink
                          records for candidates at/above AUTO_MATCH_THRESHOLD.
            create_suggestions: If True, creates SUGGESTED ReconciliationLink
                                records for candidates at/above SUGGESTION_THRESHOLD
                                but below AUTO_MATCH_THRESHOLD.

        Returns:
            dict with keys:
                - candidates: list of all MatchCandidate objects found
                - auto_matched: list of created MATCHED ReconciliationLinks
                - suggested: list of created SUGGESTED ReconciliationLinks
                - stats: dict with counts per rule
        """
        if bank_transactions is None:
            bank_transactions = self._get_unreconciled_bank_transactions()

        if not bank_transactions:
            logger.info("No unreconciled bank transactions to match.")
            return {
                "candidates": [],
                "auto_matched": [],
                "suggested": [],
                "stats": {},
            }

        # Prefetch normalized data
        tx_ids = [bt.id for bt in bank_transactions]
        normalized_map = {
            n.bank_transaction_id: n
            for n in BankTransactionNormalized.objects.filter(
                bank_transaction_id__in=tx_ids
            ).select_related("bank_transaction")
        }

        # Get all potentially matching ledger entries
        date_range = self._compute_date_range(bank_transactions)
        candidate_entries = self._get_candidate_ledger_entries(date_range)
        logger.info(
            "Matching %d bank transactions against %d ledger entries.",
            len(bank_transactions),
            len(candidate_entries),
        )

        # Run all matching rules
        all_candidates: list[MatchCandidate] = []

        for bt in bank_transactions:
            normalized = normalized_map.get(bt.id)
            tx_candidates = self._match_single_transaction(
                bt, normalized, candidate_entries
            )
            all_candidates.extend(tx_candidates)

        # Sort by confidence descending, then by rule priority
        all_candidates.sort(key=lambda c: (-c.confidence, c.rule_name))

        # Deduplicate: each bank transaction should only have one winning match
        # per ledger entry. Keep the highest confidence match for each pair.
        all_candidates = self._deduplicate_candidates(all_candidates)

        # Create ReconciliationLink records
        auto_matched = []
        suggested = []

        if auto_confirm or create_suggestions:
            auto_matched, suggested = self._persist_matches(
                all_candidates,
                auto_confirm=auto_confirm,
                create_suggestions=create_suggestions,
            )

        # Compute stats
        stats = self._compute_stats(all_candidates)

        return {
            "candidates": all_candidates,
            "auto_matched": auto_matched,
            "suggested": suggested,
            "stats": stats,
        }

    def match_single(
        self,
        bank_transaction: BankTransaction,
    ) -> list[MatchCandidate]:
        """
        Match a single bank transaction against all candidate ledger entries.

        Returns a list of MatchCandidate sorted by confidence descending.
        """
        normalized = BankTransactionNormalized.objects.filter(
            bank_transaction=bank_transaction
        ).first()

        date_range = (
            bank_transaction.value_date or bank_transaction.transaction_date,
            bank_transaction.value_date or bank_transaction.transaction_date,
        )
        candidate_entries = self._get_candidate_ledger_entries(date_range)

        candidates = self._match_single_transaction(
            bank_transaction, normalized, candidate_entries
        )
        candidates.sort(key=lambda c: -c.confidence)

        return candidates[: self.MAX_CANDIDATES_PER_TX]

    def force_match(
        self,
        bank_transaction: BankTransaction,
        ledger_entry: LedgerEntry,
        user,
        remarks: str = "",
    ) -> ReconciliationLink:
        """
        Create a forced match between a bank transaction and ledger entry.

        This bypasses all matching rules and creates a FORCE_MATCHED link.
        """
        amount = self._get_entry_amount(ledger_entry)

        link = ReconciliationLink.objects.create(
            society=self.society,
            voucher_entry=ledger_entry,
            bank_transaction=bank_transaction,
            matched_amount=amount,
            match_type=ReconciliationLink.MatchType.FORCE,
            confidence_score=100,
            matched_by=user,
            matched_at=timezone.now(),
            is_manual=True,
            remarks=remarks,
            status=ReconciliationLink.Status.FORCE_MATCHED,
        )

        logger.info(
            "Force match created: BankTx#%s ↔ LedgerEntry#%s by %s",
            bank_transaction.id,
            ledger_entry.id,
            user,
        )
        return link

    # ------------------------------------------------------------------
    # Matching Rules
    # ------------------------------------------------------------------

    def _match_single_transaction(
        self,
        bt: BankTransaction,
        normalized: Optional[BankTransactionNormalized],
        candidate_entries: list[LedgerEntry],
    ) -> list[MatchCandidate]:
        """Apply all matching rules to a single bank transaction."""
        candidates: list[MatchCandidate] = []

        # Rule 1: UTR Exact Match
        candidates.extend(self._rule_utr_match(bt, normalized, candidate_entries))

        # Rule 2: Cheque Number Match
        candidates.extend(self._rule_cheque_match(bt, candidate_entries))

        # Rule 3: Date + Amount + Direction
        candidates.extend(self._rule_date_amount_match(bt, candidate_entries))

        # Rule 4: Flat Number + Amount
        if normalized and normalized.extracted_flat_no:
            candidates.extend(
                self._rule_flat_amount_match(bt, normalized, candidate_entries)
            )

        # Rule 5: Narration Similarity
        if normalized and normalized.cleaned_narration:
            candidates.extend(
                self._rule_narration_match(bt, normalized, candidate_entries)
            )

        # Rule 6: Amount Only (within date window)
        candidates.extend(self._rule_amount_only(bt, candidate_entries))

        return candidates

    # Rule 1: UTR Exact Match — Confidence: 99
    def _rule_utr_match(
        self,
        bt: BankTransaction,
        normalized: Optional[BankTransactionNormalized],
        entries: list[LedgerEntry],
    ) -> list[MatchCandidate]:
        candidates = []

        utr = None
        if normalized and normalized.extracted_utr:
            utr = normalized.extracted_utr.upper().strip()
        elif bt.reference_no:
            utr = bt.reference_no.upper().strip()

        if not utr or len(utr) < 6:
            return candidates

        for entry in entries:
            voucher_ref = (entry.voucher.reference_number or "").upper().strip()
            if voucher_ref and utr in voucher_ref:
                entry_amount = self._get_entry_amount(entry)
                candidates.append(
                    MatchCandidate(
                        bank_transaction=bt,
                        ledger_entry=entry,
                        confidence=self.UTR_MATCH_CONFIDENCE,
                        rule_name="UTR Exact Match",
                        match_type=(
                            "EXACT" if entry_amount == bt.amount else "PARTIAL"
                        ),
                        matched_amount=(
                            entry_amount if entry_amount <= bt.amount else bt.amount
                        ),
                        details={
                            "utr": utr,
                            "voucher_ref": voucher_ref,
                            "amount_match": entry_amount == bt.amount,
                        },
                    )
                )

        return candidates

    # Rule 2: Cheque Number Match — Confidence: 98
    def _rule_cheque_match(
        self,
        bt: BankTransaction,
        entries: list[LedgerEntry],
    ) -> list[MatchCandidate]:
        candidates = []

        if not bt.cheque_no:
            return candidates

        cheque = bt.cheque_no.strip().upper()

        for entry in entries:
            voucher_ref = (entry.voucher.reference_number or "").strip().upper()
            if cheque and cheque in voucher_ref:
                entry_amount = self._get_entry_amount(entry)
                # Bonus confidence for exact amount match
                conf = self.CHEQUE_MATCH_CONFIDENCE
                if entry_amount == bt.amount:
                    conf = min(conf + 1, 99)

                candidates.append(
                    MatchCandidate(
                        bank_transaction=bt,
                        ledger_entry=entry,
                        confidence=conf,
                        rule_name="Cheque Number Match",
                        match_type=(
                            "EXACT" if entry_amount == bt.amount else "PARTIAL"
                        ),
                        matched_amount=(
                            entry_amount if entry_amount <= bt.amount else bt.amount
                        ),
                        details={
                            "cheque_no": cheque,
                            "amount_match": entry_amount == bt.amount,
                        },
                    )
                )

        return candidates

    # Rule 3: Date + Amount + Direction — Confidence: 85
    def _rule_date_amount_match(
        self,
        bt: BankTransaction,
        entries: list[LedgerEntry],
    ) -> list[MatchCandidate]:
        candidates = []

        for entry in entries:
            entry_amount = self._get_entry_amount(entry)
            voucher_date = entry.voucher.voucher_date

            if bt.amount != entry_amount:
                continue

            if voucher_date != bt.transaction_date:
                continue

            # Direction check: bank CREDIT = money IN (receipt),
            #                  bank DEBIT  = money OUT (payment)
            direction_ok = self._check_direction_match(bt, entry)
            if not direction_ok:
                continue

            candidates.append(
                MatchCandidate(
                    bank_transaction=bt,
                    ledger_entry=entry,
                    confidence=self.DATE_AMOUNT_MATCH_CONFIDENCE,
                    rule_name="Date + Amount Match",
                    match_type="EXACT",
                    matched_amount=bt.amount,
                    details={
                        "date": str(bt.transaction_date),
                        "amount": str(bt.amount),
                    },
                )
            )

        return candidates

    # Rule 4: Flat Number + Amount — Confidence: 75
    def _rule_flat_amount_match(
        self,
        bt: BankTransaction,
        normalized: BankTransactionNormalized,
        entries: list[LedgerEntry],
    ) -> list[MatchCandidate]:
        candidates = []
        flat_no = normalized.extracted_flat_no.upper().strip()

        for entry in entries:
            entry_amount = self._get_entry_amount(entry)

            # Check if entry is linked to a unit and the unit identifier matches.
            if entry.unit:
                unit_identifier = self._get_unit_identifier(entry.unit)
                if flat_no not in unit_identifier.upper():
                    continue
            else:
                # Try matching flat number in voucher narration
                voucher_narration = (entry.voucher.narration or "").upper()
                if flat_no not in voucher_narration:
                    continue

            conf = self.FLAT_AMOUNT_MATCH_CONFIDENCE
            if entry_amount == bt.amount:
                conf = min(conf + 5, 89)

            candidates.append(
                MatchCandidate(
                    bank_transaction=bt,
                    ledger_entry=entry,
                    confidence=conf,
                    rule_name="Flat Number + Amount",
                    match_type=(
                        "EXACT" if entry_amount == bt.amount else "PARTIAL"
                    ),
                    matched_amount=(
                        entry_amount if entry_amount <= bt.amount else bt.amount
                    ),
                    details={
                        "flat_no": flat_no,
                        "amount_match": entry_amount == bt.amount,
                    },
                )
            )

        return candidates

    # Rule 5: Narration Similarity — Confidence: 70
    def _rule_narration_match(
        self,
        bt: BankTransaction,
        normalized: BankTransactionNormalized,
        entries: list[LedgerEntry],
    ) -> list[MatchCandidate]:
        candidates = []
        bank_narration = normalized.cleaned_narration.lower()

        for entry in entries:
            entry_amount = self._get_entry_amount(entry)

            if entry_amount != bt.amount:
                continue

            voucher_narration = (entry.voucher.narration or "").lower()
            if not voucher_narration:
                continue

            similarity = self._text_similarity(bank_narration, voucher_narration)
            if similarity < 0.4:  # Minimum 40% text overlap
                continue

            conf = int(self.NARRATION_MATCH_CONFIDENCE * similarity)

            candidates.append(
                MatchCandidate(
                    bank_transaction=bt,
                    ledger_entry=entry,
                    confidence=conf,
                    rule_name="Narration Similarity",
                    match_type="EXACT",
                    matched_amount=bt.amount,
                    details={
                        "similarity": round(similarity, 2),
                        "bank_narration": bank_narration[:100],
                        "voucher_narration": voucher_narration[:100],
                    },
                )
            )

        return candidates

    # Rule 6: Amount Only — Confidence: 50
    def _rule_amount_only(
        self,
        bt: BankTransaction,
        entries: list[LedgerEntry],
    ) -> list[MatchCandidate]:
        candidates = []
        tx_date = bt.transaction_date

        for entry in entries:
            entry_amount = self._get_entry_amount(entry)

            if entry_amount != bt.amount:
                continue

            voucher_date = entry.voucher.voucher_date
            date_diff = abs((voucher_date - tx_date).days)

            if date_diff > self.AMOUNT_ONLY_DATE_WINDOW:
                continue

            # Confidence decays with date distance
            conf = max(
                self.AMOUNT_ONLY_CONFIDENCE - (date_diff * 2),
                30,
            )

            candidates.append(
                MatchCandidate(
                    bank_transaction=bt,
                    ledger_entry=entry,
                    confidence=conf,
                    rule_name="Amount Only",
                    match_type="EXACT",
                    matched_amount=bt.amount,
                    details={
                        "date_diff_days": date_diff,
                        "voucher_date": str(voucher_date),
                    },
                )
            )

        return candidates

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_unit_identifier(unit) -> str:
        """Return the canonical unit label used for flat-number matching."""
        return str(getattr(unit, "identifier", "") or "")

    def _get_unreconciled_bank_transactions(self) -> list[BankTransaction]:
        """Get bank transactions that have no completed reconciliation link."""
        reconciled_ids = ReconciliationLink.objects.filter(
            society=self.society,
            status__in=[
                ReconciliationLink.Status.MATCHED,
                ReconciliationLink.Status.FORCE_MATCHED,
                ReconciliationLink.Status.IGNORED,
            ],
        ).values_list("bank_transaction_id", flat=True)

        return list(
            BankTransaction.objects.filter(
                bank_statement_import__society=self.society,
            )
            .exclude(id__in=reconciled_ids)
            .exclude(is_duplicate=True)
            .select_related("bank_statement_import")
            .order_by("transaction_date")
        )

    def _get_candidate_ledger_entries(
        self,
        date_range: tuple[date, date],
    ) -> list[LedgerEntry]:
        """
        Get ledger entries that are candidates for reconciliation.

        Filters for:
        - Posted vouchers only
        - Bank payment modes (BANK_TRANSFER, CHEQUE, UPI)
        - Within the date range (with some buffer)
        """
        min_date, max_date = date_range
        buffer = timedelta(days=self.AMOUNT_ONLY_DATE_WINDOW + 3)

        # Already reconciled entries (fully matched)
        reconciled_ids = ReconciliationLink.objects.filter(
            society=self.society,
            status__in=[
                ReconciliationLink.Status.MATCHED,
                ReconciliationLink.Status.FORCE_MATCHED,
            ],
        ).values_list("voucher_entry_id", flat=True)

        entries = LedgerEntry.objects.filter(
            voucher__society=self.society,
            voucher__posted_at__isnull=False,
            voucher__payment_mode__in=[
                Voucher.PaymentMode.BANK_TRANSFER,
                Voucher.PaymentMode.CHEQUE,
                Voucher.PaymentMode.UPI,
            ],
            voucher__voucher_date__gte=min_date - buffer,
            voucher__voucher_date__lte=max_date + buffer,
        ).exclude(
            id__in=reconciled_ids,
        ).select_related(
            "voucher", "account", "unit"
        ).order_by(
            "voucher__voucher_date"
        )

        return list(entries)

    def _compute_date_range(
        self,
        bank_transactions: list[BankTransaction],
    ) -> tuple[date, date]:
        """Compute the date range spanned by the given bank transactions."""
        dates = [
            bt.value_date or bt.transaction_date for bt in bank_transactions
        ]
        return (min(dates), max(dates))

    @staticmethod
    def _get_entry_amount(entry: LedgerEntry) -> Decimal:
        """Get the absolute amount of a ledger entry (debit or credit)."""
        return entry.debit if entry.debit > 0 else entry.credit

    @staticmethod
    def _check_direction_match(
        bt: BankTransaction, entry: LedgerEntry
    ) -> bool:
        """
        Verify that the bank transaction direction matches the ledger entry.

        Bank CREDIT → Money received → LedgerEntry should DEBIT bank (asset increases)
        Bank DEBIT  → Money paid     → LedgerEntry should CREDIT bank (asset decreases)

        But for reconciliation, we're matching against whichever side is the
        bank/counterparty side, not the bank account itself. So we match:
        - Bank CREDIT (incoming) → Voucher type RECEIPT
        - Bank DEBIT (outgoing)  → Voucher type PAYMENT
        """
        if bt.dr_cr == BankTransaction.DrCr.CREDIT:
            # Money coming in — should match RECEIPT vouchers
            return entry.voucher.voucher_type in (
                Voucher.VoucherType.RECEIPT,
            )
        else:
            # Money going out — should match PAYMENT vouchers
            return entry.voucher.voucher_type in (
                Voucher.VoucherType.PAYMENT,
                Voucher.VoucherType.JOURNAL,
            )

    @staticmethod
    def _text_similarity(a: str, b: str) -> float:
        """
        Compute Jaccard-like similarity between two text strings.

        Uses word-level token overlap. Returns 0.0 to 1.0.
        """
        words_a = set(a.split())
        words_b = set(b.split())

        if not words_a or not words_b:
            return 0.0

        intersection = words_a & words_b
        union = words_a | words_b

        return len(intersection) / len(union)

    def _deduplicate_candidates(
        self,
        candidates: list[MatchCandidate],
    ) -> list[MatchCandidate]:
        """
        Remove duplicate matches for the same (bank_tx, ledger_entry) pair.

        Keeps the candidate with the highest confidence.
        """
        seen: dict[tuple[int, int], MatchCandidate] = {}

        for c in candidates:
            key = (c.bank_transaction.id, c.ledger_entry.id)
            if key not in seen or c.confidence > seen[key].confidence:
                seen[key] = c

        return list(seen.values())

    def _persist_matches(
        self,
        candidates: list[MatchCandidate],
        *,
        auto_confirm: bool,
        create_suggestions: bool,
    ) -> tuple[list[ReconciliationLink], list[ReconciliationLink]]:
        """
        Create ReconciliationLink records from candidates.

        - Candidates at/above AUTO_MATCH_THRESHOLD → MATCHED status
        - Candidates at/above SUGGESTION_THRESHOLD but below AUTO_MATCH_THRESHOLD → SUGGESTED status
        - Skips candidates where a non-PENDING link already exists for the bank transaction
        """
        auto_matched: list[ReconciliationLink] = []
        suggested: list[ReconciliationLink] = []

        # Track which bank transactions already got a non-PENDING link
        bank_tx_already_matched = set(
            ReconciliationLink.objects.filter(
                society=self.society,
                bank_transaction__in=[
                    c.bank_transaction for c in candidates
                ],
            )
            .exclude(status=ReconciliationLink.Status.PENDING)
            .values_list("bank_transaction_id", flat=True)
        )

        for candidate in candidates:
            if candidate.bank_transaction.id in bank_tx_already_matched:
                continue

            if (
                auto_confirm
                and candidate.confidence >= self.AUTO_MATCH_THRESHOLD
            ):
                link = ReconciliationLink.objects.create(
                    society=self.society,
                    voucher_entry=candidate.ledger_entry,
                    bank_transaction=candidate.bank_transaction,
                    matched_amount=candidate.matched_amount,
                    match_type=candidate.match_type,
                    confidence_score=candidate.confidence,
                    is_manual=False,
                    status=ReconciliationLink.Status.MATCHED,
                    remarks=f"Auto-matched: {candidate.rule_name}",
                )
                auto_matched.append(link)
                bank_tx_already_matched.add(candidate.bank_transaction.id)

            elif (
                create_suggestions
                and candidate.confidence >= self.SUGGESTION_THRESHOLD
            ):
                # Check if a suggestion already exists for this pair
                existing = ReconciliationLink.objects.filter(
                    society=self.society,
                    voucher_entry=candidate.ledger_entry,
                    bank_transaction=candidate.bank_transaction,
                    status=ReconciliationLink.Status.SUGGESTED,
                ).first()

                if not existing:
                    suggestion_match_type = candidate.match_type
                    if suggestion_match_type == ReconciliationLink.MatchType.EXACT:
                        suggestion_match_type = ReconciliationLink.MatchType.PARTIAL

                    link = ReconciliationLink.objects.create(
                        society=self.society,
                        voucher_entry=candidate.ledger_entry,
                        bank_transaction=candidate.bank_transaction,
                        matched_amount=candidate.matched_amount,
                        match_type=suggestion_match_type,
                        confidence_score=candidate.confidence,
                        is_manual=False,
                        status=ReconciliationLink.Status.SUGGESTED,
                        remarks=f"Suggested: {candidate.rule_name}",
                    )
                    suggested.append(link)

        logger.info(
            "Persisted %d auto-matches and %d suggestions.",
            len(auto_matched),
            len(suggested),
        )
        return auto_matched, suggested

    @staticmethod
    def _compute_stats(candidates: list[MatchCandidate]) -> dict:
        """Compute matching statistics grouped by rule."""
        stats: dict[str, dict] = {}
        for c in candidates:
            if c.rule_name not in stats:
                stats[c.rule_name] = {"count": 0, "total_confidence": 0}
            stats[c.rule_name]["count"] += 1
            stats[c.rule_name]["total_confidence"] += c.confidence

        for rule_name, data in stats.items():
            data["avg_confidence"] = round(
                data["total_confidence"] / data["count"], 1
            )

        return stats


__all__ = ["MatchingEngine", "MatchCandidate"]
