from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedTransaction:
    """Normalized structure for a single bank statement row from any parser."""

    transaction_date: str
    narration: str
    amount: float
    dr_cr: str  # DEBIT or CREDIT
    reference_no: str = ""
    cheque_no: str = ""
    value_date: Optional[str] = None
    balance: Optional[str] = None
    raw_row: dict = field(default_factory=dict)


@dataclass
class ParseResult:
    """Result of parsing a bank statement file."""

    transactions: list[ParsedTransaction]
    statement_start_date: Optional[str] = None
    statement_end_date: Optional[str] = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    bank_name: str = ""
    account_number: str = ""
    parser_name: str = ""

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0 and len(self.transactions) > 0


class BaseStatementParser(ABC):
    """Abstract base for all bank statement parsers."""

    bank_name: str = "Unknown"
    _supported_extensions: tuple[str, ...] = ()

    @classmethod
    def supports_extension(cls, extension: str) -> bool:
        """Check if this parser supports the given file extension."""
        return extension.lower().lstrip(".") in cls._supported_extensions

    @abstractmethod
    def parse(self, file_obj, filename: str = "") -> ParseResult:
        """
        Parse a bank statement file and return normalized transactions.

        Args:
            file_obj: A file-like object opened in binary mode.
            filename: The original filename (used for extension detection).

        Returns:
            ParseResult containing normalized transactions and any errors/warnings.
        """
        ...

    def _detect_dr_cr(
        self,
        debit_col,
        credit_col,
    ) -> tuple:
        """
        Given debit and credit column values, determine the amount and direction.

        Returns:
            Tuple of (amount, dr_cr) where dr_cr is 'DEBIT' or 'CREDIT'.
        """
        debit = self._parse_amount(debit_col)
        credit = self._parse_amount(credit_col)

        if debit and debit > 0:
            return debit, "DEBIT"
        elif credit and credit > 0:
            return credit, "CREDIT"
        return 0.0, "CREDIT"

    @staticmethod
    def _parse_amount(value) -> Optional[float]:
        """Parse a string amount, handling commas and whitespace."""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        cleaned = str(value).strip().replace(",", "").replace(" ", "")
        if cleaned == "" or cleaned == "-":
            return None
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _clean_text(value) -> str:
        """Normalize text: strip, collapse whitespace."""
        if value is None:
            return ""
        return " ".join(str(value).strip().split())