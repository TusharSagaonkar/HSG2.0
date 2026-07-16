"""
Test suite for gateops Phase 9 — Contractor Management.

Test conventions:
- SocietyTestCase base class provides cls.society and cls.user (created once
  per class via SocietyFactory with django_get_or_create, avoiding repeated
  bootstrap signal cascades).
- Per-test mutable records (contractors, contracts, workers, permits) are
  created per-test or via helper methods.
- Seeded Gate and VisitorCategory records are fetched in setUpTestData().

Covers:
- Contractor, Contract, Worker, WorkPermit models (clean, defaults, soft-delete)
- ContractorService (CRUD, expiry checks, attendance, rule engine context)
- Contractor management views (list, detail, create, deactivate, dashboard)
"""
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.http import Http404
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.test_base import SocietyTestCase
from core.test_factories import SocietyFactory, UserFactory
from gateops.models import (
    Contract,
    Contractor,
    Gate,
    GateEvent,
    GateOpsAuditLog,
    Person,
    VisitorCategory,
    WorkPermit,
    Worker,
)
from gateops.services.contractor_service import ContractorService
from gateops.services.gate_event_lifecycle import GateEventLifecycleService
from housing_accounting.selection import SESSION_SELECTED_SOCIETY_ID
from societies.services import create_society


