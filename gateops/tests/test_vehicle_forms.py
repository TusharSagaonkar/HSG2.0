"""Phase 6 tests for the ``VehicleRegisterForm`` ModelForm.

Covers:
- Valid data acceptance and ``vehicle_number`` normalization (uppercase+strip).
- Required-field validation (empty ``vehicle_number`` is invalid).
- Society-scoped querysets for ``vehicle_category`` and ``person``.
- Unfiltered querysets when no ``society`` kwarg is supplied.
- Field exclusion of ``society``, ``is_watchlisted``, ``is_repeat``,
  ``watchlist_reason``.
- Presence of a crispy-forms ``FormHelper`` with a submit button.

Conventions match ``test_vehicle_service.py``: the society is created once
per class via ``SocietyTestCase`` (triggering the gateops bootstrap signal)
and per-test mutable records are created in ``setUp``.
"""
from django.test import TestCase

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit

from core.test_base import SocietyTestCase
from core.test_factories import SocietyFactory
from gateops.forms import VehicleRegisterForm
from gateops.models import Person, VehicleCategory


class VehicleRegisterFormTest(SocietyTestCase):
    """Tests for the ``VehicleRegisterForm`` ModelForm."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Fetch seeded vehicle categories (bootstrap signal seeds 6).
        cls.vehicle_cat = VehicleCategory.objects.get(
            society=cls.society, code="VISITOR"
        )
        cls.delivery_cat = VehicleCategory.objects.get(
            society=cls.society, code="DELIVERY"
        )
        # Second society for cross-society queryset scoping tests.
        cls.other_society = SocietyFactory(name="Vehicle Form Beta")
        cls.other_vehicle_cat = VehicleCategory.objects.get(
            society=cls.other_society, code="VISITOR"
        )

    def setUp(self):
        super().setUp()
        self.person = Person.objects.create(
            society=self.society, name="Form Driver", phone="+919999999999"
        )
        self.other_person = Person.objects.create(
            society=self.other_society, name="Other Driver", phone="+917777777777"
        )

    # --- validity & normalization ---------------------------------------

    def test_valid_data_is_valid_and_normalizes_vehicle_number(self):
        """A valid form normalizes ``vehicle_number`` to uppercase+strip."""
        form = VehicleRegisterForm(
            data={
                "vehicle_number": "  mh12 ab 1234  ",
                "vehicle_category": self.vehicle_cat.pk,
                "person": self.person.pk,
                "notes": "Test notes",
            },
            society=self.society,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["vehicle_number"], "MH12 AB 1234")

    def test_empty_vehicle_number_is_invalid(self):
        """An empty ``vehicle_number`` fails validation."""
        form = VehicleRegisterForm(
            data={
                "vehicle_number": "",
                "vehicle_category": self.vehicle_cat.pk,
                "person": self.person.pk,
            },
            society=self.society,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("vehicle_number", form.errors)

    # --- society-scoped querysets ----------------------------------------

    def test_society_kwarg_scopes_vehicle_category_queryset(self):
        """The ``vehicle_category`` queryset is limited to the given society."""
        form = VehicleRegisterForm(society=self.society)

        qs = form.fields["vehicle_category"].queryset
        category_pks = set(qs.values_list("pk", flat=True))
        self.assertIn(self.vehicle_cat.pk, category_pks)
        self.assertIn(self.delivery_cat.pk, category_pks)
        self.assertNotIn(self.other_vehicle_cat.pk, category_pks)

    def test_society_kwarg_scopes_person_queryset(self):
        """The ``person`` queryset is limited to the given society."""
        form = VehicleRegisterForm(society=self.society)

        qs = form.fields["person"].queryset
        person_pks = set(qs.values_list("pk", flat=True))
        self.assertIn(self.person.pk, person_pks)
        self.assertNotIn(self.other_person.pk, person_pks)

    def test_no_society_kwarg_leaves_querysets_unfiltered(self):
        """Without a ``society`` kwarg the querysets are not narrowed.

        The default (unfiltered) queryset includes categories/persons from all
        societies, so the other society's records must be present.
        """
        form = VehicleRegisterForm()

        vehicle_pks = set(form.fields["vehicle_category"].queryset.values_list("pk", flat=True))
        self.assertIn(self.other_vehicle_cat.pk, vehicle_pks)

        person_pks = set(form.fields["person"].queryset.values_list("pk", flat=True))
        self.assertIn(self.other_person.pk, person_pks)

    # --- field exclusion -------------------------------------------------

    def test_excludes_protected_fields(self):
        """Watchlist, repeat, society, and watchlist_reason are not in the form."""
        form = VehicleRegisterForm(society=self.society)
        excluded = {"society", "is_watchlisted", "is_repeat", "watchlist_reason"}
        self.assertFalse(excluded & set(form.fields.keys()))

    def test_includes_expected_fields(self):
        """The form exposes exactly the four editable fields."""
        form = VehicleRegisterForm(society=self.society)
        self.assertEqual(
            set(form.fields.keys()),
            {"vehicle_number", "vehicle_category", "person", "notes"},
        )

    # --- crispy FormHelper -----------------------------------------------

    def test_has_form_helper_with_submit_button(self):
        """The form carries a crispy ``FormHelper`` with a submit input."""
        form = VehicleRegisterForm(society=self.society)

        self.assertTrue(hasattr(form, "helper"))
        self.assertIsInstance(form.helper, FormHelper)
        # The helper must contain at least one Submit input.
        submit_inputs = [
            layout
            for layout in form.helper.inputs
            if isinstance(layout, Submit)
        ]
        self.assertTrue(submit_inputs, "FormHelper must contain a Submit input.")


class VehicleRegisterFormMinimalTest(TestCase):
    """A lightweight test that does not depend on the shared society factory.

    Ensures the form can be instantiated without a society and that the
    helper is attached even in the no-society path.
    """

    def test_form_without_society_still_has_helper(self):
        form = VehicleRegisterForm()
        self.assertIsInstance(form.helper, FormHelper)
        self.assertTrue(
            any(isinstance(layout, Submit) for layout in form.helper.inputs)
        )
