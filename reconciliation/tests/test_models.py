"""
Tests for reconciliation model validation, constraints, and methods.
"""

import hashlib
from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError

from reconciliation.models import BankStatementImport
from reconciliation.tests.factories import (
    BankAccountFactory,
    BankStatementImportFactory,
    BankTransactionFactory,
    BankTransactionNormalizedFactory,
    ReconciliationHistoryFactory,
    ReconciliationLinkFactory,
    SocietyFactory,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# BankTransaction
# ---------------------------------------------------------------------------

class TestBankTransactionModel:
    def test_amount_must_be_positive(self):
        bt = BankTransactionFactory.build(amount=-100.00)
        with pytest.raises(ValidationError, match="positive"):
            bt.full_clean()

    def test_amount_cannot_be_zero(self):
        bt = BankTransactionFactory.build(amount=0)
        with pytest.raises(ValidationError, match="positive"):
            bt.full_clean()

    def test_create_valid_transaction(self):
        bt = BankTransactionFactory()
        assert bt.pk is not None
        assert bt.amount > 0

    def test_immutable_fields_cannot_be_changed_after_creation(self):
        bt = BankTransactionFactory(narration="Original")
        bt.narration = "Modified"
        with pytest.raises(ValidationError, match="immutable"):
            bt.save()

    def test_is_duplicate_is_mutable(self):
        bt = BankTransactionFactory()
        bt.is_duplicate = True
        bt.save()
        bt.refresh_from_db()
        assert bt.is_duplicate is True

    def test_delete_raises_validation_error(self):
        bt = BankTransactionFactory()
        with pytest.raises(ValidationError, match="cannot be deleted"):
            bt.delete()

    def test_compute_duplicate_hash_deterministic(self):
        h1 = BankTransactionFactory.build().compute_duplicate_hash(
            date(2025, 6, 1), 1000.00, "Payment  Flat 101", "REF001"
        )
        h2 = BankTransactionFactory.build().compute_duplicate_hash(
            date(2025, 6, 1), 1000.00, "Payment  Flat 101", "REF001"
        )
        assert h1 == h2

    def test_compute_duplicate_hash_differs_on_date(self):
        h1 = BankTransactionFactory.build().compute_duplicate_hash(
            date(2025, 6, 1), 1000.00, "Payment", "REF001"
        )
        h2 = BankTransactionFactory.build().compute_duplicate_hash(
            date(2025, 6, 2), 1000.00, "Payment", "REF001"
        )
        assert h1 != h2

    def test_compute_duplicate_hash_differs_on_amount(self):
        h1 = BankTransactionFactory.build().compute_duplicate_hash(
            date(2025, 6, 1), 1000.00, "Payment", "REF001"
        )
        h2 = BankTransactionFactory.build().compute_duplicate_hash(
            date(2025, 6, 1), 1000.01, "Payment", "REF001"
        )
        assert h1 != h2

    def test_compute_duplicate_hash_case_insensitive_narration(self):
        h1 = BankTransactionFactory.build().compute_duplicate_hash(
            date(2025, 6, 1), 1000.00, "PAYMENT", "REF001"
        )
        h2 = BankTransactionFactory.build().compute_duplicate_hash(
            date(2025, 6, 1), 1000.00, "payment", "REF001"
        )
        assert h1 == h2

    def test_compute_duplicate_hash_case_insensitive_reference(self):
        h1 = BankTransactionFactory.build().compute_duplicate_hash(
            date(2025, 6, 1), 1000.00, "Payment", "REF001"
        )
        h2 = BankTransactionFactory.build().compute_duplicate_hash(
            date(2025, 6, 1), 1000.00, "Payment", "ref001"
        )
        assert h1 == h2

    def test_str_representation(self):
        bt = BankTransactionFactory(
            transaction_date=date(2025, 6, 1),
            amount=1500.00,
            dr_cr="CREDIT",
            reference_no="ABC123",
        )
        assert "2025-06-01" in str(bt)
        assert "1500" in str(bt)
        assert "ABC123" in str(bt)


# ---------------------------------------------------------------------------
# BankStatementImport
# ---------------------------------------------------------------------------

class TestBankStatementImportModel:
    def test_create_valid_import(self):
        imp = BankStatementImportFactory()
        assert imp.pk is not None
        assert imp.import_status == "COMPLETED"

    def test_start_date_must_be_before_end_date(self):
        imp = BankStatementImportFactory.build(
            statement_start_date=date(2025, 6, 30),
            statement_end_date=date(2025, 6, 1),
        )
        with pytest.raises(ValidationError, match="before end date"):
            imp.full_clean()

    def test_start_date_equal_end_date_is_valid(self):
        imp = BankStatementImportFactory(
            statement_start_date=date(2025, 6, 15),
            statement_end_date=date(2025, 6, 15),
        )
        imp.full_clean()

    def test_bank_account_must_match_society(self):
        other_society = SocietyFactory()
        imp = BankStatementImportFactory.create(society=other_society)
        imp.full_clean()  # fine: bank_account.society matches imp.society

        different_society = SocietyFactory()
        imp2 = BankStatementImportFactory.create()
        imp2.society = different_society
        with pytest.raises(ValidationError, match="same society"):
            imp2.full_clean()

    def test_compute_file_hash(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        f1 = SimpleUploadedFile("test.csv", b"hello world")
        h1 = BankStatementImport.compute_file_hash(f1)
        f2 = SimpleUploadedFile("test.csv", b"hello world")
        h2 = BankStatementImport.compute_file_hash(f2)
        assert h1 == h2

    def test_compute_file_hash_differs(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        f1 = SimpleUploadedFile("a.csv", b"data1")
        f2 = SimpleUploadedFile("b.csv", b"data2")
        assert BankStatementImport.compute_file_hash(f1) != BankStatementImport.compute_file_hash(f2)

    def test_str_representation(self):
        imp = BankStatementImportFactory(file_name="march_statement.csv")
        assert "march_statement.csv" in str(imp)


# ---------------------------------------------------------------------------
# ReconciliationLink
# ---------------------------------------------------------------------------

class TestReconciliationLinkModel:
    def test_create_valid_link(self):
        link = ReconciliationLinkFactory()
        assert link.pk is not None
        assert link.status == "MATCHED"

    def test_matched_amount_must_be_positive(self):
        link = ReconciliationLinkFactory.build(matched_amount=-500.00)
        with pytest.raises(ValidationError, match="positive"):
            link.full_clean()

    def test_matched_amount_cannot_be_zero(self):
        link = ReconciliationLinkFactory.build(matched_amount=0)
        with pytest.raises(ValidationError, match="positive"):
            link.full_clean()

    def test_confidence_score_must_be_between_0_and_100(self):
        link = ReconciliationLinkFactory.build(confidence_score=150)
        with pytest.raises(ValidationError, match="between 0 and 100"):
            link.full_clean()

    def test_confidence_score_cannot_be_negative(self):
        link = ReconciliationLinkFactory.build(confidence_score=-5)
        with pytest.raises(ValidationError, match="between 0 and 100"):
            link.full_clean()

    def test_exact_match_requires_matched_or_force_matched(self):
        link = ReconciliationLinkFactory.build(
            match_type="EXACT",
            status="SUGGESTED",
        )
        with pytest.raises(ValidationError, match="MATCHED or FORCE_MATCHED"):
            link.full_clean()

    def test_exact_match_with_force_matched_is_valid(self):
        link = ReconciliationLinkFactory(
            match_type="EXACT",
            status="FORCE_MATCHED",
        )
        link.full_clean()

    def test_voucher_entry_society_mismatch(self):
        other_society = SocietyFactory()
        link = ReconciliationLinkFactory.build()
        link.voucher_entry.voucher.society = other_society
        link.voucher_entry.voucher.save()
        with pytest.raises(ValidationError, match="same society"):
            link.full_clean()

    def test_bank_transaction_society_mismatch(self):
        other_society = SocietyFactory()
        link = ReconciliationLinkFactory.build()
        link.bank_transaction.bank_statement_import.society = other_society
        link.bank_transaction.bank_statement_import.save()
        with pytest.raises(ValidationError, match="same society"):
            link.full_clean()

    def test_confirm_match_from_suggested(self, user):
        link = ReconciliationLinkFactory(status="SUGGESTED", match_type="PARTIAL", matched_by=None, matched_at=None)
        link.confirm_match(user)
        assert link.status == "MATCHED"
        assert link.is_manual is True
        assert link.matched_by == user
        assert link.matched_at is not None

    def test_confirm_match_from_pending(self, user):
        link = ReconciliationLinkFactory(status="PENDING", match_type="PARTIAL", matched_by=None, matched_at=None)
        link.confirm_match(user)
        assert link.status == "MATCHED"

    def test_confirm_match_raises_if_already_matched(self, user):
        link = ReconciliationLinkFactory(status="MATCHED")
        with pytest.raises(ValidationError, match="Cannot confirm"):
            link.confirm_match(user)

    def test_unmatch_from_matched(self, user):
        link = ReconciliationLinkFactory(status="MATCHED", match_type="PARTIAL")
        link.unmatch(user, reason="Wrong match")
        assert link.status == "REVERSED"
        assert "Wrong match" in link.remarks

    def test_unmatch_raises_if_suggested(self, user):
        link = ReconciliationLinkFactory(status="SUGGESTED", match_type="PARTIAL")
        with pytest.raises(ValidationError, match="Cannot unmatch"):
            link.unmatch(user)

    def test_mark_duplicate(self, user):
        link = ReconciliationLinkFactory(status="MATCHED", match_type="PARTIAL")
        link.mark_duplicate(user)
        assert link.status == "DUPLICATE"
        assert link.matched_by == user

    def test_mark_exception(self, user):
        link = ReconciliationLinkFactory(status="MATCHED", match_type="PARTIAL")
        link.mark_exception(user, exception_type="BANK_ONLY", reason="No book entry found")
        assert link.status == "EXCEPTION"
        assert link.exception_type == "BANK_ONLY"
        assert "No book entry found" in link.remarks

    def test_str_representation(self):
        link = ReconciliationLinkFactory(status="MATCHED", confidence_score=85)
        assert "MATCHED" in str(link)
        assert "85%" in str(link)


# ---------------------------------------------------------------------------
# ReconciliationHistory
# ---------------------------------------------------------------------------

class TestReconciliationHistoryModel:
    def test_create_valid_history(self):
        hist = ReconciliationHistoryFactory()
        assert hist.pk is not None
        assert hist.action == "CREATED"

    def test_update_raises_value_error(self):
        hist = ReconciliationHistoryFactory()
        hist.action = "UPDATED"
        with pytest.raises(ValueError, match="immutable"):
            hist.save()

    def test_delete_raises_value_error(self):
        hist = ReconciliationHistoryFactory()
        with pytest.raises(ValueError, match="cannot be deleted"):
            hist.delete()

    def test_str_representation(self):
        hist = ReconciliationHistoryFactory(action="CREATED", previous_status="", new_status="MATCHED")
        rep = str(hist)
        assert "CREATED" in rep
        assert "MATCHED" in rep


# ---------------------------------------------------------------------------
# BankTransactionNormalized
# ---------------------------------------------------------------------------

class TestBankTransactionNormalizedModel:
    def test_create_normalized_record(self):
        norm = BankTransactionNormalizedFactory()
        assert norm.pk is not None
        assert norm.bank_transaction is not None

    def test_one_to_one_constraint(self):
        bt = BankTransactionFactory()
        BankTransactionNormalizedFactory(bank_transaction=bt)
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            BankTransactionNormalizedFactory(bank_transaction=bt)

    def test_str_representation(self):
        norm = BankTransactionNormalizedFactory()
        assert "Normalized:" in str(norm)


# ---------------------------------------------------------------------------
# Signal — ReconciliationHistory auto-creation
# ---------------------------------------------------------------------------

class TestReconciliationLinkSignal:
    def test_creating_link_creates_history_record(self, user):
        link = ReconciliationLinkFactory(matched_by=user)
        hist = link.history.first()
        assert hist is not None
        assert hist.action == "CREATED"
        assert hist.new_status == link.status

    def test_confirm_match_creates_history_record(self, user):
        link = ReconciliationLinkFactory(status="SUGGESTED", match_type="PARTIAL", matched_by=None, matched_at=None)
        link.confirm_match(user)
        assert link.history.filter(action="CONFIRMED").exists()

    def test_unmatch_creates_history_record(self, user):
        link = ReconciliationLinkFactory(status="MATCHED", match_type="PARTIAL")
        link.unmatch(user, reason="Mistake")
        assert link.history.filter(action="REVERSED").exists()

    def test_mark_duplicate_creates_history_record(self, user):
        link = ReconciliationLinkFactory(status="MATCHED", match_type="PARTIAL")
        link.mark_duplicate(user)
        assert link.history.filter(action="DUPLICATE").exists()

    def test_mark_exception_creates_history_record(self, user):
        link = ReconciliationLinkFactory(status="MATCHED", match_type="PARTIAL")
        link.mark_exception(user, exception_type="BANK_ONLY", reason="Investigate")
        assert link.history.filter(action="EXCEPTION").exists()