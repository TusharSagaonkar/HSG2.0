from datetime import date
from datetime import timedelta
from decimal import Decimal
from http import HTTPStatus

import pytest
from django.urls import reverse
from django.utils import timezone

from accounting.models import Account
from accounting.models import AccountCategory
from accounting.models import AccountingPeriod
from accounting.models import FinancialYear
from accounting.models import LedgerEntry
from accounting.models import Voucher
from housing_accounting.selection import SESSION_SELECTED_FINANCIAL_YEAR_ID
from housing_accounting.selection import SESSION_SELECTED_SOCIETY_ID
from housing.models import Society
from societies.models import Membership
from societies.roles import ROLE_OWNER

pytestmark = pytest.mark.django_db


def test_voucher_entry_requires_authentication(client):
    response = client.get(reverse("accounting:voucher-entry"))
    assert response.status_code == HTTPStatus.FOUND


def test_voucher_entry_shows_shortcuts_navbar_button(client, user):
    society = Society.objects.create(name="Shortcut Button Society")
    client.force_login(user)

    response = client.get(reverse("accounting:voucher-entry"), {"society": society.pk})

    assert response.status_code == HTTPStatus.OK
    assert 'data-shortcut-help-trigger' in response.content.decode()


def test_voucher_posting_menu_requires_authentication(client):
    response = client.get(reverse("accounting:voucher-posting"))
    assert response.status_code == HTTPStatus.FOUND


def test_voucher_posting_menu_has_new_voucher_entry_get_action(client, user):
    society = Society.objects.create(name="Posting Menu Society")
    client.force_login(user)
    session = client.session
    session[SESSION_SELECTED_SOCIETY_ID] = society.id
    session.save()

    response = client.get(reverse("accounting:voucher-posting"))

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert (
        f'<form method="get" action="{reverse("accounting:voucher-entry")}" class="m-0">'
        in content
    )
    assert "New Voucher Entry" in content


def test_voucher_posting_menu_shows_posted_vouchers(client, user):
    society = Society.objects.create(name="Posting Posted Society")
    other_society = Society.objects.create(name="Other Posting Posted Society")
    Membership.objects.create(user=user, society=society, role=ROLE_OWNER)
    client.force_login(user)
    session = client.session
    session[SESSION_SELECTED_SOCIETY_ID] = society.id
    session.save()

    draft_voucher = Voucher.objects.create(
        society=society,
        voucher_type="GENERAL",
        voucher_date=timezone.localdate(),
        narration="Visible draft voucher",
    )
    posted_voucher = Voucher.objects.create(
        society=society,
        voucher_type="GENERAL",
        voucher_date=timezone.localdate(),
        narration="Visible posted voucher",
        voucher_number=1,
    )
    other_posted_voucher = Voucher.objects.create(
        society=other_society,
        voucher_type="GENERAL",
        voucher_date=timezone.localdate(),
        narration="Hidden posted voucher",
        voucher_number=1,
    )

    asset_category, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Cash",
        account_type=AccountCategory.AccountType.ASSET,
    )
    income_category, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Income",
        account_type=AccountCategory.AccountType.INCOME,
    )
    cash = Account.objects.create(
        society=society,
        name="Posting Cash",
        code="9.9.10",
        category=asset_category,
        account_type=Account.AccountType.ASSET,
    )
    income = Account.objects.create(
        society=society,
        name="Posting Income",
        code="9.9.11",
        category=income_category,
        account_type=Account.AccountType.INCOME,
    )
    LedgerEntry.objects.create(voucher=draft_voucher, account=cash, debit=Decimal("500.00"))
    LedgerEntry.objects.create(voucher=draft_voucher, account=income, credit=Decimal("500.00"))
    LedgerEntry.objects.create(voucher=posted_voucher, account=cash, debit=Decimal("700.00"))
    LedgerEntry.objects.create(voucher=posted_voucher, account=income, credit=Decimal("700.00"))
    Voucher.objects.filter(pk=posted_voucher.pk).update(posted_at=timezone.now())
    posted_voucher.refresh_from_db()

    other_asset_category, _ = AccountCategory.objects.get_or_create(
        society=other_society,
        name="Cash",
        account_type=AccountCategory.AccountType.ASSET,
    )
    other_income_category, _ = AccountCategory.objects.get_or_create(
        society=other_society,
        name="Income",
        account_type=AccountCategory.AccountType.INCOME,
    )
    other_cash = Account.objects.create(
        society=other_society,
        name="Other Posting Cash",
        code="9.9.10",
        category=other_asset_category,
        account_type=Account.AccountType.ASSET,
    )
    other_income = Account.objects.create(
        society=other_society,
        name="Other Posting Income",
        code="9.9.11",
        category=other_income_category,
        account_type=Account.AccountType.INCOME,
    )
    LedgerEntry.objects.create(voucher=other_posted_voucher, account=other_cash, debit=Decimal("900.00"))
    LedgerEntry.objects.create(voucher=other_posted_voucher, account=other_income, credit=Decimal("900.00"))
    Voucher.objects.filter(pk=other_posted_voucher.pk).update(posted_at=timezone.now())
    other_posted_voucher.refresh_from_db()

    response = client.get(reverse("accounting:voucher-posting"))

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "Draft Vouchers" in content
    assert "Posted Vouchers" in content
    assert draft_voucher.display_number in content
    assert posted_voucher.display_number in content
    assert "700.00" in content
    assert other_society.name not in content
    assert "900.00" not in content


