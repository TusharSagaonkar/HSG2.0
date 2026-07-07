"""Phase 5 tests for the Pass lifecycle service and model.

Covers: Pass model validation (``clean``, ``is_valid``, unique constraint,
soft-delete), :class:`PassService` issuance (``generate``), validation
(``validate``), usage accounting (``record_usage`` with auto-expiry), the
state machine (``revoke`` / ``suspend`` / ``reactivate``), bulk expiry
(``expire_expired_passes``), audit logging, multi-tenant safety, and the
thin HTTP views layered over the service.

Convention: societies are created once per class in ``setUpTestData`` (society
creation triggers the expensive gateops bootstrap signal which seeds the
default pass types QR_PASS / OTP_PASS / DAILY_PASS). Per-test mutable
records (persons, passes) are created in ``setUp`` or in the test body so
each test starts from a clean state.
"""

from datetime import timedelta

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, RequestFactory
from django.urls import reverse
from django.utils import timezone

from gateops.models import (
    GateOpsAuditLog,
    GateOpsSocietyConfig,
    Pass,
    PassType,
    Person,
)
from gateops.services.pass_service import PassService
from housing_accounting.selection import SESSION_SELECTED_SOCIETY_ID
from housing_accounting.users.tests.factories import UserFactory
from societies.models import Society
from societies.services import create_society


