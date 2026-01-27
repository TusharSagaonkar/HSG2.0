from django.test import TestCase
from accounting.models import AccountCategory


class AccountCategoryTest(TestCase):

    def test_create_category(self):
        cat = AccountCategory.objects.create(
            name="Maintenance Income",
            account_type=AccountCategory.AccountType.INCOME,
        )
        self.assertEqual(cat.account_type, "INCOME")

    def test_unique_name_per_type(self):
        AccountCategory.objects.create(
            name="Bank",
            account_type="ASSET",
        )
        with self.assertRaises(Exception):
            AccountCategory.objects.create(
                name="Bank",
                account_type="ASSET",
            )