def test_voucher_detail_requires_authentication(client):
    society = Society.objects.create(name="Auth Society")
    voucher = Voucher.objects.create(
        society=society,
        voucher_type="GENERAL",
        voucher_date=timezone.localdate(),
    )
    response = client.get(reverse("accounting:voucher-detail", kwargs={"pk": voucher.pk}))
    assert response.status_code == HTTPStatus.FOUND


def test_voucher_entry_create_draft(client, user):
    society = Society.objects.create(name="Test Society")
    client.force_login(user)

    response = client.post(
        reverse("accounting:voucher-entry"),
        data={
            "society": str(society.pk),
            "voucher_type": "GENERAL",
            "voucher_date": "2024-08-06",
            "narration": "Frontend draft",
            "entries-TOTAL_FORMS": "2",
            "entries-INITIAL_FORMS": "0",
            "entries-MIN_NUM_FORMS": "0",
            "entries-MAX_NUM_FORMS": "1000",
            "entries-0-account": str(
                Account.objects.get(society=society, name="Cash in Hand").pk
            ),
            "entries-0-unit": "",
            "entries-0-debit": "1000.00",
            "entries-0-credit": "",
            "entries-1-account": str(
                Account.objects.get(society=society, name="Maintenance Charges").pk
            ),
            "entries-1-unit": "",
            "entries-1-debit": "",
            "entries-1-credit": "1000.00",
        },
    )

    assert response.status_code == HTTPStatus.FOUND
    assert response.url == reverse("accounting:voucher-posting")
    assert Voucher.objects.count() == 1
    assert LedgerEntry.objects.count() == 2


def test_voucher_entry_accounts_filtered_by_selected_society(client, user):
    society_one = Society.objects.create(name="Society One")
    society_two = Society.objects.create(name="Society Two")
    client.force_login(user)

    society_one_account = Account.objects.filter(society=society_one).order_by("id").first()
    society_two_account = Account.objects.filter(society=society_two).order_by("id").first()

    response = client.get(
        reverse("accounting:voucher-entry"),
        data={"society": society_one.pk},
    )

    content = response.content.decode()
    assert response.status_code == HTTPStatus.OK
    assert f'value="{society_one_account.pk}"' in content
    assert f'value="{society_two_account.pk}"' not in content


