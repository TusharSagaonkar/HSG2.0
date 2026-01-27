from django.test import TestCase
from django.core.exceptions import ValidationError
from datetime import date
from decimal import Decimal

from accounting.models import (
    Voucher,
    LedgerEntry,
    Account,
    AccountCategory,
)
from housing.models import Society, Unit, Structure


class SameAccountDrCrTest(TestCase):

    def setUp(self):
        self.society = Society.objects.create(name="Test Society")

        self.structure = Structure.objects.create(
            society=self.society,
            name="Building A",
            structure_type="BUILDING",  # adjust if enum differs
        )

        self.unit = Unit.objects.create(
            structure=self.structure,
            identifier="101",
            unit_type=Unit.UnitType.FLAT,
        )

        self.asset_cat = AccountCategory.objects.create(
            name="Maintenance Receivable",
            account_type="ASSET",
        )

        self.receivable = Account.objects.create(
            society=self.society,
            name="Maintenance Receivable",
            category=self.asset_cat,
        )
        self.income_cat = AccountCategory.objects.create(
            name="Maintenance Income",
            account_type="INCOME",
        )

        self.income = Account.objects.create(
            society=self.society,
            name="Maintenance Income",
            category=self.income_cat,
        )


    def test_same_account_debit_and_credit_in_same_voucher_is_blocked(self):
        v = Voucher.objects.create(
            society=self.society,
            voucher_type="GENERAL",
            voucher_date=date.today(),
        )

        LedgerEntry.objects.create(
            voucher=v,
            account=self.receivable,
            unit=self.unit,
            debit=Decimal("1000.00"),
        )

        LedgerEntry.objects.create(
            voucher=v,
            account=self.receivable,
            unit=self.unit,
            credit=Decimal("1000.00"),
        )

        with self.assertRaises(ValidationError):
            v.post()

    def test_same_account_multiple_debits_allowed(self):
        v = Voucher.objects.create(
            society=self.society,
            voucher_type="GENERAL",
            voucher_date=date.today(),
        )

        LedgerEntry.objects.create(
            voucher=v,
            account=self.receivable,
            unit=self.unit,
            debit=Decimal("1000.00"),
        )

        LedgerEntry.objects.create(
            voucher=v,
            account=self.receivable,
            unit=self.unit,
            debit=Decimal("500.00"),
        )

        LedgerEntry.objects.create(
            voucher=v,
            account=self.income,
            credit=Decimal("1500.00"),
        )

        v.post()  # should NOT raise