"""
Tests for the StatementImportService — import workflows and duplicate detection.
"""

import io
from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from accounting.models import Account
from reconciliation.models import BankStatementImport, BankTransaction
from reconciliation.services.importer import ImportError, StatementImportService
from reconciliation.tests.factories import (
    BankAccountFactory,
    BankStatementImportFactory,
    SocietyFactory,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_csv_file(name: str, content: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content.encode("utf-8"), content_type="text/csv")


SAMPLE_CSV = (
    "Date,Narration,Chq/Ref Number,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance\r\n"
    '01/06/25,RTGS-UTR123456,UTR123456,01/06/25,0,5000,50000\r\n'
    '02/06/25,CHQ-987654,CHQ654321,02/06/25,2000,0,48000\r\n'
)

SAMPLE_XLSX_ROWS = [
    ["Date", "Narration", "Chq/Ref Number", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"],
    ["01/06/25", "RTGS-UTR-X", "UTR-X", "01/06/25", 0, 5000, 50000],
    ["02/06/25", "CHQ-Y", "CHQ-Y", "02/06/25", 2000, 0, 48000],
]


def _make_xlsx_file(name: str) -> SimpleUploadedFile:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    for row in SAMPLE_XLSX_ROWS:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------------------------------------------------------------------------
# CSV Import
# ---------------------------------------------------------------------------

class TestCSVImport:
    def test_import_csv_creates_transactions(self, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        service = StatementImportService(user=user, society=society, bank_account=bank_account)
        file_obj = _make_csv_file("test.csv", SAMPLE_CSV)
        imp = service.import_file(file_obj)

        assert imp.import_status == "COMPLETED"
        assert imp.row_count == 2
        assert BankTransaction.objects.filter(bank_statement_import=imp).count() == 2

    def test_import_sets_start_and_end_dates(self, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        service = StatementImportService(user=user, society=society, bank_account=bank_account)
        file_obj = _make_csv_file("test.csv", SAMPLE_CSV)
        imp = service.import_file(file_obj)

        assert imp.statement_start_date is not None
        assert imp.statement_end_date is not None

    def test_import_preserves_raw_row_data(self, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        service = StatementImportService(user=user, society=society, bank_account=bank_account)
        file_obj = _make_csv_file("test.csv", SAMPLE_CSV)
        imp = service.import_file(file_obj)

        txn = BankTransaction.objects.filter(bank_statement_import=imp).first()
        assert txn is not None
        assert txn.raw_row_data  # JSON field populated
        assert txn.duplicate_hash  # hash populated


# ---------------------------------------------------------------------------
# XLSX Import
# ---------------------------------------------------------------------------

class TestXLSXImport:
    def test_import_xlsx_creates_transactions(self, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        service = StatementImportService(user=user, society=society, bank_account=bank_account)
        file_obj = _make_xlsx_file("test.xlsx")
        imp = service.import_file(file_obj)

        assert imp.import_status == "COMPLETED"
        assert imp.row_count == 2


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

class TestDuplicateFileDetection:
    def test_reimport_same_file_raises(self, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        service = StatementImportService(user=user, society=society, bank_account=bank_account)
        file_obj = _make_csv_file("test.csv", SAMPLE_CSV)
        service.import_file(file_obj)  # first import succeeds

        file_obj2 = _make_csv_file("test.csv", SAMPLE_CSV)
        with pytest.raises(ImportError, match="already been imported"):
            service.import_file(file_obj2)

    def test_internal_duplicate_detection(self, user):
        """Two identical rows in the same batch: second is marked is_duplicate=True."""
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        service = StatementImportService(user=user, society=society, bank_account=bank_account)

        dup_csv = (
            "Date,Narration,Chq/Ref Number,Withdrawal Amt.,Deposit Amt.\r\n"
            '01/06/25,SamePayment,REF001,0,1000\r\n'
            '01/06/25,SamePayment,REF001,0,1000\r\n'
        )
        file_obj = _make_csv_file("dup.csv", dup_csv)
        imp = service.import_file(file_obj)

        txns = list(BankTransaction.objects.filter(bank_statement_import=imp).order_by("id"))
        assert len(txns) == 2
        assert txns[0].is_duplicate is False
        assert txns[1].is_duplicate is True


class TestUnsupportedFormat:
    def test_unsupported_extension_raises(self, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        service = StatementImportService(user=user, society=society, bank_account=bank_account)
        file_obj = SimpleUploadedFile("test.pdf", b"not-a-bank-statement", content_type="application/pdf")

        with pytest.raises(ImportError, match="Unsupported"):
            service.import_file(file_obj)


class TestFailedImport:
    def test_invalid_csv_marks_import_failed(self, user):
        society = SocietyFactory()
        bank_account = BankAccountFactory(society=society)
        service = StatementImportService(user=user, society=society, bank_account=bank_account)

        # CSV with no valid transaction rows — parser may return 0 transactions
        bad_csv = "Date,Narration,Debit,Credit,Balance\n, , , ,\n"
        file_obj = _make_csv_file("bad.csv", bad_csv)

        try:
            service.import_file(file_obj)
        except ImportError:
            pass  # expected

        imp = BankStatementImport.objects.filter(
            society=society, import_status="FAILED"
        ).first()
        assert imp is not None
        assert imp.error_log != ""


class TestHelperMethods:
    def test_get_extension(self):
        assert StatementImportService._get_extension("file.csv") == "csv"
        assert StatementImportService._get_extension("FILE.XLSX") == "xlsx"
        assert StatementImportService._get_extension("no_ext") == ""

    def test_parse_date_safe_iso(self):
        assert StatementImportService._parse_date_safe("2025-06-01") == date(2025, 6, 1)

    def test_parse_date_safe_dd_mm_yyyy(self):
        assert StatementImportService._parse_date_safe("01/06/2025") == date(2025, 6, 1)

    def test_parse_date_safe_none(self):
        assert StatementImportService._parse_date_safe(None) is None

    def test_parse_decimal_plain(self):
        from decimal import Decimal
        assert StatementImportService._parse_decimal("1500.50") == Decimal("1500.50")

    def test_parse_decimal_with_commas(self):
        from decimal import Decimal
        assert StatementImportService._parse_decimal("1,500") == Decimal("1500")

    def test_parse_decimal_none(self):
        assert StatementImportService._parse_decimal(None) is None