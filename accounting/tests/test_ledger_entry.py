from django.test import TestCase
from django.core.exceptions import ValidationError
from datetime import date
from accounting.models import (
    LedgerEntry,
    Voucher,
    Account,
    AccountCategory,
)
from housing.models import Society, Structure, Unit


class LedgerEntryTest(TestCase):

    def setUp(self):
        self.society = Society.objects.create(name="Test Society")
        cat, _ = AccountCategory.objects.get_or_create(
            society=self.society,
            name="Cash",
            account_type="ASSET",
        )
        self.account = Account.objects.create(
            society=self.society,
            name="Cash",
            category=cat,
        )
        self.voucher = Voucher.objects.create(
            society=self.society,
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

    def test_negative_amounts_are_blocked(self):
        entry = LedgerEntry(
            voucher=self.voucher,
            account=self.account,
            debit=-1,
            credit=0,
        )
        with self.assertRaises(ValidationError):
            entry.full_clean()

    def test_inactive_account_is_blocked(self):
        self.account.is_active = False
        self.account.save(update_fields=["is_active"])
        entry = LedgerEntry(
            voucher=self.voucher,
            account=self.account,
            debit=10,
            credit=0,
        )
        with self.assertRaises(ValidationError):
            entry.full_clean()

    def test_cross_society_account_is_blocked(self):
        other_society = Society.objects.create(name="Other Society")
        cat, _ = AccountCategory.objects.get_or_create(
            society=other_society,
            name="Other Cash",
            account_type="ASSET",
        )
        other_account = Account.objects.create(
            society=other_society,
            name="Other Cash",
            category=cat,
        )
        entry = LedgerEntry(
            voucher=self.voucher,
            account=other_account,
            debit=10,
            credit=0,
        )
        with self.assertRaises(ValidationError):
            entry.full_clean()

    def test_cross_society_unit_is_blocked(self):
        other_society = Society.objects.create(name="Unit Other Society")
        other_structure = Structure.objects.create(
            society=other_society,
            structure_type=Structure.StructureType.BUILDING,
            name="Building B",
        )
        other_unit = Unit.objects.create(
            structure=other_structure,
            unit_type=Unit.UnitType.FLAT,
            identifier="201",
        )
        entry = LedgerEntry(
            voucher=self.voucher,
            account=self.account,
            unit=other_unit,
            debit=10,
            credit=0,
        )
        with self.assertRaises(ValidationError):
            entry.full_clean()
