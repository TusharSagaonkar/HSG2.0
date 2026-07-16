"""Tests for field-level security utilities.

These tests validate:
- ``visible_fields()`` returns all fields when no FieldVisibility rules exist (default-allow)
- ``visible_fields()`` hides fields when a global rule has visible=False for the user's role
- ``visible_fields()`` shows fields when a global rule has visible=True for the user's role
- Society-specific rules override global rules
- ``hidden_fields()`` returns the complement of visible_fields()
- ``filter_dict_by_visibility()`` removes hidden fields from a dict
- Role hierarchy: owner sees fields hidden from viewer
- Wildcard role "*" applies to all roles
"""

import pytest

from core.test_factories import SocietyFactory
from core.test_factories import UserFactory
from societies.field_security import filter_dict_by_visibility
from societies.field_security import hidden_fields
from societies.field_security import visible_fields
from societies.models import FieldVisibility
from societies.models import Membership
from societies.services import create_society


pytestmark = pytest.mark.django_db


def _make_member(society, role, invited_by=None):
    """Create a user with a membership in the given society with the given role."""
    user = UserFactory()
    Membership.objects.create(
        user=user,
        society=society,
        role=role,
        invited_by=invited_by or user,
    )
    return user


class TestVisibleFieldsDefaultAllow:
    """Tests for default-allow behavior when no rules exist."""

    def test_all_fields_visible_without_rules(self):
        society = SocietyFactory()
        owner = create_society(user=UserFactory(), name="Field Sec Owner Society").created_by
        # Use the society created by create_society so owner has a membership
        instance = society
        owner_user = UserFactory()
        Membership.objects.create(
            user=owner_user,
            society=society,
            role=Membership.Role.OWNER,
            invited_by=owner_user,
        )
        visible = visible_fields(instance, owner_user, society)
        all_fields = {f.name for f in instance._meta.get_fields() if hasattr(f, "name")}
        assert visible == all_fields

    def test_all_fields_visible_for_viewer_without_rules(self):
        society = SocietyFactory()
        viewer = _make_member(society, Membership.Role.VIEWER)
        instance = society
        visible = visible_fields(instance, viewer, society)
        all_fields = {f.name for f in instance._meta.get_fields() if hasattr(f, "name")}
        assert visible == all_fields


class TestVisibleFieldsGlobalRules:
    """Tests for global FieldVisibility rules (society=None)."""

    def test_global_rule_hides_field_for_role(self):
        society = SocietyFactory()
        viewer = _make_member(society, Membership.Role.VIEWER)
        instance = society
        model_label = instance._meta.label

        # Create a global rule hiding 'address' from viewer
        FieldVisibility.objects.create(
            society=None,
            model_name=model_label,
            field_name="address",
            role="viewer",
            visible=False,
        )

        visible = visible_fields(instance, viewer, society)
        assert "address" not in visible

    def test_global_rule_shows_field_for_role(self):
        society = SocietyFactory()
        viewer = _make_member(society, Membership.Role.VIEWER)
        instance = society
        model_label = instance._meta.label

        # Create a global rule showing 'address' for viewer (explicit visible=True)
        FieldVisibility.objects.create(
            society=None,
            model_name=model_label,
            field_name="address",
            role="viewer",
            visible=True,
        )

        visible = visible_fields(instance, viewer, society)
        assert "address" in visible

    def test_global_rule_hides_field_for_wildcard_role(self):
        society = SocietyFactory()
        viewer = _make_member(society, Membership.Role.VIEWER)
        instance = society
        model_label = instance._meta.label

        # Wildcard rule hides 'registration_number' from all roles
        FieldVisibility.objects.create(
            society=None,
            model_name=model_label,
            field_name="registration_number",
            role="*",
            visible=False,
        )

        visible = visible_fields(instance, viewer, society)
        assert "registration_number" not in visible

    def test_wildcard_rule_applies_to_all_roles(self):
        society = SocietyFactory()
        owner = _make_member(society, Membership.Role.OWNER)
        accountant = _make_member(society, Membership.Role.ACCOUNTANT)
        member = _make_member(society, Membership.Role.MEMBER)
        viewer = _make_member(society, Membership.Role.VIEWER)
        instance = society
        model_label = instance._meta.label

        FieldVisibility.objects.create(
            society=None,
            model_name=model_label,
            field_name="address",
            role="*",
            visible=False,
        )

        for user in [owner, accountant, member, viewer]:
            visible = visible_fields(instance, user, society)
            assert "address" not in visible, f"address should be hidden for {user}"


