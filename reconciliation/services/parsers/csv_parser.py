import csv
import io
import re
from typing import Optional

from .base import BaseStatementParser, ParseResult, ParsedTransaction


class CSVParser(BaseStatementParser):
    """
    Parser for CSV bank statements.

    Supports common Indian bank formats:
      - HDFC, ICICI, SBI, Axis, and generic CSV exports.

    Auto-detects delimiter (comma or tab) and header row via heuristics.
    """

    bank_name = "Generic CSV"
    _supported_extensions = ("csv", "txt")

    # Column name mapping patterns (case-insensitive)
    DATE_PATTERNS = (
        r"(?:transaction|txn|value|posting)?\s*date",
        r"^date$",
    )
    NARRATION_PATTERNS = (
        r"narration|description|particulars|remarks|details|memo|notes",
    )
    REFERENCE_PATTERNS = (
        r"(?:transaction|txn|bank|utr)?\s*(?:ref(?:erence)?|id)(?:\s*(?:no|number|#))?",
        r"cheque\s*(?:no|number|#)?",
    )
    CHEQUE_PATTERNS = (
        r"che(?:que|ck)(?:\s*(?:no|number|#))?",
        r"chq",
    )
    DEBIT_PATTERNS = (
        r"\b(?:debit|withdrawal|dr)\b",
        r"\bpayment\b",
        r"\bpaid\s*out\b",
    )
    CREDIT_PATTERNS = (
        r"\b(?:credit|deposit|cr)\b",
        r"\breceipt\b",
        r"\breceived\b",
        r"\bpaid\s*in\b",
    )
    BALANCE_PATTERNS = (
        r"balance|closing\s*balance|available\s*balance",
    )
    VALUE_DATE_PATTERNS = (
        r"value\s*(?:date|dt)|val\s*(?:date|dt)",
    )

    SUMMARY_KEYWORDS = {"total", "closing", "opening", "b/f", "c/f", "balance c/f",
                        "balance b/f", "grand total", "opening balance", "closing balance"}

    def parse(self, file_obj, filename: str = "") -> ParseResult:
        result = ParseResult(transactions=[], parser_name="CSV")

        try:
            raw = file_obj.read()
            text = self._decode_content(raw)
        except Exception as e:
            result.errors.append(f"Failed to read file: {e}")
            return result

        if not text.strip():
            return result

        delimiter = self._detect_delimiter(text)

        try:
            reader = csv.reader(io.StringIO(text), delimiter=delimiter)
            rows = list(reader)
        except Exception as e:
            result.errors.append(f"CSV parsing failed: {e}")
            return result

        if len(rows) < 2:
            return result

        header_idx, column_map = self._find_header(rows)
        if header_idx is None:
            result.errors.append(
                "Could not detect a valid header row. "
                "Expected columns: Date, Narration, Amount/Dr/Cr, etc."
            )
            return result

        for row_idx in range(header_idx + 1, len(rows)):
            row = rows[row_idx]
            if not row or self._is_empty_row(row):
                continue

            if self._is_summary_row(row):
                continue

            txn = self._parse_row(row, column_map)
            if txn:
                result.transactions.append(txn)
            else:
                result.warnings.append(
                    f"Row {row_idx + 1}: Skipped — missing date or amount."
                )

        if result.transactions:
            dates = sorted(t.transaction_date for t in result.transactions)
            result.statement_start_date = dates[0]
            result.statement_end_date = dates[-1]

        return result

    def _decode_content(self, raw: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def _detect_delimiter(self, text: str) -> str:
        first_line = text.split("\n")[0] if text else ""
        if "\t" in first_line:
            return "\t"
        return ","

    def _is_summary_row(self, row: list[str]) -> bool:
        """Detect summary/footer rows like TOTAL, CLOSING, etc."""
        first_cell = (row[0] or "").strip().lower() if row else ""
        return first_cell in self.SUMMARY_KEYWORDS

    def _find_header(self, rows: list[list[str]]) -> tuple[Optional[int], dict]:
        patterns = {
            "date": self.DATE_PATTERNS,
            "narration": self.NARRATION_PATTERNS,
            "reference_no": self.REFERENCE_PATTERNS,
            "cheque_no": self.CHEQUE_PATTERNS,
            "debit": self.DEBIT_PATTERNS,
            "credit": self.CREDIT_PATTERNS,
            "balance": self.BALANCE_PATTERNS,
            "value_date": self.VALUE_DATE_PATTERNS,
        }

        best_score = 0
        best_idx = None
        best_map = {}

        max_scan = min(10, len(rows))
        for idx in range(max_scan):
            row = rows[idx]
            col_map = {}
            score = 0

            for col_idx, cell in enumerate(row):
                cell_clean = self._clean_text(cell).lower()
                if not cell_clean:
                    continue

                for col_name, pattern_list in patterns.items():
                    for pattern in pattern_list:
                        if re.search(pattern, cell_clean, re.IGNORECASE):
                            col_map[col_name] = col_idx
                            score += 1
                            break

            if score > best_score:
                best_score = score
                best_idx = idx
                best_map = col_map

        if best_score >= 3 and "date" in best_map:
            return best_idx, best_map

        return None, {}

    def _parse_row(
        self,
        row: list[str],
        column_map: dict,
    ) -> Optional[ParsedTransaction]:
        date_val = self._get_cell(row, column_map, "date")
        narration_val = self._get_cell(row, column_map, "narration")

        if not narration_val:
            narration_val = self._get_cell(row, column_map, "reference_no")

        if not date_val:
            return None

        debit_val = self._get_cell(row, column_map, "debit")
        credit_val = self._get_cell(row, column_map, "credit")

        amount, dr_cr = self._detect_dr_cr(debit_val, credit_val)

        if amount <= 0:
            return None

        return ParsedTransaction(
            transaction_date=self._clean_text(date_val),
            narration=self._clean_text(narration_val),
            amount=amount,
            dr_cr=dr_cr,
            reference_no=self._clean_text(
                self._get_cell(row, column_map, "reference_no")
            ),
            cheque_no=self._clean_text(
                self._get_cell(row, column_map, "cheque_no")
            ),
            value_date=self._clean_text(
                self._get_cell(row, column_map, "value_date")
            ) or None,
            balance=self._clean_text(
                self._get_cell(row, column_map, "balance")
            ) or None,
            raw_row=dict(enumerate(row)),
        )

    @staticmethod
    def _get_cell(row: list[str], column_map: dict, key: str) -> str:
        idx = column_map.get(key)
        if idx is not None and idx < len(row):
            return row[idx] or ""
        return ""

    @staticmethod
    def _is_empty_row(row: list[str]) -> bool:
        return all(not cell or not cell.strip() for cell in row)