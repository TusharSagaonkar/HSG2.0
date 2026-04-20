from http import HTTPStatus
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from accounting.models import Account
from accounting.models import VoucherTemplate
from accounting.models import VoucherTemplateRow
from housing.models import Society
from housing.models import Structure
from housing.models import Unit

pytestmark = pytest.mark.django_db


def _build_unit(society, identifier="101"):
    structure = Structure.objects.create(
        society=society,
        structure_type=Structure.StructureType.BUILDING,
        name=f"{society.name} Building",
    )
    return Unit.objects.create(
        structure=structure,
        unit_type=Unit.UnitType.FLAT,
        identifier=identifier,
    )


def test_voucher_template_allows_multiple_templates_per_type():
    society = Society.objects.create(name="Template Society")

    VoucherTemplate.objects.create(
        society=society,
        voucher_type="PAYMENT",
        name="Electricity",
        payment_mode="CASH",
    )
    VoucherTemplate.objects.create(
        society=society,
        voucher_type="PAYMENT",
        name="Vendor",
        payment_mode="CASH",
    )

    assert VoucherTemplate.objects.filter(society=society, voucher_type="PAYMENT").count() == 2


def test_voucher_template_ordering_is_deterministic():
    society = Society.objects.create(name="Ordering Society")

    lowest = VoucherTemplate.objects.create(
        society=society,
        voucher_type="RECEIPT",
        name="Lowest",
        payment_mode="CASH",
        usage_count=1,
        sort_order=10,
    )
    middle = VoucherTemplate.objects.create(
        society=society,
        voucher_type="PAYMENT",
        name="Middle",
        payment_mode="CASH",
        usage_count=4,
        sort_order=5,
    )
    highest = VoucherTemplate.objects.create(
        society=society,
        voucher_type="GENERAL",
        name="Pinned",
        is_pinned=True,
        usage_count=0,
        sort_order=99,
    )

    ordered_ids = list(
        VoucherTemplate.ordered_for_quick_actions(
            VoucherTemplate.objects.filter(society=society)
        ).values_list("id", flat=True)
    )

    assert ordered_ids == [highest.id, middle.id, lowest.id]


def test_voucher_template_row_rejects_cross_society_account_and_unit():
    society_one = Society.objects.create(name="Source Society")
    society_two = Society.objects.create(name="Other Society")
    template = VoucherTemplate.objects.create(
        society=society_one,
        voucher_type="GENERAL",
        name="Cross Society",
    )
    foreign_account = Account.objects.get(society=society_two, name="Cash in Hand")
    foreign_unit = _build_unit(society_two, identifier="202")

    row = VoucherTemplateRow(
        template=template,
        account=foreign_account,
        unit=foreign_unit,
        side=VoucherTemplateRow.Side.DEBIT,
    )

    with pytest.raises(ValidationError) as exc_info:
        row.full_clean()

    message = str(exc_info.value)
    assert "same society" in message


def test_voucher_entry_orders_templates_and_hides_inactive_ones(client, user):
    society = Society.objects.create(name="Quick Buttons Society")
    client.force_login(user)

    first = VoucherTemplate.objects.create(
        society=society,
        voucher_type="GENERAL",
        name="Pinned Transfer",
        is_pinned=True,
    )
    second = VoucherTemplate.objects.create(
        society=society,
        voucher_type="PAYMENT",
        name="Frequent Payment",
        payment_mode="CASH",
        usage_count=7,
        sort_order=50,
    )
    third = VoucherTemplate.objects.create(
        society=society,
        voucher_type="RECEIPT",
        name="Manual Order",
        payment_mode="CASH",
        usage_count=7,
        sort_order=60,
    )
    VoucherTemplate.objects.create(
        society=society,
        voucher_type="JOURNAL",
        name="Hidden Inactive",
        is_active=False,
    )

    response = client.get(reverse("accounting:voucher-entry"), {"society": society.pk})

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert content.index(f"template_id={first.id}") < content.index(f"template_id={second.id}")
    assert content.index(f"template_id={second.id}") < content.index(f"template_id={third.id}")
    assert "Hidden Inactive" not in content
    assert "Ctrl+1" in content
    assert 'data-template-shortcut-index="1"' in content


def test_voucher_entry_prefills_rows_and_tracks_usage(client, user):
    society = Society.objects.create(name="Prefill Society")
    client.force_login(user)
    cash = Account.objects.get(society=society, name="Cash in Hand")
    income = Account.objects.get(society=society, name="Maintenance Charges")
    unit = _build_unit(society)
    template = VoucherTemplate.objects.create(
        society=society,
        voucher_type="RECEIPT",
        name="Maintenance Receipt",
        payment_mode="CASH",
        narration="Collected maintenance",
    )
    VoucherTemplateRow.objects.create(
        template=template,
        account=cash,
        side=VoucherTemplateRow.Side.DEBIT,
        default_amount="500.00",
        order=1,
    )
    VoucherTemplateRow.objects.create(
        template=template,
        account=income,
        unit=unit,
        side=VoucherTemplateRow.Side.CREDIT,
        default_amount="500.00",
        order=2,
    )

    response = client.get(
        reverse("accounting:voucher-entry"),
        {"society": society.pk, "template_id": template.pk},
    )

    assert response.status_code == HTTPStatus.OK
    template.refresh_from_db()
    assert template.usage_count == 1
    assert template.last_used_at is not None
    assert response.context["voucher_form"].initial["voucher_type"] == "RECEIPT"
    assert response.context["voucher_form"].initial["narration"] == "Collected maintenance"
    form_rows = response.context["entry_formset"].forms
    assert form_rows[0].initial["account"] == cash.pk
    assert form_rows[0].initial["debit"] == Decimal("500.00")
    assert form_rows[1].initial["account"] == income.pk
    assert form_rows[1].initial["unit"] == unit.pk
    assert form_rows[1].initial["credit"] == Decimal("500.00")


def test_voucher_entry_skips_inactive_template_row_references(client, user):
    society = Society.objects.create(name="Inactive Row Society")
    client.force_login(user)
    inactive_account = Account.objects.get(society=society, name="Cash in Hand")
    inactive_account.is_active = False
    inactive_account.save(update_fields=["is_active"])
    template = VoucherTemplate.objects.create(
        society=society,
        voucher_type="GENERAL",
        name="Inactive Row Template",
    )
    VoucherTemplateRow.objects.create(
        template=template,
        account=inactive_account,
        side=VoucherTemplateRow.Side.DEBIT,
        default_amount="100.00",
        order=1,
    )

    response = client.get(
        reverse("accounting:voucher-entry"),
        {"society": society.pk, "template_id": template.pk},
    )

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "Some template rows were skipped because they reference inactive data" in content
    assert response.context["entry_formset"].forms[0].initial == {}
