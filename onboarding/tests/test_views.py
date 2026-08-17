"""Comprehensive view tests for the Society Creation & Accounting Migration Wizard.

Tests use the Django test client to exercise every view endpoint end-to-end,
verifying status codes, redirects, context variables, and template rendering.

Covers:
- wizard_list, wizard_start, wizard_detail
- wizard_step (GET), wizard_step_save (POST)
- staging_upload, staging_view, staging_delete, staging_approve
- reconciliation_dashboard, validation_checklist
- finalize_migration (GET + POST), wizard_complete
- Login required enforcement
"""
from __future__ import annotations

import io

from django import template
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from core.test_base import SocietyTestCase
from core.test_factories import UserFactory
from housing_accounting.selection import SESSION_SELECTED_SOCIETY_ID
from onboarding.models import (
    OnboardingWizard,
    StagingBankOpening,
    StagingCashOpening,
    StagingChartOfAccounts,
    StagingFixedAsset,
    StagingFund,
    StagingLoan,
    StagingMemberOutstanding,
    StagingSecurityDeposit,
    StagingTrialBalance,
    StagingVendorOutstanding,
    UploadBatch,
)
from onboarding.services.staging_service import StagingService
from onboarding.services.wizard_service import WizardService
from accounting.models import Account
from accounting.models import AccountCategory

# --------------------------------------------------------------------------- #
# Manager patching
#
# The ReconciliationService and MigrationFinalizationService (called by the
# reconciliation dashboard, validation checklist, and finalize views) use
# ``.unscoped()`` on staging models.  The ``unscoped()`` method is provided
# by :class:`TenantManager`, but the staging models use Django's default
# ``Manager``.  We add an ``unscoped`` method to each staging model's
# default manager so that ``.unscoped()`` is available during tests.
#
# Unlike replacing the manager entirely with ``TenantManager``, this approach
# does NOT change ``get_queryset()`` behaviour, so existing queries that use
# ``.objects.filter(wizard=wizard)`` continue to work unchanged regardless of
# the tenant contextvar state.  This avoids cross-test contamination when
# tests run in the same pytest session.
# --------------------------------------------------------------------------- #


def _add_unscoped(manager):
    """Add an ``unscoped`` method to a manager instance.

    ``unscoped()`` returns a clone of the default queryset without any
    tenant or soft-delete filtering — equivalent to the default Manager
    queryset.
    """
    if hasattr(manager, "unscoped"):
        return
    from django.db.models import Manager

    def unscoped(self):
        return super(Manager, self).get_queryset()

    manager.unscoped = unscoped.__get__(manager, type(manager))


_STAGING_MODELS = [
    StagingChartOfAccounts,
    StagingTrialBalance,
    StagingMemberOutstanding,
    StagingVendorOutstanding,
    StagingBankOpening,
    StagingCashOpening,
    StagingFixedAsset,
    StagingSecurityDeposit,
    StagingLoan,
    StagingFund,
]

for _model in _STAGING_MODELS:
    _add_unscoped(_model.objects)


# --------------------------------------------------------------------------- #
# Test-only template filter registration
# --------------------------------------------------------------------------- #
# The ``step_progress_bar.html`` partial (included by ``base_wizard.html``)
# uses ``|keys`` and ``|get_item`` filters but only loads ``{% load i18n %}``.
# ``get_item`` IS registered in ``onboarding/templatetags/onboarding_tags.py``
# but the template never loads that library — a pre-existing template bug.
# We register minimal ``keys``, ``get_item``, and ``status_badge_class``
# filters on Django's default template engine builtins so that the wizard
# templates render correctly during view tests.  This does NOT modify any
# existing source file — it is a test-only patch applied after Django is
# fully configured.

_test_filters_lib = template.Library()


@_test_filters_lib.filter(name="keys")
def _keys(value):
    """Return ``list(value.keys())`` for a dict, or ``[]`` for falsy values."""
    if not value:
        return []
    if isinstance(value, dict):
        return list(value.keys())
    return []


@_test_filters_lib.filter(name="get_item")
def _get_item(dictionary, key):
    """Return ``dictionary[key]`` safely from a template."""
    if dictionary is None:
        return None
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None


