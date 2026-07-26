"""Tests for the tabbed Society Admin page and its POST-only update views.

Covers:
  - SocietyAdminView (GET): permissions, context data, tab identifiers
  - SocietySettingsUpdateView (POST): SocietyConfig updates, permission denial
  - SocietyProfileUpdateView (POST): Society profile updates, permission denial
  - SocietyConfigForm / SocietyProfileForm: field-level validation

The test style mirrors ``housing/tests/test_views.py``: pytest-django with
``pytestmark = pytest.mark.django_db``, function-scoped ``client``/``user``
fixtures and the session-scoped ``society`` fixture from ``conftest.py``.
"""

from decimal import Decimal
from http import HTTPStatus

import pytest
from django.urls import reverse
from django.utils import timezone

from housing.forms import SocietyConfigForm
from housing.forms import SocietyProfileForm
from onboarding.models import MigrationAuditLog
from onboarding.models import OnboardingWizard
from onboarding.models import UploadBatch
from onboarding.models import WizardStepLog
from societies.models import Membership
from societies.models import SocietyConfig

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _admin_url(society):
    return reverse("housing:society-admin", kwargs={"pk": society.pk})


def _settings_url(society):
    return reverse("housing:society-settings-update", kwargs={"pk": society.pk})


def _profile_url(society):
    return reverse("housing:society-profile-update", kwargs={"pk": society.pk})


# ---------------------------------------------------------------------------
# POST-data helpers
# ---------------------------------------------------------------------------

def _valid_config_data(**overrides):
    """Return a valid POST payload for :class:`SocietyConfigForm`."""
    data = {
        "share_value": "150.00",
        "default_share_count": "5",
        "entrance_fee": "1000.00",
        "transfer_fee": "250.00",
        "premium_amount": "50.00",
        "allow_multiple_nominees": "on",
        "require_approval": "on",
        "auto_generate_vouchers": "on",
    }
    data.update(overrides)
    return data


def _valid_profile_data(**overrides):
    """Return a valid POST payload for :class:`SocietyProfileForm`."""
    data = {
        "name": "Updated Society Name",
        "registration_number": "REG-NEW-123",
        "address": "123 New Address St",
    }
    data.update(overrides)
    return data


def _make_membership(user, society, role):
    """Create or update a membership for ``user`` in ``society``.

    Uses ``update_or_create`` so repeated calls within a session-scoped
    society fixture don't trip the unique ``(user, society)`` constraint
    and don't leak a higher-privileged role from a previous test.
    """
    membership, _created = Membership.objects.update_or_create(
        user=user,
        society=society,
        defaults={"role": role, "is_active": True},
    )
    return membership


# ===========================================================================
# SocietyAdminView (GET)
# ===========================================================================

