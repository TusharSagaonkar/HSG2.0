import json
from http import HTTPStatus

import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth import get_user_model
from django.http import HttpResponseRedirect
from django.urls import reverse

from housing.models import Society
from housing.models import Structure
from housing.models import Unit
from housing.models import Member
from housing.models import SocietyEmailSettings
from housing.models import UnitOwnership

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

    def test_structure_unit_dashboard_renders(self, client, user):
        client.force_login(user)

        response = client.get(reverse("housing:structure-unit-dashboard"))

        assert response.status_code == HTTPStatus.OK
        assert "housing/structure_unit_dashboard.html" in [
            t.name for t in response.templates
        ]


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

    def test_society_detail_shows_primary_owner(self, client, user):
        society = Society.objects.create(name="Green Heights")
        structure = Structure.objects.create(
            society=society,
            structure_type=Structure.StructureType.BUILDING,
            name="Building A",
        )
        unit = Unit.objects.create(
            structure=structure,
            unit_type=Unit.UnitType.FLAT,
            identifier="101",
        )
        user_model = get_user_model()
        primary_owner = user_model.objects.create_user(
            email="primary@example.com",
            password="test-pass-123",
            name="Primary Owner",
        )
        secondary_owner = user_model.objects.create_user(
            email="secondary@example.com",
            password="test-pass-123",
            name="Secondary Owner",
        )
        UnitOwnership.objects.create(
            unit=unit,
            owner=secondary_owner,
            role=UnitOwnership.OwnershipRole.SECONDARY,
            start_date="2026-01-01",
        )
        UnitOwnership.objects.create(
            unit=unit,
            owner=primary_owner,
            role=UnitOwnership.OwnershipRole.PRIMARY,
            start_date="2026-01-01",
        )
        client.force_login(user)

        response = client.get(
            reverse("housing:society-detail", kwargs={"pk": society.pk})
        )

        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert "Primary owner:" in content
        assert "Primary Owner" in content
        assert "Secondary Owner" not in content

    def test_society_email_settings_view(self, client, user):
        society = Society.objects.create(name="Green Heights")
        client.force_login(user)

        response = client.get(
            reverse("housing:society-email-settings", kwargs={"pk": society.pk})
        )

        assert response.status_code == HTTPStatus.OK
        assert "housing/society_email_settings.html" in [
            t.name for t in response.templates
        ]
        assert response.context["society"] == society

    def test_society_email_settings_post_creates_override(self, client, user):
        society = Society.objects.create(name="Green Heights")
        client.force_login(user)

        response = client.post(
            reverse("housing:society-email-settings", kwargs={"pk": society.pk}),
            data={
                "is_active": "on",
                "provider_type": "SMTP",
                "smtp_host": "smtp.society.test",
                "smtp_port": "587",
                "smtp_username": "accounts@society.test",
                "smtp_password": "override-secret",
                "use_tls": "on",
                "default_from_email": "Society <accounts@society.test>",
                "default_reply_to": "accounts@society.test",
                "daily_limit": "100",
            },
        )

        assert response.status_code == HTTPStatus.FOUND
        settings_record = SocietyEmailSettings.objects.get(society=society)
        assert settings_record.is_active is True
        assert settings_record.smtp_host == "smtp.society.test"
        assert settings_record.smtp_password == "override-secret"  # noqa: S105

    def test_bulk_unit_create_view_renders(self, client, user):
        society = Society.objects.create(name="Bulk Heights")
        structure = Structure.objects.create(
            society=society,
            structure_type=Structure.StructureType.BUILDING,
            name="Tower A",
        )
        client.force_login(user)

        response = client.get(
            reverse("housing:unit-bulk-add"),
            {"structure": structure.pk, "society": society.pk},
        )

        assert response.status_code == HTTPStatus.OK
        assert "housing/unit_bulk_form.html" in [t.name for t in response.templates]

    def test_bulk_unit_create_view_saves_units(self, client, user):
        society = Society.objects.create(name="Bulk Heights")
        structure = Structure.objects.create(
            society=society,
            structure_type=Structure.StructureType.BUILDING,
            name="Tower A",
        )
        client.force_login(user)

        payload = [
            {
                "floor": 2,
                "column": 1,
                "identifier": "201",
                "unit_type": Unit.UnitType.FLAT,
                "area_sqft": "500",
                "chargeable_area_sqft": "525",
                "is_active": True,
            },
            {
                "floor": 2,
                "column": 2,
                "identifier": "202",
                "unit_type": Unit.UnitType.SHOP,
                "area_sqft": "350",
                "chargeable_area_sqft": "",
                "is_active": False,
            },
        ]

        response = client.post(
            reverse("housing:unit-bulk-add"),
            data={
                "structure": structure.pk,
                "floors": "2",
                "units_per_floor": "2",
                "starting_floor": "1",
                "starting_number": "1",
                "numbering_style": "continuous",
                "default_unit_type": Unit.UnitType.FLAT,
                "default_area_sqft": "",
                "default_chargeable_area_sqft": "",
                "units_json": json.dumps(payload),
            },
        )

        assert response.status_code == HTTPStatus.FOUND
        units = list(Unit.objects.filter(structure=structure).order_by("identifier"))
        assert [unit.identifier for unit in units] == ["201", "202"]
        assert units[0].is_active is True
        assert units[1].unit_type == Unit.UnitType.SHOP
        assert units[1].is_active is False


class TestMemberListFilters:
    def test_structure_filter_on_member_list(self, client, user):
        society = Society.objects.create(name="Green Heights")
        alpha = Structure.objects.create(
            society=society,
            structure_type=Structure.StructureType.BUILDING,
            name="Alpha",
        )
        beta = Structure.objects.create(
            society=society,
            structure_type=Structure.StructureType.BUILDING,
            name="Beta",
        )
        alpha_unit = Unit.objects.create(
            structure=alpha,
            unit_type=Unit.UnitType.FLAT,
            identifier="A-101",
        )
        beta_unit = Unit.objects.create(
            structure=beta,
            unit_type=Unit.UnitType.FLAT,
            identifier="B-101",
        )
        Member.objects.create(
            society=society,
            unit=alpha_unit,
            full_name="Alpha Member",
            role=Member.MemberRole.OWNER,
        )
        Member.objects.create(
            society=society,
            unit=beta_unit,
            full_name="Beta Member",
            role=Member.MemberRole.TENANT,
        )
        client.force_login(user)

        response = client.get(
            reverse("housing:member-list"),
            {"structure": str(alpha.id)},
        )

        assert response.status_code == HTTPStatus.OK
        members = list(response.context["members"])
        assert len(members) == 1
        assert members[0].full_name == "Alpha Member"

    def test_search_filter_on_member_list(self, client, user):
        society = Society.objects.create(name="Green Heights")
        structure = Structure.objects.create(
            society=society,
            structure_type=Structure.StructureType.BUILDING,
            name="Gamma",
        )
        gamma_unit = Unit.objects.create(
            structure=structure,
            unit_type=Unit.UnitType.FLAT,
            identifier="G-101",
        )
        Member.objects.create(
            society=society,
            unit=gamma_unit,
            full_name="Searchable Member",
            role=Member.MemberRole.OWNER,
        )
        Member.objects.create(
            society=society,
            unit=gamma_unit,
            full_name="Other Person",
            role=Member.MemberRole.OWNER,
        )
        client.force_login(user)

        response = client.get(reverse("housing:member-list"), {"q": "searchable"})

        assert response.status_code == HTTPStatus.OK
        members = list(response.context["members"])
        assert len(members) == 1
        assert members[0].full_name == "Searchable Member"