def test_voucher_entry_shows_validation_warning_instead_of_server_error(client, user):
    society = Society.objects.create(name="Validation Society")
    client.force_login(user)

    response = client.post(
        reverse("accounting:voucher-entry"),
        data={
            "society": str(society.pk),
            "voucher_type": "GENERAL",
            "voucher_date": "2024-08-06",
            "narration": "Missing unit validation",
            "entries-TOTAL_FORMS": "2",
            "entries-INITIAL_FORMS": "0",
            "entries-MIN_NUM_FORMS": "0",
            "entries-MAX_NUM_FORMS": "1000",
            "entries-0-account": str(
                Account.objects.get(society=society, name="Maintenance Receivable").pk
            ),
            "entries-0-unit": "",
            "entries-0-debit": "1000.00",
            "entries-0-credit": "",
            "entries-1-account": str(
                Account.objects.get(society=society, name="Maintenance Charges").pk
            ),
            "entries-1-unit": "",
            "entries-1-debit": "",
            "entries-1-credit": "1000.00",
        },
    )

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "Unit is required for receivable/payable accounts." in content
    assert "Voucher draft not saved. Please fix highlighted ledger entry issues." in content
    assert Voucher.objects.filter(
        society=society,
        narration="Missing unit validation",
    ).exists() is False


def test_voucher_entry_blocks_single_line_draft_with_warning(client, user):
    society = Society.objects.create(name="Single Line Society")
    client.force_login(user)

    response = client.post(
        reverse("accounting:voucher-entry"),
        data={
            "society": str(society.pk),
            "voucher_type": "GENERAL",
            "voucher_date": "2024-08-06",
            "narration": "Single line draft",
            "entries-TOTAL_FORMS": "2",
            "entries-INITIAL_FORMS": "0",
            "entries-MIN_NUM_FORMS": "0",
            "entries-MAX_NUM_FORMS": "1000",
            "entries-0-account": str(
                Account.objects.get(society=society, name="Cash in Hand").pk
            ),
            "entries-0-unit": "",
            "entries-0-debit": "1000.00",
            "entries-0-credit": "",
            "entries-1-account": "",
            "entries-1-unit": "",
            "entries-1-debit": "",
            "entries-1-credit": "",
        },
    )

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "Voucher must contain at least two ledger entry rows." in content
    assert "Voucher draft not saved. Please fix highlighted issues." in content
    assert Voucher.objects.filter(
        society=society,
        narration="Single line draft",
    ).exists() is False


def test_voucher_entry_displays_error_summary_for_invalid_rows(client, user):
    society = Society.objects.create(name="Summary Error Society")
    asset_category, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Cash",
        account_type=AccountCategory.AccountType.ASSET,
    )
    income_category, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Income",
        account_type=AccountCategory.AccountType.INCOME,
    )
    cash = Account.objects.create(
        society=society,
        name="Test Cash",
        code="9.9.1",
        category=asset_category,
        account_type=Account.AccountType.ASSET,
    )
    income = Account.objects.create(
        society=society,
        name="Test Income",
        code="9.9.2",
        category=income_category,
        account_type=Account.AccountType.INCOME,
    )
    client.force_login(user)

    response = client.post(
        reverse("accounting:voucher-entry"),
        data={
            "society": str(society.pk),
            "voucher_type": "GENERAL",
            "voucher_date": "2024-08-06",
            "narration": "Summary validation",
            "entries-TOTAL_FORMS": "2",
            "entries-INITIAL_FORMS": "0",
            "entries-MIN_NUM_FORMS": "0",
            "entries-MAX_NUM_FORMS": "1000",
            "entries-0-account": str(cash.pk),
            "entries-0-unit": "",
            "entries-0-debit": "1000.00",
            "entries-0-credit": "",
            "entries-1-account": str(income.pk),
            "entries-1-unit": "",
            "entries-1-debit": "",
            "entries-1-credit": "900.00",
        },
    )

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "Voucher could not be saved" in content
    assert "Ledger entries: Total debit and credit must be equal." in content
    assert f'value="{cash.pk}"' in content
    assert f'value="{income.pk}"' in content


