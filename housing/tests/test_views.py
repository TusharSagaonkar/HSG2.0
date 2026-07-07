import json
from http import HTTPStatus

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from housing.models import Structure
from housing.models import Unit
from housing.models import Member
from housing.models import SocietyEmailSettings
from housing.models import UnitOccupancy
from housing.models import UnitOwnership
from accounting.models import Account
from accounting.models import AccountCategory

pytestmark = pytest.mark.django_db


class TestHousingDashboardView:
    def test_housing_dashboard_redirects_to_home(self, client, user):
        client.force_login(user)

        response = client.get(reverse("housing:dashboard"))

        assert response.status_code == HTTPStatus.FOUND
        assert response.url == reverse("home")

    def test_structure_unit_dashboard_renders(self, client, user):
        client.force_login(user)

        response = client.get(reverse("housing:structure-unit-dashboard"))

        assert response.status_code == HTTPStatus.OK
        assert "housing/structure_unit_dashboard.html" in [
            t.name for t in response.templates
        ]


class TestSocietyViews:
    def test_society_list_view(self, client, user, society):
        client.force_login(user)

        response = client.get(reverse("housing:society-list"))

        assert response.status_code == HTTPStatus.OK
        assert "housing/society_list.html" in [t.name for t in response.templates]
        assert response.context["societies"].count() == 1

    def test_society_detail_view(self, client, user, society):
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
        assert (
            f'href="{reverse("housing:structure-unit-dashboard")}#units-dashboard"'
            in response.content.decode()
        )

    def test_society_detail_shows_primary_owner(self, client, user, society):
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

    def test_society_email_settings_view(self, client, user, society):
        client.force_login(user)

        response = client.get(
            reverse("housing:society-email-settings", kwargs={"pk": society.pk})
        )

        assert response.status_code == HTTPStatus.OK
        assert "housing/society_email_settings.html" in [
            t.name for t in response.templates
        ]
        assert response.context["society"] == society

    def test_society_email_settings_post_creates_override(self, client, user, society):
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

    def test_bulk_unit_create_view_renders(self, client, user, society):
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

    def test_bulk_unit_create_view_saves_units(self, client, user, society):
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

    def test_member_add_view_accepts_minimal_payload(self, client, user, society):
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
        client.force_login(user)

        response = client.post(
            reverse("housing:member-add"),
            data={
                "society": society.pk,
                "unit": unit.pk,
                "full_name": "Asha Mehta",
                "role": Member.MemberRole.OWNER,
                "status": Member.MemberStatus.ACTIVE,
            },
        )

        assert response.status_code == HTTPStatus.FOUND
        member = Member.objects.get(full_name="Asha Mehta")
        assert member.society == society
        assert member.unit == unit
        assert member.status == Member.MemberStatus.ACTIVE
        assert member.start_date is not None

    def test_member_add_owner_creates_primary_ownership_and_occupancy(self, client, user, society):
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
        owner_user = get_user_model().objects.create_user(
            email="owner1@example.com",
            password="test-pass-123",
            name="Owner One",
        )
        client.force_login(user)

        response = client.post(
            reverse("housing:member-add"),
            data={
                "society": society.pk,
                "unit": unit.pk,
                "full_name": "Owner One",
                "email": "owner1@example.com",
                "role": Member.MemberRole.OWNER,
                "status": Member.MemberStatus.ACTIVE,
                "start_date": "2026-01-01",
            },
        )

        assert response.status_code == HTTPStatus.FOUND
        member = Member.objects.get(full_name="Owner One")
        ownership = UnitOwnership.objects.get(unit=unit)
        occupancy = UnitOccupancy.objects.get(unit=unit, end_date__isnull=True)
        assert ownership.role == UnitOwnership.OwnershipRole.PRIMARY
        assert ownership.owner == owner_user
        assert ownership.start_date.isoformat() == "2026-01-01"
        assert occupancy.occupancy_type == UnitOccupancy.OccupancyType.OWNER
        assert occupancy.occupant == owner_user
        assert member.unit == unit

    def test_owner_member_auto_creates_user_and_ownership(self, client, user, society):
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
        client.force_login(user)

        response = client.post(
            reverse("housing:member-add"),
            data={
                "society": society.pk,
                "unit": unit.pk,
                "full_name": "Auto Owner",
                "email": "auto.owner@example.com",
                "role": Member.MemberRole.OWNER,
                "status": Member.MemberStatus.ACTIVE,
                "start_date": "2026-01-01",
            },
        )

        assert response.status_code == HTTPStatus.FOUND
        member = Member.objects.get(full_name="Auto Owner")
        ownership = UnitOwnership.objects.get(unit=unit, role=UnitOwnership.OwnershipRole.PRIMARY)
        assert ownership.owner.email == "auto.owner@example.com"
        assert ownership.owner.name == "Auto Owner"
        assert ownership.owner.is_active is True

    def test_second_owner_becomes_secondary_without_replacing_owner_occupancy(self, client, user, society):
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
        owner_user = get_user_model().objects.create_user(
            email="owner1@example.com",
            password="test-pass-123",
            name="Owner One",
        )
        second_owner_user = get_user_model().objects.create_user(
            email="owner2@example.com",
            password="test-pass-123",
            name="Owner Two",
        )
        client.force_login(user)

        client.post(
            reverse("housing:member-add"),
            data={
                "society": society.pk,
                "unit": unit.pk,
                "full_name": "Owner One",
                "email": "owner1@example.com",
                "role": Member.MemberRole.OWNER,
                "status": Member.MemberStatus.ACTIVE,
                "start_date": "2026-01-01",
            },
        )
        response = client.post(
            reverse("housing:member-add"),
            data={
                "society": society.pk,
                "unit": unit.pk,
                "full_name": "Owner Two",
                "email": "owner2@example.com",
                "role": Member.MemberRole.OWNER,
                "status": Member.MemberStatus.ACTIVE,
                "start_date": "2026-02-01",
            },
        )

        assert response.status_code == HTTPStatus.FOUND
        ownerships = list(UnitOwnership.objects.filter(unit=unit).order_by("start_date", "id"))
        occupancy = UnitOccupancy.objects.get(unit=unit, end_date__isnull=True)
        assert ownerships[0].role == UnitOwnership.OwnershipRole.PRIMARY
        assert ownerships[0].owner == owner_user
        assert ownerships[1].role == UnitOwnership.OwnershipRole.SECONDARY
        assert ownerships[1].owner == second_owner_user
        assert occupancy.occupancy_type == UnitOccupancy.OccupancyType.OWNER
        assert occupancy.occupant == owner_user

    def test_tenant_add_replaces_current_occupancy(self, client, user, society):
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
        current_owner = get_user_model().objects.create_user(
            email="owner@example.com",
            password="test-pass-123",
            name="Current Owner",
        )
        tenant_user = get_user_model().objects.create_user(
            email="tenant@example.com",
            password="test-pass-123",
            name="Tenant User",
        )
        UnitOccupancy.objects.create(
            unit=unit,
            occupant=current_owner,
            occupancy_type=UnitOccupancy.OccupancyType.OWNER,
            start_date="2026-01-01",
        )
        client.force_login(user)

        response = client.post(
            reverse("housing:member-add"),
            data={
                "society": society.pk,
                "unit": unit.pk,
                "full_name": "Tenant User",
                "email": "tenant@example.com",
                "role": Member.MemberRole.TENANT,
                "status": Member.MemberStatus.ACTIVE,
                "start_date": "2026-02-01",
            },
        )

        assert response.status_code == HTTPStatus.FOUND
        previous_occupancy = UnitOccupancy.objects.get(
            unit=unit,
            occupancy_type=UnitOccupancy.OccupancyType.OWNER,
        )
        current_occupancy = UnitOccupancy.objects.get(
            unit=unit,
            end_date__isnull=True,
        )
        assert previous_occupancy.end_date.isoformat() == "2026-01-31"
        assert current_occupancy.occupancy_type == UnitOccupancy.OccupancyType.TENANT
        assert current_occupancy.occupant == tenant_user

    def test_non_owner_member_receivable_account_is_cleared(self, client, user, society):
        asset_cat = AccountCategory.objects.create(
            society=society,
            name="Assets Receivable Test",
            account_type=AccountCategory.AccountType.ASSET,
        )
        receivable = Account.objects.create(
            society=society,
            name="Temporary Receivable",
            code="9.9.9",
            category=asset_cat,
            account_type=Account.AccountType.ASSET,
        )
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
        client.force_login(user)

        response = client.post(
            reverse("housing:member-add"),
            data={
                "society": society.pk,
                "unit": unit.pk,
                "full_name": "Tenant User",
                "email": "tenant@example.com",
                "role": Member.MemberRole.TENANT,
                "status": Member.MemberStatus.ACTIVE,
                "receivable_account": receivable.pk,
                "start_date": "2026-02-01",
            },
        )

        assert response.status_code == HTTPStatus.FOUND
        member = Member.objects.get(full_name="Tenant User")
        assert member.receivable_account is None

    def test_member_add_modal_is_slimmed_down(self, client, user, society):
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
        client.force_login(user)

        response = client.get(reverse("housing:society-detail", kwargs={"pk": society.pk}))

        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert 'id="id_full_name"' in content
        assert 'id="id_role"' in content
        assert 'name="status"' in content
        assert 'name="start_date"' not in content
        assert 'name="end_date"' not in content

    def test_member_add_page_includes_unit_search_ui(self, client, user, society):
        structure = Structure.objects.create(
            society=society,
            structure_type=Structure.StructureType.BUILDING,
            name="Tower A",
        )
        Unit.objects.create(
            structure=structure,
            unit_type=Unit.UnitType.FLAT,
            identifier="101",
        )
        client.force_login(user)

        response = client.get(reverse("housing:member-add"), {"society": society.pk})

        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert 'data-unit-search-url="' in content
        assert 'data-unit-search-results' in content
        assert 'name="unit_search"' in content


class TestMemberListFilters:
    def test_structure_filter_on_member_list(self, client, user, society):
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

    def test_search_filter_on_member_list(self, client, user, society):
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
