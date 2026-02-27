from datetime import timedelta
from decimal import Decimal
from http import HTTPStatus

import pytest
from django.urls import reverse
from django.utils import timezone

from accounting.models import Account
from housing.models import Bill
from housing.models import BillLine
from housing.models import ChargeTemplate
from housing.models import Member
from housing.models import PaymentReceipt
from housing.models import ReceiptAllocation
from housing.models import ReminderLog
from housing.models import Society
from housing.models import Structure
from housing.models import Unit

pytestmark = pytest.mark.django_db


def _build_domain_data():
    today = timezone.localdate()
    society = Society.objects.create(name="Domain Pages Society")
    structure = Structure.objects.create(
        society=society,
        structure_type=Structure.StructureType.BUILDING,
        name="A",
    )
    unit = Unit.objects.create(
        structure=structure,
        unit_type=Unit.UnitType.FLAT,
        identifier="101",
    )
    receivable = Account.objects.get(society=society, name="Maintenance Receivable")
    income = Account.objects.get(society=society, name="Maintenance Charges")
    bank = (
        Account.objects.filter(society=society, name__icontains="bank account")
        .order_by("id")
        .first()
    )
    assert bank is not None

    member = Member.objects.create(
        society=society,
        unit=unit,
        full_name="Frontend Member",
        role=Member.MemberRole.OWNER,
        status=Member.MemberStatus.ACTIVE,
        receivable_account=receivable,
        start_date=today,
    )
    template = ChargeTemplate.objects.create(
        society=society,
        name="Maintenance",
        charge_type=ChargeTemplate.ChargeType.FIXED,
        rate=Decimal("1000.00"),
        frequency=ChargeTemplate.Frequency.MONTHLY,
        due_days=10,
        late_fee_percent=Decimal("0.00"),
        effective_from=today.replace(day=1),
        income_account=income,
        receivable_account=receivable,
    )
    bill = Bill.objects.create(
        society=society,
        member=member,
        unit=unit,
        receivable_account=receivable,
        bill_number=1,
        bill_period_start=today.replace(day=1),
        bill_period_end=today,
        bill_date=today,
        due_date=today + timedelta(days=10),
        total_amount=Decimal("1000.00"),
    )
    BillLine.objects.create(
        bill=bill,
        charge_template=template,
        description="Maintenance",
        amount=Decimal("1000.00"),
        income_account=income,
    )
    receipt = PaymentReceipt.objects.create(
        society=society,
        member=member,
        unit=unit,
        receipt_date=today,
        amount=Decimal("400.00"),
        payment_mode="CASH",
        reference_number="",
        deposited_account=bank,
    )
    ReceiptAllocation.objects.create(
        receipt=receipt,
        bill=bill,
        amount=Decimal("400.00"),
    )
    ReminderLog.objects.create(
        society=society,
        member=member,
        bill=bill,
        channel=ReminderLog.Channel.EMAIL,
        message="Payment reminder",
        scheduled_for=timezone.now(),
    )
    return {
        "member": member,
        "bill": bill,
        "receipt": receipt,
        "template": template,
    }


def test_billing_pages_render(client, user):
    data = _build_domain_data()
    client.force_login(user)

    response = client.get(reverse("billing:bill-list"))
    assert response.status_code == HTTPStatus.OK
    assert "billing/bill_list.html" in [t.name for t in response.templates]

    response = client.get(
        reverse("billing:bill-detail", kwargs={"pk": data["bill"].pk}),
    )
    assert response.status_code == HTTPStatus.OK
    assert "billing/bill_detail.html" in [t.name for t in response.templates]


def test_receipt_pages_render(client, user):
    data = _build_domain_data()
    client.force_login(user)

    response = client.get(reverse("receipts:receipt-list"))
    assert response.status_code == HTTPStatus.OK
    assert "receipts/receipt_list.html" in [t.name for t in response.templates]

    response = client.get(
        reverse("receipts:receipt-detail", kwargs={"pk": data["receipt"].pk}),
    )
    assert response.status_code == HTTPStatus.OK
    assert "receipts/receipt_detail.html" in [t.name for t in response.templates]