class TestSocietyAdminView:
    """Tests for the tabbed society admin page (GET)."""

    # --- Permission / access control ----------------------------------------

    def test_owner_get_200(self, client, user, society):
        _make_membership(user, society, Membership.Role.OWNER)
        client.force_login(user)

        response = client.get(_admin_url(society))

        assert response.status_code == HTTPStatus.OK
        assert "housing/society_admin.html" in [t.name for t in response.templates]

    def test_admin_get_200(self, client, user, society):
        _make_membership(user, society, Membership.Role.ADMIN)
        client.force_login(user)

        response = client.get(_admin_url(society))

        assert response.status_code == HTTPStatus.OK

    def test_member_get_allowed(self, client, user, society):
        # The ``member`` role grants ``*.view`` which matches
        # ``societies.membership.view``, so members can view the admin page
        # (the settings/profile tabs render an access-denied notice instead).
        _make_membership(user, society, Membership.Role.MEMBER)
        client.force_login(user)

        response = client.get(_admin_url(society))

        assert response.status_code == HTTPStatus.OK

    def test_viewer_get_allowed(self, client, user, society):
        # Same rationale as member — ``*.view`` grants view access.
        _make_membership(user, society, Membership.Role.VIEWER)
        client.force_login(user)

        response = client.get(_admin_url(society))

        assert response.status_code == HTTPStatus.OK

    def test_accountant_get_forbidden(self, client, user, society):
        # Accountant only has ``housing.membership.view`` (not
        # ``societies.membership.view``) and no ``*.view`` wildcard.
        _make_membership(user, society, Membership.Role.ACCOUNTANT)
        client.force_login(user)

        response = client.get(_admin_url(society))

        assert response.status_code == HTTPStatus.FORBIDDEN

    def test_no_membership_get_forbidden(self, client, user, society):
        client.force_login(user)

        response = client.get(_admin_url(society))

        assert response.status_code == HTTPStatus.FORBIDDEN

    def test_anonymous_get_forbidden(self, client, society):
        # ``dispatch`` is overridden and calls ``has_permission`` before the
        # ``LoginRequiredMixin`` redirect can fire, so anonymous → 403.
        response = client.get(_admin_url(society))

        assert response.status_code == HTTPStatus.FORBIDDEN

    # --- Context: society config & forms ------------------------------------

    def test_context_has_society_config(self, client, user, society):
        _make_membership(user, society, Membership.Role.ADMIN)
        client.force_login(user)

        response = client.get(_admin_url(society))

        assert response.status_code == HTTPStatus.OK
        config = response.context["society_config"]
        assert isinstance(config, SocietyConfig)
        assert config.society_id == society.pk

    def test_context_has_society_config_form(self, client, user, society):
        _make_membership(user, society, Membership.Role.ADMIN)
        client.force_login(user)

        response = client.get(_admin_url(society))

        assert response.status_code == HTTPStatus.OK
        assert isinstance(
            response.context["society_config_form"], SocietyConfigForm
        )

    def test_context_has_society_profile_form_prefilled(self, client, user, society):
        _make_membership(user, society, Membership.Role.ADMIN)
        client.force_login(user)

        response = client.get(_admin_url(society))

        assert response.status_code == HTTPStatus.OK
        form = response.context["society_profile_form"]
        assert isinstance(form, SocietyProfileForm)
        assert form.initial["name"] == society.name
        assert form.initial["registration_number"] == (
            society.registration_number or ""
        )
        assert form.initial["address"] == (society.address or "")

    # --- Context: onboarding wizard -----------------------------------------

    def test_context_onboarding_wizard_none(self, client, user, society):
        _make_membership(user, society, Membership.Role.ADMIN)
        client.force_login(user)

        response = client.get(_admin_url(society))

        assert response.status_code == HTTPStatus.OK
        assert response.context["onboarding_wizard"] is None
        assert response.context["onboarding_progress_percent"] == 0

    def test_context_onboarding_wizard_instance(self, client, user, society):
        _make_membership(user, society, Membership.Role.ADMIN)
        wizard = OnboardingWizard.objects.unscoped().create(
            society=society,
            current_step=14,
            status=OnboardingWizard.Status.IN_PROGRESS,
        )
        client.force_login(user)

        response = client.get(_admin_url(society))

        assert response.status_code == HTTPStatus.OK
        assert response.context["onboarding_wizard"] is not None
        assert response.context["onboarding_wizard"].pk == wizard.pk
        # 14 / 28 * 100 == 50
        assert response.context["onboarding_progress_percent"] == 50

    def test_context_onboarding_progress_clamped_to_100(self, client, user, society):
        _make_membership(user, society, Membership.Role.ADMIN)
        OnboardingWizard.objects.unscoped().create(
            society=society,
            current_step=30,  # exceeds the 28-step total
            status=OnboardingWizard.Status.IN_PROGRESS,
        )
        client.force_login(user)

        response = client.get(_admin_url(society))

        assert response.status_code == HTTPStatus.OK
        assert response.context["onboarding_progress_percent"] == 100

    # --- Context: onboarding dashboard (steps, batches, events) -------------

    def test_context_onboarding_steps_empty_without_wizard(self, client, user, society):
        _make_membership(user, society, Membership.Role.ADMIN)
        client.force_login(user)

        response = client.get(_admin_url(society))

        assert response.status_code == HTTPStatus.OK
        assert response.context["onboarding_steps"] == []
        assert response.context["onboarding_completed_steps"] == 0
        assert response.context["onboarding_total_steps"] == 28

    def test_context_onboarding_steps_with_wizard(self, client, user, society):
        _make_membership(user, society, Membership.Role.ADMIN)
        wizard = OnboardingWizard.objects.unscoped().create(
            society=society,
            current_step=5,
            status=OnboardingWizard.Status.IN_PROGRESS,
        )
        # Mark a couple of steps as completed via WizardStepLog.
        WizardStepLog.objects.create(
            wizard=wizard,
            step_number=1,
            step_name="Society Details",
            status=WizardStepLog.Status.COMPLETED,
            completed_at=timezone.now(),
        )
        WizardStepLog.objects.create(
            wizard=wizard,
            step_number=2,
            step_name="Society Type",
            status=WizardStepLog.Status.COMPLETED,
            completed_at=timezone.now(),
        )
        client.force_login(user)

        response = client.get(_admin_url(society))

        assert response.status_code == HTTPStatus.OK
        steps = response.context["onboarding_steps"]
        assert len(steps) == 28
        # Steps 1 and 2 are completed.
        assert steps[0]["state"] == "completed"
        assert steps[1]["state"] == "completed"
        # Step 5 is the current step.
        assert steps[4]["state"] == "current"
        # Step 6 is pending.
        assert steps[5]["state"] == "pending"
        assert response.context["onboarding_completed_steps"] == 2

    def test_context_onboarding_upload_batches(self, client, user, society):
        _make_membership(user, society, Membership.Role.ADMIN)
        wizard = OnboardingWizard.objects.unscoped().create(
            society=society,
            current_step=15,
            status=OnboardingWizard.Status.IN_PROGRESS,
        )
        UploadBatch.objects.unscoped().create(
            wizard=wizard,
            society=society,
            template_type=UploadBatch.TemplateType.TRIAL_BALANCE,
            file_name="tb.xlsx",
            row_count=42,
            status=UploadBatch.Status.UPLOADED,
        )
        client.force_login(user)

        response = client.get(_admin_url(society))

        assert response.status_code == HTTPStatus.OK
        batches = response.context["onboarding_upload_batches"]
        assert len(batches) == 1
        assert batches[0]["template_type"] == "TRIAL_BALANCE"
        assert batches[0]["row_count"] == 42
        assert response.context["onboarding_uploaded_count"] == 1
        assert response.context["onboarding_total_staging_rows"] == 42

    def test_context_onboarding_migration_events(self, client, user, society):
        _make_membership(user, society, Membership.Role.ADMIN)
        wizard = OnboardingWizard.objects.unscoped().create(
            society=society,
            current_step=10,
            status=OnboardingWizard.Status.IN_PROGRESS,
        )
        MigrationAuditLog.objects.create(
            wizard=wizard,
            society=society,
            action="WIZARD_STARTED",
        )
        client.force_login(user)

        response = client.get(_admin_url(society))

        assert response.status_code == HTTPStatus.OK
        events = response.context["onboarding_migration_events"]
        assert len(events) == 1
        assert events[0]["action"] == "WIZARD_STARTED"

    # --- Context: quick stats -----------------------------------------------

    def test_context_has_stats(self, client, user, society):
        _make_membership(user, society, Membership.Role.ADMIN)
        client.force_login(user)

        response = client.get(_admin_url(society))

        assert response.status_code == HTTPStatus.OK
        assert response.context["total_members"] == 0
        assert response.context["total_users"] == 1
        assert response.context["active_users"] == 1
        # User.email_verified defaults to False → 1 pending verification.
        assert response.context["pending_verifications"] == 1

    # --- Template: tab identifiers ------------------------------------------

    def test_response_contains_tab_identifiers(self, client, user, society):
        _make_membership(user, society, Membership.Role.ADMIN)
        client.force_login(user)

        response = client.get(_admin_url(society))
        content = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        for tab_id in (
            "overview",
            "users",
            "settings",
            "profile",
            "onboarding",
            "integrations",
        ):
            assert f'id="{tab_id}"' in content


