"""Tests for the has_permission() authorization primitive.

These tests validate the canonical authorization function that checks whether a
user has a specific permission in a society. Covers unauthenticated users,
membership-based access, super-admin/superuser bypass, and role-based checks.
"""

import pytest
from django.test import RequestFactory

from core.test_factories import SocietyFactory
from core.test_factories import UserFactory
from societies.models import Membership
from societies.permissions import has_permission
from societies.services import create_society


pytestmark = pytest.mark.django_db


class TestUnauthenticatedAndNoMembership:
    """Tests for users without authentication or membership."""

    def test_unauthenticated_user_returns_false(self):
        from django.contrib.auth.models import AnonymousUser

        user = AnonymousUser()
        society = SocietyFactory()
        assert has_permission(user, "accounting.voucher.view", society) is False

    def test_unauthenticated_user_any_permission_returns_false(self):
        from django.contrib.auth.models import AnonymousUser

        user = AnonymousUser()
        society = SocietyFactory()
        assert has_permission(user, "accounting.voucher.post", society) is False

    def test_authenticated_user_no_membership_returns_false(self):
        user = UserFactory()
        society = SocietyFactory()
        assert has_permission(user, "accounting.voucher.view", society) is False

    def test_society_none_returns_false(self):
        user = UserFactory()
        assert has_permission(user, "accounting.voucher.view", None) is False


class TestSuperAdminBypass:
    """Tests for super-admin and Django superuser bypass."""

    def test_super_admin_bypass_returns_true(self):
        super_admin = UserFactory(is_super_admin=True)
        society = SocietyFactory()
        assert has_permission(super_admin, "accounting.voucher.post", society) is True

    def test_super_admin_bypass_any_permission(self):
        super_admin = UserFactory(is_super_admin=True)
        society = SocietyFactory()
        assert has_permission(super_admin, "housing.society.edit", society) is True

    def test_super_admin_bypass_without_society(self):
        super_admin = UserFactory(is_super_admin=True)
        # Super admin bypass happens before the society=None check
        assert has_permission(super_admin, "accounting.voucher.post", None) is True

    def test_django_superuser_bypass_returns_true(self):
        superuser = UserFactory(is_superuser=True, is_staff=True)
        society = SocietyFactory()
        assert has_permission(superuser, "accounting.voucher.post", society) is True

    def test_django_superuser_bypass_any_permission(self):
        superuser = UserFactory(is_superuser=True, is_staff=True)
        society = SocietyFactory()
        assert has_permission(superuser, "housing.society.edit", society) is True


class TestCrossSocietyAccess:
    """Tests for cross-society access isolation."""

    def test_user_with_membership_in_society_a_true_for_a(self):
        user = UserFactory()
        society_a = create_society(user=user, name="Society A Perm Test")
        assert has_permission(user, "accounting.voucher.view", society_a) is True

    def test_user_with_membership_in_society_a_false_for_b(self):
        user = UserFactory()
        society_a = create_society(user=user, name="Society A Cross Test")
        society_b = SocietyFactory(name="Society B Cross Test")
        # User has no membership in society_b
        assert has_permission(user, "accounting.voucher.view", society_b) is False


class TestRoleBasedPermissions:
    """Tests for role-specific permission checks."""

    def test_owner_role_has_all_permissions(self):
        owner = UserFactory()
        society = create_society(user=owner, name="Owner Perm Society")
        assert has_permission(owner, "accounting.voucher.post", society) is True
        assert has_permission(owner, "housing.society.edit", society) is True
        assert has_permission(owner, "gateops.pass.create", society) is True
        assert has_permission(owner, "societies.role.assign", society) is True

    def test_accountant_role_has_accounting_permissions(self):
        owner = UserFactory()
        society = create_society(user=owner, name="Accountant Perm Society")
        accountant = UserFactory()
        Membership.objects.create(
            user=accountant,
            society=society,
            role=Membership.Role.ACCOUNTANT,
            invited_by=owner,
        )
        assert has_permission(accountant, "accounting.voucher.post", society) is True
        assert has_permission(accountant, "accounting.voucher.view", society) is True
        assert has_permission(accountant, "reconciliation.transaction.import", society) is True

    def test_accountant_role_cannot_edit_society(self):
        owner = UserFactory()
        society = create_society(user=owner, name="Accountant Edit Society")
        accountant = UserFactory()
        Membership.objects.create(
            user=accountant,
            society=society,
            role=Membership.Role.ACCOUNTANT,
            invited_by=owner,
        )
        assert has_permission(accountant, "housing.society.edit", society) is False

    def test_member_role_has_view_permissions(self):
        owner = UserFactory()
        society = create_society(user=owner, name="Member View Society")
        member = UserFactory()
        Membership.objects.create(
            user=member,
            society=society,
            role=Membership.Role.MEMBER,
            invited_by=owner,
        )
        assert has_permission(member, "accounting.voucher.view", society) is True
        assert has_permission(member, "housing.member.view", society) is True

    def test_member_role_cannot_create_edit_delete(self):
        owner = UserFactory()
        society = create_society(user=owner, name="Member No Write Society")
        member = UserFactory()
        Membership.objects.create(
            user=member,
            society=society,
            role=Membership.Role.MEMBER,
            invited_by=owner,
        )
        assert has_permission(member, "accounting.voucher.create", society) is False
        assert has_permission(member, "accounting.voucher.edit", society) is False
        assert has_permission(member, "accounting.voucher.delete", society) is False
        assert has_permission(member, "accounting.voucher.post", society) is False

    def test_viewer_role_has_view_permissions_only(self):
        owner = UserFactory()
        society = create_society(user=owner, name="Viewer View Society")
        viewer = UserFactory()
        Membership.objects.create(
            user=viewer,
            society=society,
            role=Membership.Role.VIEWER,
            invited_by=owner,
        )
        assert has_permission(viewer, "accounting.voucher.view", society) is True
        assert has_permission(viewer, "housing.member.view", society) is True

    def test_viewer_role_cannot_post(self):
        owner = UserFactory()
        society = create_society(user=owner, name="Viewer No Post Society")
        viewer = UserFactory()
        Membership.objects.create(
            user=viewer,
            society=society,
            role=Membership.Role.VIEWER,
            invited_by=owner,
        )
        assert has_permission(viewer, "accounting.voucher.post", society) is False
        assert has_permission(viewer, "accounting.voucher.create", society) is False

    def test_inactive_membership_returns_false(self):
        owner = UserFactory()
        society = create_society(user=owner, name="Inactive Mbr Society")
        member = UserFactory()
        Membership.objects.create(
            user=member,
            society=society,
            role=Membership.Role.MEMBER,
            invited_by=owner,
            is_active=False,
        )
        assert has_permission(member, "accounting.voucher.view", society) is False
