"""Tests for the Phase-2 gateops rule-engine models.

Covers: creation with valid data, ``clean()`` validation rules, conditional
``UniqueConstraint`` (active vs soft-deleted), soft-delete behaviour, society
isolation, and the append-only intent of :class:`RuleEvaluation`.
"""

from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from gateops.models import (
    HolidayCalendar,
    MaterialCategory,
    Rule,
    RuleAction,
    RuleCondition,
    RuleEvaluation,
    VisitorCategory,
)
from societies.models import Society


class RuleModelTestBase(TestCase):
    """Shared helpers for rule-engine model tests.

    Societies are created once per class via ``setUpTestData`` to avoid
    re-running the expensive accounting + gateops bootstrap signal on every
    test method.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Creating a Society triggers the gateops bootstrap signal, which
        # seeds default categories/roles/etc.
        cls.society = Society.objects.create(name="Alpha Society")
        cls.other_society = Society.objects.create(name="Beta Society")
        # Grab a default visitor category created by the bootstrap.
        cls.visitor_cat = VisitorCategory.objects.get(
            society=cls.society, code="DELIVERY"
        )
        cls.material_cat = MaterialCategory.objects.get(
            society=cls.society, code="INBOUND"
        )

    # --- helpers ----------------------------------------------------------

    def _make_rule(self, society=None, code="RULE_001", name="Test Rule", **kwargs):
        return Rule.objects.create(
            society=society or self.society,
            name=name,
            code=code,
            **kwargs,
        )


# ---------------------------------------------------------------------------
# Rule
# ---------------------------------------------------------------------------


class RuleTest(RuleModelTestBase):
    def test_creation_with_defaults(self):
        rule = self._make_rule()
        self.assertEqual(rule.priority, 100)
        self.assertTrue(rule.is_active)
        self.assertEqual(rule.applies_on, Rule.AppliesOn.BOTH)
        self.assertIsNotNone(rule.valid_from)
        self.assertIsNone(rule.valid_until)
        self.assertIsNone(rule.deleted_at)

    def test_str(self):
        rule = self._make_rule(priority=5)
        self.assertIn("RULE_001", str(rule))
        self.assertIn("priority=5", str(rule))

    def test_clean_rejects_lowercase_code(self):
        rule = Rule(society=self.society, name="X", code="lowercase")
        with self.assertRaises(ValidationError):
            rule.clean()

    def test_clean_rejects_non_alphanumeric_code(self):
        rule = Rule(society=self.society, name="X", code="RULE-001")
        with self.assertRaises(ValidationError):
            rule.clean()

    def test_clean_allows_underscore_in_code(self):
        rule = Rule(society=self.society, name="X", code="RULE_001")
        rule.clean()  # should not raise

    def test_clean_rejects_valid_until_before_valid_from(self):
        rule = Rule(
            society=self.society,
            name="X",
            code="RULE_X",
            valid_from=date(2026, 6, 1),
            valid_until=date(2026, 5, 31),
        )
        with self.assertRaises(ValidationError):
            rule.clean()

    def test_clean_rejects_valid_until_equal_valid_from(self):
        d = date(2026, 6, 1)
        rule = Rule(
            society=self.society,
            name="X",
            code="RULE_X",
            valid_from=d,
            valid_until=d,
        )
        with self.assertRaises(ValidationError):
            rule.clean()

    def test_clean_rejects_negative_priority(self):
        rule = Rule(society=self.society, name="X", code="RULE_X", priority=-1)
        with self.assertRaises(ValidationError):
            rule.clean()

    def test_clean_accepts_zero_priority(self):
        rule = Rule(society=self.society, name="X", code="RULE_X", priority=0)
        rule.clean()  # should not raise

    def test_save_calls_clean(self):
        with self.assertRaises(ValidationError):
            Rule.objects.create(
                society=self.society, name="X", code="lowercase"
            )

    def test_unique_code_per_society_when_active(self):
        self._make_rule(code="DUP_CODE")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._make_rule(code="DUP_CODE")

    def test_same_code_allowed_after_soft_delete(self):
        rule = self._make_rule(code="REUSE_ME")
        rule.is_active = False
        rule.deleted_at = timezone.now()
        rule.save()
        # Now a new active rule with the same code should be allowed.
        rule2 = self._make_rule(code="REUSE_ME", name="Second")
        self.assertTrue(rule2.is_active)

    def test_same_code_allowed_across_societies(self):
        self._make_rule(code="SHARED_CODE")
        rule2 = self._make_rule(
            society=self.other_society, code="SHARED_CODE", name="Other"
        )
        self.assertEqual(Rule.objects.filter(code="SHARED_CODE").count(), 2)

    def test_society_isolation(self):
        self._make_rule(code="SOC_A")
        self._make_rule(society=self.other_society, code="SOC_B")
        self.assertEqual(Rule.objects.filter(society=self.society).count(), 1)
        self.assertEqual(Rule.objects.filter(society=self.other_society).count(), 1)

    def test_soft_delete_fields(self):
        rule = self._make_rule()
        rule.is_active = False
        rule.deleted_at = timezone.now()
        rule.save()
        rule.refresh_from_db()
        self.assertFalse(rule.is_active)
        self.assertIsNotNone(rule.deleted_at)

    def test_scope_fks_set_null_on_category_delete(self):
        rule = self._make_rule(visitor_category=self.visitor_cat)
        self.visitor_cat.delete()
        rule.refresh_from_db()
        self.assertIsNone(rule.visitor_category)

    def test_ordering_by_priority(self):
        self._make_rule(code="R3", priority=300, name="C")
        self._make_rule(code="R1", priority=100, name="A")
        self._make_rule(code="R2", priority=200, name="B")
        codes = list(Rule.objects.filter(society=self.society).values_list("code", flat=True))
        self.assertEqual(codes, ["R1", "R2", "R3"])


# ---------------------------------------------------------------------------
# RuleCondition
# ---------------------------------------------------------------------------


class RuleConditionTest(RuleModelTestBase):
    def test_creation_with_defaults(self):
        rule = self._make_rule()
        cond = RuleCondition.objects.create(
            rule=rule,
            field=RuleCondition.ConditionField.VISITOR_TYPE,
            operator=RuleCondition.Operator.EQ,
            value="DELIVERY",
        )
        self.assertEqual(cond.logical_connector, RuleCondition.LogicalConnector.AND)
        self.assertEqual(cond.sort_order, 0)

    def test_str(self):
        rule = self._make_rule()
        cond = RuleCondition.objects.create(
            rule=rule,
            field=RuleCondition.ConditionField.TOWER,
            operator=RuleCondition.Operator.EQ,
            value="A",
        )
        self.assertIn("tower", str(cond))
        self.assertIn("eq", str(cond))

    def test_clean_requires_value_for_non_boolean_operators(self):
        rule = self._make_rule()
        cond = RuleCondition(
            rule=rule,
            field=RuleCondition.ConditionField.VISITOR_TYPE,
            operator=RuleCondition.Operator.EQ,
            value={},
        )
        with self.assertRaises(ValidationError):
            cond.clean()

    def test_clean_allows_empty_value_for_is_true(self):
        rule = self._make_rule()
        cond = RuleCondition(
            rule=rule,
            field=RuleCondition.ConditionField.IS_EMERGENCY,
            operator=RuleCondition.Operator.IS_TRUE,
            value={},
        )
        cond.clean()  # should not raise

    def test_clean_allows_empty_value_for_is_false(self):
        rule = self._make_rule()
        cond = RuleCondition(
            rule=rule,
            field=RuleCondition.ConditionField.IS_VIP,
            operator=RuleCondition.Operator.IS_FALSE,
            value={},
        )
        cond.clean()  # should not raise

    def test_clean_between_requires_start_end_dict(self):
        rule = self._make_rule()
        cond = RuleCondition(
            rule=rule,
            field=RuleCondition.ConditionField.TIME,
            operator=RuleCondition.Operator.BETWEEN,
            value={"start": "08:00"},  # missing end
        )
        with self.assertRaises(ValidationError):
            cond.clean()

    def test_clean_between_accepts_two_element_list(self):
        rule = self._make_rule()
        cond = RuleCondition(
            rule=rule,
            field=RuleCondition.ConditionField.TIME,
            operator=RuleCondition.Operator.BETWEEN,
            value=["08:00", "20:00"],
        )
        cond.clean()  # should not raise

    def test_clean_between_rejects_three_element_list(self):
        rule = self._make_rule()
        cond = RuleCondition(
            rule=rule,
            field=RuleCondition.ConditionField.TIME,
            operator=RuleCondition.Operator.BETWEEN,
            value=["08:00", "12:00", "20:00"],
        )
        with self.assertRaises(ValidationError):
            cond.clean()

    def test_clean_between_rejects_scalar(self):
        rule = self._make_rule()
        cond = RuleCondition(
            rule=rule,
            field=RuleCondition.ConditionField.TIME,
            operator=RuleCondition.Operator.BETWEEN,
            value="08:00",
        )
        with self.assertRaises(ValidationError):
            cond.clean()

    def test_cascade_delete_with_rule(self):
        rule = self._make_rule()
        RuleCondition.objects.create(
            rule=rule,
            field=RuleCondition.ConditionField.TOWER,
            operator=RuleCondition.Operator.EQ,
            value="A",
        )
        # Capture the PK before deletion: Django clears ``rule.pk`` after a
        # hard delete, which would make ``filter(rule=rule)`` raise
        # ``ValueError: Model instances passed to related filters must be saved.``
        rule_id = rule.pk
        rule.delete()
        self.assertEqual(RuleCondition.objects.filter(rule_id=rule_id).count(), 0)

    def test_ordering_by_sort_order(self):
        rule = self._make_rule()
        RuleCondition.objects.create(
            rule=rule, field=RuleCondition.ConditionField.TOWER,
            operator=RuleCondition.Operator.EQ, value="A", sort_order=2,
        )
        RuleCondition.objects.create(
            rule=rule, field=RuleCondition.ConditionField.WING,
            operator=RuleCondition.Operator.EQ, value="W1", sort_order=1,
        )
        conds = list(RuleCondition.objects.filter(rule=rule))
        self.assertEqual(conds[0].field, RuleCondition.ConditionField.WING)
        self.assertEqual(conds[1].field, RuleCondition.ConditionField.TOWER)


# ---------------------------------------------------------------------------
# RuleAction
# ---------------------------------------------------------------------------


class RuleActionTest(RuleModelTestBase):
    def test_creation_with_defaults(self):
        rule = self._make_rule()
        action = RuleAction.objects.create(
            rule=rule,
            action=RuleAction.ActionType.AUTO_APPROVE,
        )
        self.assertEqual(action.execution_order, 0)
        self.assertEqual(action.parameters, {})

    def test_str(self):
        rule = self._make_rule()
        action = RuleAction.objects.create(
            rule=rule,
            action=RuleAction.ActionType.REJECT,
            execution_order=3,
        )
        self.assertIn("reject", str(action))
        self.assertIn("order=3", str(action))

    def test_clean_rejects_negative_execution_order(self):
        rule = self._make_rule()
        action = RuleAction(
            rule=rule,
            action=RuleAction.ActionType.REJECT,
            execution_order=-1,
        )
        with self.assertRaises(ValidationError):
            action.clean()

    def test_clean_accepts_zero_execution_order(self):
        rule = self._make_rule()
        action = RuleAction(
            rule=rule,
            action=RuleAction.ActionType.REJECT,
            execution_order=0,
        )
        action.clean()  # should not raise

    def test_parameters_json_roundtrip(self):
        rule = self._make_rule()
        params = {"notify_channels": ["push", "sms"], "template": "delivery"}
        action = RuleAction.objects.create(
            rule=rule,
            action=RuleAction.ActionType.SEND_NOTIFICATION,
            parameters=params,
        )
        action.refresh_from_db()
        self.assertEqual(action.parameters, params)

    def test_cascade_delete_with_rule(self):
        rule = self._make_rule()
        RuleAction.objects.create(rule=rule, action=RuleAction.ActionType.REJECT)
        # Capture the PK before deletion: Django clears ``rule.pk`` after a
        # hard delete, which would make ``filter(rule=rule)`` raise
        # ``ValueError: Model instances passed to related filters must be saved.``
        rule_id = rule.pk
        rule.delete()
        self.assertEqual(RuleAction.objects.filter(rule_id=rule_id).count(), 0)

    def test_ordering_by_execution_order(self):
        rule = self._make_rule()
        RuleAction.objects.create(
            rule=rule, action=RuleAction.ActionType.REJECT, execution_order=2,
        )
        RuleAction.objects.create(
            rule=rule, action=RuleAction.ActionType.AUTO_APPROVE, execution_order=1,
        )
        actions = list(RuleAction.objects.filter(rule=rule))
        self.assertEqual(actions[0].action, RuleAction.ActionType.AUTO_APPROVE)
        self.assertEqual(actions[1].action, RuleAction.ActionType.REJECT)


# ---------------------------------------------------------------------------
# RuleEvaluation
# ---------------------------------------------------------------------------


class RuleEvaluationTest(RuleModelTestBase):
    def test_creation(self):
        rule = self._make_rule()
        ev = RuleEvaluation.objects.create(
            society=self.society,
            rule=rule,
            input_context={"visitor_category": "DELIVERY"},
            matched_conditions={"1": {"matched": True}},
            action_taken=RuleEvaluation.ActionTaken.AUTO_APPROVE,
            execution_time_ms=12,
        )
        self.assertIsNotNone(ev.pk)
        self.assertEqual(ev.action_taken, RuleEvaluation.ActionTaken.AUTO_APPROVE)
        self.assertEqual(ev.execution_time_ms, 12)
        self.assertEqual(ev.error_message, "")

    def test_no_match_action_choice(self):
        rule = self._make_rule()
        ev = RuleEvaluation.objects.create(
            society=self.society,
            rule=rule,
            action_taken=RuleEvaluation.ActionTaken.NO_MATCH,
        )
        self.assertEqual(ev.action_taken, RuleEvaluation.ActionTaken.NO_MATCH)

    def test_error_action_choice(self):
        ev = RuleEvaluation.objects.create(
            society=self.society,
            rule=None,
            action_taken=RuleEvaluation.ActionTaken.ERROR,
            error_message="boom",
        )
        self.assertEqual(ev.action_taken, RuleEvaluation.ActionTaken.ERROR)
        self.assertEqual(ev.error_message, "boom")

    def test_rule_set_null_on_rule_delete(self):
        rule = self._make_rule()
        ev = RuleEvaluation.objects.create(
            society=self.society,
            rule=rule,
            action_taken=RuleEvaluation.ActionTaken.AUTO_APPROVE,
        )
        rule.delete()
        ev.refresh_from_db()
        self.assertIsNone(ev.rule)

    def test_append_only_intent_documented(self):
        # The model does NOT override save()/delete() (unlike GateOpsAuditLog),
        # but the intent is documented. Verify the model has no custom save.
        self.assertNotIn("save", RuleEvaluation.__dict__)

    def test_ordering_newest_first(self):
        rule = self._make_rule()
        ev1 = RuleEvaluation.objects.create(
            society=self.society, rule=rule,
            action_taken=RuleEvaluation.ActionTaken.AUTO_APPROVE,
        )
        ev2 = RuleEvaluation.objects.create(
            society=self.society, rule=rule,
            action_taken=RuleEvaluation.ActionTaken.NO_MATCH,
        )
        evs = list(RuleEvaluation.objects.filter(society=self.society))
        # Newest first.
        self.assertEqual(evs[0].pk, ev2.pk)
        self.assertEqual(evs[1].pk, ev1.pk)

    def test_society_isolation(self):
        rule = self._make_rule()
        RuleEvaluation.objects.create(
            society=self.society, rule=rule,
            action_taken=RuleEvaluation.ActionTaken.AUTO_APPROVE,
        )
        RuleEvaluation.objects.create(
            society=self.other_society, rule=None,
            action_taken=RuleEvaluation.ActionTaken.NO_MATCH,
        )
        self.assertEqual(
            RuleEvaluation.objects.filter(society=self.society).count(), 1
        )
        self.assertEqual(
            RuleEvaluation.objects.filter(society=self.other_society).count(), 1
        )
