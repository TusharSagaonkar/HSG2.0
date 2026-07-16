"""Tests for the PermissionRegistry class.

These tests validate the declarative permission registry — the single source of
truth for all valid permission codes and the default role → permission mapping
(including wildcard matching). No database access is required.
"""

import pytest

from societies.permissions_registry import PermissionRegistry


class TestAllPermissions:
    """Tests for PermissionRegistry.all_permissions()."""

    def test_all_permissions_returns_non_empty_set(self):
        permissions = PermissionRegistry.all_permissions()
        assert len(permissions) > 0

    def test_all_permissions_returns_frozenset(self):
        permissions = PermissionRegistry.all_permissions()
        assert isinstance(permissions, frozenset)

    def test_all_permissions_contains_expected_codes(self):
        permissions = PermissionRegistry.all_permissions()
        # Spot-check codes across multiple modules
        assert "accounting.voucher.post" in permissions
        assert "accounting.voucher.view" in permissions
        assert "gateops.pass.create" in permissions
        assert "housing.society.edit" in permissions
        assert "housing.member.view" in permissions
        assert "reconciliation.transaction.import" in permissions
        assert "reports.report.export" in permissions
        assert "societies.role.assign" in permissions

    def test_all_permissions_does_not_contain_invalid_codes(self):
        permissions = PermissionRegistry.all_permissions()
        assert "invalid.permission.code" not in permissions
        assert "accounting.voucher.nonexistent" not in permissions
        assert "" not in permissions

    def test_all_permissions_covers_all_declared_modules(self):
        permissions = PermissionRegistry.all_permissions()
        # Every module declared in _MODULES should produce at least one code
        for module_name in PermissionRegistry._MODULES:
            module_codes = [c for c in permissions if c.startswith(f"{module_name}.")]
            assert len(module_codes) > 0, f"Module '{module_name}' produced no codes"


class TestIsValidPermission:
    """Tests for PermissionRegistry.is_valid_permission()."""

    def test_valid_permission_returns_true(self):
        assert PermissionRegistry.is_valid_permission("accounting.voucher.post") is True

    def test_valid_permission_other_module(self):
        assert PermissionRegistry.is_valid_permission("gateops.parcel.collect") is True

    def test_invalid_permission_returns_false(self):
        assert PermissionRegistry.is_valid_permission("invalid.permission.code") is False

    def test_invalid_action_returns_false(self):
        assert PermissionRegistry.is_valid_permission("accounting.voucher.nonexistent") is False

    def test_invalid_entity_returns_false(self):
        assert PermissionRegistry.is_valid_permission("accounting.nonexistent.view") is False

    def test_invalid_module_returns_false(self):
        assert PermissionRegistry.is_valid_permission("nonexistent.entity.view") is False

    def test_empty_string_returns_false(self):
        assert PermissionRegistry.is_valid_permission("") is False