def test_voucher_entry_uses_selected_session_society_when_post_omits_disabled_field(client, user):
    society = Society.objects.create(name="Anandsagar")
    asset_category, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Cash",
        account_type=AccountCategory.AccountType.ASSET,
    )
    income_category, _ = AccountCategory.objects.get_or_create(
        society=society,
        name="Income",
        account_type=AccountCategory.AccountType.INCOME,
    )
    cash = Account.objects.create(
        society=society,
        name="Anandsagar Cash",
        code="8.8.1",
        category=asset_category,
        account_type=Account.AccountType.ASSET,
    )
    income = Account.objects.create(
        society=society,
        name="Anandsagar Income",
        code="8.8.2",
        category=income_category,
        account_type=Account.AccountType.INCOME,
    )
    Membership.objects.create(
        user=user,
        society=society,
        role=Membership.Role.ACCOUNTANT,
        is_active=True,
    )
    client.force_login(user)
    session = client.session
    session[SESSION_SELECTED_SOCIETY_ID] = society.id
    session.save()

    response = client.post(
        reverse("accounting:voucher-entry"),
        data={
            "voucher_type": "GENERAL",
            "voucher_date": "2024-08-06",
            "narration": "Session selected society draft",
            "entries-TOTAL_FORMS": "2",
            "entries-INITIAL_FORMS": "0",
            "entries-MIN_NUM_FORMS": "0",
            "entries-MAX_NUM_FORMS": "1000",
            "entries-0-account": str(cash.pk),
            "entries-0-unit": "",
            "entries-0-debit": "1000.00",
            "entries-0-credit": "",
            "entries-1-account": str(income.pk),
            "entries-1-unit": "",
            "entries-1-debit": "",
            "entries-1-credit": "1000.00",
        },
    )

    assert response.status_code == HTTPStatus.FOUND
    assert response.url == reverse("accounting:voucher-posting")
    voucher = Voucher.objects.get(narration="Session selected society draft")
    assert voucher.society == society
    assert LedgerEntry.objects.filter(voucher=voucher).count() == 2


def test_voucher_entry_blocks_unbalanced_draft_with_warning(client, user):
    society = Society.objects.create(name="Unbalanced Society")
    client.force_login(user)

    response = client.post(
        reverse("accounting:voucher-entry"),
        data={
            "society": str(society.pk),
            "voucher_type": "GENERAL",
            "voucher_date": "2024-08-06",
            "narration": "Unbalanced draft",
            "entries-TOTAL_FORMS": "2",
            "entries-INITIAL_FORMS": "0",
            "entries-MIN_NUM_FORMS": "0",
            "entries-MAX_NUM_FORMS": "1000",
            "entries-0-account": str(
                Account.objects.get(society=society, name="Cash in Hand").pk
            ),
            "entries-0-unit": "",
            "entries-0-debit": "1000.00",
            "entries-0-credit": "",
            "entries-1-account": str(
                Account.objects.get(society=society, name="Maintenance Charges").pk
            ),
            "entries-1-unit": "",
            "entries-1-debit": "",
            "entries-1-credit": "900.00",
        },
    )

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "Total debit and credit must be equal." in content
    assert "Voucher draft not saved. Please fix highlighted issues." in content
    assert Voucher.objects.filter(
        society=society,
        narration="Unbalanced draft",
    ).exists() is False


def test_voucher_entry_blocks_future_date_with_warning(client, user):
    society = Society.objects.create(name="Future Date Society")
    client.force_login(user)
    future_date = (timezone.localdate() + timedelta(days=1)).isoformat()

    response = client.post(
        reverse("accounting:voucher-entry"),
        data={
            "society": str(society.pk),
            "voucher_type": "GENERAL",
            "voucher_date": future_date,
            "narration": "Future date draft",
            "entries-TOTAL_FORMS": "2",
            "entries-INITIAL_FORMS": "0",
            "entries-MIN_NUM_FORMS": "0",
            "entries-MAX_NUM_FORMS": "1000",
            "entries-0-account": str(
                Account.objects.get(society=society, name="Cash in Hand").pk
            ),
            "entries-0-unit": "",
            "entries-0-debit": "1000.00",
            "entries-0-credit": "",
            "entries-1-account": str(
                Account.objects.get(society=society, name="Maintenance Charges").pk
            ),
            "entries-1-unit": "",
            "entries-1-debit": "",
            "entries-1-credit": "1000.00",
        },
    )

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "Voucher date cannot be in the future." in content
    assert "Voucher draft not saved. Please fix highlighted issues." in content
    assert Voucher.objects.filter(
        society=society,
        narration="Future date draft",
    ).exists() is False


