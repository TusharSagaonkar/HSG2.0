from http import HTTPStatus

import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponseRedirect
from django.urls import reverse

from housing.models import Society
from housing.models import Structure
from housing.models import Unit

pytestmark = pytest.mark.django_db


class TestHousingDashboardView:
    def test_requires_authentication(self, rf, user):
        request = rf.get("/housing/")
        request.user = AnonymousUser()
        from housing.views import housing_dashboard_view

        response = housing_dashboard_view(request)
        login_url = reverse(settings.LOGIN_URL)

        assert isinstance(response, HttpResponseRedirect)
        assert response.status_code == HTTPStatus.FOUND
        assert response.url == f"{login_url}?next=/housing/"

    def test_authenticated_user_can_open(self, client, user):
        client.force_login(user)

        response = client.get(reverse("housing:dashboard"))

        assert response.status_code == HTTPStatus.OK
        assert "housing/dashboard.html" in [t.name for t in response.templates]


class TestSocietyViews:
    def test_society_list_view(self, client, user):
        Society.objects.create(name="Green Heights")
        client.force_login(user)

        response = client.get(reverse("housing:society-list"))

        assert response.status_code == HTTPStatus.OK
        assert "housing/society_list.html" in [t.name for t in response.templates]
        assert response.context["societies"].count() == 1

    def test_society_detail_view(self, client, user):
        society = Society.objects.create(name="Green Heights")
        structure = Structure.objects.create(
            society=society,
            structure_type=Structure.StructureType.BUILDING,
            name="Building A",
        )
        Unit.objects.create(
            structure=structure,
            unit_type=Unit.UnitType.FLAT,
            identifier="101",
        )
        client.force_login(user)

        response = client.get(
            reverse("housing:society-detail", kwargs={"pk": society.pk})
        )

        assert response.status_code == HTTPStatus.OK
        assert "housing/society_detail.html" in [t.name for t in response.templates]
        assert response.context["society"] == society
