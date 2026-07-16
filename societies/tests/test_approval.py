"""Tests for the maker-checker ApprovalRequest model.

These tests validate:
- ApprovalRequest creation with all fields
- ``is_pending`` property
- ``approve()`` sets status, reviewed_by, reviewed_at
- ``reject()`` sets status, reviewed_by, reviewed_at
- ``cancel()`` sets status to CANCELLED
- String representation includes action and entity info
"""

import pytest

from core.test_factories import SocietyFactory
from core.test_factories import UserFactory
from societies.models import ApprovalRequest


pytestmark = pytest.mark.django_db


class TestApprovalRequestCreation:
    """Tests for ApprovalRequest creation."""

    def test_creation_with_all_fields(self):
        society = SocietyFactory()
        maker = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.VOUCHER_POST,
            entity_type="voucher",
            entity_id="42",
            payload={"amount": 1000},
            requested_by=maker,
            reason="Monthly maintenance posting",
        )
        assert request.pk is not None
        assert request.society == society
        assert request.action == ApprovalRequest.Action.VOUCHER_POST
        assert request.entity_type == "voucher"
        assert request.entity_id == "42"
        assert request.payload == {"amount": 1000}
        assert request.requested_by == maker
        assert request.reason == "Monthly maintenance posting"
        assert request.status == ApprovalRequest.Status.PENDING
        assert request.requested_at is not None
        assert request.reviewed_by is None
        assert request.reviewed_at is None
        assert request.review_comment == ""

    def test_creation_with_minimal_fields(self):
        society = SocietyFactory()
        maker = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.CUSTOM,
            entity_type="custom_entity",
            entity_id="1",
            requested_by=maker,
        )
        assert request.pk is not None
        assert request.payload == {}
        assert request.reason == ""
        assert request.status == ApprovalRequest.Status.PENDING

    def test_default_status_is_pending(self):
        society = SocietyFactory()
        maker = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.ROLE_CHANGE,
            entity_type="membership",
            entity_id="5",
            requested_by=maker,
        )
        assert request.status == ApprovalRequest.Status.PENDING


class TestApprovalRequestIsPending:
    """Tests for the is_pending property."""

    def test_new_request_is_pending(self):
        society = SocietyFactory()
        maker = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.VOUCHER_POST,
            entity_type="voucher",
            entity_id="1",
            requested_by=maker,
        )
        assert request.is_pending is True

    def test_approved_request_not_pending(self):
        society = SocietyFactory()
        maker = UserFactory()
        reviewer = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.VOUCHER_POST,
            entity_type="voucher",
            entity_id="1",
            requested_by=maker,
        )
        request.approve(reviewer=reviewer)
        assert request.is_pending is False

    def test_rejected_request_not_pending(self):
        society = SocietyFactory()
        maker = UserFactory()
        reviewer = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.VOUCHER_POST,
            entity_type="voucher",
            entity_id="1",
            requested_by=maker,
        )
        request.reject(reviewer=reviewer)
        assert request.is_pending is False

    def test_cancelled_request_not_pending(self):
        society = SocietyFactory()
        maker = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.VOUCHER_POST,
            entity_type="voucher",
            entity_id="1",
            requested_by=maker,
        )
        request.cancel()
        assert request.is_pending is False


class TestApprovalRequestApprove:
    """Tests for ApprovalRequest.approve()."""

    def test_approve_sets_status_to_approved(self):
        society = SocietyFactory()
        maker = UserFactory()
        reviewer = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.VOUCHER_POST,
            entity_type="voucher",
            entity_id="1",
            requested_by=maker,
        )
        request.approve(reviewer=reviewer)
        assert request.status == ApprovalRequest.Status.APPROVED

    def test_approve_sets_reviewed_by(self):
        society = SocietyFactory()
        maker = UserFactory()
        reviewer = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.VOUCHER_POST,
            entity_type="voucher",
            entity_id="1",
            requested_by=maker,
        )
        request.approve(reviewer=reviewer)
        assert request.reviewed_by == reviewer

    def test_approve_sets_reviewed_at(self):
        society = SocietyFactory()
        maker = UserFactory()
        reviewer = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.VOUCHER_POST,
            entity_type="voucher",
            entity_id="1",
            requested_by=maker,
        )
        assert request.reviewed_at is None
        request.approve(reviewer=reviewer)
        assert request.reviewed_at is not None

    def test_approve_sets_comment(self):
        society = SocietyFactory()
        maker = UserFactory()
        reviewer = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.VOUCHER_POST,
            entity_type="voucher",
            entity_id="1",
            requested_by=maker,
        )
        request.approve(reviewer=reviewer, comment="Looks good")
        assert request.review_comment == "Looks good"

    def test_approve_persists_to_database(self):
        society = SocietyFactory()
        maker = UserFactory()
        reviewer = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.VOUCHER_POST,
            entity_type="voucher",
            entity_id="1",
            requested_by=maker,
        )
        request.approve(reviewer=reviewer, comment="Approved")
        request.refresh_from_db()
        assert request.status == ApprovalRequest.Status.APPROVED
        assert request.reviewed_by == reviewer
        assert request.reviewed_at is not None
        assert request.review_comment == "Approved"