def test_voucher_entry_blocks_inactive_account_with_warning(client, user):
    society = Society.objects.create(name="Inactive Account Society")
    client.force_login(user)
    cash = Account.objects.get(society=society, name="Cash in Hand")
    cash.is_active = False
    cash.save(update_fields=["is_active"])

    response = client.post(
        reverse("accounting:voucher-entry"),
        data={
            "society": str(society.pk),
            "voucher_type": "GENERAL",
            "voucher_date": "2024-08-06",
            "narration": "Inactive account draft",
            "entries-TOTAL_FORMS": "2",
            "entries-INITIAL_FORMS": "0",
            "entries-MIN_NUM_FORMS": "0",
            "entries-MAX_NUM_FORMS": "1000",
            "entries-0-account": str(cash.pk),
            "entries-0-unit": "",
            "entries-0-debit": "1000.00",
            "entries-0-credit": "",
            "entries-1-account": str(
                Account.objects.get(society=society, name="Maintenance Charges").pk
            ),
            "entries-1-unit": "",
            "entries-1-debit": "",
            "entries-1-credit": "1000.00",
        },
    )

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "Selected account is inactive." in content
    assert "Voucher draft not saved. Please fix highlighted issues." in content
    assert Voucher.objects.filter(
        society=society,
        narration="Inactive account draft",
    ).exists() is False


def test_voucher_posting_action_posts_voucher(client, user):
    society = Society.objects.create(name="Post Society")
    client.force_login(user)

    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    AccountingPeriod.objects.filter(
        society=society,
        financial_year=fy,
        start_date=date(2024, 4, 1),
        end_date=date(2024, 4, 30),
    ).update(is_open=True)

    voucher = Voucher.objects.create(
        society=society,
        voucher_type="GENERAL",
        voucher_date=date(2024, 4, 10),
        narration="Post me",
    )

    cash = Account.objects.get(society=society, name="Cash in Hand")
    income = Account.objects.get(society=society, name="Maintenance Charges")
    LedgerEntry.objects.create(voucher=voucher, account=cash, debit=Decimal("500.00"))
    LedgerEntry.objects.create(voucher=voucher, account=income, credit=Decimal("500.00"))

    response = client.post(reverse("accounting:voucher-post", kwargs={"pk": voucher.pk}))

    voucher.refresh_from_db()
    assert response.status_code == HTTPStatus.FOUND
    assert response.url == reverse("accounting:voucher-posting")
    assert voucher.posted_at is not None


def test_voucher_posting_action_deletes_draft_voucher(client, user):
    society = Society.objects.create(name="Delete Draft Society")
    client.force_login(user)

    voucher = Voucher.objects.create(
        society=society,
        voucher_type="GENERAL",
        voucher_date=date(2024, 4, 10),
        narration="Delete me",
    )

    response = client.post(
        reverse("accounting:voucher-delete-draft", kwargs={"pk": voucher.pk})
    )

    assert response.status_code == HTTPStatus.FOUND
    assert response.url == reverse("accounting:voucher-posting")
    assert Voucher.objects.filter(pk=voucher.pk).exists() is False


def test_voucher_posting_delete_draft_action_blocks_posted_voucher(client, user):
    society = Society.objects.create(name="Delete Draft Block Society")
    client.force_login(user)

    voucher = Voucher.objects.create(
        society=society,
        voucher_type="GENERAL",
        voucher_date=date(2024, 4, 10),
        narration="Already posted",
        posted_at=timezone.now(),
    )

    response = client.post(
        reverse("accounting:voucher-delete-draft", kwargs={"pk": voucher.pk})
    )

    assert response.status_code == HTTPStatus.FOUND
    assert response.url == reverse("accounting:voucher-posting")
    assert Voucher.objects.filter(pk=voucher.pk).exists() is True


