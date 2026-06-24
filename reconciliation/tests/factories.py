"""
Test factories for the reconciliation engine.

Each factory produces valid, standalone instances that can be chained
to build complete reconciliation test scenarios.
"""

import io
from datetime import date, timedelta
from decimal import Decimal

import factory
from factory.django import DjangoModelFactory
from django.utils import timezone

from accounting.models import (
    Account,
    AccountCategory,
    FinancialYear,
    LedgerEntry,
    Voucher,
)
from housing_accounting.users.tests.factories import UserFactory
from reconciliation.models import (
    BankStatementImport,
    BankParserProfile,
    BankTransaction,
    BankTransactionNormalized,
    ReconciliationHistory,
    ReconciliationLink,
)
from societies.models import Society


# ---------------------------------------------------------------------------
# Society
# ---------------------------------------------------------------------------

class SocietyFactory(DjangoModelFactory):
    """Minimal society for test isolation."""

    name = factory.Sequence(lambda n: f"Test Society {n}")
    registration_number = factory.Sequence(lambda n: f"REG-{n:05d}")

    class Meta:
        model = Society
        django_get_or_create = ("name",)


# ---------------------------------------------------------------------------
# Accounting scaffolding
# ---------------------------------------------------------------------------

class FinancialYearFactory(DjangoModelFactory):
    """Active financial year spanning the current year."""

    society = factory.SubFactory(SocietyFactory)
    name = "FY 2025-26"
    start_date = date(2025, 4, 1)
    end_date = date(2026, 3, 31)
    is_open = True

    class Meta:
        model = FinancialYear
        django_get_or_create = ("society", "start_date", "end_date")


class AccountCategoryFactory(DjangoModelFactory):
    society = factory.SubFactory(SocietyFactory)
    name = factory.Sequence(lambda n: f"Category {n}")
    account_type = AccountCategory.AccountType.ASSET  # sensible default

    class Meta:
        model = AccountCategory


class AccountFactory(DjangoModelFactory):
    society = factory.SubFactory(SocietyFactory)
    account_type = Account.AccountType.ASSET
    category = factory.SubFactory(
        AccountCategoryFactory,
        society=factory.SelfAttribute('..society'),
        account_type=factory.SelfAttribute('..account_type'),
    )
    name = factory.Sequence(lambda n: f"Account {n}")
    code = factory.Sequence(lambda n: f"{n}.0")
    is_active = True

    class Meta:
        model = Account
        django_get_or_create = ("society", "code")


class BankAccountFactory(AccountFactory):
    """Bank-type account with is_bank=True."""

    name = factory.Sequence(lambda n: f"Bank Account {n}")
    code = factory.Sequence(lambda n: f"1.99.{n}")
    account_type = Account.AccountType.ASSET
    sub_type = Account.SubType.BANK
    is_bank = True


class ExpenseAccountFactory(AccountFactory):
    """Generic expense account."""

    name = factory.Sequence(lambda n: f"Expense Account {n}")
    code = factory.Sequence(lambda n: f"4.{n}")
    account_type = Account.AccountType.EXPENSE
    sub_type = Account.SubType.EXPENSE


class IncomeAccountFactory(AccountFactory):
    """Generic income account."""

    name = factory.Sequence(lambda n: f"Income Account {n}")
    code = factory.Sequence(lambda n: f"3.{n}")
    account_type = Account.AccountType.INCOME
    sub_type = Account.SubType.INCOME


# ---------------------------------------------------------------------------
# Voucher + LedgerEntry (book-side records)
# ---------------------------------------------------------------------------

class VoucherFactory(DjangoModelFactory):
    society = factory.SubFactory(SocietyFactory)
    voucher_type = Voucher.VoucherType.RECEIPT
    voucher_date = factory.LazyFunction(lambda: date.today())
    narration = "Test voucher"

    class Meta:
        model = Voucher
        skip_postgeneration_save = True


class LedgerEntryFactory(DjangoModelFactory):
    voucher = factory.SubFactory(VoucherFactory)
    account = factory.SubFactory(AccountFactory)
    debit = Decimal("0.00")
    credit = Decimal("0.00")

    class Meta:
        model = LedgerEntry


