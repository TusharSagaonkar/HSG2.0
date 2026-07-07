"""Tests for the Phase-2 gateops rule engine service.

Covers: end-to-end evaluation, all 13 operators, logical connectors (AND/OR),
priority ordering, date-range filtering, category scope filtering, holiday
condition, RuleEvaluation logging, and the dry-run RuleTestService.
"""

from datetime import date, time

from django.test import TestCase
from django.utils import timezone

from gateops.models import (
    HolidayCalendar,
    Rule,
    RuleAction,
    RuleCondition,
    RuleEvaluation,
    VisitorCategory,
)
from gateops.services.rule_engine import RuleEngineService, RuleEvaluationResult
from gateops.services.rule_tester import RuleTestService
from societies.models import Society


class RuleEngineTestBase(TestCase):
    """Shared helpers for rule-engine tests.

    The society is created once per class via ``setUpTestData`` to avoid
    re-running the expensive accounting + gateops bootstrap signal on every
    test method.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.society = Society.objects.create(name="Engine Society")
        cls.visitor_cat = VisitorCategory.objects.get(
            society=cls.society, code="DELIVERY"
        )
        cls.contractor_cat = VisitorCategory.objects.get(
            society=cls.society, code="CONTRACTOR"
        )

    # --- helpers ----------------------------------------------------------

    def _make_rule(self, code="RULE_001", priority=100, **kwargs):
        return Rule.objects.create(
            society=self.society,
            name=kwargs.pop("name", "Test Rule"),
            code=code,
            priority=priority,
            **kwargs,
        )

    def _add_condition(self, rule, field, operator, value, connector="and", sort_order=0):
        return RuleCondition.objects.create(
            rule=rule,
            field=field,
            operator=operator,
            value=value,
            logical_connector=connector,
            sort_order=sort_order,
        )

    def _add_action(self, rule, action=RuleAction.ActionType.AUTO_APPROVE, order=0):
        return RuleAction.objects.create(
            rule=rule,
            action=action,
            execution_order=order,
        )

    def _base_context(self, **overrides):
        ctx = {
            "society": self.society,
            "applies_on": Rule.AppliesOn.ENTRY,
            "date": timezone.localdate(),
            "visitor_category": "DELIVERY",
            "visitor_category_id": self.visitor_cat.pk,
        }
        ctx.update(overrides)
        return ctx


# ---------------------------------------------------------------------------
# End-to-end evaluation
# ---------------------------------------------------------------------------


class RuleEngineEvaluateTest(RuleEngineTestBase):
    def test_matching_rule_returns_action(self):
        rule = self._make_rule()
        self._add_condition(
            rule, RuleCondition.ConditionField.VISITOR_TYPE,
            RuleCondition.Operator.EQ, "DELIVERY",
        )
        self._add_action(rule, RuleAction.ActionType.AUTO_APPROVE)

        result = RuleEngineService.evaluate(self._base_context())
        self.assertTrue(result.matched)
        self.assertEqual(result.rule.pk, rule.pk)
        self.assertEqual(result.action, RuleAction.ActionType.AUTO_APPROVE)

    def test_no_matching_rule_returns_default(self):
        rule = self._make_rule()
        self._add_condition(
            rule, RuleCondition.ConditionField.VISITOR_TYPE,
            RuleCondition.Operator.EQ, "GUEST",
        )
        self._add_action(rule, RuleAction.ActionType.AUTO_APPROVE)

        result = RuleEngineService.evaluate(self._base_context(visitor_category="DELIVERY"))
        self.assertFalse(result.matched)
        self.assertIsNone(result.rule)
        self.assertEqual(result.action, RuleAction.ActionType.REQUIRE_APPROVAL)

    def test_no_rules_at_all_returns_default(self):
        result = RuleEngineService.evaluate(self._base_context())
        self.assertFalse(result.matched)
        self.assertEqual(result.action, RuleAction.ActionType.REQUIRE_APPROVAL)

    def test_rule_with_no_conditions_always_matches(self):
        rule = self._make_rule()
        self._add_action(rule, RuleAction.ActionType.DIRECT_ENTRY)
        result = RuleEngineService.evaluate(self._base_context())
        self.assertTrue(result.matched)
        self.assertEqual(result.action, RuleAction.ActionType.DIRECT_ENTRY)

    def test_evaluation_log_created_on_match(self):
        rule = self._make_rule()
        self._add_condition(
            rule, RuleCondition.ConditionField.VISITOR_TYPE,
            RuleCondition.Operator.EQ, "DELIVERY",
        )
        self._add_action(rule, RuleAction.ActionType.AUTO_APPROVE)
        RuleEngineService.evaluate(self._base_context())
        ev = RuleEvaluation.objects.get(society=self.society, rule=rule)
        self.assertEqual(ev.action_taken, RuleEvaluation.ActionTaken.AUTO_APPROVE)

    def test_evaluation_log_created_on_no_match(self):
        RuleEngineService.evaluate(self._base_context())
        ev = RuleEvaluation.objects.get(society=self.society, rule__isnull=True)
        self.assertEqual(ev.action_taken, RuleEvaluation.ActionTaken.NO_MATCH)

    def test_result_is_dataclass(self):
        rule = self._make_rule()
        self._add_action(rule)
        result = RuleEngineService.evaluate(self._base_context())
        self.assertIsInstance(result, RuleEvaluationResult)
        self.assertIsNotNone(result.evaluation)
        self.assertIsInstance(result.execution_time_ms, int)


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------


class RuleEngineOperatorTest(RuleEngineTestBase):
    def _eval_single(self, field, operator, value, context_value, context_key="tower"):
        # ``RuleEngineService.evaluate`` returns the *first matching* rule in
        # priority order. Several operator tests call this helper twice (a
        # positive then a negative assertion) within a single method, so we
        # must isolate the rule under test by removing any rules created by a
        # previous assertion — otherwise an earlier rule could match first and
        # mask the operator behaviour being asserted.
        Rule.objects.filter(society=self.society).delete()
        rule = self._make_rule(code="RULE_OP")
        self._add_condition(rule, field, operator, value)
        self._add_action(rule)
        ctx = self._base_context(**{context_key: context_value})
        return RuleEngineService.evaluate(ctx).matched

    def test_eq(self):
        self.assertTrue(self._eval_single(
            RuleCondition.ConditionField.TOWER, RuleCondition.Operator.EQ, "A", "A"))
        self.assertFalse(self._eval_single(
            RuleCondition.ConditionField.TOWER, RuleCondition.Operator.EQ, "A", "B"))

    def test_neq(self):
        self.assertTrue(self._eval_single(
            RuleCondition.ConditionField.TOWER, RuleCondition.Operator.NEQ, "A", "B"))
        self.assertFalse(self._eval_single(
            RuleCondition.ConditionField.TOWER, RuleCondition.Operator.NEQ, "A", "A"))

    def test_gt(self):
        self.assertTrue(self._eval_single(
            RuleCondition.ConditionField.MAX_VISITORS, RuleCondition.Operator.GT, 10, 15,
            context_key="max_visitors"))
        self.assertFalse(self._eval_single(
            RuleCondition.ConditionField.MAX_VISITORS, RuleCondition.Operator.GT, 10, 5,
            context_key="max_visitors"))

    def test_gte(self):
        self.assertTrue(self._eval_single(
            RuleCondition.ConditionField.MAX_VISITORS, RuleCondition.Operator.GTE, 10, 10,
            context_key="max_visitors"))
        self.assertFalse(self._eval_single(
            RuleCondition.ConditionField.MAX_VISITORS, RuleCondition.Operator.GTE, 10, 9,
            context_key="max_visitors"))

    def test_lt(self):
        self.assertTrue(self._eval_single(
            RuleCondition.ConditionField.MAX_VISITORS, RuleCondition.Operator.LT, 10, 5,
            context_key="max_visitors"))
        self.assertFalse(self._eval_single(
            RuleCondition.ConditionField.MAX_VISITORS, RuleCondition.Operator.LT, 10, 15,
            context_key="max_visitors"))

    def test_lte(self):
        self.assertTrue(self._eval_single(
            RuleCondition.ConditionField.MAX_VISITORS, RuleCondition.Operator.LTE, 10, 10,
            context_key="max_visitors"))
        self.assertFalse(self._eval_single(
            RuleCondition.ConditionField.MAX_VISITORS, RuleCondition.Operator.LTE, 10, 11,
            context_key="max_visitors"))

    def test_in(self):
        self.assertTrue(self._eval_single(
            RuleCondition.ConditionField.TOWER, RuleCondition.Operator.IN,
            ["A", "B", "C"], "A"))
        self.assertFalse(self._eval_single(
            RuleCondition.ConditionField.TOWER, RuleCondition.Operator.IN,
            ["A", "B", "C"], "D"))

    def test_not_in(self):
        self.assertTrue(self._eval_single(
            RuleCondition.ConditionField.TOWER, RuleCondition.Operator.NOT_IN,
            ["A", "B"], "C"))
        self.assertFalse(self._eval_single(
            RuleCondition.ConditionField.TOWER, RuleCondition.Operator.NOT_IN,
            ["A", "B"], "A"))

    def test_contains(self):
        # `contains`: value is contained in the context field.
        self.assertTrue(self._eval_single(
            RuleCondition.ConditionField.TOWER, RuleCondition.Operator.CONTAINS,
            "A", "ABC"))
        self.assertFalse(self._eval_single(
            RuleCondition.ConditionField.TOWER, RuleCondition.Operator.CONTAINS,
            "Z", "ABC"))

    def test_regex(self):
        self.assertTrue(self._eval_single(
            RuleCondition.ConditionField.TOWER, RuleCondition.Operator.REGEX,
            r"^A\d+$", "A101"))
        self.assertFalse(self._eval_single(
            RuleCondition.ConditionField.TOWER, RuleCondition.Operator.REGEX,
            r"^A\d+$", "B101"))

    def test_between_dict(self):
        rule = self._make_rule()
        self._add_condition(
            rule, RuleCondition.ConditionField.MAX_VISITORS,
            RuleCondition.Operator.BETWEEN, {"start": 5, "end": 15},
        )
        self._add_action(rule)
        ctx = self._base_context(max_visitors=10)
        self.assertTrue(RuleEngineService.evaluate(ctx).matched)
        ctx = self._base_context(max_visitors=20)
        self.assertFalse(RuleEngineService.evaluate(ctx).matched)

    def test_between_list(self):
        rule = self._make_rule()
        self._add_condition(
            rule, RuleCondition.ConditionField.MAX_VISITORS,
            RuleCondition.Operator.BETWEEN, [5, 15],
        )
        self._add_action(rule)
        ctx = self._base_context(max_visitors=10)
        self.assertTrue(RuleEngineService.evaluate(ctx).matched)

    def test_is_true(self):
        rule = self._make_rule()
        self._add_condition(
            rule, RuleCondition.ConditionField.IS_EMERGENCY,
            RuleCondition.Operator.IS_TRUE, {},
        )
        self._add_action(rule)
        ctx = self._base_context(is_emergency=True)
        self.assertTrue(RuleEngineService.evaluate(ctx).matched)
        ctx = self._base_context(is_emergency=False)
        self.assertFalse(RuleEngineService.evaluate(ctx).matched)

    def test_is_false(self):
        rule = self._make_rule()
        self._add_condition(
            rule, RuleCondition.ConditionField.IS_VIP,
            RuleCondition.Operator.IS_FALSE, {},
        )
        self._add_action(rule)
        ctx = self._base_context(is_vip=False)
        self.assertTrue(RuleEngineService.evaluate(ctx).matched)
        # Missing field counts as false → is_false is True.
        ctx = self._base_context()
        ctx.pop("is_vip", None)
        self.assertTrue(RuleEngineService.evaluate(ctx).matched)

    def test_missing_field_returns_false_for_eq(self):
        rule = self._make_rule()
        self._add_condition(
            rule, RuleCondition.ConditionField.TOWER,
            RuleCondition.Operator.EQ, "A",
        )
        self._add_action(rule)
        ctx = self._base_context()
        ctx.pop("tower", None)
        self.assertFalse(RuleEngineService.evaluate(ctx).matched)


# ---------------------------------------------------------------------------
# Logical connectors
# ---------------------------------------------------------------------------


class RuleEngineConnectorTest(RuleEngineTestBase):
    def test_and_all_must_match(self):
        rule = self._make_rule()
        self._add_condition(rule, RuleCondition.ConditionField.TOWER,
            RuleCondition.Operator.EQ, "A", sort_order=0)
        self._add_condition(rule, RuleCondition.ConditionField.WING,
            RuleCondition.Operator.EQ, "W1", connector="and", sort_order=1)
        self._add_action(rule)
        # Both match.
        ctx = self._base_context(tower="A", wing="W1")
        self.assertTrue(RuleEngineService.evaluate(ctx).matched)
        # Only one matches.
        ctx = self._base_context(tower="A", wing="W2")
        self.assertFalse(RuleEngineService.evaluate(ctx).matched)

    def test_or_any_can_match(self):
        rule = self._make_rule()
        self._add_condition(rule, RuleCondition.ConditionField.TOWER,
            RuleCondition.Operator.EQ, "A", sort_order=0)
        self._add_condition(rule, RuleCondition.ConditionField.WING,
            RuleCondition.Operator.EQ, "W1", connector="or", sort_order=1)
        self._add_action(rule)
        # First matches.
        ctx = self._base_context(tower="A", wing="W2")
        self.assertTrue(RuleEngineService.evaluate(ctx).matched)
        # Second matches (OR).
        ctx = self._base_context(tower="B", wing="W1")
        self.assertTrue(RuleEngineService.evaluate(ctx).matched)
        # Neither matches.
        ctx = self._base_context(tower="B", wing="W2")
        self.assertFalse(RuleEngineService.evaluate(ctx).matched)

    def test_mixed_and_or_chain(self):
        # cond1 AND cond2 OR cond3 → (cond1 AND cond2) OR cond3
        rule = self._make_rule()
        self._add_condition(rule, RuleCondition.ConditionField.TOWER,
            RuleCondition.Operator.EQ, "A", sort_order=0)
        self._add_condition(rule, RuleCondition.ConditionField.WING,
            RuleCondition.Operator.EQ, "W1", connector="and", sort_order=1)
        self._add_condition(rule, RuleCondition.ConditionField.FLAT,
            RuleCondition.Operator.EQ, "101", connector="or", sort_order=2)
        self._add_action(rule)
        # cond3 alone matches → True.
        ctx = self._base_context(tower="B", wing="W2", flat="101")
        self.assertTrue(RuleEngineService.evaluate(ctx).matched)
        # cond1+cond2 match → True.
        ctx = self._base_context(tower="A", wing="W1", flat="999")
        self.assertTrue(RuleEngineService.evaluate(ctx).matched)
        # Nothing matches.
        ctx = self._base_context(tower="B", wing="W2", flat="999")
        self.assertFalse(RuleEngineService.evaluate(ctx).matched)


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------


class RuleEnginePriorityTest(RuleEngineTestBase):
    def test_lower_priority_number_evaluated_first(self):
        # Rule with priority 1 (higher priority) matches GUEST.
        rule_high = self._make_rule(code="HIGH", priority=1)
        self._add_condition(rule_high, RuleCondition.ConditionField.VISITOR_TYPE,
            RuleCondition.Operator.EQ, "GUEST")
        self._add_action(rule_high, RuleAction.ActionType.AUTO_APPROVE)

        # Rule with priority 100 (lower priority) matches DELIVERY.
        rule_low = self._make_rule(code="LOW", priority=100)
        self._add_condition(rule_low, RuleCondition.ConditionField.VISITOR_TYPE,
            RuleCondition.Operator.EQ, "DELIVERY")
        self._add_action(rule_low, RuleAction.ActionType.REJECT)

        # Context is DELIVERY → only the low-priority rule matches.
        result = RuleEngineService.evaluate(self._base_context(visitor_category="DELIVERY"))
        self.assertEqual(result.rule.pk, rule_low.pk)
        self.assertEqual(result.action, RuleAction.ActionType.REJECT)

    def test_first_matching_rule_wins_when_both_could_match(self):
        # Both rules match DELIVERY; the higher-priority (lower number) wins.
        rule_a = self._make_rule(code="RULE_A", priority=1)
        self._add_condition(rule_a, RuleCondition.ConditionField.VISITOR_TYPE,
            RuleCondition.Operator.EQ, "DELIVERY")
        self._add_action(rule_a, RuleAction.ActionType.AUTO_APPROVE)

        rule_b = self._make_rule(code="RULE_B", priority=2)
        self._add_condition(rule_b, RuleCondition.ConditionField.VISITOR_TYPE,
            RuleCondition.Operator.EQ, "DELIVERY")
        self._add_action(rule_b, RuleAction.ActionType.REJECT)

        result = RuleEngineService.evaluate(self._base_context(visitor_category="DELIVERY"))
        self.assertEqual(result.rule.pk, rule_a.pk)
        self.assertEqual(result.action, RuleAction.ActionType.AUTO_APPROVE)


# ---------------------------------------------------------------------------
# Date-range filtering
# ---------------------------------------------------------------------------


class RuleEngineDateRangeTest(RuleEngineTestBase):
    def test_rule_outside_valid_until_does_not_match(self):
        rule = self._make_rule(
            valid_from=date(2026, 1, 1),
            valid_until=date(2026, 6, 1),
        )
        self._add_condition(rule, RuleCondition.ConditionField.VISITOR_TYPE,
            RuleCondition.Operator.EQ, "DELIVERY")
        self._add_action(rule)
        ctx = self._base_context(date=date(2026, 7, 1), visitor_category="DELIVERY")
        result = RuleEngineService.evaluate(ctx)
        self.assertFalse(result.matched)

    def test_rule_before_valid_from_does_not_match(self):
        rule = self._make_rule(
            valid_from=date(2026, 6, 1),
            valid_until=date(2026, 12, 31),
        )
        self._add_condition(rule, RuleCondition.ConditionField.VISITOR_TYPE,
            RuleCondition.Operator.EQ, "DELIVERY")
        self._add_action(rule)
        ctx = self._base_context(date=date(2026, 5, 1), visitor_category="DELIVERY")
        result = RuleEngineService.evaluate(ctx)
        self.assertFalse(result.matched)

    def test_rule_within_range_matches(self):
        rule = self._make_rule(
            valid_from=date(2026, 1, 1),
            valid_until=date(2026, 12, 31),
        )
        self._add_condition(rule, RuleCondition.ConditionField.VISITOR_TYPE,
            RuleCondition.Operator.EQ, "DELIVERY")
        self._add_action(rule)
        ctx = self._base_context(date=date(2026, 6, 15), visitor_category="DELIVERY")
        result = RuleEngineService.evaluate(ctx)
        self.assertTrue(result.matched)

    def test_rule_with_null_valid_until_matches_any_future(self):
        rule = self._make_rule(valid_from=date(2020, 1, 1), valid_until=None)
        self._add_condition(rule, RuleCondition.ConditionField.VISITOR_TYPE,
            RuleCondition.Operator.EQ, "DELIVERY")
        self._add_action(rule)
        ctx = self._base_context(date=date(2030, 1, 1), visitor_category="DELIVERY")
        result = RuleEngineService.evaluate(ctx)
        self.assertTrue(result.matched)


# ---------------------------------------------------------------------------
# Category scope filtering
# ---------------------------------------------------------------------------


class RuleEngineScopeTest(RuleEngineTestBase):
    def test_rule_scoped_to_visitor_category_matches_by_id(self):
        rule = self._make_rule(visitor_category=self.visitor_cat)
        self._add_action(rule)
        ctx = self._base_context(visitor_category_id=self.visitor_cat.pk)
        self.assertTrue(RuleEngineService.evaluate(ctx).matched)

    def test_rule_scoped_to_visitor_category_matches_by_code(self):
        rule = self._make_rule(visitor_category=self.visitor_cat)
        self._add_action(rule)
        ctx = self._base_context(
            visitor_category="DELIVERY", visitor_category_id=None)
        self.assertTrue(RuleEngineService.evaluate(ctx).matched)

    def test_rule_scoped_to_visitor_category_does_not_match_other(self):
        rule = self._make_rule(visitor_category=self.visitor_cat)
        self._add_action(rule)
        ctx = self._base_context(
            visitor_category="CONTRACTOR",
            visitor_category_id=self.contractor_cat.pk,
        )
        self.assertFalse(RuleEngineService.evaluate(ctx).matched)

    def test_rule_with_null_scope_matches_all(self):
        rule = self._make_rule(visitor_category=None)
        self._add_action(rule)
        ctx = self._base_context(visitor_category="DELIVERY")
        self.assertTrue(RuleEngineService.evaluate(ctx).matched)
        ctx = self._base_context(visitor_category="CONTRACTOR")
        self.assertTrue(RuleEngineService.evaluate(ctx).matched)

    def test_inactive_rule_not_evaluated(self):
        rule = self._make_rule(is_active=False)
        self._add_action(rule)
        result = RuleEngineService.evaluate(self._base_context())
        self.assertFalse(result.matched)


# ---------------------------------------------------------------------------
# Holiday condition
# ---------------------------------------------------------------------------


class RuleEngineHolidayTest(RuleEngineTestBase):
    def test_holiday_condition_matches_on_holiday(self):
        holiday_date = timezone.localdate()
        HolidayCalendar.objects.create(
            society=self.society, name="Republic Day", date=holiday_date,
            affects=HolidayCalendar.Affects.ALL,
        )
        rule = self._make_rule()
        self._add_condition(rule, RuleCondition.ConditionField.HOLIDAY,
            RuleCondition.Operator.IS_TRUE, {})
        self._add_action(rule)
        ctx = self._base_context(date=holiday_date)
        self.assertTrue(RuleEngineService.evaluate(ctx).matched)

    def test_holiday_condition_no_match_on_non_holiday(self):
        ctx_date = timezone.localdate()
        HolidayCalendar.objects.create(
            society=self.society, name="Republic Day", date=date(2026, 1, 26),
            affects=HolidayCalendar.Affects.ALL,
        )
        rule = self._make_rule()
        self._add_condition(rule, RuleCondition.ConditionField.HOLIDAY,
            RuleCondition.Operator.IS_TRUE, {})
        self._add_action(rule)
        ctx = self._base_context(date=ctx_date)
        self.assertFalse(RuleEngineService.evaluate(ctx).matched)

    def test_recurring_annual_holiday_matches(self):
        ctx_date = timezone.localdate()
        HolidayCalendar.objects.create(
            society=self.society, name="Independence Day", date=ctx_date,
            is_recurring_annually=True, affects=HolidayCalendar.Affects.ALL,
        )
        rule = self._make_rule()
        self._add_condition(rule, RuleCondition.ConditionField.HOLIDAY,
            RuleCondition.Operator.IS_TRUE, {})
        self._add_action(rule)
        ctx = self._base_context(date=ctx_date)
        self.assertTrue(RuleEngineService.evaluate(ctx).matched)


# ---------------------------------------------------------------------------
# Dry-run (RuleTestService)
# ---------------------------------------------------------------------------


class RuleTestServiceTest(RuleEngineTestBase):
    def test_dry_run_matching_returns_structure(self):
        rule = self._make_rule()
        self._add_condition(rule, RuleCondition.ConditionField.TOWER,
            RuleCondition.Operator.EQ, "A")
        self._add_action(rule, RuleAction.ActionType.AUTO_APPROVE)

        result = RuleTestService.dry_run(rule, self._base_context(tower="A"))
        self.assertTrue(result["matched"])
        self.assertEqual(result["action"], RuleAction.ActionType.AUTO_APPROVE)
        self.assertIsInstance(result["matched_conditions"], list)
        self.assertEqual(len(result["matched_conditions"]), 1)
        self.assertTrue(result["matched_conditions"][0]["matched"])
        self.assertIsInstance(result["execution_time_ms"], int)

    def test_dry_run_non_matching(self):
        rule = self._make_rule()
        self._add_condition(rule, RuleCondition.ConditionField.TOWER,
            RuleCondition.Operator.EQ, "A")
        self._add_action(rule, RuleAction.ActionType.AUTO_APPROVE)

        result = RuleTestService.dry_run(rule, self._base_context(tower="B"))
        self.assertFalse(result["matched"])
        self.assertIsNone(result["action"])
        self.assertFalse(result["matched_conditions"][0]["matched"])

    def test_dry_run_does_not_persist_evaluation(self):
        rule = self._make_rule()
        self._add_condition(rule, RuleCondition.ConditionField.TOWER,
            RuleCondition.Operator.EQ, "A")
        self._add_action(rule)
        before = RuleEvaluation.objects.count()
        RuleTestService.dry_run(rule, self._base_context(tower="A"))
        after = RuleEvaluation.objects.count()
        self.assertEqual(before, after)

    def test_dry_run_no_conditions_matches(self):
        rule = self._make_rule()
        self._add_action(rule)
        result = RuleTestService.dry_run(rule, self._base_context())
        self.assertTrue(result["matched"])
        self.assertEqual(result["matched_conditions"], [])

    def test_dry_run_multiple_conditions_breakdown(self):
        rule = self._make_rule()
        self._add_condition(rule, RuleCondition.ConditionField.TOWER,
            RuleCondition.Operator.EQ, "A", sort_order=0)
        self._add_condition(rule, RuleCondition.ConditionField.WING,
            RuleCondition.Operator.EQ, "W1", connector="and", sort_order=1)
        self._add_action(rule)
        result = RuleTestService.dry_run(rule, self._base_context(tower="A", wing="W1"))
        self.assertTrue(result["matched"])
        self.assertEqual(len(result["matched_conditions"]), 2)
        self.assertTrue(all(c["matched"] for c in result["matched_conditions"]))
