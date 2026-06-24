"""
Tests for CSV and XLSX bank statement parsers.
"""

import csv
import io
from datetime import date

import openpyxl
import pytest

from reconciliation.services.parsers.csv_parser import CSVParser
from reconciliation.services.parsers.xlsx_parser import XLSXParser

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_csv(content: str) -> io.BytesIO:
    return io.BytesIO(content.encode("utf-8"))


def _make_xlsx(rows: list[list]) -> io.BytesIO:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# CSVParser
# ---------------------------------------------------------------------------

class TestCSVParser:
    def test_parse_hdfc_format(self):
        csv_content = (
            "Date,Narration,Chq/Ref Number,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance\r\n"
            '01/06/25,RTGS-UTR123456,S/O Flat101,01/06/25,0,5000,50000\r\n'
            '02/06/25,CHQ-987654,CHQ654321,02/06/25,2000,0,48000\r\n'
        )
        parser = CSVParser()
        result = parser.parse(_make_csv(csv_content), "test.csv")
        assert len(result.transactions) == 2

        t1 = result.transactions[0]
        assert t1.amount == 5000.0
        assert t1.dr_cr == "CREDIT"
        assert "RTGS-UTR123456" in t1.narration

        t2 = result.transactions[1]
        assert t2.amount == 2000.0
        assert t2.dr_cr == "DEBIT"

        assert result.errors == []
        assert result.parser_name == "CSV"

    def test_parse_with_icici_format(self):
        csv_content = (
            "Txn Date,Value Date,Description,Ref/Cheq No.,Debit,Credit,Balance\r\n"
            "01/06/2025,01/06/2025,NEFT Cr-ABCDEF-Some Name,ABCDEF,0.00,10000.00,100000.00\r\n"
        )
        parser = CSVParser()
        result = parser.parse(_make_csv(csv_content), "test.csv")
        assert len(result.transactions) == 1
        assert result.transactions[0].amount == 10000.00
        assert result.transactions[0].dr_cr == "CREDIT"

    def test_parse_with_tab_delimiter(self):
        csv_content = "Date\tNarration\tDebit\tCredit\tBalance\n01/06/25\tPayment\t1000\t0\t5000\n"
        parser = CSVParser()
        result = parser.parse(_make_csv(csv_content), "test.csv")
        assert len(result.transactions) == 1
        assert result.transactions[0].amount == 1000.0
        assert result.transactions[0].dr_cr == "DEBIT"

    def test_parse_empty_file(self):
        parser = CSVParser()
        result = parser.parse(_make_csv(""), "empty.csv")
        assert len(result.transactions) == 0

    def test_parse_header_only(self):
        parser = CSVParser()
        result = parser.parse(
            _make_csv("Date,Narration,Debit,Credit,Balance\n"), "header_only.csv"
        )
        assert len(result.transactions) == 0

    def test_parse_skips_summary_rows(self):
        csv_content = (
            "Date,Narration,Debit,Credit,Balance\n"
            "01/06/25,Payment,1000,0,5000\n"
            "TOTAL,,1000,0,\n"
        )
        parser = CSVParser()
        result = parser.parse(_make_csv(csv_content), "test.csv")
        assert len(result.transactions) == 1

    def test_parse_row_without_date_is_skipped(self):
        csv_content = (
            "Date,Narration,Debit,Credit,Balance\n"
            ",No date row,500,0,5000\n"
            "01/06/25,Valid row,300,0,4700\n"
        )
        parser = CSVParser()
        result = parser.parse(_make_csv(csv_content), "test.csv")
        assert len(result.transactions) == 1

    def test_parse_amount_with_commas(self):
        csv_content = (
            "Date,Narration,Debit,Credit,Balance\n"
            '01/06/25,Payment,"1,500",0,"4,500"\n'
        )
        parser = CSVParser()
        result = parser.parse(_make_csv(csv_content), "test.csv")
        assert len(result.transactions) == 1
        assert result.transactions[0].amount == 1500.0

    def test_parse_preserves_raw_data(self):
        csv_content = (
            "Date,Narration,Chq/Ref Number,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance\r\n"
            '01/06/25,Test,REF001,01/06/25,0,3000,30000\r\n'
        )
        parser = CSVParser()
        result = parser.parse(_make_csv(csv_content), "test.csv")
        txn = result.transactions[0]
        assert txn.raw_row
        assert txn.narration == "Test"

    def test_detect_delimiter_comma(self):
        parser = CSVParser()
        delimiter = parser._detect_delimiter("a,b,c\n1,2,3\n")
        assert delimiter == ","

    def test_detect_delimiter_tab(self):
        parser = CSVParser()
        delimiter = parser._detect_delimiter("a\tb\tc\n1\t2\t3\n")
        assert delimiter == "\t"


