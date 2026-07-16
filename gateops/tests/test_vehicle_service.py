"""
Test suite for gateops Phase 6 — Vehicle Module.

Test conventions:
- SocietyTestCase base class provides cls.society and cls.user (created once
  per class via SocietyFactory with django_get_or_create, avoiding repeated
  bootstrap signal cascades).
- Per-test mutable records (persons, vehicles) are created in setUp().
- Seeded VehicleCategory records are fetched in setUpTestData().

Covers:
- GateVehicle model (clean, normalization, constraints, soft-delete)
- VehicleService (register_or_create, lookup, watchlist, mark_repeat,
  get_watchlisted, get_recent, search, anpr_lookup)
- Vehicle views (list, detail, register, watchlist, unwatchlist, search, anpr)
"""
from datetime import timedelta
import json

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.test_base import SocietyTestCase
from core.test_factories import SocietyFactory, UserFactory
from gateops.models import (
    GateOpsAuditLog,
    GateVehicle,
    Person,
    VehicleCategory,
)
from gateops.services.vehicle_service import VehicleService
from housing_accounting.selection import SESSION_SELECTED_SOCIETY_ID
from societies.services import create_society


class GateVehicleModelTest(SocietyTestCase):
    """Model-level tests for GateVehicle (clean, normalization, constraints)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Bootstrap seeds 6 VehicleCategories: VISITOR, DELIVERY, COMMERCIAL,
        # EMERGENCY, ELECTRIC, OVERSIZED.
        cls.visitor_cat = VehicleCategory.objects.get(society=cls.society, code="VISITOR")
        cls.delivery_cat = VehicleCategory.objects.get(society=cls.society, code="DELIVERY")
        # Second society for cross-society tests (triggers bootstrap once).
        cls.other_society = SocietyFactory(name="Test Society Beta")
        cls.other_cat = VehicleCategory.objects.get(society=cls.other_society, code="VISITOR")

    def setUp(self):
        super().setUp()
        self.person = self._make_person()

    # --- helpers ---------------------------------------------------------

    def _make_person(self, **overrides):
        defaults = {"society": self.society, "name": "Test Driver", "phone": "+919999999999"}
        defaults.update(overrides)
        return Person.objects.create(**defaults)

    def _make_vehicle(self, **overrides):
        now = timezone.now()
        defaults = {
            "society": self.society,
            "person": self.person,
            "vehicle_number": "MH12AB1234",
            "vehicle_category": self.visitor_cat,
            "is_watchlisted": False,
            "is_repeat": False,
            "last_seen_at": now,
        }
        defaults.update(overrides)
        return GateVehicle.objects.create(**defaults)

    # --- creation & representation ---------------------------------------

    def test_vehicle_creation_with_valid_data(self):
        vehicle = self._make_vehicle()

        self.assertEqual(vehicle.society, self.society)
        self.assertEqual(vehicle.person, self.person)
        self.assertEqual(vehicle.vehicle_number, "MH12AB1234")
        self.assertEqual(vehicle.vehicle_category, self.visitor_cat)
        self.assertFalse(vehicle.is_watchlisted)
        self.assertFalse(vehicle.is_repeat)
        self.assertTrue(vehicle.is_active)
        self.assertIsNone(vehicle.deleted_at)
        self.assertIsNotNone(vehicle.first_seen_at)
        self.assertIsNotNone(vehicle.last_seen_at)
        self.assertIsNotNone(vehicle.created_at)
        self.assertIsNotNone(vehicle.updated_at)

    def test_vehicle_str_representation(self):
        vehicle = self._make_vehicle(vehicle_number="STR123")

        self.assertEqual(str(vehicle), f"STR123 ({self.visitor_cat.code})")

    # --- normalization ---------------------------------------------------

    def test_vehicle_number_normalized_to_uppercase(self):
        vehicle = self._make_vehicle(vehicle_number="mh12ab1234")

        self.assertEqual(vehicle.vehicle_number, "MH12AB1234")

    def test_vehicle_number_stripped(self):
        vehicle = self._make_vehicle(vehicle_number="  MH12AB1234  ")

        self.assertEqual(vehicle.vehicle_number, "MH12AB1234")

    # --- clean() validation ---------------------------------------------

    def test_clean_empty_vehicle_number_raises(self):
        vehicle = GateVehicle(
            society=self.society,
            person=self.person,
            vehicle_number="",
            vehicle_category=self.visitor_cat,
        )
        with self.assertRaises(ValidationError):
            vehicle.clean()

    def test_clean_watchlisted_without_reason_raises(self):
        vehicle = GateVehicle(
            society=self.society,
            person=self.person,
            vehicle_number="WL123",
            vehicle_category=self.visitor_cat,
            is_watchlisted=True,
            watchlist_reason="",
        )
        with self.assertRaises(ValidationError):
            vehicle.clean()

    def test_clean_watchlisted_with_reason_ok(self):
        vehicle = GateVehicle(
            society=self.society,
            person=self.person,
            vehicle_number="WL456",
            vehicle_category=self.visitor_cat,
            is_watchlisted=True,
            watchlist_reason="Suspicious activity",
        )
        vehicle.clean()  # Should not raise.
        self.assertEqual(vehicle.vehicle_number, "WL456")

    # --- is_currently_watchlisted property ------------------------------

    def test_is_currently_watchlisted_property(self):
        vehicle = self._make_vehicle(
            vehicle_number="WLP1",
            is_watchlisted=True,
            watchlist_reason="Test reason",
        )
        self.assertTrue(vehicle.is_currently_watchlisted)

        # Soft-deleted vehicle is never a current security concern.
        vehicle.is_active = False
        vehicle.save(update_fields=["is_active"])
        self.assertFalse(vehicle.is_currently_watchlisted)

    # --- constraints & soft-delete --------------------------------------

    def test_unique_vehicle_number_per_society_constraint(self):
        self._make_vehicle(vehicle_number="DUP123")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._make_vehicle(vehicle_number="DUP123")

    def test_soft_delete_allows_number_reuse(self):
        first = self._make_vehicle(vehicle_number="REUSE123")
        first.is_active = False
        first.deleted_at = timezone.now()
        first.save(update_fields=["is_active", "deleted_at"])

        # A new active vehicle with the same number must now succeed.
        second = self._make_vehicle(vehicle_number="REUSE123")
        self.assertEqual(second.vehicle_number, "REUSE123")
        self.assertTrue(second.is_active)

    def test_cross_society_same_vehicle_number_allowed(self):
        self._make_vehicle(vehicle_number="CROSS123")
        other_person = Person.objects.create(
            society=self.other_society, name="Other Driver", phone="+917777777777"
        )
        other_vehicle = GateVehicle.objects.create(
            society=self.other_society,
            person=other_person,
            vehicle_number="CROSS123",
            vehicle_category=self.other_cat,
            last_seen_at=timezone.now(),
        )
        self.assertEqual(other_vehicle.vehicle_number, "CROSS123")
        self.assertTrue(other_vehicle.is_active)


class VehicleServiceTest(SocietyTestCase):
    """Service-level tests for VehicleService.

    The society and seeded master data are created once per class via
    setUpTestData to avoid re-running the expensive gateops bootstrap signal.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.visitor_cat = VehicleCategory.objects.get(society=cls.society, code="VISITOR")
        cls.delivery_cat = VehicleCategory.objects.get(society=cls.society, code="DELIVERY")
        # Second society for cross-society tests (triggers bootstrap once).
        cls.other_society = SocietyFactory(name="Test Society Beta")
        cls.other_cat = VehicleCategory.objects.get(society=cls.other_society, code="VISITOR")

    def setUp(self):
        super().setUp()
        self.person = self._make_person()

    # --- helpers ---------------------------------------------------------

    def _make_person(self, **overrides):
        defaults = {"society": self.society, "name": "Test Driver", "phone": "+919999999999"}
        defaults.update(overrides)
        return Person.objects.create(**defaults)

    def _make_vehicle(self, **overrides):
        now = timezone.now()
        defaults = {
            "society": self.society,
            "person": self.person,
            "vehicle_number": "MH12AB1234",
            "vehicle_category": self.visitor_cat,
            "last_seen_at": now,
        }
        defaults.update(overrides)
        return GateVehicle.objects.create(**defaults)

    # --- register_or_create: basic issuance -----------------------------

    def test_register_creates_new_vehicle(self):
        vehicle = VehicleService.register_or_create(
            society=self.society,
            vehicle_number="NEW123",
            person=self.person,
            vehicle_category=self.visitor_cat,
            actor=self.user,
        )
        self.assertEqual(vehicle.society, self.society)
        self.assertEqual(vehicle.person, self.person)
        self.assertEqual(vehicle.vehicle_number, "NEW123")
        self.assertEqual(vehicle.vehicle_category, self.visitor_cat)
        self.assertFalse(vehicle.is_repeat)
        self.assertIsNotNone(vehicle.last_seen_at)
        self.assertTrue(vehicle.is_active)

    def test_register_normalizes_vehicle_number(self):
        vehicle = VehicleService.register_or_create(
            society=self.society,
            vehicle_number="mh12ab1234",
            person=self.person,
            vehicle_category=self.visitor_cat,
            actor=self.user,
        )
        self.assertEqual(vehicle.vehicle_number, "MH12AB1234")

    def test_register_existing_vehicle_updates_last_seen(self):
        old_time = timezone.now() - timedelta(hours=2)
        existing = GateVehicle.objects.create(
            society=self.society,
            person=self.person,
            vehicle_number="UPD123",
            vehicle_category=self.visitor_cat,
            last_seen_at=old_time,
        )
        updated = VehicleService.register_or_create(
            society=self.society,
            vehicle_number="UPD123",
            person=self.person,
            vehicle_category=self.visitor_cat,
            actor=self.user,
        )
        updated.refresh_from_db()
        self.assertEqual(updated.pk, existing.pk)
        self.assertGreater(updated.last_seen_at, old_time)

    def test_register_existing_vehicle_sets_repeat_flag(self):
        VehicleService.register_or_create(
            society=self.society,
            vehicle_number="REP123",
            person=self.person,
            vehicle_category=self.visitor_cat,
            actor=self.user,
        )
        updated = VehicleService.register_or_create(
            society=self.society,
            vehicle_number="REP123",
            person=self.person,
            vehicle_category=self.visitor_cat,
            actor=self.user,
        )
        self.assertTrue(updated.is_repeat)

    def test_register_cross_society_person_raises(self):
        other_person = Person.objects.create(
            society=self.other_society, name="Other Driver", phone="+917777777777"
        )
        with self.assertRaises(ValidationError):
            VehicleService.register_or_create(
                society=self.society,
                vehicle_number="CROSS1",
                person=other_person,
                vehicle_category=self.visitor_cat,
            )

    def test_register_cross_society_category_raises(self):
        with self.assertRaises(ValidationError):
            VehicleService.register_or_create(
                society=self.society,
                vehicle_number="CROSS2",
                person=self.person,
                vehicle_category=self.other_cat,
            )

    def test_register_creates_audit_log(self):
        vehicle = VehicleService.register_or_create(
            society=self.society,
            vehicle_number="AUDIT1",
            person=self.person,
            vehicle_category=self.visitor_cat,
            actor=self.user,
        )
        self.assertTrue(
            GateOpsAuditLog.objects.filter(
                entity_type="GateVehicle",
                entity_id=str(vehicle.pk),
                action=GateOpsAuditLog.Action.CREATE,
            ).exists()
        )

    def test_register_with_notes(self):
        vehicle = VehicleService.register_or_create(
            society=self.society,
            vehicle_number="NOTES1",
            person=self.person,
            vehicle_category=self.visitor_cat,
            notes="First visit notes",
            actor=self.user,
        )
        self.assertEqual(vehicle.notes, "First visit notes")

    # --- lookup ----------------------------------------------------------

    def test_lookup_finds_existing_vehicle(self):
        self._make_vehicle(vehicle_number="LOOK1")
        result = VehicleService.lookup(society=self.society, vehicle_number="LOOK1")
        self.assertIsNotNone(result)
        self.assertEqual(result.vehicle_number, "LOOK1")

    def test_lookup_normalizes_number(self):
        self._make_vehicle(vehicle_number="MH12AB1234")
        result = VehicleService.lookup(society=self.society, vehicle_number="mh12ab1234")
        self.assertIsNotNone(result)
        self.assertEqual(result.vehicle_number, "MH12AB1234")

    def test_lookup_nonexistent_returns_none(self):
        result = VehicleService.lookup(society=self.society, vehicle_number="NOPE1")
        self.assertIsNone(result)

    def test_lookup_cross_society_returns_none(self):
        self._make_vehicle(vehicle_number="XSCO1")
        result = VehicleService.lookup(society=self.other_society, vehicle_number="XSCO1")
        self.assertIsNone(result)

    def test_lookup_excludes_soft_deleted(self):
        vehicle = self._make_vehicle(vehicle_number="SOFT1")
        vehicle.is_active = False
        vehicle.deleted_at = timezone.now()
        vehicle.save(update_fields=["is_active", "deleted_at"])
        result = VehicleService.lookup(society=self.society, vehicle_number="SOFT1")
        self.assertIsNone(result)

    # --- add_to_watchlist ------------------------------------------------

    def test_add_to_watchlist_sets_flag(self):
        vehicle = self._make_vehicle(vehicle_number="WL001")
        VehicleService.add_to_watchlist(
            gate_vehicle=vehicle, reason="Suspicious activity", actor=self.user
        )
        vehicle.refresh_from_db()
        self.assertTrue(vehicle.is_watchlisted)
        self.assertEqual(vehicle.watchlist_reason, "Suspicious activity")

    def test_add_to_watchlist_empty_reason_raises(self):
        vehicle = self._make_vehicle(vehicle_number="WL002")
        with self.assertRaises(ValidationError):
            VehicleService.add_to_watchlist(gate_vehicle=vehicle, reason="")

    def test_add_to_watchlist_already_watchlisted_raises(self):
        vehicle = self._make_vehicle(vehicle_number="WL003")
        VehicleService.add_to_watchlist(gate_vehicle=vehicle, reason="First reason")
        with self.assertRaises(ValidationError):
            VehicleService.add_to_watchlist(gate_vehicle=vehicle, reason="Second reason")

    def test_add_to_watchlist_creates_audit_log(self):
        vehicle = self._make_vehicle(vehicle_number="WL004")
        VehicleService.add_to_watchlist(
            gate_vehicle=vehicle, reason="Audit test", actor=self.user
        )
        self.assertTrue(
            GateOpsAuditLog.objects.filter(
                entity_type="GateVehicle",
                entity_id=str(vehicle.pk),
                action=GateOpsAuditLog.Action.STATE_TRANSITION,
            ).exists()
        )

    # --- remove_from_watchlist ------------------------------------------

    def test_remove_from_watchlist_clears_flag(self):
        vehicle = self._make_vehicle(vehicle_number="RML001")
        VehicleService.add_to_watchlist(gate_vehicle=vehicle, reason="Temporary")
        VehicleService.remove_from_watchlist(gate_vehicle=vehicle, actor=self.user)
        vehicle.refresh_from_db()
        self.assertFalse(vehicle.is_watchlisted)
        self.assertEqual(vehicle.watchlist_reason, "")

    def test_remove_from_watchlist_not_watchlisted_raises(self):
        vehicle = self._make_vehicle(vehicle_number="RML002")
        with self.assertRaises(ValidationError):
            VehicleService.remove_from_watchlist(gate_vehicle=vehicle, actor=self.user)

    def test_remove_from_watchlist_creates_audit_log(self):
        vehicle = self._make_vehicle(vehicle_number="RML003")
        VehicleService.add_to_watchlist(gate_vehicle=vehicle, reason="To be removed")
        before = GateOpsAuditLog.objects.filter(
            entity_type="GateVehicle", entity_id=str(vehicle.pk)
        ).count()
        VehicleService.remove_from_watchlist(gate_vehicle=vehicle, actor=self.user)
        after = GateOpsAuditLog.objects.filter(
            entity_type="GateVehicle", entity_id=str(vehicle.pk)
        ).count()
        self.assertEqual(after, before + 1)

    # --- mark_repeat -----------------------------------------------------

    def test_mark_repeat_sets_flag(self):
        self._make_vehicle(vehicle_number="MRP001", is_repeat=False)
        result = VehicleService.mark_repeat(
            society=self.society, vehicle_number="MRP001", actor=self.user
        )
        result.refresh_from_db()
        self.assertTrue(result.is_repeat)

    def test_mark_repeat_nonexistent_returns_none(self):
        result = VehicleService.mark_repeat(
            society=self.society, vehicle_number="NOPE999", actor=self.user
        )
        self.assertIsNone(result)

    def test_mark_repeat_idempotent(self):
        self._make_vehicle(vehicle_number="MRP002", is_repeat=True)
        result = VehicleService.mark_repeat(
            society=self.society, vehicle_number="MRP002", actor=self.user
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.is_repeat)

    def test_mark_repeat_creates_audit_log(self):
        self._make_vehicle(vehicle_number="MRP003", is_repeat=False)
        VehicleService.mark_repeat(
            society=self.society, vehicle_number="MRP003", actor=self.user
        )
        vehicle = VehicleService.lookup(society=self.society, vehicle_number="MRP003")
        self.assertTrue(
            GateOpsAuditLog.objects.filter(
                entity_type="GateVehicle",
                entity_id=str(vehicle.pk),
                action=GateOpsAuditLog.Action.UPDATE,
            ).exists()
        )

    # --- get_watchlisted -------------------------------------------------

    def test_get_watchlisted_returns_only_watchlisted(self):
        wl = self._make_vehicle(
            vehicle_number="GWL001", is_watchlisted=True, watchlist_reason="X"
        )
        self._make_vehicle(vehicle_number="GWL002", is_watchlisted=False)
        result = VehicleService.get_watchlisted(society=self.society)
        self.assertEqual(result.count(), 1)
        self.assertEqual(result.first().pk, wl.pk)

    def test_get_watchlisted_society_scoped(self):
        wl = self._make_vehicle(
            vehicle_number="GWL003", is_watchlisted=True, watchlist_reason="X"
        )
        other_person = Person.objects.create(
            society=self.other_society, name="Other", phone="+917777777777"
        )
        other_wl = GateVehicle.objects.create(
            society=self.other_society,
            person=other_person,
            vehicle_number="GWL004",
            vehicle_category=self.other_cat,
            is_watchlisted=True,
            watchlist_reason="Y",
            last_seen_at=timezone.now(),
        )
        result = VehicleService.get_watchlisted(society=self.society)
        pks = [v.pk for v in result]
        self.assertIn(wl.pk, pks)
        self.assertNotIn(other_wl.pk, pks)

    def test_get_watchlisted_excludes_soft_deleted(self):
        wl = self._make_vehicle(
            vehicle_number="GWL005", is_watchlisted=True, watchlist_reason="X"
        )
        wl.is_active = False
        wl.deleted_at = timezone.now()
        wl.save(update_fields=["is_active", "deleted_at"])
        result = VehicleService.get_watchlisted(society=self.society)
        self.assertEqual(result.count(), 0)

    # --- get_recent ------------------------------------------------------

    def test_get_recent_returns_ordered_by_last_seen(self):
        now = timezone.now()
        old = self._make_vehicle(vehicle_number="GR001", last_seen_at=now - timedelta(hours=2))
        mid = self._make_vehicle(vehicle_number="GR002", last_seen_at=now - timedelta(hours=1))
        new = self._make_vehicle(vehicle_number="GR003", last_seen_at=now)
        recent = list(VehicleService.get_recent(society=self.society, limit=10))
        self.assertEqual(recent[0].pk, new.pk)
        self.assertEqual(recent[1].pk, mid.pk)
        self.assertEqual(recent[2].pk, old.pk)

    def test_get_recent_respects_limit(self):
        now = timezone.now()
        for i in range(3):
            self._make_vehicle(
                vehicle_number=f"GRL{i}", last_seen_at=now - timedelta(hours=i)
            )
        recent = VehicleService.get_recent(society=self.society, limit=2)
        self.assertEqual(len(recent), 2)

    def test_get_recent_society_scoped(self):
        vehicle = self._make_vehicle(vehicle_number="GRS01")
        other_person = Person.objects.create(
            society=self.other_society, name="Other", phone="+917777777777"
        )
        other_vehicle = GateVehicle.objects.create(
            society=self.other_society,
            person=other_person,
            vehicle_number="GRS02",
            vehicle_category=self.other_cat,
            last_seen_at=timezone.now(),
        )
        recent = list(VehicleService.get_recent(society=self.society, limit=10))
        pks = [v.pk for v in recent]
        self.assertIn(vehicle.pk, pks)
        self.assertNotIn(other_vehicle.pk, pks)

    # --- search ----------------------------------------------------------

    def test_search_by_vehicle_number(self):
        self._make_vehicle(vehicle_number="SRCH01")
        results = VehicleService.search(society=self.society, query="SRCH")
        self.assertTrue(results.filter(vehicle_number="SRCH01").exists())

    def test_search_by_person_name(self):
        person = self._make_person(name="John Doe", phone="+919999988881")
        self._make_vehicle(vehicle_number="SRCH02", person=person)
        results = VehicleService.search(society=self.society, query="John")
        self.assertTrue(results.filter(vehicle_number="SRCH02").exists())

    def test_search_by_person_phone(self):
        person = self._make_person(name="Phone Test", phone="+919999988882")
        self._make_vehicle(vehicle_number="SRCH03", person=person)
        results = VehicleService.search(society=self.society, query="999988882")
        self.assertTrue(results.filter(vehicle_number="SRCH03").exists())

    def test_search_empty_query_returns_all(self):
        self._make_vehicle(vehicle_number="SRCH04")
        self._make_vehicle(vehicle_number="SRCH05")
        results = VehicleService.search(society=self.society, query="")
        self.assertEqual(results.count(), 2)

    def test_search_society_scoped(self):
        vehicle = self._make_vehicle(vehicle_number="SRCH06")
        other_person = Person.objects.create(
            society=self.other_society, name="Other", phone="+917777777777"
        )
        other_vehicle = GateVehicle.objects.create(
            society=self.other_society,
            person=other_person,
            vehicle_number="SRCH07",
            vehicle_category=self.other_cat,
            last_seen_at=timezone.now(),
        )
        results = VehicleService.search(society=self.society, query="SRCH")
        pks = [v.pk for v in results]
        self.assertIn(vehicle.pk, pks)
        self.assertNotIn(other_vehicle.pk, pks)

    # --- anpr_lookup -----------------------------------------------------

    def test_anpr_lookup_finds_vehicle(self):
        self._make_vehicle(vehicle_number="ANPR01")
        result = VehicleService.anpr_lookup(society=self.society, plate_text="ANPR01")
        self.assertTrue(result["found"])
        self.assertIsNotNone(result["vehicle"])

    def test_anpr_lookup_not_found(self):
        result = VehicleService.anpr_lookup(society=self.society, plate_text="NOPE01")
        self.assertFalse(result["found"])
        self.assertIsNone(result["vehicle"])

    def test_anpr_lookup_watchlisted_vehicle(self):
        self._make_vehicle(
            vehicle_number="ANPR02",
            is_watchlisted=True,
            watchlist_reason="Flagged",
        )
        result = VehicleService.anpr_lookup(society=self.society, plate_text="ANPR02")
        self.assertTrue(result["found"])
        self.assertTrue(result["watchlisted"])

    def test_anpr_lookup_normalizes_plate(self):
        self._make_vehicle(vehicle_number="MH12AB1234")
        result = VehicleService.anpr_lookup(
            society=self.society, plate_text=" mh12ab1234 "
        )
        self.assertTrue(result["found"])

    def test_anpr_lookup_returns_dict_format(self):
        self._make_vehicle(vehicle_number="ANPR03")
        result = VehicleService.anpr_lookup(society=self.society, plate_text="ANPR03")
        self.assertIsInstance(result, dict)
        self.assertIn("found", result)
        self.assertIn("vehicle", result)
        self.assertIn("watchlisted", result)
        self.assertIn("category_code", result)