def test_voucher_reverse_action_creates_posted_reversal(client, user):
    society = Society.objects.create(name="Reverse Society")
    client.force_login(user)

    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    AccountingPeriod.objects.filter(
        society=society,
        financial_year=fy,
        start_date=date(2024, 4, 1),
        end_date=date(2024, 4, 30),
    ).update(is_open=True)

    original = Voucher.objects.create(
        society=society,
        voucher_type="GENERAL",
        voucher_date=date(2024, 4, 10),
        narration="Original voucher",
    )

    cash = Account.objects.get(society=society, name="Cash in Hand")
    income = Account.objects.get(society=society, name="Maintenance Charges")
    LedgerEntry.objects.create(
        voucher=original,
        account=cash,
        debit=Decimal("300.00"),
    )
    LedgerEntry.objects.create(
        voucher=original,
        account=income,
        credit=Decimal("300.00"),
    )
    original.post()

    response = client.post(
        reverse("accounting:voucher-reverse", kwargs={"pk": original.pk})
    )

    assert response.status_code == HTTPStatus.FOUND
    assert response.url == reverse("accounting:voucher-list")

    reversals = Voucher.objects.filter(
        society=society,
        narration__startswith="Auto reversal of",
    ).exclude(pk=original.pk)
    assert reversals.count() == 1

    reversal = reversals.get()
    assert reversal.posted_at is not None
    assert reversal.voucher_date == original.voucher_date

    reversal_entries = list(reversal.entries.order_by("id"))
    assert len(reversal_entries) == 2
    assert reversal_entries[0].account_id == cash.id
    assert reversal_entries[0].debit == Decimal("0.00")
    assert reversal_entries[0].credit == Decimal("300.00")
    assert reversal_entries[1].account_id == income.id
    assert reversal_entries[1].debit == Decimal("300.00")
    assert reversal_entries[1].credit == Decimal("0.00")


def test_voucher_reverse_action_blocks_reverse_loops(client, user):
    society = Society.objects.create(name="Reverse Loop Society")
    client.force_login(user)

    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    AccountingPeriod.objects.filter(
        society=society,
        financial_year=fy,
        start_date=date(2024, 4, 1),
        end_date=date(2024, 4, 30),
    ).update(is_open=True)

    original = Voucher.objects.create(
        society=society,
        voucher_type="GENERAL",
        voucher_date=date(2024, 4, 10),
        narration="Loop original",
    )

    cash = Account.objects.get(society=society, name="Cash in Hand")
    income = Account.objects.get(society=society, name="Maintenance Charges")
    LedgerEntry.objects.create(voucher=original, account=cash, debit=Decimal("200.00"))
    LedgerEntry.objects.create(voucher=original, account=income, credit=Decimal("200.00"))
    original.post()

    first_response = client.post(
        reverse("accounting:voucher-reverse", kwargs={"pk": original.pk})
    )
    assert first_response.status_code == HTTPStatus.FOUND
    assert Voucher.objects.filter(reversal_of=original).count() == 1

    second_response = client.post(
        reverse("accounting:voucher-reverse", kwargs={"pk": original.pk})
    )
    assert second_response.status_code == HTTPStatus.FOUND
    assert Voucher.objects.filter(reversal_of=original).count() == 1

    reversal = Voucher.objects.get(reversal_of=original)
    reversal_response = client.post(
        reverse("accounting:voucher-reverse", kwargs={"pk": reversal.pk})
    )
    assert reversal_response.status_code == HTTPStatus.FOUND
    assert Voucher.objects.filter(reversal_of=reversal).count() == 0


def test_voucher_detail_renders_voucher_entries(client, user):
    society = Society.objects.create(name="Detail Society")
    client.force_login(user)

    voucher = Voucher.objects.create(
        society=society,
        voucher_type="GENERAL",
        voucher_date=date(2024, 8, 6),
        narration="Detail view voucher",
    )
    cash = Account.objects.get(society=society, name="Cash in Hand")
    income = Account.objects.get(society=society, name="Maintenance Charges")
    LedgerEntry.objects.create(voucher=voucher, account=cash, debit=Decimal("100.00"))
    LedgerEntry.objects.create(voucher=voucher, account=income, credit=Decimal("100.00"))

    response = client.get(reverse("accounting:voucher-detail", kwargs={"pk": voucher.pk}))

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "Detail view voucher" in content
    assert "Cash in Hand" in content
    assert "Maintenance Charges" in content


