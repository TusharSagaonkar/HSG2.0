from django.test import TestCase
from accounting.models import Account, AccountCategory


class AccountTest(TestCase):

    def setUp(self):
        self.asset_cat = AccountCategory.objects.create(
            name="Bank",
            account_type="ASSET",
        )

    def test_create_account(self):
        acc = Account.objects.create(
            name="YES Bank",
            category=self.asset_cat,
        )
        self.assertEqual(acc.account_type, "ASSET")

    def test_parent_child_account(self):
        parent = Account.objects.create(
            name="Bank",
            category=self.asset_cat,
        )
        child = Account.objects.create(
            name="YES Bank",
            category=self.asset_cat,
            parent=parent,
        )
        self.assertEqual(child.parent, parent)

    def test_duplicate_account_under_same_parent_not_allowed(self):
        parent = Account.objects.create(
            name="Bank",
            category=self.asset_cat,
        )
        Account.objects.create(
            name="YES Bank",
            category=self.asset_cat,
            parent=parent,
        )
        with self.assertRaises(Exception):
            Account.objects.create(
                name="YES Bank",
                category=self.asset_cat,
                parent=parent,
            )
