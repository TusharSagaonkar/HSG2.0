import csv
import hashlib
import io
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable

from django.core.files.base import ContentFile
from django.db import transaction as db_transaction
from django.utils import timezone

from reconciliation.models import BankStatementImport, BankTransaction
from reconciliation.services.matcher import MatchingEngine
from reconciliation.services.normalizer import NormalizerService

logger = logging.getLogger(__name__)


@dataclass
class ManualEntryRow:
    transaction_date: str
    narration: str
    amount: Decimal | str
    dr_cr: str = "CREDIT"
    reference_no: str = ""
    cheque_no: str = ""
    value_date: str | None = None
    balance: str | None = None
    raw_row: dict = field(default_factory=dict)


class ManualWorkspaceService:
    """
    Helper for the keyboard-first manual reconciliation workspace.

    Reuses the existing BankStatementImport and BankTransaction tables and
    creates one long-lived manual import batch per workspace session.
    """

    def __init__(self, user, society, bank_account, session=None):
        self.user = user
        self.society = society
        self.bank_account = bank_account
        self.session = session

    # ------------------------------------------------------------------
    # Workspace import lifecycle
    # ------------------------------------------------------------------

    def get_or_create_workspace_import(self) -> BankStatementImport:
        if self.session is not None:
            existing_id = self.session.get("manual_workspace_import_id")
            if existing_id:
                workspace_import = (
                    BankStatementImport.objects.filter(
                        id=existing_id,
                        society=self.society,
                        bank_account=self.bank_account,
                    )
                    .first()
                )
                if workspace_import and workspace_import.row_count > 0:
                    return workspace_import

        existing_import = (
            BankStatementImport.objects.filter(
                society=self.society,
                bank_account=self.bank_account,
                source_type__in=["DEMO_RECONCILIATION", "MANUAL", "COPY_PASTE"],
                import_status=BankStatementImport.ImportStatus.COMPLETED,
            )
            .exclude(row_count=0)
            .order_by("-uploaded_at", "-id")
            .first()
        )
        if existing_import:
            if self.session is not None:
                self.session["manual_workspace_import_id"] = existing_import.id
                self.session.modified = True
            return existing_import

        file_hash = self._workspace_hash()
        filename = "manual_workspace.csv"

        workspace_import = BankStatementImport.objects.create(
            society=self.society,
            bank_account=self.bank_account,
            file_name=filename,
            file_hash=file_hash,
            raw_file=ContentFile(b"Date,Narration,Ref No,Debit,Credit,Balance\n", name=filename),
            uploaded_by=self.user,
            import_status=BankStatementImport.ImportStatus.PROCESSING,
            source_type="MANUAL",
        )

        if self.session is not None:
            self.session["manual_workspace_import_id"] = workspace_import.id
            self.session.modified = True

        return workspace_import

    def _workspace_hash(self) -> str:
        seed = f"{self.society_id}:{self.bank_account_id}:{self.user_id}:{timezone.now().isoformat()}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    @property
    def society_id(self):
        return getattr(self.society, "id", None)

    @property
    def bank_account_id(self):
        return getattr(self.bank_account, "id", None)

    @property
    def user_id(self):
        return getattr(self.user, "id", None)

    # ------------------------------------------------------------------
    # Manual row persistence
    # ------------------------------------------------------------------

    def save_row(self, row: ManualEntryRow, *, source_row_index: int | None = None) -> BankTransaction:
        statement_import = self.get_or_create_workspace_import()
        transaction_date = self._parse_date(row.transaction_date)
        if not transaction_date:
            raise ValueError("A valid transaction date is required.")

        amount = self._parse_decimal(row.amount)
        if amount is None or amount <= 0:
            raise ValueError("A positive amount is required.")

        dr_cr = self._resolve_dr_cr(row)
        value_date = self._parse_date(row.value_date) if row.value_date else None
        balance = self._parse_decimal(row.balance) if row.balance not in (None, "") else None
        raw_row = row.raw_row or {
            "transaction_date": row.transaction_date,
            "narration": row.narration,
            "reference_no": row.reference_no,
            "cheque_no": row.cheque_no,
            "dr_cr": dr_cr,
            "amount": str(amount),
            "balance": str(balance) if balance is not None else "",
        }

        duplicate_hash = BankTransaction.compute_duplicate_hash(
            transaction_date,
            amount,
            row.narration or "",
            row.reference_no or "",
        )
        is_duplicate = BankTransaction.objects.filter(
            bank_statement_import=statement_import,
            duplicate_hash=duplicate_hash,
        ).exists()

        with db_transaction.atomic():
            bank_transaction = BankTransaction.objects.create(
                bank_statement_import=statement_import,
                source_row_index=source_row_index,
                transaction_date=transaction_date,
                value_date=value_date,
                narration=row.narration or "",
                reference_no=row.reference_no or "",
                cheque_no=row.cheque_no or "",
                amount=amount,
                dr_cr=dr_cr,
                balance=balance,
                raw_row_data=raw_row,
                duplicate_hash=duplicate_hash,
                is_duplicate=is_duplicate,
            )

            NormalizerService(self.society).normalize_transaction(bank_transaction)
            MatchingEngine(self.society).run_matching(
                bank_transactions=[bank_transaction],
                auto_confirm=False,
                create_suggestions=True,
            )

            statement_import.row_count = statement_import.transactions.count()
            statement_import.statement_start_date = self._min_statement_date(statement_import)
            statement_import.statement_end_date = self._max_statement_date(statement_import)
            statement_import.save(
                update_fields=[
                    "row_count",
                    "statement_start_date",
                    "statement_end_date",
                ]
            )

        return bank_transaction

    def save_rows(self, rows: Iterable[ManualEntryRow]) -> list[BankTransaction]:
        created = []
        for idx, row in enumerate(rows, start=1):
            created.append(self.save_row(row, source_row_index=idx))
        return created

    # ------------------------------------------------------------------
    # Paste parsing
    # ------------------------------------------------------------------

    def parse_pasted_rows(self, text: str) -> list[ManualEntryRow]:
        text = (text or "").strip()
        if not text:
            return []

        delimiter = "\t" if "\t" in text else ","
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        rows = list(reader)
        if not rows:
            return []

        header = [cell.strip().lower() for cell in rows[0]]
        has_header = any(cell in {"date", "narration", "debit", "credit", "balance"} for cell in header)
        data_rows = rows[1:] if has_header else rows

        parsed_rows: list[ManualEntryRow] = []
        for row in data_rows:
            if not any((cell or "").strip() for cell in row):
                continue

            padded = list(row) + [""] * max(0, 6 - len(row))
            if has_header:
                mapping = {header[i]: padded[i].strip() if i < len(padded) else "" for i in range(len(header))}
                parsed_rows.append(
                    ManualEntryRow(
                        transaction_date=mapping.get("date", ""),
                        narration=mapping.get("narration", ""),
                        amount=self._pick_amount_from_mapping(mapping),
                        dr_cr=self._pick_drcr_from_mapping(mapping),
                        reference_no=mapping.get("ref no") or mapping.get("reference") or mapping.get("ref") or "",
                        cheque_no=mapping.get("cheque") or mapping.get("chq") or "",
                        balance=mapping.get("balance") or "",
                        raw_row=mapping,
                    )
                )
                continue

            date_value, narration, ref_no, debit, credit, balance = [cell.strip() for cell in padded[:6]]
            amount, dr_cr = self._resolve_amount_and_side(debit, credit)
            parsed_rows.append(
                ManualEntryRow(
                    transaction_date=date_value,
                    narration=narration,
                    amount=amount,
                    dr_cr=dr_cr,
                    reference_no=ref_no,
                    balance=balance,
                    raw_row={
                        "Date": date_value,
                        "Narration": narration,
                        "Ref No": ref_no,
                        "Debit": debit,
                        "Credit": credit,
                        "Balance": balance,
                    },
                )
            )

        return parsed_rows

    def bulk_save_paste(self, text: str) -> list[BankTransaction]:
        return self.save_rows(self.parse_pasted_rows(text))

    # ------------------------------------------------------------------
    # Suggestions
    # ------------------------------------------------------------------

    def build_suggestions(self, bank_transaction: BankTransaction):
        engine = MatchingEngine(self.society)
        candidates = engine.match_single(bank_transaction)
        suggestions = []
        for candidate in candidates[:10]:
            ledger = candidate.ledger_entry
            suggestions.append(
                {
                    "candidate": candidate,
                    "ledger_entry": ledger,
                    "voucher": ledger.voucher,
                    "member_name": getattr(ledger.unit, "unit_number", "") or getattr(ledger.unit, "name", "") or ledger.account.name,
                }
            )
        return suggestions

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_date(value):
        if value in (None, ""):
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()

        value = str(value).strip()
        for fmt in (
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%d-%m-%Y",
            "%d/%m/%y",
            "%d-%m-%y",
            "%m/%d/%Y",
            "%m/%d/%y",
        ):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _parse_decimal(value):
        if value in (None, ""):
            return None
        try:
            return Decimal(str(value).replace(",", "").strip())
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def _resolve_amount_and_side(debit, credit):
        debit_dec = ManualWorkspaceService._parse_decimal(debit)
        credit_dec = ManualWorkspaceService._parse_decimal(credit)
        if credit_dec and credit_dec > 0:
            return credit_dec, "CREDIT"
        if debit_dec and debit_dec > 0:
            return debit_dec, "DEBIT"
        return Decimal("0.00"), "CREDIT"

    @staticmethod
    def _pick_amount_from_mapping(mapping):
        amount, _ = ManualWorkspaceService._resolve_amount_and_side(
            mapping.get("debit", ""),
            mapping.get("credit", ""),
        )
        return amount

    @staticmethod
    def _pick_drcr_from_mapping(mapping):
        _, dr_cr = ManualWorkspaceService._resolve_amount_and_side(
            mapping.get("debit", ""),
            mapping.get("credit", ""),
        )
        return dr_cr

    def _resolve_dr_cr(self, row: ManualEntryRow) -> str:
        if row.dr_cr in {"DEBIT", "CREDIT"}:
            return row.dr_cr

        debit = self._parse_decimal(getattr(row, "debit", None))
        credit = self._parse_decimal(getattr(row, "credit", None))
        _, dr_cr = self._resolve_amount_and_side(debit or "", credit or "")
        return dr_cr

    def _min_statement_date(self, statement_import: BankStatementImport):
        return (
            statement_import.transactions.order_by("transaction_date", "id")
            .values_list("transaction_date", flat=True)
            .first()
        )

    def _max_statement_date(self, statement_import: BankStatementImport):
        return (
            statement_import.transactions.order_by("-transaction_date", "-id")
            .values_list("transaction_date", flat=True)
            .first()
        )


class ManualStatementImportService:
    """Import rows typed or pasted by an operator through the same pipeline."""

    def __init__(self, user, society, bank_account, session=None):
        self.workspace_service = ManualWorkspaceService(
            user=user,
            society=society,
            bank_account=bank_account,
            session=session,
        )

    def import_rows(self, rows: Iterable[ManualEntryRow], filename: str = "manual_entry.csv") -> BankStatementImport:
        statement_import = self.workspace_service.get_or_create_workspace_import()
        created = self.workspace_service.save_rows(rows)
        statement_import.row_count = len(created)
        statement_import.source_type = "MANUAL"
        statement_import.file_name = filename
        statement_import.import_status = BankStatementImport.ImportStatus.COMPLETED
        statement_import.save(update_fields=["row_count", "source_type", "file_name", "import_status"])
        return statement_import
