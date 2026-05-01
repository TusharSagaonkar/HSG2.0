from django.test import RequestFactory, TestCase

from accounting.models import Account, AccountCategory
from accounting.views import _build_account_tree
from housing.models import Society


class AccountTreeTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.society = Society.objects.create(name="Test Society")

    def test_tree_is_sorted_by_account_type_and_sub_type(self):
        asset_bank_cat, _ = AccountCategory.objects.get_or_create(
            society=self.society,
            name="Bank & Cash",
            account_type="ASSET",
        )
        asset_member_cat, _ = AccountCategory.objects.get_or_create(
            society=self.society,
            name="Member Receivables",
            account_type="ASSET",
        )
        expense_cat, _ = AccountCategory.objects.get_or_create(
            society=self.society,
            name="Maintenance",
            account_type="EXPENSE",
        )

        accounts = [
            Account.objects.create(
                society=self.society,
                name="Office Expense",
                category=expense_cat,
                account_type="EXPENSE",
                sub_type="GENERAL",
            ),
            Account.objects.create(
                society=self.society,
                name="Member A",
                category=asset_member_cat,
                account_type="ASSET",
                sub_type="MEMBER",
            ),
            Account.objects.create(
                society=self.society,
                name="Bank Account",
                category=asset_bank_cat,
                account_type="ASSET",
                sub_type="BANK",
            ),
            Account.objects.create(
                society=self.society,
                name="Fuel Expense",
                category=expense_cat,
                account_type="EXPENSE",
                sub_type="GENERAL",
            ),
        ]

        tree = _build_account_tree(accounts)

        self.assertEqual([node["label"] for node in tree], ["Asset", "Expense"])
        self.assertEqual([node["label"] for node in tree[0]["sub_types"]], ["Bank", "Member"])
        self.assertEqual([account.name for account in tree[0]["sub_types"][0]["accounts"]], ["Bank Account"])
        self.assertEqual([account.name for account in tree[0]["sub_types"][1]["accounts"]], ["Member A"])
        self.assertEqual([node["label"] for node in tree[1]["sub_types"]], ["General"])
        self.assertEqual([account.name for account in tree[1]["sub_types"][0]["accounts"]], ["Fuel Expense", "Office Expense"])
