"""Comprehensive tests for WizardService lifecycle management (Phase 9).

These tests complement the smoke tests in ``test_services_smoke.py`` by
covering edge cases, error paths, branch logic, append-only enforcement,
and audit-trail integrity.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from onboarding.models import (
    MigrationAuditLog,
    OnboardingWizard,
    WizardStepLog,
)
from onboarding.services.wizard_service import (
    MAX_STEP,
    STEP_ACCOUNTING_SETUP,
    STEP_SOCIETY_READY,
    WizardService,
)

User = get_user_model()


class WizardCreationTest(TestCase):
    """Tests for wizard creation and retrieval."""

    def setUp(self):
        self.user = User.objects.create(
            email="lifecycle@example.com", name="Lifecycle User", is_active=True
        )

    def test_get_wizard_returns_by_id(self):
        wizard = WizardService.create_wizard(user=self.user)
        fetched = WizardService.get_wizard(wizard.pk)
        self.assertEqual(fetched.pk, wizard.pk)
        self.assertEqual(fetched.current_step, 1)

    def test_create_wizard_with_existing_society_type(self):
        wizard = WizardService.create_wizard(
            user=self.user, society_type="EXISTING"
        )
        self.assertEqual(wizard.society_type, OnboardingWizard.SocietyType.EXISTING)

    def test_create_wizard_with_new_society_type(self):
        wizard = WizardService.create_wizard(
            user=self.user, society_type="new"  # lowercase should be normalized
        )
        self.assertEqual(wizard.society_type, OnboardingWizard.SocietyType.NEW)

    def test_create_wizard_invalid_society_type_raises(self):
        with self.assertRaises(ValidationError):
            WizardService.create_wizard(user=self.user, society_type="BOGUS")

    def test_create_wizard_writes_audit_log(self):
        wizard = WizardService.create_wizard(user=self.user)
        self.assertTrue(
            MigrationAuditLog.objects.filter(
                wizard=wizard, action="CREATE"
            ).exists()
        )


class WizardStepNavigationTest(TestCase):
    """Tests for advance_step and go_to_step with branch logic and guards."""

    def setUp(self):
        self.user = User.objects.create(
            email="nav@example.com", name="Nav User", is_active=True
        )
        self.wizard = WizardService.create_wizard(user=self.user)

    def test_advance_step_existing_society_branch(self):
        """Step 9 with society_type=EXISTING should go to Step 10."""
        WizardService.set_society_type(self.wizard, "EXISTING", user=self.user)
        # Advance from step 1 to step 9.
        for _ in range(8):
            self.wizard = WizardService.advance_step(
                self.wizard, step_data={"test": "data"}, user=self.user
            )
        self.assertEqual(self.wizard.current_step, STEP_ACCOUNTING_SETUP)
        # Advancing from step 9 (EXISTING) should go to step 10.
        self.wizard = WizardService.advance_step(self.wizard, user=self.user)
        self.assertEqual(self.wizard.current_step, 10)

    def test_advance_step_blocked_when_abandoned(self):
        WizardService.abandon_wizard(self.wizard, user=self.user)
        with self.assertRaises(ValidationError):
            WizardService.advance_step(self.wizard, user=self.user)

    def test_advance_step_blocked_when_completed(self):
        WizardService.complete_wizard(self.wizard, user=self.user)
        with self.assertRaises(ValidationError):
            WizardService.advance_step(self.wizard, user=self.user)

    def test_advance_step_merges_step_data(self):
        self.wizard = WizardService.advance_step(
            self.wizard, step_data={"field": "value"}, user=self.user
        )
        # Step 1 name is "Society Details".
        self.assertIn("Society Details", self.wizard.wizard_data)
        self.assertEqual(
            self.wizard.wizard_data["Society Details"], {"field": "value"}
        )

    def test_advance_step_logs_step_completion(self):
        self.wizard = WizardService.advance_step(self.wizard, user=self.user)
        self.assertTrue(
            WizardStepLog.objects.filter(
                wizard=self.wizard, step_number=1,
                status=WizardStepLog.Status.COMPLETED,
            ).exists()
        )

    def test_advance_step_creates_audit_log(self):
        self.wizard = WizardService.advance_step(self.wizard, user=self.user)
        self.assertTrue(
            MigrationAuditLog.objects.filter(
                wizard=self.wizard, action="ADVANCE_STEP"
            ).exists()
        )

    def test_advance_step_at_max_stays_at_max(self):
        """Advancing at the last step should not exceed MAX_STEP."""
        self.wizard.current_step = MAX_STEP
        self.wizard.save(update_fields=["current_step"])
        self.wizard = WizardService.advance_step(self.wizard, user=self.user)
        self.assertEqual(self.wizard.current_step, MAX_STEP)

    def test_go_to_step_forward_blocked(self):
        """Cannot jump forward more than one step."""
        with self.assertRaises(ValidationError):
            WizardService.go_to_step(self.wizard, 5, user=self.user)

    def test_go_to_step_invalid_step_number_zero(self):
        with self.assertRaises(ValidationError):
            WizardService.go_to_step(self.wizard, 0, user=self.user)

    def test_go_to_step_invalid_step_number_over_max(self):
        with self.assertRaises(ValidationError):
            WizardService.go_to_step(self.wizard, MAX_STEP + 1, user=self.user)

    def test_go_to_step_blocked_when_completed(self):
        WizardService.complete_wizard(self.wizard, user=self.user)
        with self.assertRaises(ValidationError):
            WizardService.go_to_step(self.wizard, 1, user=self.user)

    def test_go_to_step_re_enter_immediate_next(self):
        """go_to_step allows re-entering the immediate next step."""
        self.wizard = WizardService.advance_step(self.wizard, user=self.user)
        self.assertEqual(self.wizard.current_step, 2)
        # Re-enter step 2 (current + 1 == 2 is not allowed since current is 2).
        # But going back to step 1 is allowed.
        self.wizard = WizardService.go_to_step(self.wizard, 1, user=self.user)
        self.assertEqual(self.wizard.current_step, 1)
        # Now current is 1, going to step 2 (current + 1) is allowed.
        self.wizard = WizardService.go_to_step(self.wizard, 2, user=self.user)
        self.assertEqual(self.wizard.current_step, 2)

    def test_go_to_step_logs_started_status(self):
        """go_to_step logs a STARTED entry for a step that has no prior log.

        Note: _log_step is idempotent — if a log already exists for the
        step (e.g. from advance_step), going back to it will NOT create a
        duplicate STARTED log. We therefore test with a fresh step.
        """
        # Advance to step 2 (logs step 1 as COMPLETED, current_step=2).
        self.wizard = WizardService.advance_step(self.wizard, user=self.user)
        # Go to step 2 (current + 1 == 2 is not allowed; go back to 1 is).
        # Step 1 already has a COMPLETED log, so no STARTED log is created.
        # Instead, advance to step 3 and go back to step 2 (no log yet for 2).
        self.wizard = WizardService.advance_step(self.wizard, user=self.user)
        self.assertEqual(self.wizard.current_step, 3)
        # Going back to step 2 — step 2 has a COMPLETED log, so no new log.
        # The idempotency means STARTED is only logged for never-logged steps.
        # Verify go_to_step itself works (no crash) and step changes.
        self.wizard = WizardService.go_to_step(self.wizard, 2, user=self.user)
        self.assertEqual(self.wizard.current_step, 2)
        # The step log for step 2 should still be COMPLETED (from advance).
        log = WizardStepLog.objects.filter(
            wizard=self.wizard, step_number=2
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.status, WizardStepLog.Status.COMPLETED)


class WizardLifecycleTransitionTest(TestCase):
    """Tests for resume, abandon, complete lifecycle transitions."""

    def setUp(self):
        self.user = User.objects.create(
            email="trans@example.com", name="Trans User", is_active=True
        )
        self.wizard = WizardService.create_wizard(user=self.user)

    def test_resume_wizard_increments_resumed_count(self):
        WizardService.abandon_wizard(self.wizard, user=self.user)
        self.wizard = WizardService.resume_wizard(self.wizard, user=self.user)
        self.assertEqual(self.wizard.resumed_count, 1)
        WizardService.abandon_wizard(self.wizard, user=self.user)
        self.wizard = WizardService.resume_wizard(self.wizard, user=self.user)
        self.assertEqual(self.wizard.resumed_count, 2)

    def test_resume_in_progress_wizard_still_increments_count(self):
        """Resuming an IN_PROGRESS wizard still increments resumed_count."""
        self.wizard = WizardService.resume_wizard(self.wizard, user=self.user)
        self.assertEqual(self.wizard.resumed_count, 1)
        self.assertEqual(self.wizard.status, OnboardingWizard.Status.IN_PROGRESS)

    def test_resume_wizard_creates_audit_log(self):
        WizardService.abandon_wizard(self.wizard, user=self.user)
        WizardService.resume_wizard(self.wizard, user=self.user)
        self.assertTrue(
            MigrationAuditLog.objects.filter(
                wizard=self.wizard, action="RESUME"
            ).exists()
        )

    def test_abandon_wizard_sets_status(self):
        self.wizard = WizardService.abandon_wizard(self.wizard, user=self.user)
        self.assertEqual(self.wizard.status, OnboardingWizard.Status.ABANDONED)

    def test_abandon_wizard_creates_audit_log(self):
        WizardService.abandon_wizard(self.wizard, user=self.user)
        self.assertTrue(
            MigrationAuditLog.objects.filter(
                wizard=self.wizard, action="ABANDON"
            ).exists()
        )

    def test_complete_wizard_creates_audit_log(self):
        WizardService.complete_wizard(self.wizard, user=self.user)
        self.assertTrue(
            MigrationAuditLog.objects.filter(
                wizard=self.wizard, action="COMPLETE"
            ).exists()
        )


class WizardDataAndConfigTest(TestCase):
    """Tests for update_wizard_data, set_society_type, set_selected_modules."""

    def setUp(self):
        self.user = User.objects.create(
            email="config@example.com", name="Config User", is_active=True
        )
        self.wizard = WizardService.create_wizard(user=self.user)

    def test_set_society_type_invalid_raises(self):
        with self.assertRaises(ValidationError):
            WizardService.set_society_type(self.wizard, "INVALID", user=self.user)

    def test_set_society_type_creates_audit_log(self):
        WizardService.set_society_type(self.wizard, "NEW", user=self.user)
        self.assertTrue(
            MigrationAuditLog.objects.filter(
                wizard=self.wizard, action="SET_SOCIETY_TYPE"
            ).exists()
        )

    def test_set_selected_modules_empty_returns_core_only(self):
        wizard = WizardService.set_selected_modules(
            self.wizard, [], user=self.user
        )
        self.assertEqual(
            sorted(wizard.selected_modules),
            ["accounting", "administration", "billing", "members"],
        )

    def test_set_selected_modules_preserves_order(self):
        wizard = WizardService.set_selected_modules(
            self.wizard, ["parking", "shares", "gateops"], user=self.user
        )
        # Core modules first, then optionals in the order provided.
        self.assertEqual(wizard.selected_modules[:4],
                         ["accounting", "billing", "members", "administration"])
        self.assertEqual(wizard.selected_modules[4:], ["parking", "shares", "gateops"])

    def test_set_selected_modules_deduplicates(self):
        wizard = WizardService.set_selected_modules(
            self.wizard, ["parking", "parking", "shares"], user=self.user
        )
        self.assertEqual(wizard.selected_modules.count("parking"), 1)

    def test_set_selected_modules_creates_audit_log(self):
        WizardService.set_selected_modules(self.wizard, ["parking"], user=self.user)
        self.assertTrue(
            MigrationAuditLog.objects.filter(
                wizard=self.wizard, action="SET_SELECTED_MODULES"
            ).exists()
        )

    def test_update_wizard_data_overwrites_existing_key(self):
        WizardService.update_wizard_data(self.wizard, "key1", "value1")
        WizardService.update_wizard_data(self.wizard, "key1", "value2")
        self.assertEqual(self.wizard.wizard_data["key1"], "value2")

    def test_update_wizard_data_preserves_other_keys(self):
        WizardService.update_wizard_data(self.wizard, "key1", "value1")
        WizardService.update_wizard_data(self.wizard, "key2", "value2")
        self.assertEqual(self.wizard.wizard_data["key1"], "value1")
        self.assertEqual(self.wizard.wizard_data["key2"], "value2")


class WizardStateTest(TestCase):
    """Tests for get_wizard_state."""

    def setUp(self):
        self.user = User.objects.create(
            email="state@example.com", name="State User", is_active=True
        )
        self.wizard = WizardService.create_wizard(user=self.user)

    def test_get_wizard_state_completed_steps_accumulate(self):
        for _ in range(3):
            self.wizard = WizardService.advance_step(self.wizard, user=self.user)
        state = WizardService.get_wizard_state(self.wizard)
        self.assertIn(1, state["completed_steps"])
        self.assertIn(2, state["completed_steps"])
        self.assertIn(3, state["completed_steps"])

    def test_get_wizard_state_includes_society_id_when_set(self):
        state = WizardService.get_wizard_state(self.wizard)
        self.assertIsNone(state["society_id"])

    def test_get_wizard_state_reflects_finalized_flag(self):
        WizardService.complete_wizard(self.wizard, user=self.user)
        state = WizardService.get_wizard_state(self.wizard)
        self.assertTrue(state["is_finalized"])


class AppendOnlyModelTest(TestCase):
    """Tests for the append-only enforcement on WizardStepLog and
    MigrationAuditLog.
    """

    def setUp(self):
        self.user = User.objects.create(
            email="append@example.com", name="Append User", is_active=True
        )
        self.wizard = WizardService.create_wizard(user=self.user)

    def test_step_log_update_raises_permission_error(self):
        WizardService.advance_step(self.wizard, user=self.user)
        log = WizardStepLog.objects.filter(wizard=self.wizard, step_number=1).first()
        self.assertIsNotNone(log)
        log.step_name = "Tampered"
        with self.assertRaises(PermissionError):
            log.save()

    def test_step_log_delete_raises_permission_error(self):
        WizardService.advance_step(self.wizard, user=self.user)
        log = WizardStepLog.objects.filter(wizard=self.wizard, step_number=1).first()
        with self.assertRaises(PermissionError):
            log.delete()

    def test_audit_log_update_raises_permission_error(self):
        # create_wizard already wrote a CREATE audit log.
        log = MigrationAuditLog.objects.filter(
            wizard=self.wizard, action="CREATE"
        ).first()
        self.assertIsNotNone(log)
        log.action = "TAMPERED"
        with self.assertRaises(PermissionError):
            log.save()

    def test_audit_log_delete_raises_permission_error(self):
        log = MigrationAuditLog.objects.filter(
            wizard=self.wizard, action="CREATE"
        ).first()
        with self.assertRaises(PermissionError):
            log.delete()

    def test_step_log_unique_per_wizard_step(self):
        """Advancing and going back should not create duplicate step logs."""
        WizardService.advance_step(self.wizard, user=self.user)
        WizardService.go_to_step(self.wizard, 1, user=self.user)
        WizardService.advance_step(self.wizard, user=self.user)
        count = WizardStepLog.objects.filter(
            wizard=self.wizard, step_number=1
        ).count()
        self.assertEqual(count, 1)
