from django.test import TestCase
from datetime import date

from housing.models import Society
from accounting.models.model_FinancialYear import FinancialYear
from accounting.models.model_AccountingPeriod import AccountingPeriod
from accounting.models.model_AccountCategory import AccountCategory
from accounting.models.model_Account import Account
from accounting.models.model_Voucher import Voucher
from accounting.models.model_LedgerEntry import LedgerEntry


class TrialBalanceDataSetupTest(TestCase):
    """
    Controlled accounting dataset for validating
    Trial Balance, P&L, Balance Sheet, etc.
    """

    @classmethod
    def setUpTestData(cls):
        # -------------------------------------------------
        # Society
        # -------------------------------------------------
        cls.society = Society.objects.create(
            name="Test Society"
        )

        # -------------------------------------------------
        # Financial Year (OPEN, society-scoped)
        # -------------------------------------------------
        cls.fy = FinancialYear.objects.create(
            society=cls.society,
            name="FY 2024-25",
            start_date=date(2024, 4, 1),
            end_date=date(2025, 3, 31),
            is_open=True,
        )

        # -------------------------------------------------
        # Accounting Period (OPEN, GLOBAL)
        # -------------------------------------------------
        AccountingPeriod.objects.create(
            year=2024,
            month=4,
            is_open=True,
        )

        # -------------------------------------------------
        # Account Categories
        # -------------------------------------------------
        asset = AccountCategory.objects.create(
            name="Assets",
            account_type=AccountCategory.AccountType.ASSET,
        )
        income = AccountCategory.objects.create(
            name="Income",
            account_type=AccountCategory.AccountType.INCOME,
        )
        expense = AccountCategory.objects.create(
            name="Expenses",
            account_type=AccountCategory.AccountType.EXPENSE,
        )
        equity = AccountCategory.objects.create(
            name="Equity",
            account_type=AccountCategory.AccountType.EQUITY,
        )

        # -------------------------------------------------
        # Accounts (ALL society-scoped)
        # -------------------------------------------------
        cls.cash = Account.objects.create(
            society=cls.society,
            name="Cash",
            category=asset,
            is_active=True,
        )

        cls.bank = Account.objects.create(
            society=cls.society,
            name="Bank",
            category=asset,
            is_active=True,
        )

        cls.maintenance_receivable = Account.objects.create(
            society=cls.society,
            name="Maintenance Receivable",
            category=asset,
            is_active=True,
        )

        cls.maintenance_income = Account.objects.create(
            society=cls.society,
            name="Maintenance Income",
            category=income,
            is_active=True,
        )

        cls.electricity_expense = Account.objects.create(
            society=cls.society,
            name="Electricity Expense",
            category=expense,
            is_active=True,
        )

        cls.salary_expense = Account.objects.create(
            society=cls.society,
            name="Salary Expense",
            category=expense,
            is_active=True,
        )

        cls.opening_equity = Account.objects.create(
            society=cls.society,
            name="Opening Balance Equity",
            category=equity,
            is_active=True,
        )

        # -------------------------------------------------
        # Voucher 1 — Opening Balance (01-Apr-2024)
        # -------------------------------------------------
        v1 = Voucher.objects.create(
            society=cls.society,
            voucher_type=Voucher.VoucherType.OPENING,
            voucher_date=date(2024, 4, 1),
            narration="Opening Balances",
        )

        LedgerEntry.objects.create(voucher=v1, account=cls.cash, debit=50000)
        LedgerEntry.objects.create(voucher=v1, account=cls.bank, debit=150000)
        LedgerEntry.objects.create(voucher=v1, account=cls.opening_equity, credit=200000)

        v1.post()

        # -------------------------------------------------
        # Voucher 2 — Maintenance Billed (05-Apr-2024)
        # -------------------------------------------------
        v2 = Voucher.objects.create(
            society=cls.society,
            voucher_type=Voucher.VoucherType.GENERAL,
            voucher_date=date(2024, 4, 5),
            narration="Maintenance billed",
        )

        LedgerEntry.objects.create(
            voucher=v2,
            account=cls.maintenance_receivable,
            debit=20000,
        )
        LedgerEntry.objects.create(
            voucher=v2,
            account=cls.maintenance_income,
            credit=20000,
        )

        v2.post()

        # -------------------------------------------------
        # Voucher 3 — Maintenance Collected (10-Apr-2024)
        # -------------------------------------------------
        v3 = Voucher.objects.create(
            society=cls.society,
            voucher_type=Voucher.VoucherType.RECEIPT,
            voucher_date=date(2024, 4, 10),
            narration="Maintenance collected",
        )

        LedgerEntry.objects.create(voucher=v3, account=cls.cash, debit=15000)
        LedgerEntry.objects.create(
            voucher=v3,
            account=cls.maintenance_receivable,
            credit=15000,
        )

        v3.post()

        # -------------------------------------------------
        # Voucher 4 — Electricity Bill Paid (15-Apr-2024)
        # -------------------------------------------------
        v4 = Voucher.objects.create(
            society=cls.society,
            voucher_type=Voucher.VoucherType.PAYMENT,
            voucher_date=date(2024, 4, 15),
            narration="Electricity bill paid",
        )

        LedgerEntry.objects.create(
            voucher=v4,
            account=cls.electricity_expense,
            debit=5000,
        )
        LedgerEntry.objects.create(
            voucher=v4,
            account=cls.cash,
            credit=5000,
        )

        v4.post()

        # -------------------------------------------------
        # Voucher 5 — Salary Paid (20-Apr-2024)
        # -------------------------------------------------
        v5 = Voucher.objects.create(
            society=cls.society,
            voucher_type=Voucher.VoucherType.PAYMENT,
            voucher_date=date(2024, 4, 20),
            narration="Salary paid",
        )

        LedgerEntry.objects.create(
            voucher=v5,
            account=cls.salary_expense,
            debit=8000,
        )
        LedgerEntry.objects.create(
            voucher=v5,
            account=cls.bank,
            credit=8000,
        )

        v5.post()

    # -------------------------------------------------
    # Sanity Check
    # -------------------------------------------------
    def test_dataset_is_balanced(self):
        """
        Total debit must equal total credit
        """
        from django.db.models import Sum

        totals = LedgerEntry.objects.aggregate(
            debit=Sum("debit"),
            credit=Sum("credit"),
        )

        self.assertEqual(totals["debit"], totals["credit"])