class PassModelTest(TestCase):
    """Model-level tests for :class:`Pass` (clean, is_valid, constraints)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Creating a Society triggers the gateops bootstrap signal, which
        # seeds default PassTypes (QR_PASS, OTP_PASS, DAILY_PASS).
        cls.society = Society.objects.create(name="Pass Model Society")
        cls.user = UserFactory(password="password")
        cls.qr_pass_type = PassType.objects.get(society=cls.society, code="QR_PASS")

    def setUp(self):
        super().setUp()
        self.person = Person.objects.create(
            society=self.society, name="Model Visitor", phone="9999900000"
        )

    def _make_pass(self, **overrides):
        now = timezone.now()
        defaults = {
            "society": self.society,
            "person": self.person,
            "pass_type": self.qr_pass_type,
            "code": "MODEL-CODE-1",
            "valid_from": now - timedelta(hours=1),
            "valid_until": now + timedelta(hours=1),
            "status": Pass.Status.ACTIVE,
            "usage_count": 0,
            "max_usage": 1,
        }
        defaults.update(overrides)
        return Pass.objects.create(**defaults)

    # --- creation & representation ---------------------------------------

    def test_pass_creation_with_valid_data(self):
        now = timezone.now()
        pass_obj = self._make_pass()

        self.assertEqual(pass_obj.society, self.society)
        self.assertEqual(pass_obj.person, self.person)
        self.assertEqual(pass_obj.pass_type, self.qr_pass_type)
        self.assertEqual(pass_obj.code, "MODEL-CODE-1")
        self.assertEqual(pass_obj.status, Pass.Status.ACTIVE)
        self.assertEqual(pass_obj.usage_count, 0)
        self.assertEqual(pass_obj.max_usage, 1)
        self.assertTrue(pass_obj.is_active)
        self.assertIsNone(pass_obj.deleted_at)
        self.assertIsNotNone(pass_obj.created_at)
        self.assertIsNotNone(pass_obj.updated_at)
        # valid_until must be after valid_from.
        self.assertGreater(pass_obj.valid_until, pass_obj.valid_from)

    def test_pass_str_representation(self):
        pass_obj = self._make_pass(code="STR-CODE")

        self.assertEqual(
            str(pass_obj), f"STR-CODE ({pass_obj.get_status_display()})"
        )

    # --- clean() validation ---------------------------------------------

    def test_pass_clean_valid_until_before_valid_from_raises(self):
        now = timezone.now()
        pass_obj = Pass(
            society=self.society,
            person=self.person,
            pass_type=self.qr_pass_type,
            code="BAD-WINDOW",
            valid_from=now,
            valid_until=now - timedelta(hours=1),
        )
        with self.assertRaises(ValidationError):
            pass_obj.clean()

    def test_pass_clean_max_usage_zero_raises(self):
        now = timezone.now()
        pass_obj = Pass(
            society=self.society,
            person=self.person,
            pass_type=self.qr_pass_type,
            code="ZERO-USAGE",
            valid_from=now,
            valid_until=now + timedelta(hours=1),
            max_usage=0,
        )
        with self.assertRaises(ValidationError):
            pass_obj.clean()

    def test_pass_clean_empty_code_raises(self):
        now = timezone.now()
        pass_obj = Pass(
            society=self.society,
            person=self.person,
            pass_type=self.qr_pass_type,
            code="",
            valid_from=now,
            valid_until=now + timedelta(hours=1),
        )
        with self.assertRaises(ValidationError):
            pass_obj.clean()

    # --- is_valid property ----------------------------------------------

    def test_pass_is_valid_property_active_within_window(self):
        pass_obj = self._make_pass(max_usage=5, usage_count=2)

        self.assertTrue(pass_obj.is_valid)

    def test_pass_is_valid_property_expired_status(self):
        pass_obj = self._make_pass(status=Pass.Status.EXPIRED)

        self.assertFalse(pass_obj.is_valid)

    def test_pass_is_valid_property_outside_window(self):
        now = timezone.now()
        # Window entirely in the past.
        pass_obj = self._make_pass(
            valid_from=now - timedelta(hours=2),
            valid_until=now - timedelta(hours=1),
        )

        self.assertFalse(pass_obj.is_valid)

    def test_pass_is_valid_property_usage_exceeded(self):
        pass_obj = self._make_pass(max_usage=1, usage_count=1)

        self.assertFalse(pass_obj.is_valid)

    def test_pass_is_valid_property_unlimited_usage(self):
        # max_usage=None means unlimited; high usage must not block validity.
        pass_obj = self._make_pass(max_usage=None, usage_count=999)

        self.assertTrue(pass_obj.is_valid)

    # --- constraints & soft-delete --------------------------------------

    def test_pass_unique_code_per_society_constraint(self):
        self._make_pass(code="DUP-CODE")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._make_pass(code="DUP-CODE")

    def test_pass_soft_delete_pattern(self):
        # Soft-delete the first pass (is_active=False) so its code can be reused.
        first = self._make_pass(code="REUSE-CODE")
        first.is_active = False
        first.deleted_at = timezone.now()
        first.save(update_fields=["is_active", "deleted_at"])

        # A new active pass with the same code must now succeed.
        second = self._make_pass(code="REUSE-CODE")
        self.assertEqual(second.code, "REUSE-CODE")
        self.assertTrue(second.is_active)


class PassServiceTest(TestCase):
    """Service-level tests for :class:`PassService`.

    The society and seeded master data are created once per class via
    ``setUpTestData`` to avoid re-running the expensive gateops bootstrap
    signal on every test method.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Creating a Society triggers the gateops bootstrap signal, which
        # seeds default PassTypes (QR_PASS, OTP_PASS, DAILY_PASS).
        cls.society = Society.objects.create(name="Pass Test Society")
        cls.user = UserFactory(password="password")
        # Fetch seeded pass types.
        cls.qr_pass_type = PassType.objects.get(society=cls.society, code="QR_PASS")
        cls.otp_pass_type = PassType.objects.get(society=cls.society, code="OTP_PASS")
        cls.daily_pass_type = PassType.objects.get(
            society=cls.society, code="DAILY_PASS"
        )
        # A second society for cross-society scoping checks (created once).
        cls.other_society = Society.objects.create(name="Other Pass Society")
        cls.other_qr_pass_type = PassType.objects.get(
            society=cls.other_society, code="QR_PASS"
        )

    def setUp(self):
        super().setUp()
        self.person = self._make_person()

    # --- helpers ---------------------------------------------------------

    def _make_person(self, **overrides):
        defaults = {
            "society": self.society,
            "name": "Test Visitor",
            "phone": "+919999999999",
        }
        defaults.update(overrides)
        return Person.objects.create(**defaults)

    def _make_pass_type(self, **overrides):
        """Create a non-seeded pass type (PIN / DIGITAL) for code-format tests."""
        defaults = {
            "society": self.society,
            "name": "Custom Pass",
            "code": "CUSTOM_PASS",
            "validation_method": PassType.ValidationMethod.PIN,
            "duration_type": PassType.DurationType.ONE_TIME,
            "default_validity_hours": 24,
        }
        defaults.update(overrides)
        return PassType.objects.create(**defaults)

    def _make_pass(self, **overrides):
        """Directly create a Pass row bypassing the service (for setup)."""
        now = timezone.now()
        defaults = {
            "society": self.society,
            "person": self.person,
            "pass_type": self.qr_pass_type,
            "code": f"DIRECT-{timezone.now().timestamp()}",
            "valid_from": now - timedelta(hours=1),
            "valid_until": now + timedelta(hours=1),
            "status": Pass.Status.ACTIVE,
            "usage_count": 0,
            "max_usage": 1,
        }
        defaults.update(overrides)
        return Pass.objects.create(**defaults)

    # --- generate: basic issuance ---------------------------------------

    def test_generate_creates_active_pass(self):
        pass_obj = PassService.generate(
            pass_type=self.qr_pass_type, person=self.person, actor=self.user
        )

        self.assertEqual(pass_obj.status, Pass.Status.ACTIVE)
        self.assertEqual(pass_obj.usage_count, 0)
        self.assertTrue(pass_obj.code)
        self.assertEqual(pass_obj.society, self.society)
        self.assertEqual(pass_obj.person, self.person)
        self.assertEqual(pass_obj.pass_type, self.qr_pass_type)
        # valid_until = valid_from + default_validity_hours.
        self.assertEqual(
            pass_obj.valid_until - pass_obj.valid_from,
            timedelta(hours=self.qr_pass_type.default_validity_hours),
        )
        # valid_from defaults to ~now.
        self.assertLess(abs(pass_obj.valid_from - timezone.now()), timedelta(seconds=5))

    def test_generate_qr_pass_code_format(self):
        pass_obj = PassService.generate(
            pass_type=self.qr_pass_type, person=self.person
        )

        # QR codes are URL-safe tokens (non-empty, no spaces).
        self.assertTrue(pass_obj.code)
        self.assertNotEqual(pass_obj.code, "")

    def test_generate_otp_pass_code_format(self):
        config = GateOpsSocietyConfig.objects.get(society=self.society)
        pass_obj = PassService.generate(
            pass_type=self.otp_pass_type, person=self.person
        )

        self.assertTrue(pass_obj.code.isdigit())
        self.assertEqual(len(pass_obj.code), config.otp_length)

    def test_generate_pin_pass_code_format(self):
        pin_type = self._make_pass_type(
            code="PIN_PASS",
            name="PIN Pass",
            validation_method=PassType.ValidationMethod.PIN,
        )
        pass_obj = PassService.generate(pass_type=pin_type, person=self.person)

        self.assertEqual(len(pass_obj.code), 6)
        self.assertTrue(pass_obj.code.isalnum())
        # PIN codes are uppercase alphanumeric.
        self.assertEqual(pass_obj.code, pass_obj.code.upper())

    def test_generate_digital_pass_code_format(self):
        digital_type = self._make_pass_type(
            code="DIGITAL_PASS",
            name="Digital Pass",
            validation_method=PassType.ValidationMethod.DIGITAL,
        )
        pass_obj = PassService.generate(pass_type=digital_type, person=self.person)

        # Digital passes are long URL-safe tokens (longer than QR's 16-byte token).
        self.assertTrue(pass_obj.code)
        self.assertGreater(len(pass_obj.code), 20)

    def test_generate_none_validation_method_empty_code(self):
        # DAILY_PASS uses ValidationMethod.NONE. The service's _generate_code
        # helper returns an empty string for NONE passes (no credential is
        # presented). The model's clean() rejects a truly blank code, so we
        # assert the helper's contract directly rather than via generate(),
        # which would raise ValidationError before saving.
        code = PassService._generate_code(self.daily_pass_type, self.society)

        self.assertEqual(code, "")

    # --- generate: guards ------------------------------------------------

    def test_generate_blacklisted_person_raises(self):
        blacklisted = self._make_person(
            name="Blacklisted",
            phone="+918888888888",
            is_blacklisted=True,
            blacklist_reason="Banned for misconduct",
        )
        with self.assertRaises(ValidationError):
            PassService.generate(pass_type=self.qr_pass_type, person=blacklisted)

    def test_generate_inactive_pass_type_raises(self):
        self.qr_pass_type.is_active = False
        self.qr_pass_type.save(update_fields=["is_active"])
        try:
            with self.assertRaises(ValidationError):
                PassService.generate(pass_type=self.qr_pass_type, person=self.person)
        finally:
            # Restore for other tests (class-level data).
            self.qr_pass_type.is_active = True
            self.qr_pass_type.save(update_fields=["is_active"])

    def test_generate_cross_society_raises(self):
        other_person = Person.objects.create(
            society=self.other_society, name="Other Visitor", phone="+917777777777"
        )
        with self.assertRaises(ValidationError):
            PassService.generate(
                pass_type=self.qr_pass_type, person=other_person
            )

    # --- generate: overrides --------------------------------------------

    def test_generate_custom_valid_from(self):
        start = timezone.now() + timedelta(hours=2)
        pass_obj = PassService.generate(
            pass_type=self.qr_pass_type, person=self.person, valid_from=start
        )

        self.assertEqual(pass_obj.valid_from, start)
        self.assertEqual(
            pass_obj.valid_until, start + timedelta(hours=self.qr_pass_type.default_validity_hours)
        )

    def test_generate_custom_max_usage(self):
        pass_obj = PassService.generate(
            pass_type=self.qr_pass_type, person=self.person, max_usage=5
        )

        self.assertEqual(pass_obj.max_usage, 5)

    # --- generate: audit -------------------------------------------------

    def test_generate_creates_audit_log(self):
        pass_obj = PassService.generate(
            pass_type=self.qr_pass_type, person=self.person, actor=self.user
        )

        self.assertTrue(
            GateOpsAuditLog.objects.filter(
                entity_type="Pass",
                entity_id=str(pass_obj.pk),
                action=GateOpsAuditLog.Action.CREATE,
            ).exists()
        )

    # --- validate --------------------------------------------------------

    def test_validate_valid_pass_returns_pass(self):
        pass_obj = PassService.generate(
            pass_type=self.qr_pass_type, person=self.person
        )

        result = PassService.validate(society=self.society, code=pass_obj.code)

        self.assertEqual(result.pk, pass_obj.pk)

    def test_validate_nonexistent_code_raises(self):
        with self.assertRaises(ValidationError):
            PassService.validate(society=self.society, code="DOES-NOT-EXIST")

    def test_validate_expired_status_raises(self):
        pass_obj = self._make_pass(status=Pass.Status.EXPIRED)
        with self.assertRaises(ValidationError):
            PassService.validate(society=self.society, code=pass_obj.code)

    def test_validate_suspended_status_raises(self):
        pass_obj = self._make_pass(status=Pass.Status.SUSPENDED)
        with self.assertRaises(ValidationError):
            PassService.validate(society=self.society, code=pass_obj.code)

    def test_validate_revoked_status_raises(self):
        pass_obj = self._make_pass(status=Pass.Status.REVOKED)
        with self.assertRaises(ValidationError):
            PassService.validate(society=self.society, code=pass_obj.code)

    def test_validate_outside_window_raises(self):
        now = timezone.now()
        pass_obj = self._make_pass(
            valid_from=now + timedelta(hours=1),
            valid_until=now + timedelta(hours=2),
        )
        with self.assertRaises(ValidationError):
            PassService.validate(society=self.society, code=pass_obj.code)

    def test_validate_usage_limit_reached_raises(self):
        pass_obj = self._make_pass(max_usage=1, usage_count=1)
        with self.assertRaises(ValidationError):
            PassService.validate(society=self.society, code=pass_obj.code)

    def test_validate_cross_society_raises(self):
        # A pass issued in self.society must not validate from other_society.
        pass_obj = PassService.generate(
            pass_type=self.qr_pass_type, person=self.person
        )
        with self.assertRaises(ValidationError):
            PassService.validate(society=self.other_society, code=pass_obj.code)

    # --- record_usage ----------------------------------------------------

    def test_record_usage_increments_count(self):
        pass_obj = self._make_pass(max_usage=5, usage_count=0)

        PassService.record_usage(pass_obj=pass_obj, actor=self.user)
        pass_obj.refresh_from_db()

        self.assertEqual(pass_obj.usage_count, 1)
        self.assertEqual(pass_obj.status, Pass.Status.ACTIVE)

    def test_record_usage_auto_expires_on_quota(self):
        pass_obj = self._make_pass(max_usage=1, usage_count=0)

        PassService.record_usage(pass_obj=pass_obj, actor=self.user)
        pass_obj.refresh_from_db()

        self.assertEqual(pass_obj.usage_count, 1)
        self.assertEqual(pass_obj.status, Pass.Status.EXPIRED)

    def test_record_usage_creates_audit_log(self):
        pass_obj = self._make_pass(max_usage=5, usage_count=0)

        PassService.record_usage(pass_obj=pass_obj, actor=self.user)

        self.assertTrue(
            GateOpsAuditLog.objects.filter(
                entity_type="Pass",
                entity_id=str(pass_obj.pk),
                action=GateOpsAuditLog.Action.STATE_TRANSITION,
            ).exists()
        )

    # --- revoke ----------------------------------------------------------

    def test_revoke_active_pass(self):
        pass_obj = self._make_pass(status=Pass.Status.ACTIVE)

        PassService.revoke(pass_obj=pass_obj, actor=self.user, reason="Misuse")
        pass_obj.refresh_from_db()

        self.assertEqual(pass_obj.status, Pass.Status.REVOKED)

    def test_revoke_suspended_pass(self):
        pass_obj = self._make_pass(status=Pass.Status.SUSPENDED)

        PassService.revoke(pass_obj=pass_obj, actor=self.user)
        pass_obj.refresh_from_db()

        self.assertEqual(pass_obj.status, Pass.Status.REVOKED)

    def test_revoke_already_revoked_raises(self):
        pass_obj = self._make_pass(status=Pass.Status.REVOKED)
        with self.assertRaises(ValidationError):
            PassService.revoke(pass_obj=pass_obj, actor=self.user)

    def test_revoke_expired_raises(self):
        pass_obj = self._make_pass(status=Pass.Status.EXPIRED)
        with self.assertRaises(ValidationError):
            PassService.revoke(pass_obj=pass_obj, actor=self.user)

    def test_revoke_creates_audit_log(self):
        pass_obj = self._make_pass(status=Pass.Status.ACTIVE)

        PassService.revoke(pass_obj=pass_obj, actor=self.user, reason="Test")

        self.assertTrue(
            GateOpsAuditLog.objects.filter(
                entity_type="Pass",
                entity_id=str(pass_obj.pk),
                action=GateOpsAuditLog.Action.STATE_TRANSITION,
            ).exists()
        )

    # --- suspend ---------------------------------------------------------

    def test_suspend_active_pass(self):
        pass_obj = self._make_pass(status=Pass.Status.ACTIVE)

        PassService.suspend(pass_obj=pass_obj, actor=self.user, reason="Review")
        pass_obj.refresh_from_db()

        self.assertEqual(pass_obj.status, Pass.Status.SUSPENDED)

    def test_suspend_already_suspended_raises(self):
        pass_obj = self._make_pass(status=Pass.Status.SUSPENDED)
        with self.assertRaises(ValidationError):
            PassService.suspend(pass_obj=pass_obj, actor=self.user)

    def test_suspend_revoked_raises(self):
        pass_obj = self._make_pass(status=Pass.Status.REVOKED)
        with self.assertRaises(ValidationError):
            PassService.suspend(pass_obj=pass_obj, actor=self.user)

    # --- reactivate ------------------------------------------------------

    def test_reactivate_suspended_pass(self):
        pass_obj = self._make_pass(status=Pass.Status.SUSPENDED)

        PassService.reactivate(pass_obj=pass_obj, actor=self.user)
        pass_obj.refresh_from_db()

        self.assertEqual(pass_obj.status, Pass.Status.ACTIVE)

    def test_reactivate_active_raises(self):
        pass_obj = self._make_pass(status=Pass.Status.ACTIVE)
        with self.assertRaises(ValidationError):
            PassService.reactivate(pass_obj=pass_obj, actor=self.user)

    def test_reactivate_revoked_raises(self):
        pass_obj = self._make_pass(status=Pass.Status.REVOKED)
        with self.assertRaises(ValidationError):
            PassService.reactivate(pass_obj=pass_obj, actor=self.user)

    def test_reactivate_expired_window_raises(self):
        now = timezone.now()
        # A suspended pass whose valid_until has already passed cannot be revived.
        pass_obj = self._make_pass(
            status=Pass.Status.SUSPENDED,
            valid_from=now - timedelta(hours=2),
            valid_until=now - timedelta(hours=1),
        )
        with self.assertRaises(ValidationError):
            PassService.reactivate(pass_obj=pass_obj, actor=self.user)

    # --- expire_expired_passes ------------------------------------------

    def test_expire_expired_passes_bulk(self):
        now = timezone.now()
        # Two stale ACTIVE passes and one still-valid pass.
        stale1 = self._make_pass(
            code="STALE-1",
            valid_from=now - timedelta(hours=2),
            valid_until=now - timedelta(hours=1),
        )
        stale2 = self._make_pass(
            code="STALE-2",
            valid_from=now - timedelta(hours=3),
            valid_until=now - timedelta(hours=2),
        )
        fresh = self._make_pass(
            code="FRESH-1",
            valid_from=now - timedelta(hours=1),
            valid_until=now + timedelta(hours=1),
        )

        PassService.expire_expired_passes()

        stale1.refresh_from_db()
        stale2.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(stale1.status, Pass.Status.EXPIRED)
        self.assertEqual(stale2.status, Pass.Status.EXPIRED)
        self.assertEqual(fresh.status, Pass.Status.ACTIVE)

    def test_expire_expired_passes_society_scoped(self):
        now = timezone.now()
        # Stale pass in self.society.
        own_stale = self._make_pass(
            code="OWN-STALE",
            valid_from=now - timedelta(hours=2),
            valid_until=now - timedelta(hours=1),
        )
        # Stale pass in other_society.
        other_person = Person.objects.create(
            society=self.other_society, name="Other", phone="+916666666666"
        )
        other_stale = Pass.objects.create(
            society=self.other_society,
            person=other_person,
            pass_type=self.other_qr_pass_type,
            code="OTHER-STALE",
            valid_from=now - timedelta(hours=2),
            valid_until=now - timedelta(hours=1),
            status=Pass.Status.ACTIVE,
            usage_count=0,
            max_usage=1,
        )

        PassService.expire_expired_passes(society=self.society)

        own_stale.refresh_from_db()
        other_stale.refresh_from_db()
        self.assertEqual(own_stale.status, Pass.Status.EXPIRED)
        # Other society's pass must be untouched.
        self.assertEqual(other_stale.status, Pass.Status.ACTIVE)

    def test_expire_expired_passes_returns_count(self):
        now = timezone.now()
        self._make_pass(
            code="CNT-1",
            valid_from=now - timedelta(hours=2),
            valid_until=now - timedelta(hours=1),
        )
        self._make_pass(
            code="CNT-2",
            valid_from=now - timedelta(hours=3),
            valid_until=now - timedelta(hours=2),
        )

        count = PassService.expire_expired_passes(society=self.society)

        self.assertEqual(count, 2)