class TestSocietySpecificRulesOverride:
    """Tests that society-specific rules override global rules."""

    def test_society_rule_overrides_global_hide_with_show(self):
        society = SocietyFactory()
        viewer = _make_member(society, Membership.Role.VIEWER)
        instance = society
        model_label = instance._meta.label

        # Global rule: hide address from viewer
        FieldVisibility.objects.create(
            society=None,
            model_name=model_label,
            field_name="address",
            role="viewer",
            visible=False,
        )
        # Society-specific rule: show address to viewer
        FieldVisibility.objects.create(
            society=society,
            model_name=model_label,
            field_name="address",
            role="viewer",
            visible=True,
        )

        visible = visible_fields(instance, viewer, society)
        assert "address" in visible

    def test_society_rule_overrides_global_show_with_hide(self):
        society = SocietyFactory()
        viewer = _make_member(society, Membership.Role.VIEWER)
        instance = society
        model_label = instance._meta.label

        # Global rule: show address to viewer
        FieldVisibility.objects.create(
            society=None,
            model_name=model_label,
            field_name="address",
            role="viewer",
            visible=True,
        )
        # Society-specific rule: hide address from viewer
        FieldVisibility.objects.create(
            society=society,
            model_name=model_label,
            field_name="address",
            role="viewer",
            visible=False,
        )

        visible = visible_fields(instance, viewer, society)
        assert "address" not in visible

    def test_society_rule_does_not_affect_other_society(self):
        society_a = SocietyFactory()
        society_b = SocietyFactory(name="Society B Field Sec")
        viewer_a = _make_member(society_a, Membership.Role.VIEWER)
        instance_a = society_a
        model_label = instance_a._meta.label

        # Society A rule: hide address from viewer
        FieldVisibility.objects.create(
            society=society_a,
            model_name=model_label,
            field_name="address",
            role="viewer",
            visible=False,
        )

        # For society_a, address should be hidden
        visible_a = visible_fields(instance_a, viewer_a, society_a)
        assert "address" not in visible_a

        # For society_b (no rule), address should be visible (default-allow)
        viewer_b = _make_member(society_b, Membership.Role.VIEWER)
        visible_b = visible_fields(society_b, viewer_b, society_b)
        assert "address" in visible_b


class TestRoleHierarchy:
    """Tests for role hierarchy in field visibility."""

    def test_owner_sees_fields_hidden_from_viewer(self):
        society = SocietyFactory()
        owner = _make_member(society, Membership.Role.OWNER)
        viewer = _make_member(society, Membership.Role.VIEWER)
        instance = society
        model_label = instance._meta.label

        # Rule: hide 'address' from viewer
        FieldVisibility.objects.create(
            society=None,
            model_name=model_label,
            field_name="address",
            role="viewer",
            visible=False,
        )

        # Viewer cannot see address
        visible_viewer = visible_fields(instance, viewer, society)
        assert "address" not in visible_viewer

        # Owner CAN see address (owner is above viewer in hierarchy)
        visible_owner = visible_fields(instance, owner, society)
        assert "address" in visible_owner

    def test_admin_sees_fields_hidden_from_member(self):
        society = SocietyFactory()
        admin = _make_member(society, Membership.Role.ADMIN)
        member = _make_member(society, Membership.Role.MEMBER)
        instance = society
        model_label = instance._meta.label

        # Rule: hide 'registration_number' from member
        FieldVisibility.objects.create(
            society=None,
            model_name=model_label,
            field_name="registration_number",
            role="member",
            visible=False,
        )

        # Member cannot see registration_number
        visible_member = visible_fields(instance, member, society)
        assert "registration_number" not in visible_member

        # Admin CAN see registration_number
        visible_admin = visible_fields(instance, admin, society)
        assert "registration_number" in visible_admin

    def test_accountant_sees_fields_hidden_from_viewer(self):
        society = SocietyFactory()
        accountant = _make_member(society, Membership.Role.ACCOUNTANT)
        viewer = _make_member(society, Membership.Role.VIEWER)
        instance = society
        model_label = instance._meta.label

        # Rule: hide 'address' from viewer
        FieldVisibility.objects.create(
            society=None,
            model_name=model_label,
            field_name="address",
            role="viewer",
            visible=False,
        )

        # Viewer cannot see address
        visible_viewer = visible_fields(instance, viewer, society)
        assert "address" not in visible_viewer

        # Accountant CAN see address (accountant is above viewer)
        visible_accountant = visible_fields(instance, accountant, society)
        assert "address" in visible_accountant


