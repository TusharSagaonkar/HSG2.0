import io
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from django.db import transaction as db_transaction
from django.utils import timezone

from reconciliation.models import (
    BankStatementImport,
    BankTransaction,
)
from reconciliation.services.detector import StatementFormatDetector
from reconciliation.services.file_reader import StatementFileReader
from reconciliation.services.parsers.registry import ParserRegistry
from reconciliation.services.profile_resolver import BankProfileResolver
from reconciliation.services.parsers import CSVParser, XLSXParser

logger = logging.getLogger(__name__)


class ImportError(Exception):
    """Raised when statement import fails."""


class StatementImportService:
    """
    Orchestrates the full bank statement import workflow:

    1. Compute file hash → check for duplicate imports
    2. Create BankStatementImport record (status=PROCESSING)
    3. Auto-detect file format → route to correct parser
    4. Parse rows → validate → bulk create BankTransaction records
    5. Detect internal duplicates via duplicate_hash
    6. Mark import COMPLETED or FAILED
    """

    # Map file extensions to parser classes
    PARSER_MAP = {
        "csv": CSVParser,
        "txt": CSVParser,
        "xlsx": XLSXParser,
        "xls": XLSXParser,
    }

    def __init__(self, user, society, bank_account):
        self.user = user
        self.society = society
        self.bank_account = bank_account

    def import_file(self, file_obj) -> BankStatementImport:
        """
        Execute the full import pipeline for an uploaded file.

        Args:
            file_obj: Django UploadedFile instance.

        Returns:
            BankStatementImport record.

        Raises:
            ImportError: If import fails at any stage.
        """
        filename = file_obj.name
        extension = self._get_extension(filename)
        if extension not in self.PARSER_MAP:
            raise ImportError(
                f"Unsupported file format: .{extension}. "
                f"Supported: {', '.join(sorted(self.PARSER_MAP.keys()))}"
            )

        # Step 1: Compute file hash
        file_obj.seek(0)
        file_hash = BankStatementImport.compute_file_hash(file_obj)
        file_obj.seek(0)

        # Step 2: Check for duplicate import
        duplicate = BankStatementImport.objects.filter(
            society=self.society,
            bank_account=self.bank_account,
            file_hash=file_hash,
            import_status=BankStatementImport.ImportStatus.COMPLETED,
        ).first()

        if duplicate:
            raise ImportError(
                f"This statement has already been imported "
                f"(Import #{duplicate.id} on {duplicate.uploaded_at.date()})."
            )

        # Step 3: Create import record
        statement_import = BankStatementImport.objects.create(
            society=self.society,
            bank_account=self.bank_account,
            file_name=filename,
            file_hash=file_hash,
            raw_file=file_obj,
            uploaded_by=self.user,
            import_status=BankStatementImport.ImportStatus.PROCESSING,
        )

        try:
            # Step 4: Detect format and resolve parser
            reader = StatementFileReader()
            detector = StatementFormatDetector()
            profile_resolver = BankProfileResolver()

            raw_bytes = reader.read(file_obj)
            detection = detector.detect(filename, header_row=None, content=raw_bytes)
            profile = profile_resolver.resolve(
                self.society,
                detection.bank_name,
                detection.file_type,
                detection.confidence,
                detection.format_name,
            )
            parser_class = ParserRegistry.get_parser_class(
                detection.file_type,
                getattr(profile, "parser_class", None),
            ) or self.PARSER_MAP[extension]
            parser = parser_class()

            file_obj.seek(0)
            result = parser.parse(file_obj, filename=filename)

            if not result.is_valid:
                error_msg = "; ".join(result.errors) if result.errors else "No transactions found."
                raise ImportError(error_msg)

            # Step 5: Bulk create BankTransaction records
            with db_transaction.atomic():
                bank_txns = self._create_transactions(
                    statement_import,
                    result.transactions,
                )

                # Step 6: Update import record
                statement_import.statement_start_date = self._parse_date_safe(
                    result.statement_start_date
                )
                statement_import.statement_end_date = self._parse_date_safe(
                    result.statement_end_date
                )
                statement_import.row_count = len(bank_txns)
                statement_import.import_status = BankStatementImport.ImportStatus.COMPLETED
                statement_import.save(
                    update_fields=[
                        "statement_start_date",
                        "statement_end_date",
                        "row_count",
                        "import_status",
                    ]
                )

            logger.info(
                "Statement import #%d completed: %d transactions from '%s'",
                statement_import.id,
                len(bank_txns),
                filename,
            )

        except Exception as e:
            # Step 7: Mark as FAILED
            statement_import.import_status = BankStatementImport.ImportStatus.FAILED
            statement_import.error_log = str(e)
            statement_import.save(update_fields=["import_status", "error_log"])
            logger.exception("Statement import #%d failed: %s", statement_import.id, e)
            raise ImportError(f"Import failed: {e}") from e

        return statement_import

    def _create_transactions(
        self,
        statement_import: BankStatementImport,
        parsed_rows: list,
    ) -> list[BankTransaction]:
        """
        Convert parsed rows into BankTransaction records with bulk creation.
        Also detects internal duplicates within the batch.
        """
        bank_txns = []
        seen_hashes = set()

        for row in parsed_rows:
            txn_date = self._parse_date_safe(row.transaction_date)
            if not txn_date:
                continue

            amount = self._parse_decimal(row.amount)
            if amount is None or amount <= 0:
                continue

            value_date = self._parse_date_safe(row.value_date) if row.value_date else None
            balance = self._parse_decimal(row.balance) if row.balance else None

            dup_hash = BankTransaction.compute_duplicate_hash(
                txn_date, amount, row.narration, row.reference_no
            )

            is_duplicate = dup_hash in seen_hashes
            seen_hashes.add(dup_hash)

            bank_txns.append(
                BankTransaction(
                    bank_statement_import=statement_import,
                    transaction_date=txn_date,
                    value_date=value_date,
                    narration=row.narration or "",
                    reference_no=row.reference_no or "",
                    cheque_no=row.cheque_no or "",
                    amount=amount,
                    dr_cr=row.dr_cr or "CREDIT",
                    balance=balance,
                    raw_row_data=row.raw_row,
                    duplicate_hash=dup_hash,
                    is_duplicate=is_duplicate,
                )
            )

        if bank_txns:
            BankTransaction.objects.bulk_create(bank_txns, batch_size=500)

        return bank_txns

    @staticmethod
    def _get_extension(filename: str) -> str:
        """Extract file extension from filename."""
        if "." in filename:
            return filename.rsplit(".", 1)[-1].lower()
        return ""

    @staticmethod
    def _parse_date_safe(value) -> Optional[date]:
        """Parse a date string safely, returning None on failure."""
        if value is None:
            return None
        if isinstance(value, date):
            return value
        if isinstance(value, datetime):
            return value.date()

        value_str = str(value).strip()
        if not value_str:
            return None

        formats = [
            "%Y-%m-%d",
            "%d-%m-%Y",
            "%d-%m-%y",
            "%d/%m/%Y",
            "%d/%m/%y",
            "%m/%d/%Y",
            "%m/%d/%y",
            "%d-%b-%Y",
            "%d-%b-%y",
            "%d %b %Y",
            "%d %B %Y",
        ]

        for fmt in formats:
            try:
                parsed = datetime.strptime(value_str, fmt)
                return parsed.date()
            except ValueError:
                continue

        return None

    @staticmethod
    def _parse_decimal(value) -> Optional[Decimal]:
        """Parse a decimal value safely, returning None on failure."""
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        if isinstance(value, (int, float)):
            return Decimal(str(value))

        cleaned = str(value).strip().replace(",", "").replace(" ", "")
        if cleaned in ("", "-", "N/A", "None"):
            return None

        try:
            return Decimal(cleaned)
        except Exception:
            return None
