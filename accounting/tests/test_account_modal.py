from http import HTTPStatus

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from accounting.models import Account
from accounting.models import AccountCategory
from housing.models import Society
from housing_accounting.selection import SESSION_SELECTED_SOCIETY_ID

pytestmark = pytest.mark.django_db


def _make_superuser():
    user_model = get_user_model()
    return user_model.objects.create_user(
        email="admin@example.com",
        password="test-pass-123",
        name="Admin User",
        is_superuser=True,
        is_staff=True,
    )


def test_account_add_modal_loads_for_child_account(client):
    user = _make_superuser()
    client.force_login(user)
    society = Society.objects.create(name="Modal Society")
    category = AccountCategory.objects.create(
        society=society,
        name="Modal Assets",
        account_type=Account.AccountType.ASSET,
    )
    parent = Account.objects.create(
        society=society,
        name="Bank",
        code="91",
        category=category,
        account_type=Account.AccountType.ASSET,
    )
    Account.objects.create(
        society=society,
        name="Existing child",
        code="91.1",
        category=category,
        parent=parent,
        account_type=Account.AccountType.ASSET,
    )

    response = client.get(
        reverse("accounting:account-add") + f"?parent={parent.pk}",
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == HTTPStatus.OK
    assert "account-form" in response.content.decode()
    assert "Add account" in response.content.decode()
    assert "Modal Society" in response.content.decode()
    assert "Bank" in response.content.decode()
    assert 'name="code"' not in response.content.decode()
    assert "91.2" in response.content.decode()


def test_account_add_modal_creates_child_account(client):
    user = _make_superuser()
    client.force_login(user)
    society = Society.objects.create(name="Create Society")
    category = AccountCategory.objects.create(
        society=society,
        name="Create Modal Assets",
        account_type=Account.AccountType.ASSET,
    )
    parent = Account.objects.create(
        society=society,
        name="Assets",
        code="92",
        category=category,
        account_type=Account.AccountType.ASSET,
    )

    response = client.post(
        reverse("accounting:account-add") + f"?parent={parent.pk}",
        data={
            "name": "Cash",
            "code": "92.1",
            "category": category.pk,
            "account_type": Account.AccountType.ASSET,
            "sub_type": Account.SubType.BANK,
            "is_active": "on",
            "is_gst": "",
            "gst_type": Account.GstType.NONE,
            "is_bank": "on",
            "is_member_related": "",
            "is_vendor_related": "",
            "is_contra": "",
            "is_clearing": "",
        },
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == HTTPStatus.OK
    payload = response.json()
    assert payload["success"] is True
    account = Account.objects.get(society=society, parent=parent, name="Cash")
    assert account.code == "92.1"


def test_account_edit_modal_updates_account(client):
    user = _make_superuser()
    client.force_login(user)
    society = Society.objects.create(name="Edit Society")
    category = AccountCategory.objects.create(
        society=society,
        name="Edit Modal Assets",
        account_type=Account.AccountType.ASSET,
    )
    account = Account.objects.create(
        society=society,
        name="Old Name",
        code="93",
        category=category,
        account_type=Account.AccountType.ASSET,
    )

    response = client.post(
        reverse("accounting:account-edit", kwargs={"pk": account.pk}),
        data={
            "name": "New Name",
            "code": "93",
            "category": category.pk,
            "account_type": Account.AccountType.ASSET,
            "sub_type": Account.SubType.GENERAL,
            "is_active": "on",
            "is_gst": "",
            "gst_type": Account.GstType.NONE,
            "is_bank": "",
            "is_member_related": "",
            "is_vendor_related": "",
            "is_contra": "",
            "is_clearing": "",
        },
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == HTTPStatus.OK
    payload = response.json()
    assert payload["success"] is True
    account.refresh_from_db()
    assert account.name == "New Name"


def test_account_tree_page_exposes_modal_triggers(client):
    user = _make_superuser()
    client.force_login(user)
    society = Society.objects.create(name="Tree Society")
    session = client.session
    session[SESSION_SELECTED_SOCIETY_ID] = society.pk
    session.save()

    category = AccountCategory.objects.create(
        society=society,
        name="Tree Modal Assets",
        account_type=Account.AccountType.ASSET,
    )
    account = Account.objects.create(
        society=society,
        name="Cash",
        code="94",
        category=category,
        account_type=Account.AccountType.ASSET,
    )

    response = client.get(reverse("accounting:account-tree"))

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "id=\"accountFormModal\"" in content
    assert "Add Root" in content
    assert f'data-account-form-url="{reverse("accounting:account-add")}?parent={account.pk}"' in content
    assert f'data-account-form-url="{reverse("accounting:account-edit", kwargs={"pk": account.pk})}"' in content
