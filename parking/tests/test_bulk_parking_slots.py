from http import HTTPStatus

import pytest
from django.urls import reverse

from housing.models import Society
from parking.models import ParkingSlot

pytestmark = pytest.mark.django_db


def test_bulk_parking_slot_create_view_renders(client, user):
    Society.objects.create(name="Bulk Parking Society")
    client.force_login(user)

    response = client.get(reverse("parking:slot-bulk-add"))

    assert response.status_code == HTTPStatus.OK
    assert "parking/slot_bulk_form.html" in [template.name for template in response.templates]


def test_bulk_parking_slot_create_view_saves_generated_slots(client, user):
    society = Society.objects.create(name="Bulk Parking Society")
    client.force_login(user)

    response = client.post(
        reverse("parking:slot-bulk-add"),
        data={
            "society": society.pk,
            "count": "3",
            "prefix": "B1-",
            "starting_number": "1",
            "padding": "2",
            "custom_slot_names": "",
            "parking_model": ParkingSlot.ParkingModel.COMMON,
            "slot_type": ParkingSlot.SlotType.BASEMENT,
            "is_active": "on",
            "is_transferable": "on",
        },
    )

    assert response.status_code == HTTPStatus.FOUND
    slots = list(ParkingSlot.objects.filter(society=society).order_by("slot_number"))
    assert [slot.slot_number for slot in slots] == ["B1-01", "B1-02", "B1-03"]
    assert all(slot.slot_type == ParkingSlot.SlotType.BASEMENT for slot in slots)


def test_bulk_parking_slot_create_view_saves_custom_slot_names(client, user):
    society = Society.objects.create(name="Custom Parking Society")
    client.force_login(user)

    response = client.post(
        reverse("parking:slot-bulk-add"),
        data={
            "society": society.pk,
            "count": "100",
            "prefix": "P-",
            "starting_number": "1",
            "padding": "3",
            "custom_slot_names": "EV-01\nEV-02, VIS-01",
            "parking_model": ParkingSlot.ParkingModel.COMMON,
            "slot_type": ParkingSlot.SlotType.VISITOR,
            "is_active": "on",
            "is_rotational": "on",
            "is_transferable": "on",
        },
    )

    assert response.status_code == HTTPStatus.FOUND
    slots = list(ParkingSlot.objects.filter(society=society).order_by("slot_number"))
    assert [slot.slot_number for slot in slots] == ["EV-01", "EV-02", "VIS-01"]
    assert all(slot.is_rotational for slot in slots)


def test_bulk_parking_slot_create_view_rejects_existing_slots(client, user):
    society = Society.objects.create(name="Duplicate Parking Society")
    ParkingSlot.objects.create(society=society, slot_number="P-001")
    client.force_login(user)

    response = client.post(
        reverse("parking:slot-bulk-add"),
        data={
            "society": society.pk,
            "count": "2",
            "prefix": "P-",
            "starting_number": "1",
            "padding": "3",
            "custom_slot_names": "",
            "parking_model": ParkingSlot.ParkingModel.COMMON,
            "slot_type": ParkingSlot.SlotType.OPEN,
            "is_active": "on",
            "is_transferable": "on",
        },
    )

    assert response.status_code == HTTPStatus.OK
    assert ParkingSlot.objects.filter(society=society).count() == 1
    assert "P-001" in response.content.decode()
