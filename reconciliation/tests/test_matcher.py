"""
Tests for the MatchingEngine — all 6 matching rules, auto-match thresholds,
deduplication, and persistence.
"""

from datetime import date, timedelta

import pytest
from django.utils import timezone

from accounting.models import LedgerEntry, Voucher
from members.models import Structure, Unit
from reconciliation.models import (
    BankTransaction,
    BankTransactionNormalized,
    ReconciliationLink,
)
from reconciliation.services.matcher import MatchingEngine, MatchCandidate
from reconciliation.tests.factories import (
    BankTransactionFactory,
    BankTransactionNormalizedFactory,
    LedgerEntryFactory,
    SocietyFactory,
    VoucherFactory,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers — build a matching-ready ledger entry (posted, bank payment mode)
# ---------------------------------------------------------------------------

def _make_posted_voucher(society, voucher_type, payment_mode, voucher_date, narration="", reference_number=""):
    """Create a voucher that test ledger-entry helpers mark as posted."""
    return VoucherFactory(
        society=society,
        voucher_type=voucher_type,
        payment_mode=payment_mode,
        voucher_date=voucher_date,
        narration=narration,
        reference_number=reference_number,
    )


def _make_matching_ledger_entry(society, voucher, account, amount, dr_cr):
    """Create a ledger entry, then mark its voucher as posted for matcher queries."""
    entry = LedgerEntryFactory(
        voucher=voucher,
        account=account,
        debit=amount if dr_cr == "DEBIT" else 0,
        credit=amount if dr_cr == "CREDIT" else 0,
    )
    Voucher.objects.filter(pk=voucher.pk).update(
        voucher_number=voucher.pk,
        posted_at=timezone.now(),
    )
    voucher.refresh_from_db()
    return entry


# ---------------------------------------------------------------------------
# Rule 1 — UTR Exact Match
# ---------------------------------------------------------------------------

class TestRuleUTRMatch:
    def test_utr_match_found(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            transaction_date=date(2025, 6, 15),
            narration="NEFT UTR: ABC1234567",
            reference_no="ABC1234567",
            amount=5000,
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        norm = BankTransactionNormalizedFactory(
            bank_transaction=bt,
            extracted_utr="ABC1234567",
        )

        v = _make_posted_voucher(
            society,
            Voucher.VoucherType.RECEIPT,
            Voucher.PaymentMode.BANK_TRANSFER,
            date(2025, 6, 15),
            reference_number="ABC1234567",
        )
        entry = _make_matching_ledger_entry(society, v, bt.bank_statement_import.bank_account, 5000, "DEBIT")

        candidates = engine._rule_utr_match(bt, norm, [entry])
        assert len(candidates) == 1
        assert candidates[0].confidence == 99
        assert candidates[0].match_type == "EXACT"

    def test_utr_match_partial_on_amount(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            amount=5000,
            reference_no="UTR999",
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        norm = BankTransactionNormalizedFactory(bank_transaction=bt, extracted_utr="UTR999")

        v = _make_posted_voucher(
            society,
            Voucher.VoucherType.RECEIPT,
            Voucher.PaymentMode.BANK_TRANSFER,
            date(2025, 6, 15),
            reference_number="UTR999",
        )
        entry = _make_matching_ledger_entry(society, v, bt.bank_statement_import.bank_account, 3000, "DEBIT")

        candidates = engine._rule_utr_match(bt, norm, [entry])
        assert len(candidates) == 1
        assert candidates[0].match_type == "PARTIAL"
        assert candidates[0].matched_amount == 3000

    def test_utr_match_no_norm_falls_back_to_reference_no(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            amount=1000,
            reference_no="FALLBACK123",
            dr_cr=BankTransaction.DrCr.CREDIT,
        )

        v = _make_posted_voucher(
            society,
            Voucher.VoucherType.RECEIPT,
            Voucher.PaymentMode.BANK_TRANSFER,
            date(2025, 6, 15),
            reference_number="FALLBACK123",
        )
        entry = _make_matching_ledger_entry(society, v, bt.bank_statement_import.bank_account, 1000, "DEBIT")

        candidates = engine._rule_utr_match(bt, None, [entry])
        assert len(candidates) == 1

    def test_utr_match_short_utr_skipped(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            reference_no="AB",
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        norm = BankTransactionNormalizedFactory(bank_transaction=bt, extracted_utr="AB")

        candidates = engine._rule_utr_match(bt, norm, [])
        assert len(candidates) == 0


# ---------------------------------------------------------------------------
# Rule 2 — Cheque Number Match
# ---------------------------------------------------------------------------

class TestRuleChequeMatch:
    def test_cheque_match_exact(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            cheque_no="CHQ001",
            amount=7500,
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        v = _make_posted_voucher(
            society,
            Voucher.VoucherType.RECEIPT,
            Voucher.PaymentMode.CHEQUE,
            date(2025, 6, 15),
            reference_number="CHQ001",
        )
        entry = _make_matching_ledger_entry(society, v, bt.bank_statement_import.bank_account, 7500, "DEBIT")

        candidates = engine._rule_cheque_match(bt, [entry])
        assert len(candidates) == 1
        # Exact amount match gives +1 bonus → 99
        assert candidates[0].confidence == 99
        assert candidates[0].match_type == "EXACT"

    def test_cheque_match_no_cheque_skips(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            cheque_no="",
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        candidates = engine._rule_cheque_match(bt, [])
        assert len(candidates) == 0


# ---------------------------------------------------------------------------
# Rule 3 — Date + Amount Match
# ---------------------------------------------------------------------------

class TestRuleDateAmountMatch:
    def test_date_amount_match_exact(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            transaction_date=date(2025, 6, 1),
            amount=2000,
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        v = _make_posted_voucher(
            society,
            Voucher.VoucherType.RECEIPT,
            Voucher.PaymentMode.BANK_TRANSFER,
            date(2025, 6, 1),
        )
        entry = _make_matching_ledger_entry(society, v, bt.bank_statement_import.bank_account, 2000, "DEBIT")

        candidates = engine._rule_date_amount_match(bt, [entry])
        assert len(candidates) == 1
        assert candidates[0].confidence == 85
        assert candidates[0].match_type == "EXACT"

    def test_date_amount_mismatch_date(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            transaction_date=date(2025, 6, 1),
            amount=2000,
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        v = _make_posted_voucher(
            society,
            Voucher.VoucherType.RECEIPT,
            Voucher.PaymentMode.BANK_TRANSFER,
            date(2025, 6, 2),  # different date
        )
        entry = _make_matching_ledger_entry(society, v, bt.bank_statement_import.bank_account, 2000, "DEBIT")

        candidates = engine._rule_date_amount_match(bt, [entry])
        assert len(candidates) == 0

    def test_date_amount_wrong_direction(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            transaction_date=date(2025, 6, 1),
            amount=2000,
            dr_cr=BankTransaction.DrCr.CREDIT,  # incoming
        )
        v = _make_posted_voucher(
            society,
            Voucher.VoucherType.PAYMENT,  # outgoing
            Voucher.PaymentMode.BANK_TRANSFER,
            date(2025, 6, 1),
        )
        entry = _make_matching_ledger_entry(society, v, bt.bank_statement_import.bank_account, 2000, "CREDIT")

        candidates = engine._rule_date_amount_match(bt, [entry])
        assert len(candidates) == 0


# ---------------------------------------------------------------------------
# Rule 4 — Flat Number + Amount
# ---------------------------------------------------------------------------

class TestRuleFlatAmountMatch:
    def test_flat_amount_match(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            narration="Maintenance Flat 101",
            amount=5000,
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        norm = BankTransactionNormalizedFactory(
            bank_transaction=bt,
            extracted_flat_no="101",
        )
        v = _make_posted_voucher(
            society,
            Voucher.VoucherType.RECEIPT,
            Voucher.PaymentMode.BANK_TRANSFER,
            date(2025, 6, 15),
            narration="Maintenance for Flat 101",
        )
        entry = _make_matching_ledger_entry(society, v, bt.bank_statement_import.bank_account, 5000, "DEBIT")

        candidates = engine._rule_flat_amount_match(bt, norm, [entry])
        assert len(candidates) == 1
        # exact amount → 75 + 5 = 80
        assert candidates[0].confidence == 80

    def test_flat_amount_matches_linked_unit_identifier(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)
        structure = Structure.objects.create(
            society=society,
            structure_type=Structure.StructureType.BUILDING,
            name="A Wing",
        )
        unit = Unit.objects.create(
            structure=structure,
            unit_type=Unit.UnitType.FLAT,
            identifier="101",
        )

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            narration="Maintenance Flat 101",
            amount=5000,
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        norm = BankTransactionNormalizedFactory(
            bank_transaction=bt,
            extracted_flat_no="101",
        )
        v = _make_posted_voucher(
            society,
            Voucher.VoucherType.RECEIPT,
            Voucher.PaymentMode.BANK_TRANSFER,
            date(2025, 6, 15),
            narration="Maintenance receipt",
        )
        entry = _make_matching_ledger_entry(society, v, bt.bank_statement_import.bank_account, 5000, "DEBIT")
        LedgerEntry.objects.filter(pk=entry.pk).update(unit=unit)
        entry.refresh_from_db()

        candidates = engine._rule_flat_amount_match(bt, norm, [entry])
        assert len(candidates) == 1
        assert candidates[0].details["flat_no"] == "101"

    def test_flat_amount_no_flat_skipped(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        norm = BankTransactionNormalizedFactory(
            bank_transaction=bt,
            extracted_flat_no="",  # no flat extracted
        )
        # Rule is only called when extracted_flat_no is non-empty from _match_single_transaction
        candidates = engine._rule_flat_amount_match(bt, norm, [])
        assert len(candidates) == 0


# ---------------------------------------------------------------------------
# Rule 5 — Narration Similarity
# ---------------------------------------------------------------------------

class TestRuleNarrationMatch:
    def test_narration_similarity_match(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            narration="Quarterly maintenance for flat 101",
            amount=3000,
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        norm = BankTransactionNormalizedFactory(
            bank_transaction=bt,
            cleaned_narration="quarterly maintenance for flat 101",
        )
        v = _make_posted_voucher(
            society,
            Voucher.VoucherType.RECEIPT,
            Voucher.PaymentMode.BANK_TRANSFER,
            date(2025, 6, 15),
            narration="quarterly maintenance payment flat 101",
        )
        entry = _make_matching_ledger_entry(society, v, bt.bank_statement_import.bank_account, 3000, "DEBIT")

        candidates = engine._rule_narration_match(bt, norm, [entry])
        assert len(candidates) >= 1
        assert candidates[0].confidence > 0

    def test_narration_amount_mismatch_skipped(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            narration="Test payment",
            amount=3000,
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        norm = BankTransactionNormalizedFactory(bank_transaction=bt, cleaned_narration="test payment")
        v = _make_posted_voucher(
            society,
            Voucher.VoucherType.RECEIPT,
            Voucher.PaymentMode.BANK_TRANSFER,
            date(2025, 6, 15),
            narration="test payment",
        )
        entry = _make_matching_ledger_entry(society, v, bt.bank_statement_import.bank_account, 5000, "DEBIT")

        candidates = engine._rule_narration_match(bt, norm, [entry])
        assert len(candidates) == 0  # amount must match for narration rule


# ---------------------------------------------------------------------------
# Rule 6 — Amount Only
# ---------------------------------------------------------------------------

class TestRuleAmountOnly:
    def test_amount_only_within_window(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            transaction_date=date(2025, 6, 15),
            amount=1000,
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        v = _make_posted_voucher(
            society,
            Voucher.VoucherType.RECEIPT,
            Voucher.PaymentMode.BANK_TRANSFER,
            date(2025, 6, 18),  # 3 days difference, within 7-day window
        )
        entry = _make_matching_ledger_entry(society, v, bt.bank_statement_import.bank_account, 1000, "DEBIT")

        candidates = engine._rule_amount_only(bt, [entry])
        assert len(candidates) == 1
        # confidence = 50 - (3 * 2) = 44
        assert candidates[0].confidence == 44

    def test_amount_only_outside_window_skipped(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            transaction_date=date(2025, 6, 1),
            amount=1000,
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        v = _make_posted_voucher(
            society,
            Voucher.VoucherType.RECEIPT,
            Voucher.PaymentMode.BANK_TRANSFER,
            date(2025, 6, 15),  # 14 days, outside 7-day window
        )
        entry = _make_matching_ledger_entry(society, v, bt.bank_statement_import.bank_account, 1000, "DEBIT")

        candidates = engine._rule_amount_only(bt, [entry])
        assert len(candidates) == 0

    def test_amount_only_min_confidence_30(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            transaction_date=date(2025, 6, 1),
            amount=1000,
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        v = _make_posted_voucher(
            society,
            Voucher.VoucherType.RECEIPT,
            Voucher.PaymentMode.BANK_TRANSFER,
            date(2025, 6, 8),  # 7 days, exactly at window edge
        )
        entry = _make_matching_ledger_entry(society, v, bt.bank_statement_import.bank_account, 1000, "DEBIT")

        candidates = engine._rule_amount_only(bt, [entry])
        assert len(candidates) == 1
        # 50 - (7*2) = 36, min is 30, so 36
        assert candidates[0].confidence == 36


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestDeduplicateCandidates:
    def test_dedup_keeps_highest_confidence(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            amount=5000,
        )
        v = _make_posted_voucher(
            society,
            Voucher.VoucherType.RECEIPT,
            Voucher.PaymentMode.BANK_TRANSFER,
            date(2025, 6, 15),
        )
        entry = _make_matching_ledger_entry(society, v, bt.bank_statement_import.bank_account, 5000, "DEBIT")

        c1 = MatchCandidate(bt, entry, 85, "Rule A", "EXACT", 5000, {})
        c2 = MatchCandidate(bt, entry, 99, "Rule B", "EXACT", 5000, {})

        result = engine._deduplicate_candidates([c1, c2])
        assert len(result) == 1
        assert result[0].confidence == 99


# ---------------------------------------------------------------------------
# Auto-match / Suggestion thresholds
# ---------------------------------------------------------------------------

class TestAutoMatchThresholds:
    def test_candidate_above_85_auto_matched(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            amount=5000,
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        v = _make_posted_voucher(
            society,
            Voucher.VoucherType.RECEIPT,
            Voucher.PaymentMode.BANK_TRANSFER,
            bt.transaction_date,
        )
        entry = _make_matching_ledger_entry(society, v, bt.bank_statement_import.bank_account, 5000, "DEBIT")

        candidate = MatchCandidate(bt, entry, 85, "Test Rule", "EXACT", 5000, {})
        auto_matched, suggested = engine._persist_matches(
            [candidate], auto_confirm=True, create_suggestions=True
        )
        assert len(auto_matched) == 1
        assert auto_matched[0].status == ReconciliationLink.Status.MATCHED

    def test_candidate_below_50_not_persisted(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            amount=5000,
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        v = _make_posted_voucher(
            society,
            Voucher.VoucherType.RECEIPT,
            Voucher.PaymentMode.BANK_TRANSFER,
            bt.transaction_date,
        )
        entry = _make_matching_ledger_entry(society, v, bt.bank_statement_import.bank_account, 5000, "DEBIT")

        candidate = MatchCandidate(bt, entry, 40, "Weak Rule", "EXACT", 5000, {})
        auto_matched, suggested = engine._persist_matches(
            [candidate], auto_confirm=True, create_suggestions=True
        )
        assert len(auto_matched) == 0
        assert len(suggested) == 0

    def test_candidate_between_50_and_85_suggested(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            amount=5000,
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        v = _make_posted_voucher(
            society,
            Voucher.VoucherType.RECEIPT,
            Voucher.PaymentMode.BANK_TRANSFER,
            bt.transaction_date,
        )
        entry = _make_matching_ledger_entry(society, v, bt.bank_statement_import.bank_account, 5000, "DEBIT")

        candidate = MatchCandidate(bt, entry, 70, "Mid Rule", "EXACT", 5000, {})
        auto_matched, suggested = engine._persist_matches(
            [candidate], auto_confirm=True, create_suggestions=True
        )
        assert len(auto_matched) == 0
        assert len(suggested) == 1
        assert suggested[0].status == ReconciliationLink.Status.SUGGESTED


# ---------------------------------------------------------------------------
# Force match
# ---------------------------------------------------------------------------

class TestForceMatch:
    def test_force_match_creates_link(self, user):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            amount=7500,
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        v = _make_posted_voucher(
            society,
            Voucher.VoucherType.RECEIPT,
            Voucher.PaymentMode.BANK_TRANSFER,
            bt.transaction_date,
        )
        entry = _make_matching_ledger_entry(society, v, bt.bank_statement_import.bank_account, 7500, "DEBIT")

        link = engine.force_match(bt, entry, user, remarks="Manual override")
        assert link.status == ReconciliationLink.Status.FORCE_MATCHED
        assert link.is_manual is True
        assert link.matched_by == user
        assert link.confidence_score == 100


# ---------------------------------------------------------------------------
# _check_direction_match helper
# ---------------------------------------------------------------------------

class TestCheckDirectionMatch:
    def test_credit_matches_receipt(self):
        bt = BankTransactionFactory.build(dr_cr=BankTransaction.DrCr.CREDIT)
        v = VoucherFactory.build(voucher_type=Voucher.VoucherType.RECEIPT)
        entry = LedgerEntryFactory.build(voucher=v)
        assert MatchingEngine._check_direction_match(bt, entry) is True

    def test_debit_matches_payment(self):
        bt = BankTransactionFactory.build(dr_cr=BankTransaction.DrCr.DEBIT)
        v = VoucherFactory.build(voucher_type=Voucher.VoucherType.PAYMENT)
        entry = LedgerEntryFactory.build(voucher=v)
        assert MatchingEngine._check_direction_match(bt, entry) is True

    def test_debit_matches_journal(self):
        bt = BankTransactionFactory.build(dr_cr=BankTransaction.DrCr.DEBIT)
        v = VoucherFactory.build(voucher_type=Voucher.VoucherType.JOURNAL)
        entry = LedgerEntryFactory.build(voucher=v)
        assert MatchingEngine._check_direction_match(bt, entry) is True

    def test_credit_does_not_match_payment(self):
        bt = BankTransactionFactory.build(dr_cr=BankTransaction.DrCr.CREDIT)
        v = VoucherFactory.build(voucher_type=Voucher.VoucherType.PAYMENT)
        entry = LedgerEntryFactory.build(voucher=v)
        assert MatchingEngine._check_direction_match(bt, entry) is False


# ---------------------------------------------------------------------------
# _text_similarity helper
# ---------------------------------------------------------------------------

class TestTextSimilarity:
    def test_identical(self):
        assert MatchingEngine._text_similarity("hello world", "hello world") == 1.0

    def test_partial_overlap(self):
        sim = MatchingEngine._text_similarity("maintenance payment flat 101", "maintenance flat 101 paid")
        assert 0.0 < sim < 1.0

    def test_no_overlap(self):
        assert MatchingEngine._text_similarity("abc", "xyz") == 0.0

    def test_empty_string(self):
        assert MatchingEngine._text_similarity("", "anything") == 0.0
        assert MatchingEngine._text_similarity("anything", "") == 0.0


# ---------------------------------------------------------------------------
# _get_entry_amount helper
# ---------------------------------------------------------------------------

class TestGetEntryAmount:
    def test_debit_entry(self):
        entry = LedgerEntryFactory.build(debit=5000, credit=0)
        assert MatchingEngine._get_entry_amount(entry) == 5000

    def test_credit_entry(self):
        entry = LedgerEntryFactory.build(debit=0, credit=3000)
        assert MatchingEngine._get_entry_amount(entry) == 3000


# ---------------------------------------------------------------------------
# _compute_stats helper
# ---------------------------------------------------------------------------

class TestComputeStats:
    def test_stats_summary(self):
        bt = BankTransactionFactory.build()
        c1 = MatchCandidate(bt, None, 80, "Rule A", "EXACT", 1000, {})
        c2 = MatchCandidate(bt, None, 90, "Rule A", "EXACT", 1000, {})
        c3 = MatchCandidate(bt, None, 50, "Rule B", "PARTIAL", 500, {})

        stats = MatchingEngine._compute_stats([c1, c2, c3])
        assert "Rule A" in stats
        assert stats["Rule A"]["count"] == 2
        assert stats["Rule A"]["avg_confidence"] == 85.0
        assert stats["Rule B"]["count"] == 1
        assert stats["Rule B"]["avg_confidence"] == 50.0


# ---------------------------------------------------------------------------
# run_matching integration
# ---------------------------------------------------------------------------

class TestRunMatchingIntegration:
    def test_run_matching_finds_candidates(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)

        bt = BankTransactionFactory(
            bank_statement_import__society=society,
            transaction_date=date(2025, 6, 15),
            narration="NEFT UTR: INTEGTEST001",
            reference_no="INTEGTEST001",
            amount=5000,
            dr_cr=BankTransaction.DrCr.CREDIT,
        )
        BankTransactionNormalizedFactory(
            bank_transaction=bt,
            extracted_utr="INTEGTEST001",
            cleaned_narration="neft utr integtest001",
        )

        v = _make_posted_voucher(
            society,
            Voucher.VoucherType.RECEIPT,
            Voucher.PaymentMode.BANK_TRANSFER,
            date(2025, 6, 15),
            reference_number="INTEGTEST001",
        )
        _make_matching_ledger_entry(society, v, bt.bank_statement_import.bank_account, 5000, "DEBIT")

        result = engine.run_matching(bank_transactions=[bt])
        assert len(result["candidates"]) >= 1
        assert len(result["auto_matched"]) >= 1

    def test_run_matching_empty_returns_noop(self):
        society = SocietyFactory()
        engine = MatchingEngine(society)
        result = engine.run_matching(bank_transactions=[])
        assert result["candidates"] == []
        assert result["auto_matched"] == []
        assert result["suggested"] == []