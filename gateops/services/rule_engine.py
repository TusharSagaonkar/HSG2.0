"""Rule engine service for the ``gateops`` app.

Evaluates configurable :class:`Rule` definitions against a gate-event context
and returns the action to take. Every gate decision flows through this
engine — no business rules are hardcoded.

Algorithm (see ``plans/gate_operations_platform_design.md`` §4.5):

1. Load active rules for the society where ``applies_on`` matches the event
   direction (or ``BOTH``), within the valid date window, ordered by
   ``priority`` ascending (lower = higher priority).
2. Filter rules by scope: a rule with ``visitor_category`` set only applies
   if the context's visitor category matches; ``None`` scope = applies to all.
3. For each rule (priority order), evaluate its conditions as a chained
   boolean expression. The first condition seeds the result; each subsequent
   condition is combined using its own ``logical_connector``.
4. The first matching rule wins — execute its first action (by
   ``execution_order``), log a :class:`RuleEvaluation`, and return.
5. If no rule matches, log a ``NO_MATCH`` evaluation and return the default
   action ``REQUIRE_APPROVAL`` (safe middle ground).

Design decisions
-----------------
- **First-action-wins:** when a rule matches, only its lowest-``execution_order``
  action's effect is returned (the action code is recorded). Additional
  actions on the same rule are surfaced in ``RuleEvaluationResult.actions``
  for callers that wish to execute side-effects (notifications, escalations),
  but the *decision* is the first action. This matches the design's
  "execute first matching action" guidance.
- **Logical connector ownership:** each condition's ``logical_connector``
  joins *that* condition to the running result. The first condition's
  connector is ignored. This makes each row self-describing and matches the
  pseudocode ``result = result AND/OR condN``.
- **Graceful degradation:** missing context fields cause a condition to
  evaluate to ``False`` (except ``is_false``, which is ``True`` when the
  field is absent/None). Type-incompatible comparisons also yield ``False``
  rather than raising.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from django.db.models import Q

from gateops.models import (
    HolidayCalendar,
    Rule,
    RuleAction,
    RuleCondition,
    RuleEvaluation,
)

logger = logging.getLogger(__name__)

# Default action when no rule matches. The design states "do not force
# resident approval for every visitor" — REQUIRE_APPROVAL is the safe middle
# ground (security/resident can approve) without forcing resident approval.
DEFAULT_NO_MATCH_ACTION = RuleAction.ActionType.REQUIRE_APPROVAL


@dataclass
class RuleEvaluationResult:
    """Outcome of a rule-engine evaluation.

    Attributes:
        matched: whether any rule matched.
        rule: the matched :class:`Rule` (or ``None`` for no-match/error).
        action: the action code string to apply.
        actions: all :class:`RuleAction` rows for the matched rule (ordered).
        evaluation: the persisted :class:`RuleEvaluation` log record.
        execution_time_ms: total evaluation time in milliseconds.
    """

    matched: bool
    rule: Rule | None
    action: str
    actions: list
    evaluation: RuleEvaluation
    execution_time_ms: int


# Mapping from RuleCondition.ConditionField to context keys (with fallbacks).
# The first existing key wins; nested lookups are expressed as tuples.
_FIELD_CONTEXT_KEYS: dict[str, tuple] = {
    RuleCondition.ConditionField.VISITOR_TYPE: ("visitor_category", "visitor_type", "visitor_category_code"),
    RuleCondition.ConditionField.VISITOR_CATEGORY: ("visitor_category", "visitor_category_code"),
    RuleCondition.ConditionField.VEHICLE: ("vehicle", "vehicle_id"),
    RuleCondition.ConditionField.VEHICLE_CATEGORY: ("vehicle_category", "vehicle_category_code"),
    RuleCondition.ConditionField.TOWER: ("tower",),
    RuleCondition.ConditionField.WING: ("wing",),
    RuleCondition.ConditionField.FLAT: ("flat",),
    RuleCondition.ConditionField.RESIDENT: ("resident", "resident_id"),
    RuleCondition.ConditionField.GUARD: ("guard", "guard_id"),
    RuleCondition.ConditionField.GATE: ("gate", "gate_id"),
    RuleCondition.ConditionField.TIME: ("time", "current_time"),
    RuleCondition.ConditionField.DATE: ("date", "current_date"),
    RuleCondition.ConditionField.MAX_VISITORS: ("max_visitors", "visitors_inside_count"),
    RuleCondition.ConditionField.MAX_STAY: ("max_stay", "max_stay_hours"),
    RuleCondition.ConditionField.CONTRACTOR_EXPIRY: ("contractor_expiry",),
    RuleCondition.ConditionField.IS_EMERGENCY: ("is_emergency",),
    RuleCondition.ConditionField.IS_VIP: ("is_vip",),
    RuleCondition.ConditionField.RISK_SCORE: ("risk_score",),
    # BLACKLIST / PASS_VALID / HOLIDAY are resolved specially below.
}

# Fields whose value lives under the nested "person" / "pass" sub-dicts.
_NESTED_PERSON_FIELDS = {
    RuleCondition.ConditionField.BLACKLIST: ("is_blacklisted",),
}
_NESTED_PASS_FIELDS = {
    RuleCondition.ConditionField.PASS_VALID: ("is_valid",),
}


class RuleEngineService:
    """Evaluates rules against a gate-event context."""

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    @classmethod
    def evaluate(cls, context: dict) -> RuleEvaluationResult:
        """Evaluate all active rules for the society in priority order.

        Returns the first matching rule's action. If no rule matches,
        returns the default action (``REQUIRE_APPROVAL``).
        """
        start = time.perf_counter()
        society = cls._extract_society(context)
        applies_on = cls._extract_applies_on(context)
        eval_date = cls._extract_date(context)

        try:
            rules = cls._load_rules(society, applies_on, eval_date)
            for rule in rules:
                if not cls._scope_matches(rule, context):
                    continue
                matched, matched_conditions = cls._evaluate_rule(rule, context)
                if matched:
                    actions = list(rule.actions.order_by("execution_order", "id"))
                    action_code = (
                        actions[0].action if actions else DEFAULT_NO_MATCH_ACTION
                    )
                    elapsed = cls._elapsed_ms(start)
                    evaluation = cls._log_evaluation(
                        society=society,
                        rule=rule,
                        context=context,
                        matched_conditions=matched_conditions,
                        action_taken=action_code,
                        execution_time_ms=elapsed,
                    )
                    return RuleEvaluationResult(
                        matched=True,
                        rule=rule,
                        action=action_code,
                        actions=actions,
                        evaluation=evaluation,
                        execution_time_ms=elapsed,
                    )

            # No rule matched.
            elapsed = cls._elapsed_ms(start)
            evaluation = cls._log_evaluation(
                society=society,
                rule=None,
                context=context,
                matched_conditions={},
                action_taken=RuleEvaluation.ActionTaken.NO_MATCH,
                execution_time_ms=elapsed,
            )
            return RuleEvaluationResult(
                matched=False,
                rule=None,
                action=DEFAULT_NO_MATCH_ACTION,
                actions=[],
                evaluation=evaluation,
                execution_time_ms=elapsed,
            )
        except Exception as exc:  # noqa: BLE001 — log and degrade gracefully.
            logger.exception("Rule engine evaluation failed: %s", exc)
            elapsed = cls._elapsed_ms(start)
            evaluation = cls._log_evaluation(
                society=society,
                rule=None,
                context=context,
                matched_conditions={},
                action_taken=RuleEvaluation.ActionTaken.ERROR,
                execution_time_ms=elapsed,
                error_message=str(exc),
            )
            return RuleEvaluationResult(
                matched=False,
                rule=None,
                action=DEFAULT_NO_MATCH_ACTION,
                actions=[],
                evaluation=evaluation,
                execution_time_ms=elapsed,
            )

    # ------------------------------------------------------------------ #
    # Condition evaluation
    # ------------------------------------------------------------------ #

    @classmethod
    def _evaluate_rule(cls, rule: Rule, context: dict) -> tuple[bool, dict]:
        """Evaluate all conditions of a rule as a chained boolean.

        Returns ``(matched, matched_conditions_detail)``.
        """
        conditions = list(rule.conditions.order_by("sort_order", "id"))
        if not conditions:
            # A rule with no conditions always matches (unconditional rule).
            return True, {}

        result = True
        matched_detail: dict = {}
        for index, condition in enumerate(conditions):
            cond_matched = cls._evaluate_condition(condition, context)
            matched_detail[str(condition.pk)] = {
                "field": condition.field,
                "operator": condition.operator,
                "value": condition.value,
                "matched": cond_matched,
            }
            if index == 0:
                result = cond_matched
            else:
                connector = condition.logical_connector
                if connector == RuleCondition.LogicalConnector.OR:
                    result = result or cond_matched
                else:  # AND (default)
                    result = result and cond_matched
        return result, matched_detail

    @classmethod
    def _evaluate_condition(cls, condition: RuleCondition, context: dict) -> bool:
        """Evaluate a single condition against the context."""
        op = condition.operator
        field_value = cls._resolve_field_value(condition.field, context)
        cmp_value = condition.value

        # Boolean operators ignore `value`.
        if op == RuleCondition.Operator.IS_TRUE:
            return bool(field_value) is True
        if op == RuleCondition.Operator.IS_FALSE:
            # Missing/None counts as "false" → is_true is False, is_false is True.
            return bool(field_value) is False

        # For all other operators a missing field value means no match.
        if field_value is None:
            return False

        try:
            if op == RuleCondition.Operator.EQ:
                return field_value == cmp_value
            if op == RuleCondition.Operator.NEQ:
                return field_value != cmp_value
            if op == RuleCondition.Operator.GT:
                return field_value > cmp_value
            if op == RuleCondition.Operator.GTE:
                return field_value >= cmp_value
            if op == RuleCondition.Operator.LT:
                return field_value < cmp_value
            if op == RuleCondition.Operator.LTE:
                return field_value <= cmp_value
            if op == RuleCondition.Operator.IN:
                return field_value in (cmp_value or [])
            if op == RuleCondition.Operator.NOT_IN:
                return field_value not in (cmp_value or [])
            if op == RuleCondition.Operator.CONTAINS:
                # `value` is contained in the context field (string/list).
                return cmp_value in field_value
            if op == RuleCondition.Operator.REGEX:
                return re.search(str(cmp_value), str(field_value)) is not None
            if op == RuleCondition.Operator.BETWEEN:
                start, end = cls._unpack_between(cmp_value)
                return start <= field_value <= end
        except (TypeError, ValueError):
            # Type-incompatible comparison → no match (graceful degradation).
            return False
        return False

    @staticmethod
    def _unpack_between(value: Any) -> tuple:
        """Normalize a BETWEEN value into a ``(start, end)`` tuple."""
        if isinstance(value, dict):
            return value["start"], value["end"]
        if isinstance(value, (list, tuple)):
            return value[0], value[1]
        raise ValueError("between value must be a dict or 2-element list")

    @classmethod
    def _resolve_field_value(cls, condition_field: str, context: dict) -> Any:
        """Resolve a condition field to its value in the context.

        Returns ``None`` when the field is absent. The HOLIDAY field is
        computed via :meth:`_check_holiday`.
        """
        # HOLIDAY is computed, not looked up.
        if condition_field == RuleCondition.ConditionField.HOLIDAY:
            return cls._check_holiday(context)

        # Nested person fields (e.g. blacklist).
        if condition_field in _NESTED_PERSON_FIELDS:
            person = context.get("person") or {}
            if not isinstance(person, dict):
                return None
            for key in _NESTED_PERSON_FIELDS[condition_field]:
                if key in person:
                    return person[key]
            # Fall back to top-level keys.
            for key in _NESTED_PERSON_FIELDS[condition_field]:
                if key in context:
                    return context[key]
            return None

        # Nested pass fields.
        if condition_field in _NESTED_PASS_FIELDS:
            gate_pass = context.get("pass") or {}
            if not isinstance(gate_pass, dict):
                return None
            for key in _NESTED_PASS_FIELDS[condition_field]:
                if key in gate_pass:
                    return gate_pass[key]
            for key in _NESTED_PASS_FIELDS[condition_field]:
                if key in context:
                    return context[key]
            return None

        # Direct / fallback context keys.
        keys = _FIELD_CONTEXT_KEYS.get(condition_field)
        if keys is None:
            # Unknown field name — try a direct lookup as a last resort.
            return context.get(condition_field)
        for key in keys:
            if key in context:
                return context[key]
        return None

    # ------------------------------------------------------------------ #
    # Holiday check
    # ------------------------------------------------------------------ #

    @classmethod
    def _check_holiday(cls, context: dict) -> bool:
        """Return True if the context's date is a society holiday that
        affects the relevant visitor category.
        """
        society = cls._extract_society(context)
        if society is None:
            return False
        eval_date = cls._extract_date(context)
        if eval_date is None:
            return False

        # Exact date match OR recurring annual match on (month, day).
        holidays = HolidayCalendar.objects.filter(society=society).filter(
            Q(date=eval_date)
            | Q(is_recurring_annually=True, date__month=eval_date.month, date__day=eval_date.day)
        )
        if not holidays.exists():
            return False

        affects = context.get("visitor_category_affects") or context.get("affects")
        visitor_cat_id = context.get("visitor_category_id")
        visitor_cat_code = context.get("visitor_category_code") or context.get("visitor_category")

        for holiday in holidays:
            if holiday.affects == HolidayCalendar.Affects.ALL:
                return True
            if holiday.affects == HolidayCalendar.Affects.VISITORS:
                return True
            # Category-specific holidays require knowing the visitor's flags.
            # Resolve lazily to avoid an extra query when not needed.
            cat = cls._visitor_category_for(society, visitor_cat_id, visitor_cat_code)
            if cat is None:
                # Cannot determine category — treat category-specific holiday
                # as not affecting (conservative: only ALL/VISITORS hit here).
                continue
            if holiday.affects == HolidayCalendar.Affects.CONTRACTORS and cat.is_contractor:
                return True
            if holiday.affects == HolidayCalendar.Affects.DELIVERIES and cat.is_delivery:
                return True
        return False

    @staticmethod
    def _visitor_category_for(society, visitor_cat_id, visitor_cat_code):
        """Look up the VisitorCategory by id or code (cached per-call)."""
        from gateops.models import VisitorCategory

        qs = VisitorCategory.objects.filter(society=society)
        if visitor_cat_id is not None:
            cat = qs.filter(pk=visitor_cat_id).first()
            if cat:
                return cat
        if visitor_cat_code is not None:
            cat = qs.filter(code=visitor_cat_code).first()
            if cat:
                return cat
        return None

    # ------------------------------------------------------------------ #
    # Rule loading & scope filtering
    # ------------------------------------------------------------------ #

    @classmethod
    def _load_rules(cls, society, applies_on: str, eval_date):
        """Load active, in-window rules for the society, ordered by priority."""
        if society is None:
            return Rule.objects.none()
        qs = Rule.objects.filter(society=society, is_active=True).filter(
            Q(applies_on=applies_on) | Q(applies_on=Rule.AppliesOn.BOTH)
        )
        # Validity window: valid_from <= date <= valid_until (or valid_until null).
        if eval_date is not None:
            qs = qs.filter(Q(valid_from__lte=eval_date)).filter(
                Q(valid_until__isnull=True) | Q(valid_until__gte=eval_date)
            )
        return qs.order_by("priority", "id").select_related(
            "visitor_category", "vehicle_category", "material_category", "gate"
        )

    @classmethod
    def _scope_matches(cls, rule: Rule, context: dict) -> bool:
        """Return True if the rule's category/gate scope matches the context.

        A rule with a ``None`` category/gate scope applies to all. When a
        category is set, the context must supply either a matching id or a
        matching code (resolved against the rule's already-loaded category).
        """
        if rule.visitor_category_id is not None:
            if not cls._category_matches(
                rule.visitor_category,
                context.get("visitor_category_id"),
                context.get("visitor_category_code") or context.get("visitor_category"),
            ):
                return False
        if rule.vehicle_category_id is not None:
            if not cls._category_matches(
                rule.vehicle_category,
                context.get("vehicle_category_id"),
                context.get("vehicle_category_code") or context.get("vehicle_category"),
            ):
                return False
        if rule.material_category_id is not None:
            if not cls._category_matches(
                rule.material_category,
                context.get("material_category_id"),
                context.get("material_category_code") or context.get("material_category"),
            ):
                return False
        if rule.gate_id is not None:
            ctx_gate = context.get("gate_id") or context.get("gate")
            if cls._to_int(ctx_gate) != rule.gate_id:
                return False
        return True

    @staticmethod
    def _category_matches(rule_category, ctx_cat_id, ctx_cat_code) -> bool:
        """Compare a rule's category FK against the context's category.

        ``rule_category`` is the already-loaded category instance (or None).
        Matches by id first, then by uppercase code.
        """
        if rule_category is None:
            return False
        if ctx_cat_id is not None:
            return RuleEngineService._to_int(ctx_cat_id) == rule_category.pk
        if ctx_cat_code is not None:
            return str(ctx_cat_code).upper() == rule_category.code
        return False

    # ------------------------------------------------------------------ #
    # Logging
    # ------------------------------------------------------------------ #

    @classmethod
    def _log_evaluation(
        cls,
        *,
        society,
        rule,
        context,
        matched_conditions,
        action_taken,
        execution_time_ms,
        error_message="",
    ) -> RuleEvaluation:
        """Persist a RuleEvaluation log row (append-only by intent)."""
        # NOTE: `gate_event` FK is intentionally omitted — the GateEvent model
        # does not exist until Phase 3. The gate_event reference (if present in
        # the context) is preserved for debugging inside `input_context` via
        # `_sanitize_context(context)` below.
        return RuleEvaluation.objects.create(
            society=society,
            rule=rule,
            input_context=cls._sanitize_context(context),
            matched_conditions=matched_conditions,
            action_taken=action_taken,
            execution_time_ms=execution_time_ms,
            created_by=context.get("created_by") or context.get("actor"),
            error_message=error_message,
        )

    @staticmethod
    def _sanitize_context(context: dict) -> dict:
        """Return a JSON-serializable copy of the context for logging.

        The context may hold Django model instances (e.g. ``Society``) or other
        non-JSON-native objects. We round-trip through ``json`` with
        ``default=str`` so every value becomes a JSON-native type. This is
        required because psycopg's JSON adapter serializes with plain
        ``json.dumps`` (no ``default`` hook) at insert time — returning the raw
        context here would raise ``TypeError: Object of type Society is not
        JSON serializable`` when the ``RuleEvaluation`` row is saved.
        """
        import json

        try:
            return json.loads(json.dumps(context, default=str))
        except (TypeError, ValueError):
            return {k: str(v) for k, v in context.items()}

    # ------------------------------------------------------------------ #
    # Context extraction helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_society(context: dict):
        society = context.get("society")
        if society is not None:
            return society
        society_id = context.get("society_id")
        if society_id is None:
            return None
        from societies.models import Society

        return Society.objects.filter(pk=society_id).first()

    @staticmethod
    def _extract_applies_on(context: dict) -> str:
        """Determine ENTRY/EXIT from the context."""
        applies_on = context.get("applies_on")
        if applies_on in (Rule.AppliesOn.ENTRY, Rule.AppliesOn.EXIT):
            return applies_on
        direction = context.get("direction") or context.get("event_type")
        if direction in ("inbound", "arrival", "entry", "in"):
            return Rule.AppliesOn.ENTRY
        if direction in ("outbound", "departure", "exit", "out"):
            return Rule.AppliesOn.EXIT
        # Default to ENTRY when undeterminable.
        return Rule.AppliesOn.ENTRY

    @staticmethod
    def _extract_date(context: dict):
        """Extract the evaluation date from the context."""
        eval_date = context.get("date") or context.get("current_date")
        if eval_date is None:
            return date.today()
        if isinstance(eval_date, date):
            return eval_date
        if isinstance(eval_date, datetime):
            return eval_date.date()
        try:
            return date.fromisoformat(str(eval_date))
        except ValueError:
            return date.today()

    @staticmethod
    def _to_int(value) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _elapsed_ms(start: float) -> int:
        return int((time.perf_counter() - start) * 1000)