def test_voucher_list_has_modal_trigger_on_voucher_number(client, user):
    society = Society.objects.create(name="Modal Trigger Society")
    client.force_login(user)
    voucher = Voucher.objects.create(
        society=society,
        voucher_type="GENERAL",
        voucher_date=timezone.localdate(),
    )
    session = client.session
    session[SESSION_SELECTED_SOCIETY_ID] = society.id
    session.save()

    response = client.get(reverse("accounting:voucher-list"))

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert reverse("accounting:voucher-detail", kwargs={"pk": voucher.pk}) in content


def test_voucher_list_shows_reversed_status_with_posted(client, user):
    society = Society.objects.create(name="Reversed Status Society")
    client.force_login(user)

    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    AccountingPeriod.objects.filter(
        society=society,
        financial_year=fy,
        start_date=date(2024, 4, 1),
        end_date=date(2024, 4, 30),
    ).update(is_open=True)

    original = Voucher.objects.create(
        society=society,
        voucher_type="GENERAL",
        voucher_date=date(2024, 4, 10),
        narration="Needs reversal badge",
    )
    cash = Account.objects.get(society=society, name="Cash in Hand")
    income = Account.objects.get(society=society, name="Maintenance Charges")
    LedgerEntry.objects.create(voucher=original, account=cash, debit=Decimal("250.00"))
    LedgerEntry.objects.create(voucher=original, account=income, credit=Decimal("250.00"))
    original.post()

    reverse_response = client.post(
        reverse("accounting:voucher-reverse", kwargs={"pk": original.pk})
    )
    assert reverse_response.status_code == HTTPStatus.FOUND

    session = client.session
    session[SESSION_SELECTED_SOCIETY_ID] = society.id
    session[SESSION_SELECTED_FINANCIAL_YEAR_ID] = fy.id
    session.save()

    list_response = client.get(reverse("accounting:voucher-list"))
    assert list_response.status_code == HTTPStatus.OK

    content = list_response.content.decode()
    assert "Posted" in content
    assert "Reversed" in content


def test_accounting_dashboard_shows_reversed_status_with_posted(client, user):
    society = Society.objects.create(name="Dashboard Reversed Status Society")
    client.force_login(user)

    fy = FinancialYear.objects.create(
        society=society,
        name="FY 2024-25",
        start_date=date(2024, 4, 1),
        end_date=date(2025, 3, 31),
        is_open=True,
    )
    AccountingPeriod.objects.filter(
        society=society,
        financial_year=fy,
        start_date=date(2024, 4, 1),
        end_date=date(2024, 4, 30),
    ).update(is_open=True)

    original = Voucher.objects.create(
        society=society,
        voucher_type="GENERAL",
        voucher_date=date(2024, 4, 10),
        narration="Dashboard reversal badge",
    )
    cash = Account.objects.get(society=society, name="Cash in Hand")
    income = Account.objects.get(society=society, name="Maintenance Charges")
    LedgerEntry.objects.create(voucher=original, account=cash, debit=Decimal("300.00"))
    LedgerEntry.objects.create(voucher=original, account=income, credit=Decimal("300.00"))
    original.post()

    reverse_response = client.post(
        reverse("accounting:voucher-reverse", kwargs={"pk": original.pk})
    )
    assert reverse_response.status_code == HTTPStatus.FOUND

    session = client.session
    session[SESSION_SELECTED_SOCIETY_ID] = society.id
    session[SESSION_SELECTED_FINANCIAL_YEAR_ID] = fy.id
    session.save()

    response = client.get(reverse("accounting:dashboard"))
    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert 'text-bg-danger">Reversed<' in content
