"""Declarative permission registry — single source of truth for permission codes.

Permission codes are dot-namespaced: ``<module>.<entity>.<action>``.

This module is intentionally a plain Python class (NOT a database table). It is
loaded at import time and provides O(1) lookups for permission validation and
role-permission matching (including wildcards).

Phase 1: default role → permission mapping lives here.
Phase 2: per-society overrides will live in a ``RolePermission`` table, but
this registry remains the canonical list of *valid* permission codes.
"""

from __future__ import annotations

from typing import ClassVar


class PermissionRegistry:
    """Declarative registry of all valid permission codes in the system.

    Usage::

        PermissionRegistry.is_valid_permission("accounting.voucher.post")
        PermissionRegistry.role_has_permission("accountant", "accounting.voucher.view")
        PermissionRegistry.all_permissions()
    """

    # Declarative mapping of module → {entity → [actions]}.
    # Only existing apps are listed here.
    _MODULES: ClassVar[dict[str, dict[str, list[str]]]] = {
        "accounting": {
            "voucher": ["create", "view", "edit", "delete", "post", "reverse", "export", "print"],
            "account": ["create", "view", "edit", "lock", "unlock"],
            "financialyear": ["create", "view", "edit", "lock", "unlock"],
            "report": ["view", "export"],
            "vouchertemplate": ["create", "view", "edit", "delete"],
        },
        "housing": {
            "society": ["view", "edit"],
            "structure": ["create", "view", "edit", "delete"],
            "unit": ["create", "view", "edit", "delete"],
            "member": ["create", "view", "edit", "delete"],
            "bill": ["create", "view", "edit", "delete", "post"],
            "receipt": ["create", "view", "edit", "delete", "post"],
            "chargetemplate": ["create", "view", "edit", "delete"],
            "membership": ["create", "view", "edit", "delete"],
        },
        "members": {
            "member": ["create", "view", "edit", "delete"],
            "nominee": ["create", "view", "edit", "delete"],
        },
        "gateops": {
            "gateevent": ["create", "view", "edit", "approve", "reject"],
            "pass": ["create", "view", "edit", "delete", "revoke", "suspend", "reactivate", "validate"],
            "vehicle": ["create", "view", "edit", "delete", "watchlist"],
            "material": ["create", "view", "edit", "delete", "return", "cancel"],
            "parcel": ["create", "view", "edit", "delete", "collect", "return", "mark_lost"],
            "rule": ["create", "view", "edit", "delete", "toggle"],
        },
        "parking": {
            "vehicle": ["create", "view", "edit", "delete"],
            "permit": ["create", "view", "edit", "delete"],
            "slot": ["create", "view", "edit", "delete"],
        },
        "reconciliation": {
            "transaction": ["create", "view", "edit", "delete", "import"],
            "link": ["create", "view", "edit", "delete"],
            "report": ["view", "export"],
        },
        "reports": {
            "report": ["view", "generate", "export", "print"],
        },
        "notifications": {
            "template": ["create", "view", "edit", "delete"],
            "queue": ["view", "process"],
        },
        "shares": {
            "certificate": ["create", "view", "edit", "delete"],
            "ledger": ["create", "view", "edit", "delete"],
        },
        "societies": {
            "membership": ["create", "view", "edit", "delete"],
            "role": ["view", "assign", "revoke"],
        },
    }

    # Default role → permission mapping.
    #   - "*"                          → all permissions
    #   - "accounting.*"               → any permission starting with "accounting."
    #   - "*.view"                     → any permission ending with ".view"
    #   - exact code                   → that single permission
    _ROLE_PERMISSIONS: ClassVar[dict[str, set[str]]] = {
        "owner": "*",  # all permissions
        "admin": "*",  # all permissions (refined in Phase 2)
        "accountant": {
            "accounting.*",
            "housing.bill.*",
            "housing.receipt.*",
            "housing.chargetemplate.*",
            "reconciliation.*",
            "reports.*",
            "members.*",
            "housing.society.view",
            "housing.structure.view",
            "housing.unit.view",
            "housing.member.view",
            "housing.membership.view",
        },
        "member": {"*.view", "reports.report.export"},
        "viewer": {"*.view"},
    }

    # Full set of valid permission codes, generated at class load time.
    _ALL_PERMISSIONS: ClassVar[frozenset[str]] = frozenset(
        f"{module}.{entity}.{action}"
        for module, entities in _MODULES.items()
        for entity, actions in entities.items()
        for action in actions
    )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def all_permissions(cls) -> frozenset[str]:
        """Return the full immutable set of all valid permission codes."""
        return cls._ALL_PERMISSIONS

    @classmethod
    def is_valid_permission(cls, code: str) -> bool:
        """Return True if ``code`` is a registered permission code."""
        return code in cls._ALL_PERMISSIONS

    @classmethod
    def role_has_permission(cls, role: str | None, permission_code: str) -> bool:
        """Check if a role grants a specific permission.

        Supports wildcard matching:
          - ``"*"``        → all permissions
          - ``"module.*"`` → any permission starting with ``"module."``
          - ``"*.action"``→ any permission ending with ``".action"``
          - exact code    → that single permission

        Args:
            role: The role string (e.g. ``"accountant"``). ``None`` → False.
            permission_code: Dot-namespaced permission string.

        Returns:
            True if the role grants the permission.
        """
        if role is None:
            return False

        patterns = cls._ROLE_PERMISSIONS.get(role)
        if patterns is None:
            return False

        # Normalize: a bare ``"*"`` string means all permissions.
        if patterns == "*":
            return True

        # Exact match (fast path).
        if permission_code in patterns:
            return True

        for pattern in patterns:
            if pattern == "*":
                return True
            if cls._wildcard_match(pattern, permission_code):
                return True
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _wildcard_match(pattern: str, code: str) -> bool:
        """Match a single wildcard pattern against a permission code.

        Supported patterns:
          - ``"module.*"``  → prefix match on ``"module."``
          - ``"*.action"``  → suffix match on ``".action"``
          - exact string    → equality
        """
        if pattern == code:
            return True
        if pattern.endswith(".*"):
            prefix = pattern[:-1]  # keep trailing dot: "module."
            return code.startswith(prefix)
        if pattern.startswith("*."):
            suffix = pattern[1:]  # keep leading dot: ".action"
            return code.endswith(suffix)
        return False