# ---------------------------------------------------------------------------
# XLSXParser
# ---------------------------------------------------------------------------

class TestXLSXParser:
    def test_parse_hdfc_format(self):
        rows = [
            ["Date", "Narration", "Chq/Ref Number", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"],
            ["01/06/25", "RTGS-UTR123456", "UTR123456", "01/06/25", 0, 5000, 50000],
            ["02/06/25", "CHQ Payment", "CHQ001", "02/06/25", 2000, 0, 48000],
        ]
        parser = XLSXParser()
        result = parser.parse(_make_xlsx(rows), "test.xlsx")
        assert len(result.transactions) == 2

        t1 = result.transactions[0]
        assert t1.amount == 5000.0
        assert t1.dr_cr == "CREDIT"

        t2 = result.transactions[1]
        assert t2.amount == 2000.0
        assert t2.dr_cr == "DEBIT"

        assert result.errors == []
        assert result.parser_name == "XLSX"

    def test_parse_empty_xlsx(self):
        rows = [["Date", "Narration", "Debit", "Credit", "Balance"]]
        parser = XLSXParser()
        result = parser.parse(_make_xlsx(rows), "empty.xlsx")
        assert len(result.transactions) == 0

    def test_parse_skips_summary_rows(self):
        rows = [
            ["Date", "Narration", "Debit", "Credit", "Balance"],
            ["01/06/25", "Payment", 1000, 0, 5000],
            ["TOTAL", "", 1000, 0, ""],
        ]
        parser = XLSXParser()
        result = parser.parse(_make_xlsx(rows), "test.xlsx")
        assert len(result.transactions) == 1

    def test_parse_preserves_raw_data(self):
        rows = [
            ["Date", "Narration", "Chq/Ref Number", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"],
            ["01/06/25", "Test Payment", "REF001", "01/06/25", 0, 3000, 30000],
        ]
        parser = XLSXParser()
        result = parser.parse(_make_xlsx(rows), "test.xlsx")
        txn = result.transactions[0]
        assert txn.raw_row
        assert txn.narration == "Test Payment"


# ---------------------------------------------------------------------------
# Base parser _parse_amount
# ---------------------------------------------------------------------------

class TestBaseParserParseAmount:
    def test_parse_plain_number(self):
        from reconciliation.services.parsers.base import BaseStatementParser
        assert BaseStatementParser._parse_amount("1000") == 1000.0

    def test_parse_amount_with_commas(self):
        from reconciliation.services.parsers.base import BaseStatementParser
        assert BaseStatementParser._parse_amount("1,500.50") == 1500.50

    def test_parse_amount_with_whitespace(self):
        from reconciliation.services.parsers.base import BaseStatementParser
        assert BaseStatementParser._parse_amount("  2000  ") == 2000.0

    def test_parse_empty_string_returns_none(self):
        from reconciliation.services.parsers.base import BaseStatementParser
        assert BaseStatementParser._parse_amount("") is None

    def test_parse_none_returns_none(self):
        from reconciliation.services.parsers.base import BaseStatementParser
        assert BaseStatementParser._parse_amount(None) is None


# ---------------------------------------------------------------------------
# Base parser _detect_dr_cr
# ---------------------------------------------------------------------------

class TestBaseParserDetectDrCr:
    def test_debit_from_debit_column(self):
        from reconciliation.services.parsers.csv_parser import CSVParser
        parser = CSVParser()
        amount, dr_cr = parser._detect_dr_cr("1000", None)
        assert amount == 1000.0
        assert dr_cr == "DEBIT"

    def test_credit_from_credit_column(self):
        from reconciliation.services.parsers.csv_parser import CSVParser
        parser = CSVParser()
        amount, dr_cr = parser._detect_dr_cr(None, "500")
        assert amount == 500.0
        assert dr_cr == "CREDIT"

    def test_both_present_uses_debit(self):
        from reconciliation.services.parsers.csv_parser import CSVParser
        parser = CSVParser()
        amount, dr_cr = parser._detect_dr_cr("1000", "500")
        assert amount == 1000.0
        assert dr_cr == "DEBIT"