# ---------------------------------------------------------------------------
# Bank-side records
# ---------------------------------------------------------------------------

class BankStatementImportFactory(DjangoModelFactory):
    society = factory.SubFactory(SocietyFactory)
    bank_account = factory.SubFactory(
        BankAccountFactory,
        society=factory.SelfAttribute('..society'),
    )
    file_name = "test_statement.csv"
    file_hash = factory.LazyFunction(lambda: "abc123hash")
    raw_file = factory.django.FileField(
        filename="test.csv",
        data=b"dummy",
    )
    uploaded_by = factory.SubFactory(UserFactory)
    import_status = BankStatementImport.ImportStatus.COMPLETED
    statement_start_date = factory.LazyFunction(lambda: date.today() - timedelta(days=30))
    statement_end_date = factory.LazyFunction(lambda: date.today())
    row_count = 0

    class Meta:
        model = BankStatementImport


class BankParserProfileFactory(DjangoModelFactory):
    society = factory.SubFactory(SocietyFactory)
    bank_name = "HDFC"
    format_name = "HDFC_CSV"
    file_type = "csv"
    header_signature = {"headers": ["date", "narration", "debit", "credit"]}
    parser_class = "reconciliation.services.parsers.csv_parser.CSVParser"
    is_active = True
    priority = 100
    confidence_floor = 70
    notes = ""

    class Meta:
        model = BankParserProfile


class BankTransactionFactory(DjangoModelFactory):
    bank_statement_import = factory.SubFactory(BankStatementImportFactory)
    transaction_date = factory.LazyFunction(lambda: date.today())
    narration = "Test bank transaction"
    reference_no = factory.Sequence(lambda n: f"BT-REF-{n:06d}")
    amount = Decimal("1000.00")
    dr_cr = BankTransaction.DrCr.CREDIT
    raw_row_data = {"source": "factory"}
    duplicate_hash = ""

    class Meta:
        model = BankTransaction


class BankTransactionNormalizedFactory(DjangoModelFactory):
    bank_transaction = factory.SubFactory(BankTransactionFactory)
    cleaned_narration = "Test bank transaction"
    extracted_utr = ""
    extracted_flat_no = ""
    extracted_reference = ""
    extracted_amount_words = ""

    class Meta:
        model = BankTransactionNormalized


# ---------------------------------------------------------------------------
# Reconciliation link + history
# ---------------------------------------------------------------------------

class ReconciliationLinkFactory(DjangoModelFactory):
    society = factory.SubFactory(SocietyFactory)
    matched_amount = Decimal("1000.00")
    match_type = ReconciliationLink.MatchType.EXACT
    confidence_score = 99
    status = ReconciliationLink.Status.MATCHED
    matched_by = factory.SubFactory(UserFactory)
    matched_at = factory.LazyFunction(timezone.now)

    class Meta:
        model = ReconciliationLink

    @factory.lazy_attribute
    def bank_transaction(self):
        if self.society.pk is None:
            self.society.save()
        statement = BankStatementImportFactory(society=self.society)
        return BankTransactionFactory(bank_statement_import=statement)

    @factory.lazy_attribute
    def voucher_entry(self):
        if self.society.pk is None:
            self.society.save()
        v = VoucherFactory(society=self.society)
        return LedgerEntryFactory(
            voucher=v,
            account=AccountFactory(society=self.society),
            debit=Decimal("1000.00"),
            credit=Decimal("0.00"),
        )


class ReconciliationHistoryFactory(DjangoModelFactory):
    reconciliation_link = factory.SubFactory(ReconciliationLinkFactory)
    action = ReconciliationHistory.Action.CREATED
    new_status = ReconciliationLink.Status.MATCHED
    new_match_type = ReconciliationLink.MatchType.EXACT
    new_confidence = 99
    performed_by = factory.SubFactory(UserFactory)
    details = {}

    class Meta:
        model = ReconciliationHistory
        skip_postgeneration_save = True
