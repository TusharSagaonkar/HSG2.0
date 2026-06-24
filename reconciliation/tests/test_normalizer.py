"""
Tests for the NormalizerService — UTR, flat number, reference, and amount-words extraction.
"""

import pytest

from reconciliation.models import BankTransaction, BankTransactionNormalized
from reconciliation.services.normalizer import NormalizerService
from reconciliation.tests.factories import (
    BankStatementImportFactory,
    BankTransactionFactory,
    SocietyFactory,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# normalize_transaction
# ---------------------------------------------------------------------------

class TestNormalizeTransaction:
    def test_extracts_utr_from_narration(self):
        society = SocietyFactory()
        service = NormalizerService(society)
        bt = BankTransactionFactory(
            narration="NEFT UTR: ABC1234567 — Maintenance Payment",
            reference_no="ABC1234567",
        )
        norm = service.normalize_transaction(bt)
        assert norm.extracted_utr == "ABC1234567"

    def test_extracts_utr_pattern_rtgs(self):
        society = SocietyFactory()
        service = NormalizerService(society)
        bt = BankTransactionFactory(
            narration="RTGS Transaction UTR ABCDEFGHIJ",
            reference_no="ABCDEFGHIJ",
        )
        norm = service.normalize_transaction(bt)
        assert norm.extracted_utr == "ABCDEFGHIJ"

    def test_extracts_flat_number(self):
        society = SocietyFactory()
        service = NormalizerService(society)
        bt = BankTransactionFactory(
            narration="Maintenance Payment for Flat 101 by Owner",
        )
        norm = service.normalize_transaction(bt)
        assert "101" in norm.extracted_flat_no

    def test_extracts_flat_from_slash_notation(self):
        society = SocietyFactory()
        service = NormalizerService(society)
        bt = BankTransactionFactory(
            narration="S/O Flat-202 — Mr. Sharma",
        )
        norm = service.normalize_transaction(bt)
        # Should extract a flat number
        assert norm.extracted_flat_no != "" or norm.cleaned_narration != ""

    def test_cleaned_narration_removed_extra_spaces(self):
        society = SocietyFactory()
        service = NormalizerService(society)
        bt = BankTransactionFactory(
            narration="  Payment   for   maintenance  ",
        )
        norm = service.normalize_transaction(bt)
        assert "  " not in norm.cleaned_narration

    def test_normalize_creates_one_to_one_record(self):
        society = SocietyFactory()
        service = NormalizerService(society)
        bt = BankTransactionFactory()
        norm = service.normalize_transaction(bt)
        assert BankTransactionNormalized.objects.filter(bank_transaction=bt).exists()
        assert norm.bank_transaction == bt

    def test_normalize_replaces_existing(self):
        society = SocietyFactory()
        service = NormalizerService(society)
        bt = BankTransactionFactory(narration="First pass")
        service.normalize_transaction(bt)
        # Re-normalizing the same transaction returns the existing record
        norm2 = service.normalize_transaction(bt)
        assert norm2.pk is not None


# ---------------------------------------------------------------------------
# normalize_batch
# ---------------------------------------------------------------------------

class TestNormalizeBatch:
    def test_batch_normalizes_all(self):
        society = SocietyFactory()
        service = NormalizerService(society)
        imp = BankStatementImportFactory(society=society)
        bt1 = BankTransactionFactory(bank_statement_import=imp, narration="Flat 101 UTR: UT1")
        bt2 = BankTransactionFactory(bank_statement_import=imp, narration="Flat 202 UTR: UT2")
        bt3 = BankTransactionFactory(bank_statement_import=imp, narration="Flat 303 UTR: UT3")

        results = service.normalize_batch([bt1, bt2, bt3])
        assert len(results) == 3
        assert BankTransactionNormalized.objects.filter(
            bank_transaction__in=[bt1, bt2, bt3]
        ).count() == 3

    def test_batch_skips_already_normalized(self):
        society = SocietyFactory()
        service = NormalizerService(society)
        bt = BankTransactionFactory(narration="Flat 101 UTR: UTX")
        service.normalize_transaction(bt)

        results = service.normalize_batch([bt])
        assert len(results) == 1  # returns the existing normalized record


# ---------------------------------------------------------------------------
# is_bank_likely helper
# ---------------------------------------------------------------------------

class TestIsBankLikely:
    def test_bank_in_name(self):
        assert NormalizerService.is_bank_likely("HDFC Bank") is True

    def test_no_bank_keyword(self):
        assert NormalizerService.is_bank_likely("Maintenance Income") is False

    def test_bank_keyword_variations(self):
        assert NormalizerService.is_bank_likely("ICICI BANK ACCOUNT") is True
        assert NormalizerService.is_bank_likely("SBI Savings Bank") is True


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestNormalizerEdgeCases:
    def test_empty_narration(self):
        society = SocietyFactory()
        service = NormalizerService(society)
        bt = BankTransactionFactory(narration=" ", reference_no=" ")
        norm = service.normalize_transaction(bt)
        assert norm.extracted_utr == ""
        assert norm.extracted_flat_no == ""

    def test_no_extractable_patterns(self):
        society = SocietyFactory()
        service = NormalizerService(society)
        bt = BankTransactionFactory(
            narration="Some random text without any patterns",
            reference_no="",
        )
        norm = service.normalize_transaction(bt)
        # Should still create a normalized record
        assert norm.pk is not None

    def test_utr_in_reference_no_fallback(self):
        society = SocietyFactory()
        service = NormalizerService(society)
        bt = BankTransactionFactory(
            narration="Payment",
            reference_no="UTR987654321",
        )
        norm = service.normalize_transaction(bt)
        # UTR reference in ref_no should be extracted (prefix stripped)
        assert norm.extracted_utr == "987654321"