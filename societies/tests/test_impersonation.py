"""Tests for the super-admin impersonation workflow.

These tests validate:
- ``start_impersonation()`` with valid super-admin creates a session
- ``start_impersonation()`` with non-super-admin raises PermissionDenied
- ``start_impersonation()`` without reason raises PermissionDenied
- ``start_impersonation()`` ends previous active sessions for the same impersonator
- ``get_active_impersonation()`` returns the active session / None
- ``ImpersonationSession.is_active`` property (active vs expired)
- ``ImpersonationSession.end()`` sets status to ENDED and sets ended_at
- ``end_impersonation()`` calls session.end()
"""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from core.test_factories import SocietyFactory
from core.test_factories import UserFactory
from societies.models import ImpersonationSession
from societies.services import end_impersonation
from societies.services import get_active_impersonation
from societies.services import start_impersonation


pytestmark = pytest.mark.django_db


class TestStartImpersonation:
    """Tests for start_impersonation()."""

    def test_valid_super_admin_creates_session(self):
        super_admin = UserFactory(is_super_admin=True)
        society = SocietyFactory()
        session = start_impersonation(
            impersonator=super_admin,
            society=society,
            reason="Investigating billing discrepancy",
        )
        assert session.pk is not None
        assert session.impersonator == super_admin
        assert session.target_society == society
        assert session.reason == "Investigating billing discrepancy"
        assert session.status == ImpersonationSession.Status.ACTIVE
        assert session.started_at is not None
        assert session.ended_at is None
        assert session.expires_at > timezone.now()

    def test_django_superuser_creates_session(self):
        superuser = UserFactory(is_superuser=True, is_staff=True)
        society = SocietyFactory()
        session = start_impersonation(
            impersonator=superuser,
            society=society,
            reason="Support ticket #123",
        )
        assert session.pk is not None
        assert session.impersonator == superuser
        assert session.status == ImpersonationSession.Status.ACTIVE

    def test_non_super_admin_raises_permission_denied(self):
        regular_user = UserFactory()
        society = SocietyFactory()
        with pytest.raises(PermissionDenied, match="super-admin"):
            start_impersonation(
                impersonator=regular_user,
                society=society,
                reason="Trying to impersonate",
            )

    def test_without_reason_raises_permission_denied(self):
        super_admin = UserFactory(is_super_admin=True)
        society = SocietyFactory()
        with pytest.raises(PermissionDenied, match="reason"):
            start_impersonation(
                impersonator=super_admin,
                society=society,
                reason="",
            )

    def test_whitespace_only_reason_raises_permission_denied(self):
        super_admin = UserFactory(is_super_admin=True)
        society = SocietyFactory()
        with pytest.raises(PermissionDenied, match="reason"):
            start_impersonation(
                impersonator=super_admin,
                society=society,
                reason="   ",
            )

    def test_ends_previous_active_sessions(self):
        super_admin = UserFactory(is_super_admin=True)
        society = SocietyFactory()
        # Start first session
        first_session = start_impersonation(
            impersonator=super_admin,
            society=society,
            reason="First session",
        )
        assert first_session.status == ImpersonationSession.Status.ACTIVE

        # Start second session — should end the first
        second_session = start_impersonation(
            impersonator=super_admin,
            society=society,
            reason="Second session",
        )

        first_session.refresh_from_db()
        assert first_session.status == ImpersonationSession.Status.EXPIRED
        assert first_session.ended_at is not None
        assert second_session.status == ImpersonationSession.Status.ACTIVE

    def test_reason_is_stripped(self):
        super_admin = UserFactory(is_super_admin=True)
        society = SocietyFactory()
        session = start_impersonation(
            impersonator=super_admin,
            society=society,
            reason="  Padded reason  ",
        )
        assert session.reason == "Padded reason"

    def test_custom_duration(self):
        super_admin = UserFactory(is_super_admin=True)
        society = SocietyFactory()
        session = start_impersonation(
            impersonator=super_admin,
            society=society,
            reason="Quick check",
            duration_minutes=30,
        )
        expected_expiry = timezone.now() + timedelta(minutes=30)
        # Allow a small tolerance for execution time
        assert abs((session.expires_at - expected_expiry).total_seconds()) < 5