class TestRoleHasPermission:
    """Tests for PermissionRegistry.role_has_permission()."""

    # --- Owner role (has all permissions) ---

    def test_owner_has_accounting_permission(self):
        assert PermissionRegistry.role_has_permission("owner", "accounting.voucher.post") is True

    def test_owner_has_housing_permission(self):
        assert PermissionRegistry.role_has_permission("owner", "housing.society.edit") is True

    def test_owner_has_gateops_permission(self):
        assert PermissionRegistry.role_has_permission("owner", "gateops.pass.create") is True

    def test_owner_has_all_permissions(self):
        for code in PermissionRegistry.all_permissions():
            assert PermissionRegistry.role_has_permission("owner", code) is True

    # --- Admin role (has all permissions) ---

    def test_admin_has_accounting_permission(self):
        assert PermissionRegistry.role_has_permission("admin", "accounting.voucher.post") is True

    def test_admin_has_all_permissions(self):
        for code in PermissionRegistry.all_permissions():
            assert PermissionRegistry.role_has_permission("admin", code) is True

    # --- Accountant role ---

    def test_accountant_has_accounting_permission(self):
        assert PermissionRegistry.role_has_permission("accountant", "accounting.voucher.post") is True

    def test_accountant_has_accounting_view(self):
        assert PermissionRegistry.role_has_permission("accountant", "accounting.account.view") is True

    def test_accountant_has_reconciliation_permission(self):
        assert PermissionRegistry.role_has_permission("accountant", "reconciliation.transaction.import") is True

    def test_accountant_has_reports_permission(self):
        assert PermissionRegistry.role_has_permission("accountant", "reports.report.export") is True

    def test_accountant_cannot_edit_society(self):
        assert PermissionRegistry.role_has_permission("accountant", "housing.society.edit") is False

    def test_accountant_cannot_manage_roles(self):
        assert PermissionRegistry.role_has_permission("accountant", "societies.role.assign") is False

    def test_accountant_can_view_society(self):
        assert PermissionRegistry.role_has_permission("accountant", "housing.society.view") is True

    # --- Member role (has *.view) ---

    def test_member_has_view_permission(self):
        assert PermissionRegistry.role_has_permission("member", "accounting.voucher.view") is True

    def test_member_has_view_permission_other_module(self):
        assert PermissionRegistry.role_has_permission("member", "housing.member.view") is True

    def test_member_cannot_post_voucher(self):
        assert PermissionRegistry.role_has_permission("member", "accounting.voucher.post") is False

    def test_member_cannot_create_voucher(self):
        assert PermissionRegistry.role_has_permission("member", "accounting.voucher.create") is False

    def test_member_cannot_delete_voucher(self):
        assert PermissionRegistry.role_has_permission("member", "accounting.voucher.delete") is False

    def test_member_can_export_reports(self):
        # member has "reports.report.export" explicitly
        assert PermissionRegistry.role_has_permission("member", "reports.report.export") is True

    # --- Viewer role (has *.view only) ---

    def test_viewer_has_view_permission(self):
        assert PermissionRegistry.role_has_permission("viewer", "accounting.voucher.view") is True

    def test_viewer_has_view_permission_other_module(self):
        assert PermissionRegistry.role_has_permission("viewer", "gateops.pass.view") is True

    def test_viewer_cannot_post_voucher(self):
        assert PermissionRegistry.role_has_permission("viewer", "accounting.voucher.post") is False

    def test_viewer_cannot_create(self):
        assert PermissionRegistry.role_has_permission("viewer", "accounting.voucher.create") is False

    def test_viewer_cannot_export_reports(self):
        # viewer only has *.view, not reports.report.export
        assert PermissionRegistry.role_has_permission("viewer", "reports.report.export") is False

    # --- Edge cases ---

    def test_nonexistent_role_returns_false(self):
        assert PermissionRegistry.role_has_permission("nonexistent_role", "any.permission") is False

    def test_none_role_returns_false(self):
        assert PermissionRegistry.role_has_permission(None, "accounting.voucher.post") is False

    def test_invalid_permission_code_for_owner(self):
        # Owner has "*" which matches everything, even invalid codes
        assert PermissionRegistry.role_has_permission("owner", "invalid.permission.code") is True

    def test_invalid_permission_code_for_member(self):
        # Member has *.view — invalid code doesn't end in .view
        assert PermissionRegistry.role_has_permission("member", "invalid.permission.code") is False


class TestWildcardMatching:
    """Tests for wildcard pattern matching in role_has_permission()."""

    def test_module_wildcard_matches(self):
        # accountant has "accounting.*" which matches "accounting.voucher.post"
        assert PermissionRegistry.role_has_permission("accountant", "accounting.voucher.post") is True

    def test_module_wildcard_matches_all_in_module(self):
        for code in PermissionRegistry.all_permissions():
            if code.startswith("accounting."):
                assert PermissionRegistry.role_has_permission("accountant", code) is True

    def test_action_wildcard_matches(self):
        # member has "*.view" which matches "accounting.voucher.view"
        assert PermissionRegistry.role_has_permission("member", "accounting.voucher.view") is True

    def test_action_wildcard_matches_all_view(self):
        for code in PermissionRegistry.all_permissions():
            if code.endswith(".view"):
                assert PermissionRegistry.role_has_permission("member", code) is True

    def test_star_matches_everything(self):
        # owner/admin have "*" which matches all valid codes
        for code in PermissionRegistry.all_permissions():
            assert PermissionRegistry.role_has_permission("owner", code) is True
            assert PermissionRegistry.role_has_permission("admin", code) is True

    def test_wildcard_match_helper_exact(self):
        assert PermissionRegistry._wildcard_match("accounting.voucher.post", "accounting.voucher.post") is True

    def test_wildcard_match_helper_module_prefix(self):
        assert PermissionRegistry._wildcard_match("accounting.*", "accounting.voucher.post") is True
        assert PermissionRegistry._wildcard_match("accounting.*", "accounting.voucher.view") is True

    def test_wildcard_match_helper_module_prefix_no_match(self):
        assert PermissionRegistry._wildcard_match("accounting.*", "housing.society.view") is False

    def test_wildcard_match_helper_action_suffix(self):
        assert PermissionRegistry._wildcard_match("*.view", "accounting.voucher.view") is True
        assert PermissionRegistry._wildcard_match("*.view", "housing.member.view") is True

    def test_wildcard_match_helper_action_suffix_no_match(self):
        assert PermissionRegistry._wildcard_match("*.view", "accounting.voucher.post") is False

    def test_wildcard_match_helper_no_wildcard_no_match(self):
        assert PermissionRegistry._wildcard_match("accounting.voucher.view", "accounting.voucher.post") is False