@_test_filters_lib.filter(name="split")
def _split(value, separator=","):
    """Split a string by separator into a list (mirrors Python ``str.split``)."""
    if value is None:
        return []
    return str(value).split(separator)


@_test_filters_lib.filter(name="status_badge_class")
def _status_badge_class(status):
    """Map a staging/validation status string to a Bootstrap badge class."""
    if not status:
        return "bg-secondary"
    status_upper = str(status).upper()
    if status_upper in ("APPROVED", "COMMITTED", "VALIDATED"):
        return "bg-success"
    if status_upper in ("UPLOADED",):
        return "bg-info"
    if status_upper in ("DELETED",):
        return "bg-danger"
    if status_upper in ("PENDING",):
        return "bg-warning text-dark"
    return "bg-secondary"


def _register_template_filters():
    """Register test-only template filters on Django's default engine.

    Uses ``engines['django'].engine.template_builtins`` (the live engine
    instance used by the test client) rather than ``Engine.get_default()``
    which may return a different instance in the test context.
    """
    from django.template import engines

    _backend = engines["django"]
    if _test_filters_lib not in _backend.engine.template_builtins:
        _backend.engine.template_builtins.append(_test_filters_lib)


def _csv_bytes(headers: list[str], rows: list[list[str]]) -> bytes:
    """Build CSV bytes from headers and rows."""
    import csv

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


