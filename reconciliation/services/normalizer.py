import logging
import re
from decimal import Decimal
from typing import Optional

from reconciliation.models import (
    BankTransaction,
    BankTransactionNormalized,
)
from reconciliation.services.parsers.base import BaseStatementParser

logger = logging.getLogger(__name__)


class NormalizerService:
    """
    Extracts structured data from raw bank transaction narrations.

    Populates BankTransactionNormalized records with:
      - Cleaned narration text
      - Extracted UTR / transaction reference number
      - Extracted flat/unit number
      - Extracted general reference
      - Extracted amount in words

    Designed to run automatically after statement import,
    before the matching engine executes.
    """

    # UTR / transaction reference patterns (case-insensitive)
    UTR_PATTERNS = [
        # Standard UTR format: 12-22 alphanumeric chars
        re.compile(
            r'(?:UTR|Ref|Reference|Transaction\s*Ref|Txn\s*Ref)'
            r'(?:\s*No[.:]?)?[:\-\s]*'
            r'[\'"]?([A-Z0-9]{8,22})[\'"]?',
            re.IGNORECASE,
        ),
        # NEFT/RTGS/IMPS reference
        re.compile(
            r'(?:NEFT|RTGS|IMPS|UPI)[:\-\s]+'
            r'(?:Ref\w*[:\-\s]*)?'
            r'([A-Z0-9]{8,22})',
            re.IGNORECASE,
        ),
        # Generic reference number pattern
        re.compile(
            r'(?:Ref|Reference)(?:\s*(?:No|Number|#|ID))?[:\-\s]+'
            r'([A-Z0-9]{6,30})',
            re.IGNORECASE,
        ),
        # UPI transaction ID
        re.compile(
            r'(?:UPI[/\-\s]+)?([0-9]{12,14})',
            re.IGNORECASE,
        ),
    ]

    # Flat/unit number extraction patterns
    FLAT_PATTERNS = [
        # Named flats: Flat A-302, Apt 101, Unit B4
        re.compile(
            r'(?:Flat|Apt|Apartment|Unit|#)\s*[:\-\s]*'
            r'([A-Z]?\d{1,4}(?:[A-Z])?)',
            re.IGNORECASE,
        ),
        # Compact format: A302, B-101
        re.compile(
            r'\b([A-Z][-]?\d{2,4})\b',
        ),
        # "Flat no" explicit
        re.compile(
            r'flat\s*(?:no|number)?[.:\-\s]*(\d{1,4}[A-Z]?)',
            re.IGNORECASE,
        ),
    ]

    # Amount-in-words extraction
    AMOUNT_WORDS_PATTERNS = [
        # "Rupees One Thousand Only"
        re.compile(
            r'(?:Rupees|INR|Rs\.?)\s*([A-Za-z\s]+(?:\d+)?)\s*(?:Only|/-\))',
            re.IGNORECASE,
        ),
        # "Amount in words: ..."
        re.compile(
            r'Amount\s*(?:in\s*words)?[:\-\s]*([A-Za-z\s]+(?:\d+)?)',
            re.IGNORECASE,
        ),
    ]

    # Common noise words to strip from narrations
    NOISE_WORDS = {
        "by", "to", "from", "the", "and", "for", "via", "transfer",
        "net", "online", "payment", "paid", "received", "credit", "debit",
    }

    def __init__(self, society):
        self.society = society

    def normalize_transaction(
        self,
        bank_transaction: BankTransaction,
    ) -> BankTransactionNormalized:
        """
        Normalize a single bank transaction.

        If a normalized record already exists, returns the existing one.
        """
        existing = BankTransactionNormalized.objects.filter(
            bank_transaction=bank_transaction
        ).first()
        if existing:
            return existing

        narration = bank_transaction.narration or ""
        ref_no = bank_transaction.reference_no or ""

        utr = self._extract_utr(narration)
        if not utr and ref_no:
            utr = self._extract_utr(ref_no)

        return BankTransactionNormalized.objects.create(
            bank_transaction=bank_transaction,
            cleaned_narration=self._clean_narration(narration),
            extracted_utr=utr,
            extracted_flat_no=self._extract_flat_no(narration),
            extracted_reference=self._extract_reference(narration),
            extracted_amount_words=self._extract_amount_words(narration),
        )

    def normalize_batch(
        self,
        bank_transactions: list[BankTransaction],
    ) -> list[BankTransactionNormalized]:
        """
        Normalize a batch of bank transactions.

        Skips transactions that already have normalized records.
        """
        # Find already-normalized transaction IDs
        existing_ids = set(
            BankTransactionNormalized.objects.filter(
                bank_transaction__in=bank_transactions,
            ).values_list("bank_transaction_id", flat=True)
        )

        to_create = []
        for bt in bank_transactions:
            if bt.id in existing_ids:
                continue

            narration = bt.narration or ""
            ref_no = bt.reference_no or ""
            utr = self._extract_utr(narration)
            if not utr and ref_no:
                utr = self._extract_utr(ref_no)

            to_create.append(
                BankTransactionNormalized(
                    bank_transaction=bt,
                    cleaned_narration=self._clean_narration(narration),
                    extracted_utr=utr,
                    extracted_flat_no=self._extract_flat_no(narration),
                    extracted_reference=self._extract_reference(narration),
                    extracted_amount_words=self._extract_amount_words(narration),
                )
            )

        if to_create:
            normalized = BankTransactionNormalized.objects.bulk_create(
                to_create, batch_size=500
            )
            logger.info(
                "Normalized %d bank transactions (skipped %d already done).",
                len(normalized),
                len(existing_ids),
            )
            return list(normalized)

        return list(
            BankTransactionNormalized.objects.filter(
                bank_transaction__in=bank_transactions,
            )
        )

    def _clean_narration(self, text: str) -> str:
        """Clean and normalize narration text."""
        if not text:
            return ""

        # Collapse whitespace
        cleaned = " ".join(text.strip().split())

        # Remove common prefixes that add no value
        prefixes = [
            "NEFT-", "RTGS-", "IMPS-", "UPI-",
            "BY TRANSFER-", "BY-", "TO-", "FROM-",
        ]
        for prefix in prefixes:
            if cleaned.upper().startswith(prefix.upper()):
                cleaned = cleaned[len(prefix):]

        return cleaned.strip()

    def _extract_utr(self, text: str) -> str:
        """Extract UTR/transaction reference from narration."""
        if not text:
            return ""

        for pattern in self.UTR_PATTERNS:
            match = pattern.search(text)
            if match:
                utr = match.group(1).strip().upper()
                # Filter out obviously invalid matches
                # Accept purely numeric UTRs if long enough (10+ digits)
                # or alphanumeric UTRs of any length >= 6
                if utr.isdigit():
                    if len(utr) >= 8:
                        return utr
                elif len(utr) >= 6:
                    return utr

        return ""

    def _extract_flat_no(self, text: str) -> str:
        """Extract flat/unit number from narration."""
        if not text:
            return ""

        for pattern in self.FLAT_PATTERNS:
            match = pattern.search(text)
            if match:
                flat = match.group(1).strip().upper()
                # Filter out common false positives
                if flat in ("000", "1234", "9999", "NEFT", "RTGS", "IMPS", "UPI"):
                    continue
                if len(flat) >= 2:
                    return flat

        return ""

    def _extract_reference(self, text: str) -> str:
        """Extract a general reference identifier from narration."""
        if not text:
            return ""

        # Try to extract cheque number
        cheque_match = re.search(
            r'(?:Chq|Cheque|Cheque)(?:\s*(?:No|Number))?[:\-\s.]*(\d{4,10})',
            text,
            re.IGNORECASE,
        )
        if cheque_match:
            return cheque_match.group(1)

        # Extract any remaining reference-like tokens
        ref_match = re.search(
            r'(?:ref|reference|txn\s*id)[:\-\s]*([A-Z0-9]{6,20})',
            text,
            re.IGNORECASE,
        )
        if ref_match:
            return ref_match.group(1).upper()

        return ""

    def _extract_amount_words(self, text: str) -> str:
        """Extract amount in words from narration."""
        if not text:
            return ""

        for pattern in self.AMOUNT_WORDS_PATTERNS:
            match = pattern.search(text)
            if match:
                words = match.group(1).strip()
                # Filter out too-short matches
                if len(words) > 3:
                    return " ".join(words.split())

        return ""

    @classmethod
    def is_bank_likely(cls, account_name: str) -> bool:
        """Heuristic to check if an account name likely refers to a bank account."""
        if not account_name:
            return False
        name_lower = account_name.lower()
        bank_keywords = (
            "bank", "hdfc", "icici", "sbi", "axis", "pnb", "bob",
            "canara", "kotak", "yes bank", "idbi", "indusind",
            "current account", "savings account", "overdraft",
        )
        return any(kw in name_lower for kw in bank_keywords)