class TestGetActiveImpersonation:
    """Tests for get_active_impersonation()."""

    def test_returns_active_session(self):
        super_admin = UserFactory(is_super_admin=True)
        society = SocietyFactory()
        start_impersonation(
            impersonator=super_admin,
            society=society,
            reason="Active session test",
        )
        session = get_active_impersonation(super_admin)
        assert session is not None
        assert session.impersonator == super_admin
        assert session.status == ImpersonationSession.Status.ACTIVE

    def test_returns_none_when_no_session(self):
        user = UserFactory()
        assert get_active_impersonation(user) is None

    def test_returns_none_when_session_ended(self):
        super_admin = UserFactory(is_super_admin=True)
        society = SocietyFactory()
        session = start_impersonation(
            impersonator=super_admin,
            society=society,
            reason="Will end this",
        )
        session.end()
        assert get_active_impersonation(super_admin) is None

    def test_returns_none_when_session_expired(self):
        super_admin = UserFactory(is_super_admin=True)
        society = SocietyFactory()
        session = start_impersonation(
            impersonator=super_admin,
            society=society,
            reason="Will expire",
        )
        # Force expiry by setting expires_at in the past
        session.expires_at = timezone.now() - timedelta(minutes=1)
        session.save(update_fields=["expires_at"])
        assert get_active_impersonation(super_admin) is None


class TestImpersonationSessionIsActive:
    """Tests for ImpersonationSession.is_active property."""

    def test_active_non_expired_returns_true(self):
        super_admin = UserFactory(is_super_admin=True)
        society = SocietyFactory()
        session = start_impersonation(
            impersonator=super_admin,
            society=society,
            reason="Active check",
        )
        assert session.is_active is True

    def test_ended_returns_false(self):
        super_admin = UserFactory(is_super_admin=True)
        society = SocietyFactory()
        session = start_impersonation(
            impersonator=super_admin,
            society=society,
            reason="Will end",
        )
        session.end()
        assert session.is_active is False

    def test_expired_returns_false(self):
        super_admin = UserFactory(is_super_admin=True)
        society = SocietyFactory()
        session = start_impersonation(
            impersonator=super_admin,
            society=society,
            reason="Will expire",
        )
        session.expires_at = timezone.now() - timedelta(minutes=5)
        session.save(update_fields=["expires_at"])
        assert session.is_active is False


class TestImpersonationSessionEnd:
    """Tests for ImpersonationSession.end()."""

    def test_end_sets_status_to_ended(self):
        super_admin = UserFactory(is_super_admin=True)
        society = SocietyFactory()
        session = start_impersonation(
            impersonator=super_admin,
            society=society,
            reason="End test",
        )
        session.end()
        assert session.status == ImpersonationSession.Status.ENDED

    def test_end_sets_ended_at(self):
        super_admin = UserFactory(is_super_admin=True)
        society = SocietyFactory()
        session = start_impersonation(
            impersonator=super_admin,
            society=society,
            reason="End at test",
        )
        assert session.ended_at is None
        session.end()
        assert session.ended_at is not None

    def test_end_persists_to_database(self):
        super_admin = UserFactory(is_super_admin=True)
        society = SocietyFactory()
        session = start_impersonation(
            impersonator=super_admin,
            society=society,
            reason="Persist test",
        )
        session.end()
        session.refresh_from_db()
        assert session.status == ImpersonationSession.Status.ENDED
        assert session.ended_at is not None

    def test_end_already_ended_is_idempotent(self):
        super_admin = UserFactory(is_super_admin=True)
        society = SocietyFactory()
        session = start_impersonation(
            impersonator=super_admin,
            society=society,
            reason="Idempotent test",
        )
        session.end()
        first_ended_at = session.ended_at
        # Calling end() again should not change anything
        session.end()
        assert session.status == ImpersonationSession.Status.ENDED
        assert session.ended_at == first_ended_at


class TestEndImpersonationService:
    """Tests for the end_impersonation() service function."""

    def test_end_impersonation_calls_session_end(self):
        super_admin = UserFactory(is_super_admin=True)
        society = SocietyFactory()
        session = start_impersonation(
            impersonator=super_admin,
            society=society,
            reason="Service end test",
        )
        end_impersonation(session=session)
        session.refresh_from_db()
        assert session.status == ImpersonationSession.Status.ENDED
        assert session.ended_at is not None


class TestImpersonationSessionStr:
    """Tests for ImpersonationSession.__str__."""

    def test_str_representation(self):
        super_admin = UserFactory(is_super_admin=True, email="admin@test.com")
        society = SocietyFactory(name="Test Society Imp")
        session = start_impersonation(
            impersonator=super_admin,
            society=society,
            reason="Str test",
        )
        result = str(session)
        assert "active" in result.lower()
        assert "Test Society Imp" in result
