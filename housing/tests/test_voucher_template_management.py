from http import HTTPStatus
from decimal import Decimal

import pytest
from django.urls import reverse

from accounting.models import Account
from accounting.models import VoucherTemplate
from accounting.models import VoucherTemplateRow
from housing.models import Society
from housing.models import Structure
from housing.models import Unit
from societies.models import Membership

pytestmark = pytest.mark.django_db


def test_society_voucher_templates_lists_active_and_inactive_templates(client, user):
    society = Society.objects.create(name="Management Society")
    Membership.objects.create(user=user, society=society, role=Membership.Role.ADMIN)
    VoucherTemplate.objects.create(
        society=society,
        voucher_type="PAYMENT",
        name="Active Template",
        payment_mode="CASH",
        is_active=True,
    )
    VoucherTemplate.objects.create(
        society=society,
        voucher_type="GENERAL",
        name="Inactive Template",
        is_active=False,
    )
    client.force_login(user)

    response = client.get(
        reverse("housing:society-voucher-templates", kwargs={"pk": society.pk})
    )

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "Active Template" in content
    assert "Inactive Template" in content
    assert "admin:accounting_vouchertemplate_add" not in content
    assert 'id="templateModal"' in content
    assert 'data-bs-target="#templateModal"' in content
    assert 'id="deleteTemplateModal' in content


def test_society_voucher_templates_create_template_in_page(client, user):
    society = Society.objects.create(name="Create Society")
    Membership.objects.create(user=user, society=society, role=Membership.Role.ADMIN)
    client.force_login(user)
    structure = Structure.objects.create(
        society=society,
        structure_type=Structure.StructureType.BUILDING,
        name="Tower A",
    )
    unit = Unit.objects.create(
        structure=structure,
        unit_type=Unit.UnitType.FLAT,
        identifier="101",
    )
    cash = Account.objects.get(society=society, name="Cash in Hand")
    income = Account.objects.get(society=society, name="Maintenance Charges")

    response = client.post(
        reverse("housing:society-voucher-templates", kwargs={"pk": society.pk}),
        data={
            "action": "save",
            "voucher_type": "RECEIPT",
            "name": "Maintenance Collection",
            "narration": "Monthly maintenance receipt",
            "payment_mode": "CASH",
            "reference_number_pattern": "",
            "is_active": "on",
            "is_pinned": "",
            "sort_order": "1",
            "rows-TOTAL_FORMS": "2",
            "rows-INITIAL_FORMS": "0",
            "rows-MIN_NUM_FORMS": "0",
            "rows-MAX_NUM_FORMS": "1000",
            "rows-0-account": str(cash.pk),
            "rows-0-unit": "",
            "rows-0-side": "DEBIT",
            "rows-0-default_amount": "500.00",
            "rows-0-order": "1",
            "rows-1-account": str(income.pk),
            "rows-1-unit": str(unit.pk),
            "rows-1-side": "CREDIT",
            "rows-1-default_amount": "500.00",
            "rows-1-order": "2",
        },
    )

    assert response.status_code == HTTPStatus.FOUND
    template = VoucherTemplate.objects.get(society=society, name="Maintenance Collection")
    assert template.payment_mode == "CASH"
    assert template.usage_count == 0
    assert VoucherTemplateRow.objects.filter(template=template).count() == 2
    assert template.rows.order_by("order").first().default_amount == Decimal("500.00")


def test_society_voucher_templates_requires_admin_or_above(client, user):
    society = Society.objects.create(name="Restricted Society")
    Membership.objects.create(user=user, society=society, role=Membership.Role.VIEWER)
    client.force_login(user)

    response = client.get(
        reverse("housing:society-voucher-templates", kwargs={"pk": society.pk})
    )

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_society_voucher_templates_toggle_active(client, user):
    society = Society.objects.create(name="Toggle Society")
    Membership.objects.create(user=user, society=society, role=Membership.Role.ADMIN)
    template = VoucherTemplate.objects.create(
        society=society,
        voucher_type="PAYMENT",
        name="Toggle Me",
        payment_mode="CASH",
        is_active=True,
    )
    client.force_login(user)

    response = client.post(
        reverse("housing:society-voucher-templates", kwargs={"pk": society.pk}),
        data={"template_id": template.pk, "action": "toggle_active"},
    )

    assert response.status_code == HTTPStatus.FOUND
    template.refresh_from_db()
    assert template.is_active is False


