"""Tests for cross-tenant IDOR (Insecure Direct Object Reference) prevention.

These tests verify that the society-scoped voucher views prevent cross-tenant
access. A user in society A must not be able to POST, DELETE, REVERSE, or VIEW
a voucher belonging to society B — they should get a 404 (not the voucher).

The tests use Django's test Client to make real HTTP requests through the full
middleware stack (including SocietyMiddleware which sets request.current_society
from the session).
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounting.models import Account
from accounting.models import AccountingPeriod
from accounting.models import LedgerEntry
from accounting.models import Voucher
from core.test_factories import SocietyFactory
from core.test_factories import UserFactory
from housing_accounting.selection import SESSION_SELECTED_SOCIETY_ID
from societies.models import Membership
from societies.models import Society


pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _login_with_society(client, user, society):
    """Log in ``user`` and set ``society`` as the selected society in the session."""
    client.force_login(user)
    session = client.session
    session[SESSION_SELECTED_SOCIETY_ID] = society.id
    session.save()


def _create_draft_voucher(society, *, voucher_date=None):
    """Create a minimal draft voucher (no ledger entries) in ``society``."""
    if voucher_date is None:
        voucher_date = timezone.localdate()
    return Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.GENERAL,
        voucher_date=voucher_date,
    )


def _create_postable_voucher(society):
    """Create a draft voucher with balanced entries that can be posted.

    Requires the society to have bootstrapped accounts (via the signal cascade).
    """
    today = timezone.localdate()
    # Ensure the accounting period for today is open
    AccountingPeriod.objects.filter(
        society=society,
        start_date__lte=today,
        end_date__gte=today,
    ).update(is_open=True)

    accounts = list(
        Account.objects.filter(society=society, is_active=True, is_gst=False)[:2]
    )
    assert len(accounts) >= 2, "Society needs at least 2 non-GST accounts"

    voucher = Voucher.objects.create(
        society=society,
        voucher_type=Voucher.VoucherType.GENERAL,
        voucher_date=today,
    )
    LedgerEntry.objects.create(
        voucher=voucher, account=accounts[0], debit=Decimal("100.00")
    )
    LedgerEntry.objects.create(
        voucher=voucher, account=accounts[1], credit=Decimal("100.00")
    )
    return voucher


def _create_posted_voucher(society):
    """Create and post a voucher in ``society`` (for reverse tests)."""
    voucher = _create_postable_voucher(society)
    voucher.post()
    voucher.refresh_from_db()
    return voucher


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def society_a(society):
    """Society A — uses the session-scoped ``society`` fixture (already bootstrapped)."""
    return society


@pytest.fixture
def society_b(db):
    """Society B — a second society for cross-tenant isolation tests.

    Created without the expensive bootstrap signal since the IDOR tests only
    need a voucher to exist (not accounts or entries) in society B.
    """
    from accounting.signals import bootstrap_accounts_for_new_society
    from django.db.models.signals import post_save

    post_save.disconnect(bootstrap_accounts_for_new_society, sender=Society)
    try:
        return Society.objects.create(name="IDOR Society B")
    finally:
        post_save.connect(bootstrap_accounts_for_new_society, sender=Society)


# ---------------------------------------------------------------------------
# Cross-tenant IDOR tests
# ---------------------------------------------------------------------------


class TestCrossTenantIDOR:
    """Tests that cross-tenant voucher access is blocked with 404."""

    def test_user_a_cannot_post_voucher_b(self, client, society_a, society_b):
        owner_a = UserFactory()
        Membership.objects.create(
            user=owner_a,
            society=society_a,
            role=Membership.Role.OWNER,
            invited_by=owner_a,
        )
        voucher_b = _create_draft_voucher(society_b)

        _login_with_society(client, owner_a, society_a)
        url = reverse("accounting:voucher-post", kwargs={"pk": voucher_b.pk})
        response = client.post(url)

        assert response.status_code == 404

    def test_user_a_cannot_delete_draft_voucher_b(self, client, society_a, society_b):
        owner_a = UserFactory()
        Membership.objects.create(
            user=owner_a,
            society=society_a,
            role=Membership.Role.OWNER,
            invited_by=owner_a,
        )
        voucher_b = _create_draft_voucher(society_b)

        _login_with_society(client, owner_a, society_a)
        url = reverse("accounting:voucher-delete-draft", kwargs={"pk": voucher_b.pk})
        response = client.post(url)

        assert response.status_code == 404

    def test_user_a_cannot_reverse_voucher_b(self, client, society_a, society_b):
        owner_a = UserFactory()
        Membership.objects.create(
            user=owner_a,
            society=society_a,
            role=Membership.Role.OWNER,
            invited_by=owner_a,
        )
        # Society B has no bootstrapped accounts, so create a simple draft
        # The reverse view fetches the voucher first (404 before reverse logic)
        voucher_b = _create_draft_voucher(society_b)

        _login_with_society(client, owner_a, society_a)
        url = reverse("accounting:voucher-reverse", kwargs={"pk": voucher_b.pk})
        response = client.post(url)

        assert response.status_code == 404

    def test_user_a_cannot_view_voucher_b(self, client, society_a, society_b):
        owner_a = UserFactory()
        Membership.objects.create(
            user=owner_a,
            society=society_a,
            role=Membership.Role.OWNER,
            invited_by=owner_a,
        )
        voucher_b = _create_draft_voucher(society_b)

        _login_with_society(client, owner_a, society_a)
        url = reverse("accounting:voucher-detail", kwargs={"pk": voucher_b.pk})
        response = client.get(url)

        assert response.status_code == 404

    def test_user_a_can_view_own_voucher(self, client, society_a, society_b):
        """Sanity check: user A CAN view a voucher in their own society."""
        owner_a = UserFactory()
        Membership.objects.create(
            user=owner_a,
            society=society_a,
            role=Membership.Role.OWNER,
            invited_by=owner_a,
        )
        voucher_a = _create_draft_voucher(society_a)

        _login_with_society(client, owner_a, society_a)
        url = reverse("accounting:voucher-detail", kwargs={"pk": voucher_a.pk})
        response = client.get(url)

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# No-society-selected tests
# ---------------------------------------------------------------------------


class TestNoSocietySelected:
    """Tests for users with no membership in any society."""

    def test_user_no_membership_redirected_on_view(self, client, society_b):
        """User with no membership gets redirected (no society selected)."""
        user = UserFactory()
        voucher_b = _create_draft_voucher(society_b)

        client.force_login(user)
        url = reverse("accounting:voucher-detail", kwargs={"pk": voucher_b.pk})
        response = client.get(url)

        # No society selected → redirect
        assert response.status_code in (302, 404)

    def test_user_no_membership_redirected_on_post(self, client, society_b):
        """User with no membership gets redirected when trying to post."""
        user = UserFactory()
        voucher_b = _create_draft_voucher(society_b)

        client.force_login(user)
        url = reverse("accounting:voucher-post", kwargs={"pk": voucher_b.pk})
        response = client.post(url)

        assert response.status_code in (302, 404)


# ---------------------------------------------------------------------------
# Role-based permission tests
# ---------------------------------------------------------------------------


class TestRoleBasedPermissions:
    """Tests for role-based permission checks on voucher views."""

    def test_member_cannot_post_voucher(self, client, society_a, society_b):
        """User with member role gets PermissionDenied (403) when posting."""
        owner = UserFactory()
        Membership.objects.create(
            user=owner,
            society=society_a,
            role=Membership.Role.OWNER,
            invited_by=owner,
        )
        member = UserFactory()
        Membership.objects.create(
            user=member,
            society=society_a,
            role=Membership.Role.MEMBER,
            invited_by=owner,
        )
        voucher_a = _create_draft_voucher(society_a)

        _login_with_society(client, member, society_a)
        url = reverse("accounting:voucher-post", kwargs={"pk": voucher_a.pk})
        response = client.post(url)

        assert response.status_code == 403
        # Verify the voucher was NOT posted
        voucher_a.refresh_from_db()
        assert voucher_a.posted_at is None

    def test_viewer_cannot_post_voucher(self, client, society_a, society_b):
        """User with viewer role gets PermissionDenied (403) when posting."""
        owner = UserFactory()
        Membership.objects.create(
            user=owner,
            society=society_a,
            role=Membership.Role.OWNER,
            invited_by=owner,
        )
        viewer = UserFactory()
        Membership.objects.create(
            user=viewer,
            society=society_a,
            role=Membership.Role.VIEWER,
            invited_by=owner,
        )
        voucher_a = _create_draft_voucher(society_a)

        _login_with_society(client, viewer, society_a)
        url = reverse("accounting:voucher-post", kwargs={"pk": voucher_a.pk})
        response = client.post(url)

        assert response.status_code == 403
        voucher_a.refresh_from_db()
        assert voucher_a.posted_at is None

    def test_accountant_can_post_voucher(self, client, society_a, society_b):
        """User with accountant role can post a voucher in their society."""
        owner = UserFactory()
        Membership.objects.create(
            user=owner,
            society=society_a,
            role=Membership.Role.OWNER,
            invited_by=owner,
        )
        accountant = UserFactory()
        Membership.objects.create(
            user=accountant,
            society=society_a,
            role=Membership.Role.ACCOUNTANT,
            invited_by=owner,
        )
        voucher_a = _create_postable_voucher(society_a)

        _login_with_society(client, accountant, society_a)
        url = reverse("accounting:voucher-post", kwargs={"pk": voucher_a.pk})
        response = client.post(url)

        # Successful post redirects to the posting menu
        assert response.status_code == 302
        # Verify the voucher was actually posted
        voucher_a.refresh_from_db()
        assert voucher_a.posted_at is not None

    def test_owner_can_post_voucher(self, client, society_a, society_b):
        """User with owner role can post a voucher in their society."""
        owner = UserFactory()
        Membership.objects.create(
            user=owner,
            society=society_a,
            role=Membership.Role.OWNER,
            invited_by=owner,
        )
        voucher_a = _create_postable_voucher(society_a)

        _login_with_society(client, owner, society_a)
        url = reverse("accounting:voucher-post", kwargs={"pk": voucher_a.pk})
        response = client.post(url)

        assert response.status_code == 302
        voucher_a.refresh_from_db()
        assert voucher_a.posted_at is not None

    def test_member_can_view_voucher(self, client, society_a, society_b):
        """User with member role can view a voucher (has *.view permission)."""
        owner = UserFactory()
        Membership.objects.create(
            user=owner,
            society=society_a,
            role=Membership.Role.OWNER,
            invited_by=owner,
        )
        member = UserFactory()
        Membership.objects.create(
            user=member,
            society=society_a,
            role=Membership.Role.MEMBER,
            invited_by=owner,
        )
        voucher_a = _create_draft_voucher(society_a)

        _login_with_society(client, member, society_a)
        url = reverse("accounting:voucher-detail", kwargs={"pk": voucher_a.pk})
        response = client.get(url)

        assert response.status_code == 200

    def test_viewer_can_view_voucher(self, client, society_a, society_b):
        """User with viewer role can view a voucher (has *.view permission)."""
        owner = UserFactory()
        Membership.objects.create(
            user=owner,
            society=society_a,
            role=Membership.Role.OWNER,
            invited_by=owner,
        )
        viewer = UserFactory()
        Membership.objects.create(
            user=viewer,
            society=society_a,
            role=Membership.Role.VIEWER,
            invited_by=owner,
        )
        voucher_a = _create_draft_voucher(society_a)

        _login_with_society(client, viewer, society_a)
        url = reverse("accounting:voucher-detail", kwargs={"pk": voucher_a.pk})
        response = client.get(url)

        assert response.status_code == 200

    def test_member_cannot_delete_draft(self, client, society_a, society_b):
        """Member role lacks accounting.voucher.delete → 403."""
        owner = UserFactory()
        Membership.objects.create(
            user=owner,
            society=society_a,
            role=Membership.Role.OWNER,
            invited_by=owner,
        )
        member = UserFactory()
        Membership.objects.create(
            user=member,
            society=society_a,
            role=Membership.Role.MEMBER,
            invited_by=owner,
        )
        voucher_a = _create_draft_voucher(society_a)

        _login_with_society(client, member, society_a)
        url = reverse("accounting:voucher-delete-draft", kwargs={"pk": voucher_a.pk})
        response = client.post(url)

        assert response.status_code == 403
        # Voucher should still exist
        assert Voucher.objects.filter(pk=voucher_a.pk).exists()

    def test_accountant_can_delete_draft(self, client, society_a, society_b):
        """Accountant role has accounting.* which includes delete."""
        owner = UserFactory()
        Membership.objects.create(
            user=owner,
            society=society_a,
            role=Membership.Role.OWNER,
            invited_by=owner,
        )
        accountant = UserFactory()
        Membership.objects.create(
            user=accountant,
            society=society_a,
            role=Membership.Role.ACCOUNTANT,
            invited_by=owner,
        )
        voucher_a = _create_draft_voucher(society_a)

        _login_with_society(client, accountant, society_a)
        url = reverse("accounting:voucher-delete-draft", kwargs={"pk": voucher_a.pk})
        response = client.post(url)

        assert response.status_code == 302
        assert not Voucher.objects.filter(pk=voucher_a.pk).exists()
