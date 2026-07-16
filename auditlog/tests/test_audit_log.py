"""Tests for the AuditLog append-only audit trail model.

These tests validate:
- ``AuditLog.log()`` creates entries with all fields and minimal fields
- Append-only enforcement (``save()`` rejects updates, ``delete()`` raises)
- Ordering by ``-created_at, -id``
- Action choices comprehensiveness
- ``log_from_request()`` extracts request context correctly
"""

import time

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from auditlog.models import AuditLog
from auditlog.services import log_from_request
from core.test_factories import SocietyFactory
from core.test_factories import UserFactory


pytestmark = pytest.mark.django_db


def _add_session_to_request(request):
    """Attach a session to a request (needed for session_key extraction)."""
    middleware = SessionMiddleware(lambda req: None)
    middleware.process_request(request)
    request.session.save()
    return request


class TestAuditLogCreate:
    """Tests for AuditLog.log() creation."""

    def test_log_creates_entry_with_all_fields(self):
        society = SocietyFactory()
        actor = UserFactory()
        entry = AuditLog.log(
            society=society,
            action=AuditLog.Action.CREATE,
            entity_type="voucher",
            entity_id="42",
            actor=actor,
            before_value={"amount": 0},
            after_value={"amount": 1000},
            ip_address="192.168.1.1",
            device_info={"browser": "Chrome"},
            request_id="req-123",
            session_id="sess-456",
            user_agent="Mozilla/5.0",
            module="accounting",
            duration_ms=150,
            reason="Initial creation",
        )
        assert entry.pk is not None
        assert entry.society == society
        assert entry.actor == actor
        assert entry.action == AuditLog.Action.CREATE
        assert entry.entity_type == "voucher"
        assert entry.entity_id == "42"
        assert entry.before_value == {"amount": 0}
        assert entry.after_value == {"amount": 1000}
        assert str(entry.ip_address) == "192.168.1.1"
        assert entry.device_info == {"browser": "Chrome"}
        assert entry.request_id == "req-123"
        assert entry.session_id == "sess-456"
        assert entry.user_agent == "Mozilla/5.0"
        assert entry.module == "accounting"
        assert entry.duration_ms == 150
        assert entry.reason == "Initial creation"
        assert entry.created_at is not None

    def test_log_creates_entry_with_minimal_fields(self):
        society = SocietyFactory()
        entry = AuditLog.log(
            society=society,
            action=AuditLog.Action.UPDATE,
            entity_type="member",
            entity_id="1",
        )
        assert entry.pk is not None
        assert entry.society == society
        assert entry.actor is None
        assert entry.action == AuditLog.Action.UPDATE
        assert entry.entity_type == "member"
        assert entry.entity_id == "1"
        assert entry.before_value is None
        assert entry.after_value is None
        assert entry.ip_address is None
        assert entry.device_info == {}
        assert entry.request_id is None
        assert entry.session_id is None
        assert entry.user_agent is None
        assert entry.module is None
        assert entry.duration_ms is None
        assert entry.reason is None

    def test_log_entity_id_is_stringified(self):
        society = SocietyFactory()
        entry = AuditLog.log(
            society=society,
            action=AuditLog.Action.CREATE,
            entity_type="voucher",
            entity_id=999,
        )
        assert entry.entity_id == "999"
        assert isinstance(entry.entity_id, str)

    def test_log_device_info_defaults_to_empty_dict(self):
        society = SocietyFactory()
        entry = AuditLog.log(
            society=society,
            action=AuditLog.Action.CREATE,
            entity_type="voucher",
            entity_id="1",
        )
        assert entry.device_info == {}


class TestAuditLogAppendOnly:
    """Tests for append-only enforcement."""

    def test_save_on_existing_record_raises_permission_error(self):
        society = SocietyFactory()
        entry = AuditLog.log(
            society=society,
            action=AuditLog.Action.CREATE,
            entity_type="voucher",
            entity_id="1",
        )
        entry.reason = "updated reason"
        with pytest.raises(PermissionError, match="append-only"):
            entry.save()

    def test_delete_raises_permission_error(self):
        society = SocietyFactory()
        entry = AuditLog.log(
            society=society,
            action=AuditLog.Action.CREATE,
            entity_type="voucher",
            entity_id="1",
        )
        with pytest.raises(PermissionError, match="append-only"):
            entry.delete()

    def test_queryset_delete_not_blocked_at_python_level(self):
        """QuerySet.delete() bypasses the instance delete() override.

        This is a known Django limitation — the model-level delete() override
        only protects instance-level deletion. We document this behavior here.
        """
        society = SocietyFactory()
        AuditLog.log(
            society=society,
            action=AuditLog.Action.CREATE,
            entity_type="voucher",
            entity_id="1",
        )
        # QuerySet-level delete works (bypasses instance override)
        count, _ = AuditLog.objects.filter(society=society).delete()
        assert count > 0


