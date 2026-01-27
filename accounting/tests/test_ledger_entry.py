from django.test import TestCase
from django.core.exceptions import ValidationError
from datetime import date
from accounting.models import (
    LedgerEntry,
    Voucher,
    Account,
    AccountCategory,
)


class LedgerEntryTest(TestCase):

    def setUp(self):
        cat = AccountCategory.objects.create(
            name="Cash",
            account_type="ASSET",
        )
        self.account = Account.objects.create(
            name="Cash",
            category=cat,
        )
        self.voucher = Voucher.objects.create(
            voucher_type="GENERAL",
            voucher_date=date.today(),
        )

    def test_cannot_have_both_debit_and_credit(self):
        entry = LedgerEntry(
            voucher=self.voucher,
            account=self.account,
            debit=100,
            credit=100,
        )
        with self.assertRaises(ValidationError):
            entry.full_clean()

    def test_must_have_either_debit_or_credit(self):
        entry = LedgerEntry(
            voucher=self.voucher,
            account=self.account,
            debit=0,
            credit=0,
        )
        with self.assertRaises(ValidationError):
            entry.full_clean()