class VehicleViewTest(TestCase):
    """Frontend tests for the Phase 6 vehicle views.

    Societies are created once per class in setUpTestData; setUp logs in and
    selects the society so every view resolves the correct tenant.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = UserFactory(password="password")
        # create_society grants the user an active OWNER membership, which the
        # society-selection middleware requires to resolve the active society.
        cls.society = create_society(user=cls.user, name="Vehicle View Society")
        cls.visitor_cat = VehicleCategory.objects.get(society=cls.society, code="VISITOR")
        cls.other_society = create_society(
            user=UserFactory(password="password"), name="Other Vehicle View Society"
        )
        cls.other_cat = VehicleCategory.objects.get(
            society=cls.other_society, code="VISITOR"
        )

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self._select_society(self.society)
        self.person = Person.objects.create(
            society=self.society, name="View Driver", phone="+918888888888"
        )
        self.vehicle = GateVehicle.objects.create(
            society=self.society,
            person=self.person,
            vehicle_number="VIEW123",
            vehicle_category=self.visitor_cat,
            last_seen_at=timezone.now(),
        )

    # --- helpers ---------------------------------------------------------

    def _select_society(self, society):
        session = self.client.session
        session[SESSION_SELECTED_SOCIETY_ID] = society.id
        session.save()

    # --- list view -------------------------------------------------------

    def test_vehicle_list_view_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("gateops:vehicle-list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    def test_vehicle_list_view_returns_200(self):
        response = self.client.get(reverse("gateops:vehicle-list"))
        self.assertEqual(response.status_code, 200)

    # --- detail view -----------------------------------------------------

    def test_vehicle_detail_view_404_for_other_society(self):
        other_person = Person.objects.create(
            society=self.other_society, name="Other Driver", phone="+917777777777"
        )
        other_vehicle = GateVehicle.objects.create(
            society=self.other_society,
            person=other_person,
            vehicle_number="OTHER404",
            vehicle_category=self.other_cat,
            last_seen_at=timezone.now(),
        )
        response = self.client.get(
            reverse("gateops:vehicle-detail", kwargs={"pk": other_vehicle.pk})
        )
        self.assertEqual(response.status_code, 404)

    # --- watchlist view --------------------------------------------------

    def test_vehicle_watchlist_view_post_only(self):
        response = self.client.get(
            reverse("gateops:vehicle-watchlist", kwargs={"pk": self.vehicle.pk})
        )
        self.assertEqual(response.status_code, 405)

    def test_vehicle_watchlist_view_watchlists_vehicle(self):
        response = self.client.post(
            reverse("gateops:vehicle-watchlist", kwargs={"pk": self.vehicle.pk}),
            data={"reason": "Suspicious activity"},
        )
        self.assertEqual(response.status_code, 302)
        self.vehicle.refresh_from_db()
        self.assertTrue(self.vehicle.is_watchlisted)
        self.assertEqual(self.vehicle.watchlist_reason, "Suspicious activity")

    # --- unwatchlist view ------------------------------------------------

    def test_vehicle_unwatchlist_view_post_only(self):
        response = self.client.get(
            reverse("gateops:vehicle-unwatchlist", kwargs={"pk": self.vehicle.pk})
        )
        self.assertEqual(response.status_code, 405)

    # --- register view ---------------------------------------------------

    def test_vehicle_register_view_get_returns_200(self):
        response = self.client.get(reverse("gateops:vehicle-register"))
        self.assertEqual(response.status_code, 200)

    def test_vehicle_register_view_post_creates_vehicle(self):
        response = self.client.post(
            reverse("gateops:vehicle-register"),
            data={
                "vehicle_number": "POST123",
                "person_id": self.person.pk,
                "vehicle_category_id": self.visitor_cat.pk,
                "notes": "Registered via view",
            },
        )
        self.assertEqual(response.status_code, 302)
        vehicle = GateVehicle.objects.get(
            society=self.society, vehicle_number="POST123"
        )
        self.assertEqual(vehicle.person, self.person)
        self.assertEqual(vehicle.vehicle_category, self.visitor_cat)
        self.assertEqual(vehicle.notes, "Registered via view")
        self.assertEqual(
            response.url,
            reverse("gateops:vehicle-detail", kwargs={"pk": vehicle.pk}),
        )

    # --- search view -----------------------------------------------------

    def test_vehicle_search_view_returns_200(self):
        response = self.client.get(
            reverse("gateops:vehicle-search"), data={"q": "VIEW"}
        )
        self.assertEqual(response.status_code, 200)

    # --- anpr lookup view ------------------------------------------------

    def test_vehicle_anpr_lookup_view_post_only(self):
        response = self.client.get(reverse("gateops:vehicle-anpr-lookup"))
        self.assertEqual(response.status_code, 405)

    def test_vehicle_anpr_lookup_view_returns_json(self):
        response = self.client.post(
            reverse("gateops:vehicle-anpr-lookup"),
            data={"plate_text": "VIEW123"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        payload = json.loads(response.content)
        self.assertIn("found", payload)
        self.assertTrue(payload["found"])


class VehicleViewTemplateTest(TestCase):
    """HTML template rendering tests for the Phase 6 vehicle views.

    These tests assert that the views render the dedicated HTML templates
    (``vehicle_list.html``, ``vehicle_detail.html``, ``vehicle_form.html``)
    rather than the previous plain-text responses, and that redirects point
    to the detail URL.

    Societies are created once per class in ``setUpTestData``; ``setUp`` logs
    in and selects the society so every view resolves the correct tenant.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = UserFactory(password="password")
        # create_society grants the user an active OWNER membership, which the
        # society-selection middleware requires to resolve the active society.
        cls.society = create_society(user=cls.user, name="Vehicle Template Society")
        cls.visitor_cat = VehicleCategory.objects.get(
            society=cls.society, code="VISITOR"
        )

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self._select_society(self.society)
        self.person = Person.objects.create(
            society=self.society, name="Template Driver", phone="+918888888888"
        )
        self.vehicle = GateVehicle.objects.create(
            society=self.society,
            person=self.person,
            vehicle_number="TMPL123",
            vehicle_category=self.visitor_cat,
            last_seen_at=timezone.now(),
        )

    # --- helpers ---------------------------------------------------------

    def _select_society(self, society):
        session = self.client.session
        session[SESSION_SELECTED_SOCIETY_ID] = society.id
        session.save()

    # --- list view: template + content -----------------------------------

    def test_vehicle_list_view_uses_list_template(self):
        """The list view renders ``vehicle_list.html``."""
        response = self.client.get(reverse("gateops:vehicle-list"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "gateops/vehicle_list.html")

    def test_vehicle_list_view_contains_vehicle_number(self):
        """The list HTML response contains the vehicle's number."""
        response = self.client.get(reverse("gateops:vehicle-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TMPL123")

    # --- detail view: template -------------------------------------------

    def test_vehicle_detail_view_uses_detail_template(self):
        """The detail view renders ``vehicle_detail.html``."""
        response = self.client.get(
            reverse("gateops:vehicle-detail", kwargs={"pk": self.vehicle.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "gateops/vehicle_detail.html")

    # --- register view: GET template -------------------------------------

    def test_vehicle_register_view_get_uses_form_template(self):
        """The register GET renders ``vehicle_form.html``."""
        response = self.client.get(reverse("gateops:vehicle-register"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "gateops/vehicle_form.html")

    # --- register view: POST valid ---------------------------------------

    def test_vehicle_register_view_post_valid_creates_and_redirects(self):
        """A valid POST creates a vehicle and redirects (302) to the detail URL."""
        response = self.client.post(
            reverse("gateops:vehicle-register"),
            data={
                "vehicle_number": "POST456",
                "person": self.person.pk,
                "vehicle_category": self.visitor_cat.pk,
                "notes": "Registered via template test",
            },
        )
        self.assertEqual(response.status_code, 302)
        vehicle = GateVehicle.objects.get(
            society=self.society, vehicle_number="POST456"
        )
        self.assertEqual(vehicle.person, self.person)
        self.assertEqual(vehicle.vehicle_category, self.visitor_cat)
        self.assertEqual(vehicle.notes, "Registered via template test")
        self.assertEqual(
            response.url,
            reverse("gateops:vehicle-detail", kwargs={"pk": vehicle.pk}),
        )

    # --- register view: POST invalid -------------------------------------

    def test_vehicle_register_view_post_invalid_rerenders_form(self):
        """An invalid POST (empty vehicle_number) re-renders the form (200).

        No vehicle must be created.
        """
        before_count = GateVehicle.objects.count()
        response = self.client.post(
            reverse("gateops:vehicle-register"),
            data={
                "vehicle_number": "",
                "person": self.person.pk,
                "vehicle_category": self.visitor_cat.pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "gateops/vehicle_form.html")
        self.assertEqual(GateVehicle.objects.count(), before_count)

    # --- watchlist view: redirect ----------------------------------------

    def test_vehicle_watchlist_view_redirects_to_detail(self):
        """The watchlist POST redirects (302) to the detail URL."""
        response = self.client.post(
            reverse("gateops:vehicle-watchlist", kwargs={"pk": self.vehicle.pk}),
            data={"reason": "Suspicious activity"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("gateops:vehicle-detail", kwargs={"pk": self.vehicle.pk}),
        )

    # --- unwatchlist view: redirect --------------------------------------

    def test_vehicle_unwatchlist_view_redirects_to_detail(self):
        """The unwatchlist POST redirects (302) to the detail URL.

        The vehicle must be watchlisted first so the service call succeeds.
        """
        VehicleService.add_to_watchlist(
            gate_vehicle=self.vehicle, reason="Temporary", actor=self.user
        )
        response = self.client.post(
            reverse("gateops:vehicle-unwatchlist", kwargs={"pk": self.vehicle.pk}),
            data={"reason": "Cleared"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("gateops:vehicle-detail", kwargs={"pk": self.vehicle.pk}),
        )

    # --- search view: template -------------------------------------------

    def test_vehicle_search_view_uses_list_template(self):
        """The search view renders ``vehicle_list.html`` with results."""
        response = self.client.get(
            reverse("gateops:vehicle-search"), data={"q": "TMPL"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "gateops/vehicle_list.html")

    # --- anpr lookup view: JSON ------------------------------------------

    def test_vehicle_anpr_lookup_view_returns_json_content_type(self):
        """The ANPR lookup view still returns JSON (content-type check)."""
        response = self.client.post(
            reverse("gateops:vehicle-anpr-lookup"),
            data={"plate_text": "TMPL123"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/json", response["Content-Type"])
        payload = json.loads(response.content)
        self.assertTrue(payload["found"])
