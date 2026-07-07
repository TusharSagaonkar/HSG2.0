"""Rule testing service for the ``gateops`` app.

Allows admins to dry-run a single :class:`Rule` against a sample context
*without* persisting a :class:`RuleEvaluation` log or executing side-effects.
This is used to validate rule behaviour before activating a rule.

Unlike :class:`RuleEngineService`, this service:
- evaluates only the supplied rule (not the full priority-ordered set);
- does NOT create a ``RuleEvaluation`` row;
- returns a detailed per-condition breakdown for debugging.
"""

from __future__ import annotations

import time
from typing import Any

from gateops.models import Rule, RuleAction, RuleCondition
from gateops.services.rule_engine import RuleEngineService


class RuleTestService:
    """Dry-run a rule against a sample context."""

    @classmethod
    def dry_run(cls, rule: Rule, sample_context: dict) -> dict:
        """Evaluate a single rule against a sample context without side effects.

        Returns::

            {
                "matched": bool,
                "matched_conditions": [
                    {
                        "condition_id": int,
                        "field": str,
                        "operator": str,
                        "value": Any,
                        "matched": bool,
                    },
                    ...
                ],
                "action": str | None,   # first action's code, or None
                "execution_time_ms": int,
            }
        """
        start = time.perf_counter()

        conditions = list(rule.conditions.order_by("sort_order", "id"))
        matched_conditions: list[dict] = []
        result = True
        for index, condition in enumerate(conditions):
            cond_matched = RuleEngineService._evaluate_condition(condition, sample_context)
            matched_conditions.append(
                {
                    "condition_id": condition.pk,
                    "field": condition.field,
                    "operator": condition.operator,
                    "value": condition.value,
                    "matched": cond_matched,
                }
            )
            if index == 0:
                result = cond_matched
            else:
                connector = condition.logical_connector
                if connector == RuleCondition.LogicalConnector.OR:
                    result = result or cond_matched
                else:
                    result = result and cond_matched

        # A rule with no conditions matches unconditionally.
        matched = result if conditions else True

        action_code: str | None = None
        if matched:
            actions = list(rule.actions.order_by("execution_order", "id"))
            if actions:
                action_code = actions[0].action

        elapsed = int((time.perf_counter() - start) * 1000)
        return {
            "matched": matched,
            "matched_conditions": matched_conditions,
            "action": action_code,
            "execution_time_ms": elapsed,
        }