def test_society_voucher_templates_update_template_in_page(client, user):
    society = Society.objects.create(name="Update Society")
    Membership.objects.create(user=user, society=society, role=Membership.Role.ADMIN)
    client.force_login(user)
    template = VoucherTemplate.objects.create(
        society=society,
        voucher_type="GENERAL",
        name="Old Name",
        narration="Old narration",
    )

    response = client.post(
        reverse("housing:society-voucher-templates", kwargs={"pk": society.pk}),
        data={
            "action": "save",
            "template_id": template.pk,
            "voucher_type": "GENERAL",
            "name": "New Name",
            "narration": "New narration",
            "payment_mode": "",
            "reference_number_pattern": "",
            "is_active": "on",
            "is_pinned": "on",
            "sort_order": "7",
            "rows-TOTAL_FORMS": "2",
            "rows-INITIAL_FORMS": "0",
            "rows-MIN_NUM_FORMS": "0",
            "rows-MAX_NUM_FORMS": "1000",
            "rows-0-account": str(Account.objects.get(society=society, name="Cash in Hand").pk),
            "rows-0-unit": "",
            "rows-0-side": "DEBIT",
            "rows-0-default_amount": "",
            "rows-0-order": "1",
            "rows-1-account": str(Account.objects.get(society=society, name="Maintenance Charges").pk),
            "rows-1-unit": "",
            "rows-1-side": "CREDIT",
            "rows-1-default_amount": "",
            "rows-1-order": "2",
        },
    )

    assert response.status_code == HTTPStatus.FOUND
    template.refresh_from_db()
    assert template.name == "New Name"
    assert template.is_pinned is True


def test_society_voucher_templates_copy_template_in_page(client, user):
    society = Society.objects.create(name="Copy Society")
    Membership.objects.create(user=user, society=society, role=Membership.Role.ADMIN)
    client.force_login(user)
    source = VoucherTemplate.objects.create(
        society=society,
        voucher_type="PAYMENT",
        name="Electricity Payment",
        narration="Pay electricity bill",
        payment_mode="CASH",
        is_active=True,
        is_pinned=True,
        sort_order=2,
    )
    cash = Account.objects.get(society=society, name="Cash in Hand")
    expense = Account.objects.get(society=society, name="Maintenance Charges")
    VoucherTemplateRow.objects.create(
        template=source,
        account=expense,
        side=VoucherTemplateRow.Side.DEBIT,
        default_amount="100.00",
        order=1,
    )
    VoucherTemplateRow.objects.create(
        template=source,
        account=cash,
        side=VoucherTemplateRow.Side.CREDIT,
        default_amount="100.00",
        order=2,
    )

    response = client.get(
        reverse("housing:society-voucher-templates", kwargs={"pk": society.pk}),
        data={"copy": source.pk},
    )

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "Copy Template" in content
    assert "Copy of Electricity Payment" in content
    assert "Pay electricity bill" in content

    response = client.post(
        reverse("housing:society-voucher-templates", kwargs={"pk": society.pk}),
        data={
            "copy": source.pk,
            "action": "save",
            "voucher_type": "PAYMENT",
            "name": "Copy of Electricity Payment",
            "narration": "Pay electricity bill",
            "payment_mode": "CASH",
            "reference_number_pattern": "",
            "is_active": "on",
            "is_pinned": "",
            "sort_order": "2",
            "rows-TOTAL_FORMS": "2",
            "rows-INITIAL_FORMS": "0",
            "rows-MIN_NUM_FORMS": "0",
            "rows-MAX_NUM_FORMS": "1000",
            "rows-0-account": str(expense.pk),
            "rows-0-unit": "",
            "rows-0-side": "DEBIT",
            "rows-0-default_amount": "100.00",
            "rows-0-order": "1",
            "rows-1-account": str(cash.pk),
            "rows-1-unit": "",
            "rows-1-side": "CREDIT",
            "rows-1-default_amount": "100.00",
            "rows-1-order": "2",
        },
    )

    assert response.status_code == HTTPStatus.FOUND
    assert VoucherTemplate.objects.filter(society=society, name="Copy of Electricity Payment").count() == 1
    assert VoucherTemplate.objects.filter(society=society, name="Electricity Payment").count() == 1


def test_society_voucher_templates_delete_template(client, user):
    society = Society.objects.create(name="Delete Template Society")
    Membership.objects.create(user=user, society=society, role=Membership.Role.ADMIN)
    template = VoucherTemplate.objects.create(
        society=society,
        voucher_type="GENERAL",
        name="Delete Me",
    )
    client.force_login(user)

    response = client.post(
        reverse("housing:society-voucher-templates", kwargs={"pk": society.pk}),
        data={"template_id": template.pk, "action": "delete"},
    )

    assert response.status_code == HTTPStatus.FOUND
    assert VoucherTemplate.objects.filter(pk=template.pk).exists() is False