# ===========================================================================
# SocietySettingsUpdateView (POST)
# ===========================================================================

class TestSocietySettingsUpdateView:
    """Tests for the POST-only SocietyConfig (share/fee) update view."""

    def test_admin_post_valid_updates_db(self, client, user, society):
        _make_membership(user, society, Membership.Role.ADMIN)
        client.force_login(user)
        config = society.share_config

        response = client.post(_settings_url(society), data=_valid_config_data())

        assert response.status_code == HTTPStatus.FOUND
        assert response.url == _admin_url(society)
        config.refresh_from_db()
        assert config.share_value == Decimal("150.00")
        assert config.default_share_count == 5
        assert config.entrance_fee == Decimal("1000.00")
        assert config.transfer_fee == Decimal("250.00")
        assert config.premium_amount == Decimal("50.00")
        assert config.allow_multiple_nominees is True
        assert config.require_approval is True
        assert config.auto_generate_vouchers is True

    def test_owner_post_valid_updates_db(self, client, user, society):
        _make_membership(user, society, Membership.Role.OWNER)
        client.force_login(user)
        config = society.share_config

        response = client.post(_settings_url(society), data=_valid_config_data())

        assert response.status_code == HTTPStatus.FOUND
        config.refresh_from_db()
        assert config.share_value == Decimal("150.00")

    def test_member_post_forbidden_no_update(self, client, user, society):
        _make_membership(user, society, Membership.Role.MEMBER)
        client.force_login(user)
        # Re-fetch the config straight from the DB so the "before" snapshot
        # is independent of any in-memory caching done by previous tests
        # (the ``society`` fixture is session-scoped and shared).
        config = SocietyConfig.objects.get(society=society)
        original_share_value = config.share_value

        # ``raise_request_exception=False`` lets the test client convert the
        # ``PermissionDenied`` raised by the view into a 403 response instead
        # of propagating it as a test error (Django 5.x default behaviour).
        response = client.post(
            _settings_url(society),
            data=_valid_config_data(),
            raise_request_exception=False,
        )

        assert response.status_code == HTTPStatus.FORBIDDEN
        config.refresh_from_db()
        assert config.share_value == original_share_value

    def test_anonymous_post_forbidden(self, client, society):
        response = client.post(_settings_url(society), data=_valid_config_data())

        assert response.status_code == HTTPStatus.FORBIDDEN

    def test_admin_post_invalid_data_no_update(self, client, user, society):
        _make_membership(user, society, Membership.Role.ADMIN)
        client.force_login(user)
        # Re-fetch the config straight from the DB so the "before" snapshot
        # is independent of any in-memory caching done by previous tests
        # (the ``society`` fixture is session-scoped and shared).
        config = SocietyConfig.objects.get(society=society)
        original_share_value = config.share_value

        response = client.post(
            _settings_url(society),
            data=_valid_config_data(share_value="-100.00"),
        )

        # Invalid form → redirect back with error flash, DB untouched.
        assert response.status_code == HTTPStatus.FOUND
        config.refresh_from_db()
        assert config.share_value == original_share_value