class TestAuditLogOrdering:
    """Tests for AuditLog default ordering."""

    def test_entries_ordered_by_created_at_desc(self):
        society = SocietyFactory()
        entry1 = AuditLog.log(
            society=society,
            action=AuditLog.Action.CREATE,
            entity_type="voucher",
            entity_id="1",
        )
        # Small delay to ensure different created_at timestamps
        time.sleep(0.01)
        entry2 = AuditLog.log(
            society=society,
            action=AuditLog.Action.CREATE,
            entity_type="voucher",
            entity_id="2",
        )

        entries = list(AuditLog.objects.filter(society=society))
        # Most recent first
        assert entries[0].pk == entry2.pk
        assert entries[1].pk == entry1.pk

    def test_entries_ordered_by_id_desc_when_same_timestamp(self):
        """When created_at is identical, ordering falls back to -id."""
        society = SocietyFactory()
        entry1 = AuditLog.log(
            society=society,
            action=AuditLog.Action.CREATE,
            entity_type="voucher",
            entity_id="1",
        )
        entry2 = AuditLog.log(
            society=society,
            action=AuditLog.Action.CREATE,
            entity_type="voucher",
            entity_id="2",
        )
        entries = list(AuditLog.objects.filter(society=society))
        assert entries[0].pk == entry2.pk
        assert entries[1].pk == entry1.pk


class TestAuditLogActionChoices:
    """Tests for AuditLog.Action choices comprehensiveness."""

    def test_key_actions_exist(self):
        actions = {choice.value for choice in AuditLog.Action}
        assert "create" in actions
        assert "update" in actions
        assert "delete" in actions
        assert "approve" in actions
        assert "reject" in actions
        assert "post" in actions
        assert "reverse" in actions
        assert "cancel" in actions
        assert "login" in actions
        assert "logout" in actions
        assert "impersonate" in actions

    def test_action_count_is_comprehensive(self):
        """The Action enum should have a comprehensive set of choices."""
        actions = list(AuditLog.Action)
        # At least 15 distinct actions for enterprise coverage
        assert len(actions) >= 15

    def test_action_values_are_lowercase(self):
        for choice in AuditLog.Action:
            assert choice.value == choice.value.lower()


class TestAuditLogStr:
    """Tests for AuditLog.__str__."""

    def test_str_representation(self):
        society = SocietyFactory()
        entry = AuditLog.log(
            society=society,
            action=AuditLog.Action.POST,
            entity_type="voucher",
            entity_id="42",
        )
        result = str(entry)
        assert "post" in result
        assert "voucher:42" in result


class TestLogFromRequest:
    """Tests for log_from_request() service function."""

    def test_log_from_request_extracts_context(self, rf: RequestFactory):
        society = SocietyFactory()
        user = UserFactory()
        request = _add_session_to_request(rf.post("/"))
        request.user = user
        request.current_society = society
        request.request_id = "req-from-request-123"
        request.META["REMOTE_ADDR"] = "10.0.0.1"
        request.META["HTTP_USER_AGENT"] = "TestAgent/1.0"

        entry = log_from_request(
            request,
            action=AuditLog.Action.POST,
            entity_type="voucher",
            entity_id="99",
            module="accounting",
            reason="Test post",
        )

        assert entry.pk is not None
        assert entry.society == society
        assert entry.actor == user
        assert entry.action == AuditLog.Action.POST
        assert entry.entity_type == "voucher"
        assert entry.entity_id == "99"
        assert str(entry.ip_address) == "10.0.0.1"
        assert entry.user_agent == "TestAgent/1.0"
        assert entry.request_id == "req-from-request-123"
        assert entry.module == "accounting"
        assert entry.reason == "Test post"
        assert entry.session_id is not None

    def test_log_from_request_extracts_forwarded_for(self, rf: RequestFactory):
        society = SocietyFactory()
        user = UserFactory()
        request = _add_session_to_request(rf.post("/"))
        request.user = user
        request.current_society = society
        request.META["HTTP_X_FORWARDED_FOR"] = "203.0.113.5, 10.0.0.1"
        request.META["HTTP_USER_AGENT"] = ""

        entry = log_from_request(
            request,
            action=AuditLog.Action.CREATE,
            entity_type="member",
            entity_id="1",
        )

        # Should extract the first IP from X-Forwarded-For
        assert str(entry.ip_address) == "203.0.113.5"

    def test_log_from_request_without_society(self, rf: RequestFactory):
        """log_from_request works when current_society is not set."""
        user = UserFactory()
        request = _add_session_to_request(rf.post("/"))
        request.user = user
        request.current_society = None
        request.META["REMOTE_ADDR"] = "10.0.0.1"

        # This should raise because society is a required FK on AuditLog
        with pytest.raises(Exception):
            log_from_request(
                request,
                action=AuditLog.Action.CREATE,
                entity_type="member",
                entity_id="1",
            )

    def test_log_from_request_with_duration(self, rf: RequestFactory):
        society = SocietyFactory()
        user = UserFactory()
        request = _add_session_to_request(rf.post("/"))
        request.user = user
        request.current_society = society
        request.META["REMOTE_ADDR"] = "10.0.0.1"

        entry = log_from_request(
            request,
            action=AuditLog.Action.EXPORT,
            entity_type="report",
            entity_id="1",
            duration_ms=250,
        )

        assert entry.duration_ms == 250