def test_notification_and_member_pages_render(client, user):
    data = _build_domain_data()
    client.force_login(user)

    response = client.get(reverse("notifications:reminder-list"))
    assert response.status_code == HTTPStatus.OK
    assert "notifications/reminder_list.html" in [t.name for t in response.templates]

    response = client.get(
        reverse("members:member-detail", kwargs={"pk": data["member"].pk}),
    )
    assert response.status_code == HTTPStatus.OK
    assert "members/member_detail.html" in [t.name for t in response.templates]


def test_structure_unit_dashboard_page_renders(client, user):
    _build_domain_data()
    client.force_login(user)

    response = client.get(reverse("housing:structure-unit-dashboard"))
    assert response.status_code == HTTPStatus.OK
    assert "housing/structure_unit_dashboard.html" in [t.name for t in response.templates]


def test_charge_template_status_change_sets_effective_to_date(client, user):
    data = _build_domain_data()
    client.force_login(user)
    template = data["template"]

    response = client.post(
        reverse("billing:charge-template-status", kwargs={"pk": template.pk}),
        data={"status": "INACTIVE"},
        follow=True,
    )

    assert response.status_code == HTTPStatus.OK
    template.refresh_from_db()
    assert template.is_active is False
    assert template.effective_to == timezone.localdate()


def test_charge_template_status_same_value_keeps_existing_effective_to(client, user):
    data = _build_domain_data()
    client.force_login(user)
    template = data["template"]
    old_date = timezone.localdate() - timedelta(days=5)
    template.is_active = False
    template.effective_to = old_date
    template.save(update_fields=["is_active", "effective_to"])

    response = client.post(
        reverse("billing:charge-template-status", kwargs={"pk": template.pk}),
        data={"status": "INACTIVE"},
        follow=True,
    )

    assert response.status_code == HTTPStatus.OK
    template.refresh_from_db()
    assert template.is_active is False
    assert template.effective_to == old_date


def test_charge_template_inactive_and_new_redirects_to_prefilled_create(client, user):
    data = _build_domain_data()
    client.force_login(user)
    template = data["template"]

    response = client.post(
        reverse("billing:charge-template-inactive-and-new", kwargs={"pk": template.pk}),
    )

    assert response.status_code == HTTPStatus.FOUND
    assert reverse("housing:charge-template-add") in response.url
    assert f"clone_from={template.pk}" in response.url
    template.refresh_from_db()
    assert template.is_active is False
    assert template.effective_to == timezone.localdate()


def test_charge_template_create_from_clone_sets_previous_version(client, user):
    data = _build_domain_data()
    client.force_login(user)
    template = data["template"]

    old_date = timezone.localdate()
    template.is_active = False
    template.effective_to = old_date
    template.save(update_fields=["is_active", "effective_to"])

    create_url = (
        f"{reverse('housing:charge-template-add')}"
        f"?clone_from={template.pk}&society={template.society_id}"
    )
    response = client.post(
        create_url,
        data={
            "society": template.society_id,
            "name": template.name,
            "description": template.description,
            "charge_type": ChargeTemplate.ChargeType.FIXED,
            "rate": Decimal("1200.00"),
            "frequency": template.frequency,
            "due_days": template.due_days,
            "late_fee_percent": template.late_fee_percent,
            "effective_from": timezone.localdate() + timedelta(days=1),
            "effective_to": "",
            "income_account": template.income_account_id,
            "receivable_account": template.receivable_account_id,
            "is_active": "on",
        },
    )

    assert response.status_code == HTTPStatus.FOUND
    new_template = (
        ChargeTemplate.objects.filter(society=template.society, name=template.name)
        .exclude(pk=template.pk)
        .latest("id")
    )
    assert new_template.previous_version_id == template.id