# ---------------------------------------------------------------------------
# Model tests (1-24)
# ---------------------------------------------------------------------------
class ContractorModelTest(SocietyTestCase):
    """Model-level tests for Contractor, Contract, Worker, WorkPermit."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other_society = SocietyFactory(name="Test Society Beta")

    # --- helpers ---------------------------------------------------------

    def _make_contractor(self, **overrides):
        defaults = {
            "society": self.society,
            "company_name": f"Acme Corp {uuid.uuid4().hex[:6]}",
        }
        defaults.update(overrides)
        return Contractor.objects.create(**defaults)

    def _make_contract(self, **overrides):
        today = timezone.now().date()
        defaults = {
            "society": self.society,
            "contractor": self._make_contractor(),
            "title": f"Contract {uuid.uuid4().hex[:6]}",
            "start_date": today,
            "end_date": today + timedelta(days=30),
            "max_workers": 10,
        }
        defaults.update(overrides)
        return Contract.objects.create(**defaults)

    def _make_person(self, **overrides):
        defaults = {
            "society": self.society,
            "name": "John Doe",
            "phone": uuid.uuid4().hex[:10],
        }
        defaults.update(overrides)
        return Person.objects.create(**defaults)

    def _make_worker(self, **overrides):
        defaults = {
            "society": self.society,
            "contract": self._make_contract(),
            "person": self._make_person(),
        }
        defaults.update(overrides)
        return Worker.objects.create(**defaults)

    def _make_work_permit(self, **overrides):
        now = timezone.now()
        defaults = {
            "society": self.society,
            "contract": self._make_contract(),
            "permit_number": f"WP-{uuid.uuid4().hex[:6]}",
            "issued_at": now,
            "expires_at": now + timedelta(days=7),
        }
        defaults.update(overrides)
        return WorkPermit.objects.create(**defaults)

    # --- Contractor model (1-7) ------------------------------------------

    def test_contractor_creation_with_all_required_fields(self):
        contractor = self._make_contractor(company_name="Acme Corp")
        self.assertEqual(contractor.society, self.society)
        self.assertEqual(contractor.company_name, "Acme Corp")
        self.assertTrue(contractor.is_active)
        self.assertIsNone(contractor.deleted_at)
        self.assertIsNotNone(contractor.created_at)
        self.assertIsNotNone(contractor.updated_at)

    def test_contractor_str_representation(self):
        contractor = self._make_contractor(company_name="Acme Corp")
        self.assertEqual(str(contractor), f"Acme Corp ({self.society.name})")

    def test_contractor_clean_rejects_blank_company_name(self):
        contractor = Contractor(society=self.society, company_name="")
        with self.assertRaises(ValidationError):
            contractor.clean()

    def test_contractor_clean_rejects_whitespace_company_name(self):
        contractor = Contractor(society=self.society, company_name="   ")
        with self.assertRaises(ValidationError):
            contractor.clean()

    def test_contractor_default_is_active_true(self):
        contractor = Contractor(society=self.society, company_name="Acme Corp")
        self.assertTrue(contractor.is_active)

    def test_contractor_soft_delete_sets_is_active_false_and_deleted_at(self):
        contractor = self._make_contractor()
        deleted_at = timezone.now()
        contractor.is_active = False
        contractor.deleted_at = deleted_at
        contractor.save(update_fields=["is_active", "deleted_at"])
        contractor.refresh_from_db()
        self.assertFalse(contractor.is_active)
        self.assertIsNotNone(contractor.deleted_at)

    def test_contractor_soft_deleted_remains_in_db(self):
        contractor = self._make_contractor()
        contractor.is_active = False
        contractor.deleted_at = timezone.now()
        contractor.save(update_fields=["is_active", "deleted_at"])
        self.assertTrue(Contractor.objects.filter(pk=contractor.pk).exists())

    # --- Contract model (8-15) ------------------------------------------

    def test_contract_creation_with_all_required_fields(self):
        today = timezone.now().date()
        contract = self._make_contract(
            title="Plumbing Work",
            start_date=today,
            end_date=today + timedelta(days=30),
            max_workers=10,
        )
        self.assertEqual(contract.society, self.society)
        self.assertEqual(contract.title, "Plumbing Work")
        self.assertEqual(contract.start_date, today)
        self.assertEqual(contract.end_date, today + timedelta(days=30))
        self.assertEqual(contract.max_workers, 10)

    def test_contract_str_representation(self):
        contractor = self._make_contractor(company_name="Acme Corp")
        contract = self._make_contract(contractor=contractor, title="Plumbing")
        self.assertEqual(str(contract), "Plumbing — Acme Corp")

    def test_contract_default_status_active(self):
        contract = self._make_contract()
        self.assertEqual(contract.status, Contract.Status.ACTIVE)

    def test_contract_default_max_workers_10(self):
        contract = self._make_contract()
        self.assertEqual(contract.max_workers, 10)

    def test_contract_clean_rejects_end_date_before_start_date(self):
        today = timezone.now().date()
        contract = Contract(
            society=self.society,
            contractor=self._make_contractor(),
            title="Bad Contract",
            start_date=today,
            end_date=today - timedelta(days=1),
            max_workers=10,
        )
        with self.assertRaises(ValidationError):
            contract.clean()

    def test_contract_clean_rejects_zero_max_workers(self):
        today = timezone.now().date()
        contract = Contract(
            society=self.society,
            contractor=self._make_contractor(),
            title="Zero Workers",
            start_date=today,
            end_date=today + timedelta(days=30),
            max_workers=0,
        )
        with self.assertRaises(ValidationError):
            contract.clean()

    def test_contract_clean_accepts_end_date_equal_start_date(self):
        today = timezone.now().date()
        contract = Contract(
            society=self.society,
            contractor=self._make_contractor(),
            title="Same Day",
            start_date=today,
            end_date=today,
            max_workers=5,
        )
        contract.clean()  # Should not raise.

    def test_contract_soft_delete_sets_is_active_false_and_deleted_at(self):
        contract = self._make_contract()
        contract.is_active = False
        contract.deleted_at = timezone.now()
        contract.save(update_fields=["is_active", "deleted_at"])
        contract.refresh_from_db()
        self.assertFalse(contract.is_active)
        self.assertIsNotNone(contract.deleted_at)

    # --- Worker model (16-19) --------------------------------------------

    def test_worker_creation_with_all_required_fields(self):
        worker = self._make_worker()
        self.assertEqual(worker.society, self.society)
        self.assertIsNotNone(worker.contract)
        self.assertIsNotNone(worker.person)
        self.assertTrue(worker.is_active)

    def test_worker_str_representation(self):
        person = self._make_person(name="Jane Smith")
        contract = self._make_contract(title="Plumbing")
        worker = self._make_worker(contract=contract, person=person)
        self.assertEqual(str(worker), "Jane Smith — Plumbing")

    def test_worker_clean_rejects_cross_society_person(self):
        contract = self._make_contract()
        other_person = Person.objects.create(
            society=self.other_society,
            name="Other Person",
            phone=uuid.uuid4().hex[:10],
        )
        worker = Worker(
            society=self.society,
            contract=contract,
            person=other_person,
        )
        with self.assertRaises(ValidationError):
            worker.clean()

    def test_worker_soft_delete_sets_is_active_false_and_deleted_at(self):
        worker = self._make_worker()
        worker.is_active = False
        worker.deleted_at = timezone.now()
        worker.save(update_fields=["is_active", "deleted_at"])
        worker.refresh_from_db()
        self.assertFalse(worker.is_active)
        self.assertIsNotNone(worker.deleted_at)

    # --- WorkPermit model (20-24) ----------------------------------------

    def test_work_permit_creation_with_all_required_fields(self):
        now = timezone.now()
        permit = self._make_work_permit(
            permit_number="WP-001",
            issued_at=now,
            expires_at=now + timedelta(days=7),
        )
        self.assertEqual(permit.society, self.society)
        self.assertEqual(permit.permit_number, "WP-001")
        self.assertEqual(permit.issued_at, now)
        self.assertEqual(permit.expires_at, now + timedelta(days=7))
        self.assertTrue(permit.is_active)

    def test_work_permit_str_representation(self):
        contract = self._make_contract(title="Plumbing")
        permit = self._make_work_permit(
            contract=contract, permit_number="WP-001"
        )
        self.assertEqual(str(permit), "WP-WP-001 — Plumbing")

    def test_work_permit_default_status_active(self):
        permit = self._make_work_permit()
        self.assertEqual(permit.status, WorkPermit.Status.ACTIVE)

    def test_work_permit_clean_rejects_expires_at_before_issued_at(self):
        now = timezone.now()
        permit = WorkPermit(
            society=self.society,
            contract=self._make_contract(),
            permit_number="WP-BAD",
            issued_at=now,
            expires_at=now - timedelta(hours=1),
        )
        with self.assertRaises(ValidationError):
            permit.clean()

    def test_work_permit_clean_rejects_expires_at_equal_issued_at(self):
        now = timezone.now()
        permit = WorkPermit(
            society=self.society,
            contract=self._make_contract(),
            permit_number="WP-EQUAL",
            issued_at=now,
            expires_at=now,
        )
        with self.assertRaises(ValidationError):
            permit.clean()


# ---------------------------------------------------------------------------
# Service tests (25-70)
# ---------------------------------------------------------------------------
class ContractorServiceTest(SocietyTestCase):
    """Service-level tests for ContractorService.

    The society and seeded master data are created once per class via
    setUpTestData to avoid re-running the expensive gateops bootstrap signal.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.gate = Gate.objects.get(society=cls.society, code="MAIN")
        cls.visitor_cat = VisitorCategory.objects.get(
            society=cls.society, code="GUEST"
        )
        cls.contractor_cat = VisitorCategory.objects.get(
            society=cls.society, code="CONTRACTOR"
        )
        cls.other_society = SocietyFactory(name="Test Society Beta")
        cls.other_gate = Gate.objects.get(society=cls.other_society, code="MAIN")
        cls.other_visitor_cat = VisitorCategory.objects.get(
            society=cls.other_society, code="GUEST"
        )

    # --- helpers ---------------------------------------------------------

    def _make_contractor(self, **overrides):
        defaults = {
            "society": self.society,
            "company_name": f"Acme Corp {uuid.uuid4().hex[:6]}",
            "actor": self.user,
        }
        defaults.update(overrides)
        return ContractorService.create_contractor(**defaults)

    def _make_contract(self, **overrides):
        today = timezone.now().date()
        defaults = {
            "society": self.society,
            "contractor": self._make_contractor(),
            "title": f"Contract {uuid.uuid4().hex[:6]}",
            "start_date": today,
            "end_date": today + timedelta(days=30),
            "max_workers": 10,
            "actor": self.user,
        }
        defaults.update(overrides)
        return ContractorService.create_contract(**defaults)

    def _make_person(self, **overrides):
        defaults = {
            "society": self.society,
            "name": "John Doe",
            "phone": uuid.uuid4().hex[:10],
        }
        defaults.update(overrides)
        return Person.objects.create(**defaults)

    def _make_worker(self, **overrides):
        defaults = {
            "society": self.society,
            "contract": self._make_contract(),
            "person": self._make_person(),
            "actor": self.user,
        }
        defaults.update(overrides)
        return ContractorService.register_worker(**defaults)

    def _make_work_permit(self, **overrides):
        now = timezone.now()
        defaults = {
            "society": self.society,
            "contract": self._make_contract(),
            "permit_number": f"WP-{uuid.uuid4().hex[:6]}",
            "issued_at": now,
            "expires_at": now + timedelta(days=7),
            "actor": self.user,
        }
        defaults.update(overrides)
        return ContractorService.issue_work_permit(**defaults)

    def _make_other_contractor(self, **overrides):
        defaults = {
            "society": self.other_society,
            "company_name": f"Other Corp {uuid.uuid4().hex[:6]}",
            "actor": self.user,
        }
        defaults.update(overrides)
        return ContractorService.create_contractor(**defaults)

    def _make_other_contract(self, **overrides):
        today = timezone.now().date()
        defaults = {
            "society": self.other_society,
            "contractor": self._make_other_contractor(),
            "title": f"Other Contract {uuid.uuid4().hex[:6]}",
            "start_date": today,
            "end_date": today + timedelta(days=30),
            "actor": self.user,
        }
        defaults.update(overrides)
        return ContractorService.create_contract(**defaults)

    def _make_gate_event(self, **overrides):
        defaults = {
            "society": self.society,
            "gate": self.gate,
            "visitor_category": self.visitor_cat,
            "event_type": GateEvent.EventType.ARRIVAL,
            "status": GateEvent.Status.ARRIVED,
            "direction": GateEvent.Direction.INBOUND,
            "arrived_at": timezone.now(),
        }
        defaults.update(overrides)
        return GateEvent.objects.create(**defaults)

    # --- Contractor CRUD (25-33) ----------------------------------------

    def test_create_contractor_creates_with_correct_fields(self):
        contractor = ContractorService.create_contractor(
            society=self.society,
            company_name="Acme Corp",
            supervisor_name="Jane Smith",
            supervisor_phone="9876543210",
            gst_number="GST123",
            actor=self.user,
        )
        self.assertEqual(contractor.society, self.society)
        self.assertEqual(contractor.company_name, "Acme Corp")
        self.assertEqual(contractor.supervisor_name, "Jane Smith")
        self.assertEqual(contractor.supervisor_phone, "9876543210")
        self.assertEqual(contractor.gst_number, "GST123")

    def test_create_contractor_sets_is_active_true(self):
        contractor = self._make_contractor()
        self.assertTrue(contractor.is_active)
        self.assertIsNone(contractor.deleted_at)

    def test_create_contractor_creates_audit_log(self):
        contractor = self._make_contractor()
        log = GateOpsAuditLog.objects.filter(
            entity_type="Contractor",
            entity_id=str(contractor.pk),
            action=GateOpsAuditLog.Action.CREATE,
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.user)
        self.assertIsNotNone(log.after_value)

    def test_create_contractor_audit_failure_doesnt_block_creation(self):
        with patch.object(
            GateOpsAuditLog,
            "log",
            side_effect=Exception("DB connection lost"),
        ):
            contractor = ContractorService.create_contractor(
                society=self.society,
                company_name="Audit Fail Corp",
                actor=self.user,
            )
        self.assertIsNotNone(contractor.pk)
        self.assertTrue(contractor.is_active)

    def test_create_contractor_rejects_blank_company_name(self):
        with self.assertRaises(ValidationError):
            ContractorService.create_contractor(
                society=self.society,
                company_name="",
            )

    def test_update_contractor_updates_allowed_fields(self):
        contractor = self._make_contractor()
        result = ContractorService.update_contractor(
            contractor=contractor,
            company_name="Updated Co",
            supervisor_name="Jane Smith",
        )
        result.refresh_from_db()
        self.assertEqual(result.company_name, "Updated Co")
        self.assertEqual(result.supervisor_name, "Jane Smith")

    def test_update_contractor_rejects_unknown_fields(self):
        contractor = self._make_contractor()
        with self.assertRaises(ValidationError):
            ContractorService.update_contractor(
                contractor=contractor,
                society=self.other_society,
            )

    def test_deactivate_contractor_sets_is_active_false(self):
        contractor = self._make_contractor()
        result = ContractorService.deactivate_contractor(
            contractor=contractor, actor=self.user
        )
        result.refresh_from_db()
        self.assertFalse(result.is_active)
        self.assertIsNotNone(result.deleted_at)

    def test_deactivate_contractor_creates_audit_log(self):
        contractor = self._make_contractor()
        ContractorService.deactivate_contractor(
            contractor=contractor, actor=self.user
        )
        log = GateOpsAuditLog.objects.filter(
            entity_type="Contractor",
            entity_id=str(contractor.pk),
            action=GateOpsAuditLog.Action.STATE_TRANSITION,
        ).first()
        self.assertIsNotNone(log)
        self.assertIsNotNone(log.before_value)
        self.assertIsNotNone(log.after_value)

    # --- Contractor list / get (34-38) -----------------------------------

    def test_list_contractors_returns_active_only_by_default(self):
        active = self._make_contractor()
        inactive = self._make_contractor()
        ContractorService.deactivate_contractor(contractor=inactive)
        result = ContractorService.list_contractors(society=self.society)
        pks = [c.pk for c in result]
        self.assertIn(active.pk, pks)
        self.assertNotIn(inactive.pk, pks)

    def test_list_contractors_include_inactive_returns_all(self):
        active = self._make_contractor()
        inactive = self._make_contractor()
        ContractorService.deactivate_contractor(contractor=inactive)
        result = ContractorService.list_contractors(
            society=self.society, include_inactive=True
        )
        pks = [c.pk for c in result]
        self.assertIn(active.pk, pks)
        self.assertIn(inactive.pk, pks)

    def test_list_contractors_society_scoped(self):
        own = self._make_contractor()
        other = self._make_other_contractor()
        result = ContractorService.list_contractors(society=self.society)
        pks = [c.pk for c in result]
        self.assertIn(own.pk, pks)
        self.assertNotIn(other.pk, pks)

    def test_get_contractor_returns_contractor(self):
        contractor = self._make_contractor()
        result = ContractorService.get_contractor(
            society=self.society, pk=contractor.pk
        )
        self.assertEqual(result.pk, contractor.pk)

    def test_get_contractor_404_for_other_society(self):
        other = self._make_other_contractor()
        with self.assertRaises(Http404):
            ContractorService.get_contractor(
                society=self.society, pk=other.pk
            )

    # --- Contract CRUD (39-43) -------------------------------------------

    def test_create_contract_creates_with_correct_fields(self):
        today = timezone.now().date()
        contractor = self._make_contractor()
        contract = ContractorService.create_contract(
            society=self.society,
            contractor=contractor,
            title="Plumbing Work",
            start_date=today,
            end_date=today + timedelta(days=30),
            max_workers=15,
            description="Kitchen plumbing",
            actor=self.user,
        )
        self.assertEqual(contract.society, self.society)
        self.assertEqual(contract.contractor, contractor)
        self.assertEqual(contract.title, "Plumbing Work")
        self.assertEqual(contract.start_date, today)
        self.assertEqual(contract.end_date, today + timedelta(days=30))
        self.assertEqual(contract.max_workers, 15)
        self.assertEqual(contract.description, "Kitchen plumbing")

    def test_create_contract_sets_status_active(self):
        contract = self._make_contract()
        self.assertEqual(contract.status, Contract.Status.ACTIVE)

    def test_create_contract_rejects_cross_society_contractor(self):
        today = timezone.now().date()
        other_contractor = self._make_other_contractor()
        with self.assertRaises(ValidationError):
            ContractorService.create_contract(
                society=self.society,
                contractor=other_contractor,
                title="Cross Society",
                start_date=today,
                end_date=today + timedelta(days=30),
            )

    def test_create_contract_creates_audit_log(self):
        contract = self._make_contract()
        log = GateOpsAuditLog.objects.filter(
            entity_type="Contract",
            entity_id=str(contract.pk),
            action=GateOpsAuditLog.Action.CREATE,
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.user)

    def test_deactivate_contract_sets_is_active_false(self):
        contract = self._make_contract()
        result = ContractorService.deactivate_contract(
            contract=contract, actor=self.user
        )
        result.refresh_from_db()
        self.assertFalse(result.is_active)
        self.assertIsNotNone(result.deleted_at)

    # --- Contract list (44-45) --------------------------------------------

    def test_list_contracts_society_scoped(self):
        own = self._make_contract()
        other = self._make_other_contract()
        result = ContractorService.list_contracts(society=self.society)
        pks = [c.pk for c in result]
        self.assertIn(own.pk, pks)
        self.assertNotIn(other.pk, pks)

    def test_list_contracts_filtered_by_contractor(self):
        contractor = self._make_contractor()
        c1 = self._make_contract(contractor=contractor, title="Job A")
        c2 = self._make_contract(title="Job B")
        result = ContractorService.list_contracts(
            society=self.society, contractor=contractor
        )
        pks = [c.pk for c in result]
        self.assertIn(c1.pk, pks)
        self.assertNotIn(c2.pk, pks)

    # --- Worker CRUD (46-49) ---------------------------------------------

    def test_register_worker_creates_with_correct_fields(self):
        contract = self._make_contract()
        person = self._make_person(name="Alice")
        worker = ContractorService.register_worker(
            society=self.society,
            contract=contract,
            person=person,
            designation="Plumber",
            id_type=Worker.IdType.AADHAAR,
            id_number="1234-5678-9012",
            actor=self.user,
        )
        self.assertEqual(worker.society, self.society)
        self.assertEqual(worker.contract, contract)
        self.assertEqual(worker.person, person)
        self.assertEqual(worker.designation, "Plumber")
        self.assertEqual(worker.id_type, Worker.IdType.AADHAAR)
        self.assertTrue(worker.is_active)

    def test_register_worker_rejects_cross_society_contract(self):
        other_contract = self._make_other_contract()
        person = self._make_person()
        with self.assertRaises(ValidationError):
            ContractorService.register_worker(
                society=self.society,
                contract=other_contract,
                person=person,
            )

    def test_register_worker_rejects_cross_society_person(self):
        contract = self._make_contract()
        other_person = Person.objects.create(
            society=self.other_society,
            name="Other Person",
            phone=uuid.uuid4().hex[:10],
        )
        with self.assertRaises(ValidationError):
            ContractorService.register_worker(
                society=self.society,
                contract=contract,
                person=other_person,
            )

    def test_register_worker_rejects_non_active_contract(self):
        contract = self._make_contract()
        contract.status = Contract.Status.SUSPENDED
        contract.save(update_fields=["status"])
        person = self._make_person()
        with self.assertRaises(ValidationError):
            ContractorService.register_worker(
                society=self.society,
                contract=contract,
                person=person,
            )

    # --- WorkPermit CRUD (50-51) ------------------------------------------

    def test_issue_work_permit_creates_with_correct_fields(self):
        now = timezone.now()
        contract = self._make_contract()
        permit = ContractorService.issue_work_permit(
            society=self.society,
            contract=contract,
            permit_number="WP-001",
            issued_at=now,
            expires_at=now + timedelta(days=7),
            safety_docs_verified=True,
            safety_briefing_given=True,
            work_area="Tower A",
            hazard_level=WorkPermit.HazardLevel.HIGH,
            actor=self.user,
        )
        self.assertEqual(permit.society, self.society)
        self.assertEqual(permit.contract, contract)
        self.assertEqual(permit.permit_number, "WP-001")
        self.assertTrue(permit.safety_docs_verified)
        self.assertTrue(permit.safety_briefing_given)
        self.assertEqual(permit.work_area, "Tower A")
        self.assertEqual(permit.hazard_level, WorkPermit.HazardLevel.HIGH)

    def test_issue_work_permit_sets_status_active(self):
        permit = self._make_work_permit()
        self.assertEqual(permit.status, WorkPermit.Status.ACTIVE)

    # --- Expiry checks (52-60) -------------------------------------------

    def test_register_worker_rejects_when_max_workers_reached(self):
        contract = self._make_contract(max_workers=1)
        person1 = self._make_person(name="Alice")
        ContractorService.register_worker(
            society=self.society,
            contract=contract,
            person=person1,
            actor=self.user,
        )
        person2 = self._make_person(name="Bob")
        with self.assertRaises(ValidationError):
            ContractorService.register_worker(
                society=self.society,
                contract=contract,
                person=person2,
                actor=self.user,
            )

    def test_deactivate_worker_sets_is_active_false(self):
        worker = self._make_worker()
        result = ContractorService.deactivate_worker(
            worker=worker, actor=self.user
        )
        result.refresh_from_db()
        self.assertFalse(result.is_active)
        self.assertIsNotNone(result.deleted_at)

    def test_revoke_work_permit_sets_status_revoked(self):
        permit = self._make_work_permit()
        result = ContractorService.revoke_work_permit(
            work_permit=permit, actor=self.user
        )
        result.refresh_from_db()
        self.assertEqual(result.status, WorkPermit.Status.REVOKED)

    def test_check_contract_expiry_returns_correct_dict(self):
        today = timezone.now().date()
        contract = self._make_contract(
            start_date=today - timedelta(days=10),
            end_date=today + timedelta(days=5),
        )
        result = ContractorService.check_contract_expiry(
            contract=contract, as_of=today
        )
        self.assertFalse(result["is_expired"])
        self.assertEqual(result["days_until_expiry"], 5)
        self.assertEqual(result["expiry_date"], contract.end_date)

    def test_check_contract_expiry_with_custom_as_of(self):
        today = timezone.now().date()
        contract = self._make_contract(
            start_date=today - timedelta(days=10),
            end_date=today + timedelta(days=5),
        )
        result = ContractorService.check_contract_expiry(
            contract=contract, as_of=today + timedelta(days=3)
        )
        self.assertFalse(result["is_expired"])
        self.assertEqual(result["days_until_expiry"], 2)

    def test_check_work_permit_expiry_returns_correct_dict(self):
        now = timezone.now()
        permit = self._make_work_permit(
            issued_at=now,
            expires_at=now + timedelta(days=5, hours=1),
        )
        result = ContractorService.check_work_permit_expiry(
            work_permit=permit, as_of=now
        )
        self.assertFalse(result["is_expired"])
        self.assertEqual(result["days_until_expiry"], 5)
        self.assertEqual(result["expiry_datetime"], permit.expires_at)

    def test_get_expired_contracts_returns_past_due(self):
        today = timezone.now().date()
        expired = self._make_contract(
            start_date=today - timedelta(days=10),
            end_date=today - timedelta(days=1),
        )
        active = self._make_contract(
            start_date=today,
            end_date=today + timedelta(days=30),
        )
        result = ContractorService.get_expired_contracts(
            society=self.society, as_of=today
        )
        pks = [c.pk for c in result]
        self.assertIn(expired.pk, pks)
        self.assertNotIn(active.pk, pks)

    def test_get_expired_work_permits_returns_past_due(self):
        now = timezone.now()
        expired = self._make_work_permit(
            issued_at=now - timedelta(days=2),
            expires_at=now - timedelta(days=1),
        )
        active = self._make_work_permit(
            issued_at=now,
            expires_at=now + timedelta(days=7),
        )
        result = ContractorService.get_expired_work_permits(
            society=self.society, as_of=now
        )
        pks = [p.pk for p in result]
        self.assertIn(expired.pk, pks)
        self.assertNotIn(active.pk, pks)

    def test_process_expiries_marks_contracts_and_permits(self):
        today = timezone.now().date()
        now = timezone.now()
        expired_contract = self._make_contract(
            start_date=today - timedelta(days=10),
            end_date=today - timedelta(days=1),
        )
        expired_permit = self._make_work_permit(
            issued_at=now - timedelta(days=2),
            expires_at=now - timedelta(days=1),
        )
        result = ContractorService.process_expiries(
            society=self.society, actor=self.user
        )
        expired_contract.refresh_from_db()
        expired_permit.refresh_from_db()
        self.assertEqual(
            expired_contract.status, Contract.Status.COMPLETED
        )
        self.assertEqual(
            expired_permit.status, WorkPermit.Status.EXPIRED
        )
        self.assertGreaterEqual(result["contracts_marked_completed"], 1)
        self.assertGreaterEqual(result["work_permits_marked_expired"], 1)

    # --- Attendance (61-65) ----------------------------------------------

    def test_check_in_worker_creates_gate_event(self):
        worker = self._make_worker()
        event = ContractorService.check_in_worker(
            worker=worker, actor=self.user
        )
        self.assertIsNotNone(event.pk)
        self.assertEqual(event.society, self.society)
        self.assertEqual(event.person, worker.person)

    def test_check_in_worker_sets_contractor_fk(self):
        worker = self._make_worker()
        event = ContractorService.check_in_worker(
            worker=worker, actor=self.user
        )
        event.refresh_from_db()
        self.assertEqual(event.contractor, worker.contract.contractor)
        self.assertEqual(event.contract, worker.contract)

    def test_check_out_worker_raises_when_no_active_event(self):
        worker = self._make_worker()
        with self.assertRaises(ValidationError):
            ContractorService.check_out_worker(worker=worker)

    def test_check_out_worker_transitions_to_exited(self):
        worker = self._make_worker()
        event = ContractorService.check_in_worker(
            worker=worker, actor=self.user
        )
        event.refresh_from_db()
        # Drive the event through approve → entered so check_out can find it.
        GateEventLifecycleService.approve(event, approved_by=self.user)
        GateEventLifecycleService.record_entry(event)
        result = ContractorService.check_out_worker(
            worker=worker, actor=self.user
        )
        result.refresh_from_db()
        self.assertEqual(result.status, GateEvent.Status.EXITED)
        self.assertIsNotNone(result.exited_at)

    def test_get_active_workers_on_site_returns_entered_events(self):
        worker = self._make_worker()
        event = ContractorService.check_in_worker(
            worker=worker, actor=self.user
        )
        event.refresh_from_db()
        GateEventLifecycleService.approve(event, approved_by=self.user)
        GateEventLifecycleService.record_entry(event)
        result = ContractorService.get_active_workers_on_site(
            society=self.society
        )
        pks = [e.pk for e in result]
        self.assertIn(event.pk, pks)

    # --- Rule engine context integration (66-70) -------------------------

    def test_build_contractor_expiry_context_returns_none_for_non_contractor(self):
        event = self._make_gate_event()
        result = GateEventLifecycleService._build_contractor_expiry_context(event)
        self.assertIsNone(result)

    def test_build_contractor_expiry_context_with_contract_fk(self):
        contract = self._make_contract()
        event = self._make_gate_event(
            contractor=contract.contractor,
            contract=contract,
        )
        result = GateEventLifecycleService._build_contractor_expiry_context(event)
        self.assertIsNotNone(result)
        self.assertIn("contract_expired", result)
        self.assertFalse(result["contract_expired"])

    def test_build_contractor_expiry_context_contract_expired(self):
        today = timezone.now().date()
        expired_contract = self._make_contract(
            start_date=today - timedelta(days=10),
            end_date=today - timedelta(days=1),
        )
        event = self._make_gate_event(
            contractor=expired_contract.contractor,
            contract=expired_contract,
        )
        result = GateEventLifecycleService._build_contractor_expiry_context(event)
        self.assertIsNotNone(result)
        self.assertTrue(result["contract_expired"])
        self.assertLess(result["days_until_contract_expiry"], 0)

    def test_build_contractor_expiry_context_with_work_permit(self):
        contract = self._make_contract()
        now = timezone.now()
        permit = self._make_work_permit(
            contract=contract,
            issued_at=now,
            expires_at=now + timedelta(days=7),
        )
        event = self._make_gate_event(
            contractor=contract.contractor,
            contract=contract,
            work_permit=permit,
        )
        result = GateEventLifecycleService._build_contractor_expiry_context(event)
        self.assertIsNotNone(result)
        self.assertTrue(result["has_active_permit"])
        self.assertFalse(result["permit_expired"])

    def test_build_contractor_expiry_context_permit_expired(self):
        contract = self._make_contract()
        now = timezone.now()
        expired_permit = self._make_work_permit(
            contract=contract,
            issued_at=now - timedelta(days=2),
            expires_at=now - timedelta(days=1),
        )
        event = self._make_gate_event(
            contractor=contract.contractor,
            contract=contract,
            work_permit=expired_permit,
        )
        result = GateEventLifecycleService._build_contractor_expiry_context(event)
        self.assertIsNotNone(result)
        self.assertTrue(result["permit_expired"])
        self.assertLess(result["days_until_permit_expiry"], 0)