class TestApprovalRequestReject:
    """Tests for ApprovalRequest.reject()."""

    def test_reject_sets_status_to_rejected(self):
        society = SocietyFactory()
        maker = UserFactory()
        reviewer = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.VOUCHER_POST,
            entity_type="voucher",
            entity_id="1",
            requested_by=maker,
        )
        request.reject(reviewer=reviewer)
        assert request.status == ApprovalRequest.Status.REJECTED

    def test_reject_sets_reviewed_by(self):
        society = SocietyFactory()
        maker = UserFactory()
        reviewer = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.VOUCHER_POST,
            entity_type="voucher",
            entity_id="1",
            requested_by=maker,
        )
        request.reject(reviewer=reviewer)
        assert request.reviewed_by == reviewer

    def test_reject_sets_reviewed_at(self):
        society = SocietyFactory()
        maker = UserFactory()
        reviewer = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.VOUCHER_POST,
            entity_type="voucher",
            entity_id="1",
            requested_by=maker,
        )
        assert request.reviewed_at is None
        request.reject(reviewer=reviewer)
        assert request.reviewed_at is not None

    def test_reject_sets_comment(self):
        society = SocietyFactory()
        maker = UserFactory()
        reviewer = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.VOUCHER_POST,
            entity_type="voucher",
            entity_id="1",
            requested_by=maker,
        )
        request.reject(reviewer=reviewer, comment="Insufficient documentation")
        assert request.review_comment == "Insufficient documentation"

    def test_reject_persists_to_database(self):
        society = SocietyFactory()
        maker = UserFactory()
        reviewer = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.VOUCHER_POST,
            entity_type="voucher",
            entity_id="1",
            requested_by=maker,
        )
        request.reject(reviewer=reviewer, comment="Rejected")
        request.refresh_from_db()
        assert request.status == ApprovalRequest.Status.REJECTED
        assert request.reviewed_by == reviewer
        assert request.reviewed_at is not None
        assert request.review_comment == "Rejected"


class TestApprovalRequestCancel:
    """Tests for ApprovalRequest.cancel()."""

    def test_cancel_sets_status_to_cancelled(self):
        society = SocietyFactory()
        maker = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.VOUCHER_POST,
            entity_type="voucher",
            entity_id="1",
            requested_by=maker,
        )
        request.cancel()
        assert request.status == ApprovalRequest.Status.CANCELLED

    def test_cancel_persists_to_database(self):
        society = SocietyFactory()
        maker = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.VOUCHER_POST,
            entity_type="voucher",
            entity_id="1",
            requested_by=maker,
        )
        request.cancel()
        request.refresh_from_db()
        assert request.status == ApprovalRequest.Status.CANCELLED

    def test_cancel_with_requester(self):
        society = SocietyFactory()
        maker = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.VOUCHER_POST,
            entity_type="voucher",
            entity_id="1",
            requested_by=maker,
        )
        request.cancel(requester=maker)
        assert request.status == ApprovalRequest.Status.CANCELLED


class TestApprovalRequestStr:
    """Tests for ApprovalRequest.__str__."""

    def test_str_includes_action_display(self):
        society = SocietyFactory()
        maker = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.VOUCHER_POST,
            entity_type="voucher",
            entity_id="42",
            requested_by=maker,
        )
        result = str(request)
        assert "Voucher Post" in result
        assert "voucher:42" in result
        assert "pending" in result

    def test_str_includes_status(self):
        society = SocietyFactory()
        maker = UserFactory()
        reviewer = UserFactory()
        request = ApprovalRequest.objects.create(
            society=society,
            action=ApprovalRequest.Action.OWNERSHIP_TRANSFER,
            entity_type="society",
            entity_id="1",
            requested_by=maker,
        )
        request.approve(reviewer=reviewer)
        result = str(request)
        assert "approved" in result


class TestApprovalRequestActions:
    """Tests for ApprovalRequest.Action choices."""

    def test_key_actions_exist(self):
        actions = {choice.value for choice in ApprovalRequest.Action}
        assert "voucher_post" in actions
        assert "voucher_reverse" in actions
        assert "ownership_transfer" in actions
        assert "role_change" in actions
        assert "membership_deactivate" in actions
        assert "bulk_billing" in actions
        assert "year_end_close" in actions
        assert "custom" in actions
