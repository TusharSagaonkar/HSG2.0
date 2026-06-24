import io

import pytest

from reconciliation.services.importer import ImportError
from reconciliation.services.detector import StatementFormatDetector
from reconciliation.services.profile_resolver import BankProfileResolver
from reconciliation.services.manual_entry import ManualStatementImportService, ManualEntryRow
from reconciliation.tests.factories import (
    BankAccountFactory,
    BankParserProfileFactory,
    SocietyFactory,
)

pytestmark = pytest.mark.django_db


class TestStatementFormatDetector:
    def test_detects_bank_from_csv_content(self):
        detector = StatementFormatDetector()
        content = (
            b"Date,Narration,Debit,Credit,Balance\n"
            b"01/06/25,HDFC BANK PAYMENT,100,0,900\n"
        )
        result = detector.detect("statement.csv", content=content)
        assert result.file_type == "csv"
        assert result.bank_name == "HDFC"
        assert result.confidence >= 85

    def test_generic_fallback(self):
        detector = StatementFormatDetector()
        result = detector.detect("statement.csv", content=b"Date,Name,Amount\n")
        assert result.bank_name == "Generic"
        assert result.format_name == "GENERIC_CSV"


class TestBankProfileResolver:
    def test_resolves_profile_by_format_name(self):
        society = SocietyFactory()
        profile = BankParserProfileFactory(
            society=society,
            bank_name="HDFC",
            file_type="csv",
            format_name="HDFC_RETAIL_CSV",
            parser_class="reconciliation.services.parsers.csv_parser.CSVParser",
        )
        resolver = BankProfileResolver()
        resolved = resolver.resolve(
            society=society,
            bank_name="HDFC",
            file_type="csv",
            confidence=80,
            format_name="HDFC_RETAIL_CSV",
        )
        assert resolved == profile

    def test_returns_none_when_no_profile(self):
        resolver = BankProfileResolver()
        assert resolver.resolve(
            society=SocietyFactory(),
            bank_name="HDFC",
            file_type="csv",
            confidence=80,
        ) is None


class TestManualStatementImportService:
    def test_import_rows_creates_transactions(self, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        service = ManualStatementImportService(user=user, society=society, bank_account=bank_account)

        statement = service.import_rows(
            [
                ManualEntryRow(
                    transaction_date="2025-06-01",
                    narration="Maintenance receipt",
                    amount=5000,
                    dr_cr="CREDIT",
                    reference_no="UTR123",
                    raw_row={"source": "manual"},
                ),
                ManualEntryRow(
                    transaction_date="2025-06-02",
                    narration="Bank charges",
                    amount=17,
                    dr_cr="DEBIT",
                    raw_row={"source": "manual"},
                ),
            ]
        )

        assert statement.import_status == "COMPLETED"
        assert statement.source_type == "MANUAL"
        assert statement.row_count == 2

    def test_import_rows_duplicate_detection(self, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        service = ManualStatementImportService(user=user, society=society, bank_account=bank_account)
        rows = [
            ManualEntryRow(transaction_date="2025-06-01", narration="Payment", amount=1000)
        ]
        service.import_rows(rows, filename="manual.csv")
        with pytest.raises(ImportError):
            service.import_rows(rows, filename="manual.csv")