# ---------------------------------------------------------------------------
# View tests (71-86)
# ---------------------------------------------------------------------------
class ContractorViewTest(TestCase):
    """Frontend tests for the Phase 9 contractor management views.

    Societies are created once per class in setUpTestData; setUp logs in and
    selects the society so every view resolves the correct tenant.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = UserFactory(password="password")
        cls.society = create_society(
            user=cls.user, name="Contractor View Society"
        )
        cls.gate = Gate.objects.get(society=cls.society, code="MAIN")
        cls.visitor_cat = VisitorCategory.objects.get(
            society=cls.society, code="GUEST"
        )
        cls.other_society = create_society(
            user=UserFactory(password="password"),
            name="Other Contractor View Society",
        )

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self._select_society(self.society)
        self.contractor = ContractorService.create_contractor(
            society=self.society, company_name="Acme Corp", actor=self.user
        )
        self.contract = ContractorService.create_contract(
            society=self.society,
            contractor=self.contractor,
            title="Plumbing Work",
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timedelta(days=30),
            actor=self.user,
        )
        self.work_permit = ContractorService.issue_work_permit(
            society=self.society,
            contract=self.contract,
            permit_number="WP-001",
            issued_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=7),
            actor=self.user,
        )
        self.other_contractor = ContractorService.create_contractor(
            society=self.other_society,
            company_name="Other Corp",
            actor=self.user,
        )

    # --- helpers ---------------------------------------------------------

    def _select_society(self, society):
        session = self.client.session
        session[SESSION_SELECTED_SOCIETY_ID] = society.id
        session.save()

    # --- login required (71) ---------------------------------------------

    def test_contractor_list_view_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("gateops:contractor-list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    # --- list / detail views (72-74) -------------------------------------

    def test_contractor_list_view_returns_200(self):
        response = self.client.get(reverse("gateops:contractor-list"))
        self.assertEqual(response.status_code, 200)

    def test_contractor_detail_view_200_for_own_society(self):
        response = self.client.get(
            reverse(
                "gateops:contractor-detail",
                kwargs={"pk": self.contractor.pk},
            )
        )
        self.assertEqual(response.status_code, 200)

    def test_contractor_detail_view_404_for_other_society(self):
        response = self.client.get(
            reverse(
                "gateops:contractor-detail",
                kwargs={"pk": self.other_contractor.pk},
            )
        )
        self.assertEqual(response.status_code, 404)

    # --- create view (75-76) ---------------------------------------------

    def test_contractor_create_view_get_returns_200(self):
        response = self.client.get(reverse("gateops:contractor-create"))
        self.assertEqual(response.status_code, 200)

    def test_contractor_create_view_post_creates_contractor(self):
        response = self.client.post(
            reverse("gateops:contractor-create"),
            data={
                "company_name": "Test Co",
                "supervisor_name": "Jane Smith",
                "supervisor_phone": "9876543210",
            },
        )
        self.assertEqual(response.status_code, 302)
        contractor = Contractor.objects.get(
            society=self.society, company_name="Test Co"
        )
        self.assertEqual(contractor.supervisor_name, "Jane Smith")
        self.assertEqual(
            response.url,
            reverse(
                "gateops:contractor-detail",
                kwargs={"pk": contractor.pk},
            ),
        )

    # --- deactivate view (77-79) -----------------------------------------

    def test_contractor_deactivate_view_post_only(self):
        response = self.client.get(
            reverse(
                "gateops:contractor-deactivate",
                kwargs={"pk": self.contractor.pk},
            )
        )
        self.assertEqual(response.status_code, 405)

    def test_contractor_deactivate_view_post_deactivates(self):
        response = self.client.post(
            reverse(
                "gateops:contractor-deactivate",
                kwargs={"pk": self.contractor.pk},
            )
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("gateops:contractor-list"))
        self.contractor.refresh_from_db()
        self.assertFalse(self.contractor.is_active)

    def test_contractor_deactivate_view_404_for_other_society(self):
        response = self.client.post(
            reverse(
                "gateops:contractor-deactivate",
                kwargs={"pk": self.other_contractor.pk},
            )
        )
        self.assertEqual(response.status_code, 404)

    # --- contract views (80-82) -------------------------------------------

    def test_contract_list_view_returns_200(self):
        response = self.client.get(reverse("gateops:contract-list"))
        self.assertEqual(response.status_code, 200)

    def test_contract_create_view_post_creates_contract(self):
        today = timezone.now().date()
        response = self.client.post(
            reverse("gateops:contract-create"),
            data={
                "contractor": self.contractor.pk,
                "title": "Electrical Work",
                "start_date": today.isoformat(),
                "end_date": (today + timedelta(days=30)).isoformat(),
                "max_workers": 10,
                "status": "active",
            },
        )
        self.assertEqual(response.status_code, 302)
        contract = Contract.objects.get(
            society=self.society, title="Electrical Work"
        )
        self.assertEqual(contract.contractor, self.contractor)
        self.assertEqual(contract.max_workers, 10)

    def test_contract_deactivate_view_post_only(self):
        response = self.client.get(
            reverse(
                "gateops:contract-deactivate",
                kwargs={"pk": self.contract.pk},
            )
        )
        self.assertEqual(response.status_code, 405)

    # --- worker / work-permit / dashboard views (83-86) ------------------

    def test_worker_list_view_returns_200(self):
        response = self.client.get(reverse("gateops:worker-list"))
        self.assertEqual(response.status_code, 200)

    def test_work_permit_list_view_returns_200(self):
        response = self.client.get(reverse("gateops:work-permit-list"))
        self.assertEqual(response.status_code, 200)

    def test_work_permit_revoke_view_post_only(self):
        response = self.client.get(
            reverse(
                "gateops:work-permit-revoke",
                kwargs={"pk": self.work_permit.pk},
            )
        )
        self.assertEqual(response.status_code, 405)

    def test_contractor_dashboard_view_returns_200(self):
        response = self.client.get(reverse("gateops:contractor-dashboard"))
        self.assertEqual(response.status_code, 200)