class TestHiddenFields:
    """Tests for hidden_fields() — the complement of visible_fields()."""

    def test_hidden_fields_returns_complement(self):
        society = SocietyFactory()
        viewer = _make_member(society, Membership.Role.VIEWER)
        instance = society
        model_label = instance._meta.label

        # Hide 'address' from viewer
        FieldVisibility.objects.create(
            society=None,
            model_name=model_label,
            field_name="address",
            role="viewer",
            visible=False,
        )

        visible = visible_fields(instance, viewer, society)
        hidden = hidden_fields(instance, viewer, society)
        all_fields = {f.name for f in instance._meta.get_fields() if hasattr(f, "name")}

        assert "address" in hidden
        assert "address" not in visible
        assert visible | hidden == all_fields
        assert visible & hidden == set()

    def test_hidden_fields_empty_without_rules(self):
        society = SocietyFactory()
        owner = _make_member(society, Membership.Role.OWNER)
        instance = society

        hidden = hidden_fields(instance, owner, society)
        assert hidden == set()


class TestFilterDictByVisibility:
    """Tests for filter_dict_by_visibility()."""

    def test_filter_removes_hidden_fields(self):
        society = SocietyFactory()
        viewer = _make_member(society, Membership.Role.VIEWER)
        instance = society
        model_label = instance._meta.label

        # Hide 'address' from viewer
        FieldVisibility.objects.create(
            society=None,
            model_name=model_label,
            field_name="address",
            role="viewer",
            visible=False,
        )

        data = {
            "name": "Test Society",
            "address": "Secret Address",
            "registration_number": "REG-001",
        }
        filtered = filter_dict_by_visibility(data, instance, viewer, society)
        assert "name" in filtered
        assert "address" not in filtered
        assert "registration_number" in filtered

    def test_filter_keeps_all_without_rules(self):
        society = SocietyFactory()
        owner = _make_member(society, Membership.Role.OWNER)
        instance = society

        data = {
            "name": "Test Society",
            "address": "Some Address",
            "registration_number": "REG-001",
        }
        filtered = filter_dict_by_visibility(data, instance, owner, society)
        assert filtered == data

    def test_filter_empty_dict(self):
        society = SocietyFactory()
        owner = _make_member(society, Membership.Role.OWNER)
        instance = society

        filtered = filter_dict_by_visibility({}, instance, owner, society)
        assert filtered == {}


class TestVisibleFieldsWithoutSociety:
    """Tests for visible_fields() when society is None."""

    def test_no_society_uses_wildcard_role(self):
        """When society is None, role defaults to '*' (all rules for '*' apply)."""
        society = SocietyFactory()
        user = UserFactory()
        instance = society
        model_label = instance._meta.label

        # Wildcard rule hides 'address'
        FieldVisibility.objects.create(
            society=None,
            model_name=model_label,
            field_name="address",
            role="*",
            visible=False,
        )

        visible = visible_fields(instance, user, None)
        assert "address" not in visible

    def test_no_society_no_rules_returns_all(self):
        society = SocietyFactory()
        user = UserFactory()
        instance = society

        visible = visible_fields(instance, user, None)
        all_fields = {f.name for f in instance._meta.get_fields() if hasattr(f, "name")}
        assert visible == all_fields