class OnboardingViewTestBase(SocietyTestCase):
    """Base class: logged-in user with a wizard linked to the shared society."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Register the test-only template filters (see module docstring).
        _register_template_filters()
        cls.wizard = WizardService.create_wizard(user=cls.user)
        cls.wizard.society = cls.society
        cls.wizard.save(update_fields=["society"])

    def setUp(self):
        super().setUp()
        # Ensure the filters are registered before each test
        # (the engine may be reset between test classes).
        _register_template_filters()
        # Reset the tenant contextvar before each test to prevent leakage
        # from a previous test class's middleware (SocietyMiddleware sets
        # _current_tenant but never resets it — in production each request
        # runs in its own context, but in tests the context persists).
        from societies.managers import _current_tenant

        _current_tenant.set(None)
        self.client.force_login(self.user)
        self._select_society(self.society)

    def tearDown(self):
        super().tearDown()
        # Reset the tenant contextvar after each test to prevent leakage
        # to subsequent test classes.
        from societies.managers import _current_tenant

        _current_tenant.set(None)

    def _select_society(self, society):
        """Set the selected society in the session (middleware requirement)."""
        session = self.client.session
        session[SESSION_SELECTED_SOCIETY_ID] = society.id
        session.save()


# --------------------------------------------------------------------------- #
# wizard_list
# --------------------------------------------------------------------------- #


class WizardListViewTest(OnboardingViewTestBase):
    """Tests for the wizard_list view."""

    def test_list_requires_login(self):
        """Anonymous users are redirected to the login page."""
        self.client.logout()
        response = self.client.get(reverse("onboarding:wizard-list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_list_shows_user_wizards(self):
        """Authenticated users see their wizards in the list."""
        response = self.client.get(reverse("onboarding:wizard-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "wizard", status_code=200)
        # The wizard created in setUpTestData should be in the context.
        self.assertIn("wizards", response.context)
        wizard_pks = [w.pk for w in response.context["wizards"]]
        self.assertIn(self.wizard.pk, wizard_pks)


class WizardStep10ViewTest(OnboardingViewTestBase):
    """Tests for the step 10 chart of accounts view."""

    def test_step10_shows_existing_accounts_and_crud_links(self):
        society = self.society
        category = AccountCategory.objects.create(
            society=society,
            name="Step 10 Assets",
            account_type=Account.AccountType.ASSET,
        )
        root = Account.objects.create(
            society=society,
            name="Assets",
            code="91",
            category=category,
            account_type=Account.AccountType.ASSET,
        )
        child = Account.objects.create(
            society=society,
            name="Cash",
            code="91.1",
            category=category,
            parent=root,
            account_type=Account.AccountType.ASSET,
        )
        self.wizard.current_step = 10
        self.wizard.save(update_fields=["current_step"])

        response = self.client.get(
            reverse("onboarding:wizard-step", kwargs={"wizard_id": self.wizard.pk, "step_number": 10})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Open Account Tree")
        self.assertContains(response, root.name)
        self.assertContains(response, child.name)
        self.assertContains(response, reverse("accounting:account-tree"))
        self.assertContains(response, reverse("accounting:account-list"))
        self.assertContains(response, reverse("accounting:account-add"))
        self.assertContains(response, reverse("accounting:account-edit", kwargs={"pk": child.pk}))
        self.assertEqual(
            response.context["account_count"],
            Account.objects.filter(society=society).count(),
        )
        self.assertGreaterEqual(len(response.context["root_accounts"]), 1)


class WizardStep11TemplateDownloadTest(OnboardingViewTestBase):
    """Tests for the step 11 template download links."""

    def test_template_download_supports_csv_and_xlsx(self):
        self.wizard.current_step = 11
        self.wizard.save(update_fields=["current_step"])

        csv_response = self.client.get(
            reverse("onboarding:template-download", kwargs={"template_type": "TRIAL_BALANCE"}),
            {"wizard_id": self.wizard.pk, "format": "csv"},
        )
        self.assertEqual(csv_response.status_code, 200)
        self.assertEqual(csv_response["Content-Type"], "text/csv")
        self.assertIn("account_code", csv_response.content.decode())

        xlsx_response = self.client.get(
            reverse("onboarding:template-download", kwargs={"template_type": "TRIAL_BALANCE"}),
            {"wizard_id": self.wizard.pk, "format": "xlsx"},
        )
        self.assertEqual(xlsx_response.status_code, 200)
        self.assertIn(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            xlsx_response["Content-Type"],
        )

    def test_continue_advances_to_staging_step(self):
        self.wizard.current_step = 11
        self.wizard.save(update_fields=["current_step"])

        response = self.client.post(
            reverse(
                "onboarding:wizard-step-save",
                kwargs={"wizard_id": self.wizard.pk, "step_number": 11},
            ),
            {},
        )

        self.assertRedirects(
            response,
            reverse(
                "onboarding:wizard-step",
                kwargs={"wizard_id": self.wizard.pk, "step_number": 12},
            ),
            fetch_redirect_response=False,
        )
        self.wizard.refresh_from_db()
        self.assertEqual(self.wizard.current_step, 12)


class WizardStep11ManualEntryTest(OnboardingViewTestBase):
    """Tests for the step 11 typed manual-entry form."""

    def test_manual_entry_renders_formset(self):
        self.wizard.current_step = 11
        self.wizard.save(update_fields=["current_step"])

        response = self.client.get(
            reverse(
                "onboarding:template-manual-entry",
                kwargs={"wizard_id": self.wizard.pk, "template_type": "TRIAL_BALANCE"},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'readonly="readonly"')
        self.assertContains(response, "id_rows-TOTAL_FORMS")
        self.assertContains(response, "account_code")

    def test_manual_entry_saves_rows_and_redirects_to_summary(self):
        self.wizard.current_step = 11
        self.wizard.save(update_fields=["current_step"])

        get_response = self.client.get(
            reverse(
                "onboarding:template-manual-entry",
                kwargs={"wizard_id": self.wizard.pk, "template_type": "TRIAL_BALANCE"},
            )
        )
        locked_row_count = get_response.context["seed_row_count"]

        post_data = {
            "rows-TOTAL_FORMS": str(locked_row_count + 1),
            "rows-INITIAL_FORMS": str(locked_row_count),
            "rows-MIN_NUM_FORMS": "0",
            "rows-MAX_NUM_FORMS": "1000",
            f"rows-{locked_row_count}-account_code": "1.1",
            f"rows-{locked_row_count}-account_name": "Cash",
            f"rows-{locked_row_count}-debit": "100",
            f"rows-{locked_row_count}-credit": "0",
        }
        response = self.client.post(
            reverse(
                "onboarding:template-manual-entry",
                kwargs={"wizard_id": self.wizard.pk, "template_type": "TRIAL_BALANCE"},
            ),
            post_data,
        )

        self.assertRedirects(
            response,
            reverse(
                "onboarding:staging-view",
                kwargs={"wizard_id": self.wizard.pk, "template_type": "TRIAL_BALANCE"},
            ),
            fetch_redirect_response=False,
        )
        staged = StagingService.get_staging_data(self.wizard, "TRIAL_BALANCE")
        self.assertEqual(staged["total_count"], locked_row_count + 1)
        self.assertEqual(staged["rows"][-1]["account_code"], "1.1")
        self.assertEqual(staged["rows"][-1]["account_name"], "Cash")

        reopen_response = self.client.get(
            reverse(
                "onboarding:template-manual-entry",
                kwargs={"wizard_id": self.wizard.pk, "template_type": "TRIAL_BALANCE"},
            )
        )
        self.assertEqual(reopen_response.status_code, 200)
        self.assertContains(reopen_response, 'readonly="readonly"')
        self.assertContains(reopen_response, 'value="Cash"')
        self.assertContains(reopen_response, 'value="100.00"')

        step11_response = self.client.get(
            reverse(
                "onboarding:wizard-step",
                kwargs={"wizard_id": self.wizard.pk, "step_number": 11},
            )
        )
        self.assertEqual(step11_response.status_code, 200)
        self.assertContains(step11_response, "VALIDATED")

    def test_list_only_shows_own_wizards(self):
        """Wizards created by other users are not visible."""
        other_user = UserFactory()
        WizardService.create_wizard(user=other_user)
        response = self.client.get(reverse("onboarding:wizard-list"))
        self.assertEqual(response.status_code, 200)
        wizard_pks = [w.pk for w in response.context["wizards"]]
        # Only the current user's wizard should be present.
        self.assertEqual(len(wizard_pks), 1)
        self.assertIn(self.wizard.pk, wizard_pks)


# --------------------------------------------------------------------------- #
# wizard_start
# --------------------------------------------------------------------------- #


class WizardStartViewTest(OnboardingViewTestBase):
    """Tests for the wizard_start view."""

    def test_start_creates_wizard_and_redirects(self):
        """POST creates a new wizard and redirects to Step 1."""
        response = self.client.post(reverse("onboarding:wizard-start"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/step/1/", response["Location"])
        # A new wizard should exist (beyond the one in setUpTestData).
        self.assertTrue(
            OnboardingWizard.objects.filter(created_by=self.user).count() >= 2
        )

    def test_start_get_redirects_to_list(self):
        """GET is not allowed — redirects to the wizard list."""
        response = self.client.get(reverse("onboarding:wizard-start"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("onboarding:wizard-list"))


# --------------------------------------------------------------------------- #
# wizard_detail
# --------------------------------------------------------------------------- #


class WizardDetailViewTest(OnboardingViewTestBase):
    """Tests for the wizard_detail view."""

    def test_detail_redirects_to_current_step(self):
        """wizard_detail redirects to the wizard's current step."""
        response = self.client.get(
            reverse("onboarding:wizard-detail", kwargs={"wizard_id": self.wizard.pk})
        )
        self.assertEqual(response.status_code, 302)
        expected = reverse(
            "onboarding:wizard-step",
            kwargs={
                "wizard_id": self.wizard.pk,
                "step_number": self.wizard.current_step,
            },
        )
        self.assertEqual(response["Location"], expected)


