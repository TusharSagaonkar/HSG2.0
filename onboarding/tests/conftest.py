"""Pytest fixtures for the onboarding test suite.

The ``_current_tenant`` contextvar is a process-wide singleton.  Some services
(e.g. ``SocietySetupService.create_society``) set it directly via
``_current_tenant.set(society)`` without using the ``tenant_context`` context
manager, which means the value leaks across test classes when tests run in a
single pytest session.

This autouse fixture resets the contextvar before and after every test so
that tenant-scoped queries (``TenantManager.get_queryset``) never see a stale
society from a previous test.
"""

from __future__ import annotations

import pytest

from societies.managers import _current_tenant


@pytest.fixture(autouse=True)
def _reset_tenant_context():
    """Clear ``_current_tenant`` around every test in the onboarding suite."""
    _current_tenant.set(None)
    yield
    _current_tenant.set(None)
