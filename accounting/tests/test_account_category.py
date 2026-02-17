from django.test import TestCase
from accounting.models import AccountCategory
from housing.models import Society


class AccountCategoryTest(TestCase):
    def setUp(self):
        self.society = Society.objects.create(name="Test Society")

    def test_create_category(self):
        cat = AccountCategory.objects.create(
            society=self.society,
            name="Custom Income",
            account_type=AccountCategory.AccountType.INCOME,
        )
        self.assertEqual(cat.account_type, "INCOME")

    def test_unique_name_per_type(self):
        AccountCategory.objects.create(
            society=self.society,
            name="Bank",
            account_type="ASSET",
        )
        with self.assertRaises(Exception):
            AccountCategory.objects.create(
                society=self.society,
                name="Bank",
                account_type="ASSET",
            )
