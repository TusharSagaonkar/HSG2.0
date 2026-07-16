"""Smoke tests for the onboarding service layer (Phases 2 & 3).

These tests verify that the service methods work end-to-end against a real
database, covering wizard lifecycle, module configuration, and the branch
logic at Step 9.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from onboarding.models import MigrationAuditLog, OnboardingWizard, WizardStepLog
from onboarding.services import (
    ModuleConfigurationService,
    WizardService,
)
from onboarding.services.wizard_service import (
    STEP_ACCOUNTING_SETUP,
    STEP_SOCIETY_READY,
)

User = get_user_model()


class WizardServiceLifecycleTest(TestCase):
    """Tests for WizardService create/advance/resume/abandon/complete."""

    def setUp(self):
        self.user = User.objects.create(
            email="test@example.com",
            name="Test User",
            is_active=True,
        )

    def test_create_wizard(self):
        wizard = WizardService.create_wizard(user=self.user)
        self.assertEqual(wizard.current_step, 1)
        self.assertEqual(wizard.status, OnboardingWizard.Status.IN_PROGRESS)
        self.assertEqual(wizard.created_by_id, self.user.id)
        self.assertTrue(
            MigrationAuditLog.objects.filter(
                wizard=wizard, action="CREATE"
            ).exists()
        )

    def test_set_society_type(self):
        wizard = WizardService.create_wizard(user=self.user)
        wizard = WizardService.set_society_type(wizard, "NEW", user=self.user)
        self.assertEqual(wizard.society_type, "NEW")
        self.assertTrue(
            MigrationAuditLog.objects.filter(
                wizard=wizard, action="SET_SOCIETY_TYPE"
            ).exists()
        )

    def test_set_selected_modules_filters_invalid(self):
        wizard = WizardService.create_wizard(user=self.user)
        wizard = WizardService.set_selected_modules(
            wizard, ["parking", "gateops", "invalid_module"], user=self.user
        )
        self.assertIn("accounting", wizard.selected_modules)
        self.assertIn("parking", wizard.selected_modules)
        self.assertIn("gateops", wizard.selected_modules)
        self.assertNotIn("invalid_module", wizard.selected_modules)

    def test_advance_step_with_new_society_branch(self):
        """Step 9 with society_type=NEW should jump to Step 28."""
        wizard = WizardService.create_wizard(user=self.user)
        wizard = WizardService.set_society_type(wizard, "NEW", user=self.user)

        # Advance from step 1 to step 9.
        for _ in range(8):
            wizard = WizardService.advance_step(
                wizard, step_data={"test": "data"}, user=self.user
            )
        self.assertEqual(wizard.current_step, STEP_ACCOUNTING_SETUP)

        # Advancing from step 9 (NEW) should jump to step 28.
        wizard = WizardService.advance_step(wizard, user=self.user)
        self.assertEqual(wizard.current_step, STEP_SOCIETY_READY)

    def test_get_wizard_state(self):
        wizard = WizardService.create_wizard(user=self.user)
        wizard = WizardService.set_society_type(wizard, "NEW", user=self.user)
        wizard = WizardService.advance_step(wizard, user=self.user)
        state = WizardService.get_wizard_state(wizard)
        self.assertEqual(state["current_step"], 2)
        self.assertEqual(state["society_type"], "NEW")
        self.assertFalse(state["is_finalized"])
        self.assertIn(1, state["completed_steps"])

    def test_go_to_step_backward(self):
        wizard = WizardService.create_wizard(user=self.user)
        for _ in range(3):
            wizard = WizardService.advance_step(wizard, user=self.user)
        self.assertEqual(wizard.current_step, 4)
        wizard = WizardService.go_to_step(wizard, 2, user=self.user)
        self.assertEqual(wizard.current_step, 2)

    def test_resume_abandon(self):
        wizard = WizardService.create_wizard(user=self.user)
        wizard = WizardService.abandon_wizard(wizard, user=self.user)
        self.assertEqual(wizard.status, OnboardingWizard.Status.ABANDONED)
        wizard = WizardService.resume_wizard(wizard, user=self.user)
        self.assertEqual(wizard.status, OnboardingWizard.Status.IN_PROGRESS)
        self.assertEqual(wizard.resumed_count, 1)

    def test_complete_wizard(self):
        wizard = WizardService.create_wizard(user=self.user)
        wizard = WizardService.complete_wizard(wizard, user=self.user)
        self.assertEqual(wizard.status, OnboardingWizard.Status.COMPLETED)
        self.assertTrue(wizard.is_finalized)
        self.assertIsNotNone(wizard.completed_at)

    def test_update_wizard_data(self):
        wizard = WizardService.create_wizard(user=self.user)
        wizard = WizardService.update_wizard_data(
            wizard, "custom_key", {"nested": "value"}
        )
        self.assertEqual(wizard.wizard_data["custom_key"], {"nested": "value"})


class ModuleConfigurationServiceTest(TestCase):
    """Tests for ModuleConfigurationService."""

    def setUp(self):
        self.user = User.objects.create(
            email="mod@example.com", name="Mod User", is_active=True
        )
        self.wizard = WizardService.create_wizard(user=self.user)

    def test_get_enabled_modules_defaults_to_core(self):
        enabled = ModuleConfigurationService.get_enabled_modules(self.wizard)
        self.assertIn("accounting", enabled)
        self.assertIn("billing", enabled)
        self.assertIn("members", enabled)
        self.assertIn("administration", enabled)

    def test_configure_modules_includes_core(self):
        enabled = ModuleConfigurationService.configure_modules(
            self.wizard, ["shares", "parking"], user=self.user
        )
        self.assertIn("accounting", enabled)
        self.assertIn("shares", enabled)
        self.assertIn("parking", enabled)

    def test_get_module_display_names(self):
        names = ModuleConfigurationService.get_module_display_names()
        self.assertEqual(names["accounting"], "Accounting")
        self.assertEqual(names["parking"], "Parking Management")