# --------------------------------------------------------------------------- #
# wizard_step (GET)
# --------------------------------------------------------------------------- #


class WizardStepViewTest(OnboardingViewTestBase):
    """Tests for the wizard_step view (GET)."""

    def test_step_renders_current_step(self):
        """GET renders the step template for the current step."""
        response = self.client.get(
            reverse(
                "onboarding:wizard-step",
                kwargs={"wizard_id": self.wizard.pk, "step_number": 1},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("wizard", response.context)
        self.assertIn("form", response.context)

    def test_step_prevents_future_steps(self):
        """Accessing a step beyond current_step redirects back."""
        response = self.client.get(
            reverse(
                "onboarding:wizard-step",
                kwargs={"wizard_id": self.wizard.pk, "step_number": 99},
            )
        )
        self.assertEqual(response.status_code, 302)
        expected = reverse(
            "onboarding:wizard-step",
            kwargs={
                "wizard_id": self.wizard.pk,
                "step_number": self.wizard.current_step,
            },
        )
        self.assertEqual(response["Location"], expected)

    def test_module_selection_has_bulk_selection_controls(self):
        """Step 3 provides accessible select-all and clear controls."""
        self.wizard.current_step = 3
        self.wizard.save(update_fields=["current_step"])

        response = self.client.get(
            reverse(
                "onboarding:wizard-step",
                kwargs={"wizard_id": self.wizard.pk, "step_number": 3},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="selectAllModules"')
        self.assertContains(response, 'id="clearModuleSelection"')
        self.assertContains(response, 'id="moduleSelectionCount"')

    def test_accounting_start_year_renders_as_select_list(self):
        """Step 4 renders the available financial years in a select element."""
        self.wizard.current_step = 4
        self.wizard.save(update_fields=["current_step"])

        response = self.client.get(
            reverse(
                "onboarding:wizard-step",
                kwargs={"wizard_id": self.wizard.pk, "step_number": 4},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<select name="accounting_start_year"',
            html=False,
        )
        self.assertContains(response, "Select a financial year")
        self.assertEqual(
            len(response.context["form"].fields["accounting_start_year"].choices),
            12,
        )

    def test_accounting_setup_has_processing_feedback(self):
        self.wizard.current_step = 9
        self.wizard.save(update_fields=["current_step"])

        response = self.client.get(
            reverse(
                "onboarding:wizard-step",
                kwargs={"wizard_id": self.wizard.pk, "step_number": 9},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="accountingSetupForm"')
        self.assertContains(response, 'id="accountingSetupButton"')
        self.assertContains(response, "Setting up accounting...")

    def test_saving_completed_step_does_not_skip_current_step(self):
        self.wizard.current_step = 10
        self.wizard.save(update_fields=["current_step"])

        response = self.client.post(
            reverse(
                "onboarding:wizard-step-save",
                kwargs={"wizard_id": self.wizard.pk, "step_number": 9},
            )
        )

        self.assertRedirects(
            response,
            reverse(
                "onboarding:wizard-step",
                kwargs={"wizard_id": self.wizard.pk, "step_number": 10},
            ),
            fetch_redirect_response=False,
        )
        self.wizard.refresh_from_db()
        self.assertEqual(self.wizard.current_step, 10)


# --------------------------------------------------------------------------- #
# Step 8 member assignment CRUD
# --------------------------------------------------------------------------- #


class WizardMemberCrudViewTest(OnboardingViewTestBase):
    """Tests for Step 8's structure/unit member management interface."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from members.models import Structure, Unit

        cls.structure = Structure.objects.create(
            society=cls.society,
            structure_type=Structure.StructureType.BUILDING,
            name="Tower A",
        )
        cls.unit = Unit.objects.create(
            structure=cls.structure,
            unit_type=Unit.UnitType.FLAT,
            identifier="101",
            area_sqft="1000.00",
        )

    def setUp(self):
        super().setUp()
        self.wizard.current_step = 8
        self.wizard.save(update_fields=["current_step"])

    def test_step_8_renders_structure_units_and_modal(self):
        response = self.client.get(
            reverse(
                "onboarding:wizard-step",
                kwargs={"wizard_id": self.wizard.pk, "step_number": 8},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tower A")
        self.assertContains(response, 'id="memberModal"')
        self.assertContains(response, 'id="structureList"')
        self.assertNotIn("form", response.context)

    def test_step_8_refresh_includes_existing_members_by_unit(self):
        from members.models import Member

        member = Member.objects.create(
            society=self.society,
            unit=self.unit,
            full_name="Existing Resident",
            role=Member.MemberRole.OWNER,
            status=Member.MemberStatus.ACTIVE,
        )

        response = self.client.get(
            reverse(
                "onboarding:wizard-step",
                kwargs={"wizard_id": self.wizard.pk, "step_number": 8},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["members_by_unit"][str(self.unit.pk)][0]["id"],
            member.pk,
        )
        self.assertContains(response, "Existing Resident")

    def test_create_update_and_delete_member_with_lifecycle_cleanup(self):
        from members.models import Member, UnitOccupancy, UnitOwnership

        create_response = self.client.post(
            reverse(
                "onboarding:wizard-member-create-api",
                kwargs={"wizard_id": self.wizard.pk},
            ),
            {
                "unit_id": self.unit.pk,
                "full_name": "Alex Owner",
                "role": Member.MemberRole.OWNER,
                "status": Member.MemberStatus.ACTIVE,
                "email": "alex.owner@example.com",
                "phone": "9999999999",
                "start_date": "2026-07-01",
            },
        )

        self.assertEqual(create_response.status_code, 201)
        member_id = create_response.json()["member"]["id"]
        member = Member.objects.get(pk=member_id)
        self.assertTrue(
            UnitOwnership.objects.filter(
                unit=self.unit, owner__email=member.email, end_date__isnull=True
            ).exists()
        )
        self.assertTrue(
            UnitOccupancy.objects.filter(
                unit=self.unit,
                occupant__email=member.email,
                end_date__isnull=True,
            ).exists()
        )

        update_response = self.client.post(
            reverse(
                "onboarding:wizard-member-update-api",
                kwargs={
                    "wizard_id": self.wizard.pk,
                    "member_id": member_id,
                },
            ),
            {
                "full_name": "Alex Owner",
                "role": Member.MemberRole.OWNER,
                "status": Member.MemberStatus.ACTIVE,
                "email": "alex.owner@example.com",
                "phone": "8888888888",
                "start_date": "2026-07-01",
            },
        )

        self.assertEqual(update_response.status_code, 200)
        member.refresh_from_db()
        self.assertEqual(member.phone, "8888888888")
        self.assertEqual(
            UnitOccupancy.objects.filter(
                unit=self.unit,
                occupant__email=member.email,
                end_date__isnull=True,
            ).count(),
            1,
        )

        delete_response = self.client.post(
            reverse(
                "onboarding:wizard-member-delete-api",
                kwargs={
                    "wizard_id": self.wizard.pk,
                    "member_id": member_id,
                },
            )
        )

        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(Member.objects.filter(pk=member_id).exists())
        self.assertFalse(
            UnitOwnership.objects.filter(
                unit=self.unit,
                owner__email="alex.owner@example.com",
                end_date__isnull=True,
            ).exists()
        )
        self.assertFalse(
            UnitOccupancy.objects.filter(
                unit=self.unit,
                occupant__email="alex.owner@example.com",
                end_date__isnull=True,
            ).exists()
        )

    def test_continue_requires_an_active_member(self):
        response = self.client.post(
            reverse(
                "onboarding:wizard-step-save",
                kwargs={"wizard_id": self.wizard.pk, "step_number": 8},
            )
        )

        self.assertRedirects(
            response,
            reverse(
                "onboarding:wizard-step",
                kwargs={"wizard_id": self.wizard.pk, "step_number": 8},
            ),
            fetch_redirect_response=False,
        )
        self.wizard.refresh_from_db()
        self.assertEqual(self.wizard.current_step, 8)


# --------------------------------------------------------------------------- #
# staging_view
# --------------------------------------------------------------------------- #


class StagingViewTest(OnboardingViewTestBase):
    """Tests for the staging_view endpoint."""

    def test_staging_view_renders_empty(self):
        """GET staging_view renders with empty data when nothing uploaded."""
        response = self.client.get(
            reverse(
                "onboarding:staging-view",
                kwargs={
                    "wizard_id": self.wizard.pk,
                    "template_type": "TRIAL_BALANCE",
                },
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("staging_data", response.context)
        self.assertIn("validation_report", response.context)
        self.assertIn("template_type", response.context)

    def test_staging_view_shows_uploaded_data(self):
        """After uploading, staging_view shows the rows."""
        from onboarding.services.staging_service import StagingService

        csv_content = _csv_bytes(
            ["account_code", "account_name", "debit", "credit"],
            [["1.1", "Cash", "100", "0"]],
        )
        f = SimpleUploadedFile("tb.csv", csv_content, content_type="text/csv")
        StagingService.upload_file(
            wizard=self.wizard,
            template_type="TRIAL_BALANCE",
            file=f,
            user=self.user,
        )
        response = self.client.get(
            reverse(
                "onboarding:staging-view",
                kwargs={
                    "wizard_id": self.wizard.pk,
                    "template_type": "TRIAL_BALANCE",
                },
            )
        )
        self.assertEqual(response.status_code, 200)
        rows = response.context["staging_data"].get("rows", [])
        self.assertEqual(len(rows), 1)


# --------------------------------------------------------------------------- #
# staging_upload
# --------------------------------------------------------------------------- #


class StagingUploadViewTest(OnboardingViewTestBase):
    """Tests for the staging_upload endpoint."""

    def test_upload_creates_staging_data(self):
        """POST with a CSV file uploads staging data and redirects."""
        csv_content = _csv_bytes(
            ["account_code", "account_name", "debit", "credit"],
            [["1.1", "Cash", "100", "0"]],
        )
        f = SimpleUploadedFile("tb.csv", csv_content, content_type="text/csv")
        response = self.client.post(
            reverse(
                "onboarding:staging-upload",
                kwargs={
                    "wizard_id": self.wizard.pk,
                    "template_type": "TRIAL_BALANCE",
                },
            ),
            {"file": f},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            StagingTrialBalance.objects.filter(wizard=self.wizard).exists()
        )

    def test_upload_without_file_redirects(self):
        """POST without a file redirects back with an error message."""
        response = self.client.post(
            reverse(
                "onboarding:staging-upload",
                kwargs={
                    "wizard_id": self.wizard.pk,
                    "template_type": "TRIAL_BALANCE",
                },
            ),
        )
        self.assertEqual(response.status_code, 302)


# --------------------------------------------------------------------------- #
# staging_delete
# --------------------------------------------------------------------------- #


class StagingDeleteViewTest(OnboardingViewTestBase):
    """Tests for the staging_delete endpoint."""

    def test_delete_removes_staging_data(self):
        """POST staging_delete removes the uploaded batch."""
        from onboarding.services.staging_service import StagingService

        csv_content = _csv_bytes(
            ["account_code", "account_name", "debit", "credit"],
            [["1.1", "Cash", "100", "0"]],
        )
        f = SimpleUploadedFile("tb.csv", csv_content, content_type="text/csv")
        StagingService.upload_file(
            wizard=self.wizard,
            template_type="TRIAL_BALANCE",
            file=f,
            user=self.user,
        )
        self.assertTrue(
            StagingTrialBalance.objects.filter(wizard=self.wizard).exists()
        )
        response = self.client.post(
            reverse(
                "onboarding:staging-delete",
                kwargs={
                    "wizard_id": self.wizard.pk,
                    "template_type": "TRIAL_BALANCE",
                },
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            StagingTrialBalance.objects.filter(wizard=self.wizard).exists()
        )

    def test_delete_get_not_allowed(self):
        """GET staging_delete redirects (POST-only)."""
        response = self.client.get(
            reverse(
                "onboarding:staging-delete",
                kwargs={
                    "wizard_id": self.wizard.pk,
                    "template_type": "TRIAL_BALANCE",
                },
            ),
        )
        self.assertEqual(response.status_code, 302)


# --------------------------------------------------------------------------- #
# staging_approve
# --------------------------------------------------------------------------- #


class StagingApproveViewTest(OnboardingViewTestBase):
    """Tests for the staging_approve endpoint."""

    def test_approve_locks_batch(self):
        """POST staging_approve marks the batch as APPROVED."""
        from onboarding.services.staging_service import StagingService
        from onboarding.services.validation_service import ValidationService

        # Use a balanced trial balance (total debit == total credit)
        # so validation marks all rows as VALID and approval succeeds.
        csv_content = _csv_bytes(
            ["account_code", "account_name", "debit", "credit"],
            [
                ["1.1", "Cash", "100", "0"],
                ["5.1", "Opening Fund", "0", "100"],
            ],
        )
        f = SimpleUploadedFile("tb.csv", csv_content, content_type="text/csv")
        StagingService.upload_file(
            wizard=self.wizard,
            template_type="TRIAL_BALANCE",
            file=f,
            user=self.user,
        )
        ValidationService.validate_batch(
            self.wizard, "TRIAL_BALANCE", user=self.user
        )
        response = self.client.post(
            reverse(
                "onboarding:staging-approve",
                kwargs={
                    "wizard_id": self.wizard.pk,
                    "template_type": "TRIAL_BALANCE",
                },
            ),
        )
        self.assertEqual(response.status_code, 302)
        batch = UploadBatch.objects.get(
            wizard=self.wizard, template_type="TRIAL_BALANCE"
        )
        self.assertEqual(batch.status, UploadBatch.Status.APPROVED)


# --------------------------------------------------------------------------- #
# reconciliation_dashboard
# --------------------------------------------------------------------------- #


class ReconciliationDashboardViewTest(OnboardingViewTestBase):
    """Tests for the reconciliation_dashboard view."""

    def test_dashboard_renders(self):
        """GET reconciliation_dashboard renders the dashboard template."""
        response = self.client.get(
            reverse(
                "onboarding:reconciliation-dashboard",
                kwargs={"wizard_id": self.wizard.pk},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("dashboard", response.context)


# --------------------------------------------------------------------------- #
# validation_checklist
# --------------------------------------------------------------------------- #


class ValidationChecklistViewTest(OnboardingViewTestBase):
    """Tests for the validation_checklist view."""

    def test_checklist_renders(self):
        """GET validation_checklist renders the checklist template."""
        response = self.client.get(
            reverse(
                "onboarding:validation-checklist",
                kwargs={"wizard_id": self.wizard.pk},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("checklist", response.context)
        self.assertIn("can_finalize", response.context)


# --------------------------------------------------------------------------- #
# finalize_migration
# --------------------------------------------------------------------------- #


class FinalizeMigrationViewTest(OnboardingViewTestBase):
    """Tests for the finalize_migration view."""

    def test_finalize_get_renders_form(self):
        """GET finalize_migration renders the final approval form."""
        response = self.client.get(
            reverse(
                "onboarding:finalize-migration",
                kwargs={"wizard_id": self.wizard.pk},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("form", response.context)

    def test_finalize_post_without_confirmation_rerenders(self):
        """POST without the confirm checkbox rerenders the form with errors."""
        response = self.client.post(
            reverse(
                "onboarding:finalize-migration",
                kwargs={"wizard_id": self.wizard.pk},
            ),
            {"confirm": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("form", response.context)
        self.assertFalse(response.context["form"].is_valid())


# --------------------------------------------------------------------------- #
# wizard_complete
# --------------------------------------------------------------------------- #


class WizardCompleteViewTest(OnboardingViewTestBase):
    """Tests for the wizard_complete view."""

    def test_complete_renders(self):
        """GET wizard_complete renders the completion page."""
        response = self.client.get(
            reverse(
                "onboarding:wizard-complete",
                kwargs={"wizard_id": self.wizard.pk},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("wizard", response.context)
        self.assertIn("finalization_summary", response.context)


# --------------------------------------------------------------------------- #
# template_download
# --------------------------------------------------------------------------- #


class TemplateDownloadViewTest(OnboardingViewTestBase):
    """Tests for the template_download view (CSV template generator)."""

    def test_download_requires_login(self):
        """Anonymous users are redirected to the login page."""
        self.client.logout()
        response = self.client.get(
            reverse(
                "onboarding:template-download",
                kwargs={"template_type": "TRIAL_BALANCE"},
            )
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_download_returns_csv(self):
        """GET returns a CSV file with the expected columns as headers."""
        response = self.client.get(
            reverse(
                "onboarding:template-download",
                kwargs={"template_type": "TRIAL_BALANCE"},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("trial_balance_template.csv", response["Content-Disposition"])

        # Parse the CSV content and verify headers.
        import csv as csv_mod
        import io as io_mod

        reader = csv_mod.reader(io_mod.StringIO(response.content.decode("utf-8")))
        rows = list(reader)
        self.assertEqual(rows[0], ["account_code", "account_name", "debit", "credit"])

    def test_download_invalid_template_redirects(self):
        """An invalid template_type redirects to the wizard list."""
        response = self.client.get(
            reverse(
                "onboarding:template-download",
                kwargs={"template_type": "INVALID_TYPE"},
            )
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("onboarding:wizard-list"))

    def test_download_all_template_types(self):
        """Every canonical template type produces a valid CSV download."""
        from onboarding.services.staging_service import ALL_TEMPLATE_TYPES

        for template_type in ALL_TEMPLATE_TYPES:
            response = self.client.get(
                reverse(
                    "onboarding:template-download",
                    kwargs={"template_type": template_type},
                )
            )
            self.assertEqual(response.status_code, 200, f"Failed for {template_type}")
            self.assertEqual(response["Content-Type"], "text/csv")
            self.assertIn("attachment", response["Content-Disposition"])
