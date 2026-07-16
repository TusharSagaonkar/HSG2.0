from http import HTTPStatus

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from accounting.models import Account
from accounting.models import AccountCategory
from housing.models import Society

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
        name="Assets",
        account_type=Account.AccountType.ASSET,
    )
    parent = Account.objects.create(
        society=society,
        name="Bank",
        code="1",
        category=category,
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


def test_account_add_modal_creates_child_account(client):
    user = _make_superuser()
    client.force_login(user)
    society = Society.objects.create(name="Create Society")
    category = AccountCategory.objects.create(
        society=society,
        name="Assets",
        account_type=Account.AccountType.ASSET,
    )
    parent = Account.objects.create(
        society=society,
        name="Assets",
        code="1",
        category=category,
        account_type=Account.AccountType.ASSET,
    )

    response = client.post(
        reverse("accounting:account-add") + f"?parent={parent.pk}",
        data={
            "name": "Cash",
            "code": "1.1",
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
    assert Account.objects.filter(society=society, parent=parent, name="Cash").exists()


def test_account_edit_modal_updates_account(client):
    user = _make_superuser()
    client.force_login(user)
    society = Society.objects.create(name="Edit Society")
    category = AccountCategory.objects.create(
        society=society,
        name="Assets",
        account_type=Account.AccountType.ASSET,
    )
    account = Account.objects.create(
        society=society,
        name="Old Name",
        code="2",
        category=category,
        account_type=Account.AccountType.ASSET,
    )

    response = client.post(
        reverse("accounting:account-edit", kwargs={"pk": account.pk}),
        data={
            "name": "New Name",
            "code": "2",
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