# ===========================================================================
# SocietyProfileUpdateView (POST)
# ===========================================================================

class TestSocietyProfileUpdateView:
    """Tests for the POST-only society profile (name/registration/address) update."""

    def test_admin_post_valid_updates_db(self, client, user, society):
        _make_membership(user, society, Membership.Role.ADMIN)
        client.force_login(user)

        response = client.post(_profile_url(society), data=_valid_profile_data())

        assert response.status_code == HTTPStatus.FOUND
        assert response.url == _admin_url(society)
        society.refresh_from_db()
        assert society.name == "Updated Society Name"
        assert society.registration_number == "REG-NEW-123"
        assert society.address == "123 New Address St"

    def test_owner_post_valid_updates_db(self, client, user, society):
        _make_membership(user, society, Membership.Role.OWNER)
        client.force_login(user)

        response = client.post(_profile_url(society), data=_valid_profile_data())

        assert response.status_code == HTTPStatus.FOUND
        society.refresh_from_db()
        assert society.name == "Updated Society Name"

    def test_member_post_forbidden_no_update(self, client, user, society):
        _make_membership(user, society, Membership.Role.MEMBER)
        client.force_login(user)
        # Re-fetch the society straight from the DB so the "before" snapshot
        # is independent of any in-memory caching done by previous tests
        # (the ``society`` fixture is session-scoped and shared).
        society.refresh_from_db()
        original_name = society.name

        # ``raise_request_exception=False`` lets the test client convert the
        # ``PermissionDenied`` raised by the view into a 403 response instead
        # of propagating it as a test error (Django 5.x default behaviour).
        response = client.post(
            _profile_url(society),
            data=_valid_profile_data(),
            raise_request_exception=False,
        )

        assert response.status_code == HTTPStatus.FORBIDDEN
        society.refresh_from_db()
        assert society.name == original_name

    def test_anonymous_post_forbidden(self, client, society):
        response = client.post(_profile_url(society), data=_valid_profile_data())

        assert response.status_code == HTTPStatus.FORBIDDEN

    def test_admin_post_partial_data_succeeds(self, client, user, society):
        _make_membership(user, society, Membership.Role.ADMIN)
        client.force_login(user)

        response = client.post(
            _profile_url(society), data={"name": "Only Name Society"}
        )

        assert response.status_code == HTTPStatus.FOUND
        society.refresh_from_db()
        assert society.name == "Only Name Society"


# ===========================================================================
# Form validation tests
# ===========================================================================

class TestSocietyConfigForm:
    """Validation tests for :class:`SocietyConfigForm`."""

    def test_valid_data(self):
        form = SocietyConfigForm(data=_valid_config_data())
        assert form.is_valid(), form.errors

    def test_negative_share_value_invalid(self):
        form = SocietyConfigForm(data=_valid_config_data(share_value="-100.00"))
        assert not form.is_valid()
        # The non-negative check lives in the form's ``clean()`` (cross-field
        # validation), so the error is raised under ``__all__`` rather than
        # attached to the ``share_value`` field itself.
        assert "__all__" in form.errors or "share_value" in form.errors
        assert any(
            "non-negative" in str(msg).lower()
            for msgs in form.errors.values()
            for msg in msgs
        )


class TestSocietyProfileForm:
    """Validation tests for :class:`SocietyProfileForm`."""

    def test_valid_data(self):
        form = SocietyProfileForm(data=_valid_profile_data())
        assert form.is_valid(), form.errors

    def test_empty_name_invalid(self):
        form = SocietyProfileForm(data=_valid_profile_data(name=""))
        assert not form.is_valid()
        assert "name" in form.errors

    def test_only_name_valid(self):
        # registration_number and address are optional (required=False).
        form = SocietyProfileForm(data={"name": "Only Name"})
        assert form.is_valid(), form.errors
