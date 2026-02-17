import pytest
from datetime import date
from django.utils import timezone

from accounting.models import Account
from accounting.models import AccountCategory
from accounting.models import FinancialYear
from accounting.services.standard_accounts import DEFAULT_ACCOUNT_DEFINITIONS
from accounting.services.standard_accounts import DEFAULT_CATEGORY_DEFINITIONS
from housing.models import Society

pytestmark = pytest.mark.django_db


def test_society_creation_creates_default_accounts_and_society_categories():
    society_1 = Society.objects.create(name="Green Heights")
    society_2 = Society.objects.create(name="Blue Heights")

    expected_accounts = {name for name, _, _ in DEFAULT_ACCOUNT_DEFINITIONS}

    accounts_1 = set(
        Account.objects.filter(society=society_1).values_list("name", flat=True)
    )
    accounts_2 = set(
        Account.objects.filter(society=society_2).values_list("name", flat=True)
    )

    assert expected_accounts.issubset(accounts_1)
    assert expected_accounts.issubset(accounts_2)

    categories_1 = AccountCategory.objects.filter(society=society_1).count()
    categories_2 = AccountCategory.objects.filter(society=society_2).count()
    assert categories_1 == len(DEFAULT_CATEGORY_DEFINITIONS)
    assert categories_2 == len(DEFAULT_CATEGORY_DEFINITIONS)


def test_society_delete_cascades_accounts_delete():
    society = Society.objects.create(name="Cascade Society")
    society_id = society.id
    assert Account.objects.filter(society=society).exists()

    society.delete()

    assert not Account.objects.filter(society_id=society_id).exists()


def test_society_creation_creates_current_financial_year():
    today = timezone.localdate()
    start_year = today.year if today.month >= 4 else today.year - 1
    expected_start = date(start_year, 4, 1)
    expected_end = date(start_year + 1, 3, 31)

    society = Society.objects.create(name="FY Bootstrap Society")

    fy = FinancialYear.objects.get(society=society)
    assert fy.start_date == expected_start
    assert fy.end_date == expected_end
    assert fy.is_open is True
    assert fy.name.startswith(f"FY {start_year}-")


def test_society_creation_uses_same_financial_year_name_across_societies():
    society_1 = Society.objects.create(name="FY Name Society One")
    society_2 = Society.objects.create(name="FY Name Society Two")

    fy_1 = FinancialYear.objects.get(society=society_1)
    fy_2 = FinancialYear.objects.get(society=society_2)

    assert fy_1.name == fy_2.name