class PassViewTest(TestCase):
    """Frontend tests for the Phase 5 pass views.

    Societies are created once per class in ``setUpTestData``; ``setUp`` logs
    in and selects the society so every view resolves the correct tenant.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = UserFactory(password="password")
        # create_society grants the user an active OWNER membership, which the
        # society-selection middleware requires to resolve the active society.
        cls.society = create_society(user=cls.user, name="Pass View Society")
        cls.other_society = create_society(
            user=UserFactory(password="password"), name="Other Pass View Society"
        )
        cls.qr_pass_type = PassType.objects.get(society=cls.society, code="QR_PASS")
        cls.other_qr_pass_type = PassType.objects.get(
            society=cls.other_society, code="QR_PASS"
        )

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self._select_society(self.society)
        self.person = Person.objects.create(
            society=self.society, name="View Visitor", phone="+915555555555"
        )

    def _select_society(self, society):
        session = self.client.session
        session[SESSION_SELECTED_SOCIETY_ID] = society.id
        session.save()

    def _make_pass(self, society=None, **overrides):
        society = society or self.society
        pass_type = (
            self.qr_pass_type if society == self.society else self.other_qr_pass_type
        )
        now = timezone.now()
        defaults = {
            "society": society,
            "person": Person.objects.create(
                society=society, name=f"Person {society.id}", phone="+914444444444"
            ),
            "pass_type": pass_type,
            "code": f"VIEW-{timezone.now().timestamp()}",
            "valid_from": now - timedelta(hours=1),
            "valid_until": now + timedelta(hours=1),
            "status": Pass.Status.ACTIVE,
            "usage_count": 0,
            "max_usage": 1,
        }
        defaults.update(overrides)
        return Pass.objects.create(**defaults)

    # --- list view -------------------------------------------------------

    def test_pass_list_view_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("gateops:pass-list"))

        # @login_required redirects anonymous users to the login page.
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    def test_pass_list_view_returns_200(self):
        response = self.client.get(reverse("gateops:pass-list"))

        self.assertEqual(response.status_code, 200)

    # --- detail view -----------------------------------------------------

    def test_pass_detail_view_404_for_other_society(self):
        other_pass = self._make_pass(society=self.other_society)
        # self.society is selected; the other society's pass must 404.
        response = self.client.get(
            reverse("gateops:pass-detail", kwargs={"pk": other_pass.pk})
        )

        self.assertEqual(response.status_code, 404)

    # --- revoke view -----------------------------------------------------

    def test_pass_revoke_view_post_only(self):
        pass_obj = self._make_pass(society=self.society)

        response = self.client.get(
            reverse("gateops:pass-revoke", kwargs={"pk": pass_obj.pk})
        )

        self.assertEqual(response.status_code, 405)

    def test_pass_revoke_view_revokes_pass(self):
        pass_obj = self._make_pass(society=self.society)

        response = self.client.post(
            reverse("gateops:pass-revoke", kwargs={"pk": pass_obj.pk}),
            data={"reason": "No longer needed"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("gateops:pass-detail", kwargs={"pk": pass_obj.pk}))
        pass_obj.refresh_from_db()
        self.assertEqual(pass_obj.status, Pass.Status.REVOKED)
