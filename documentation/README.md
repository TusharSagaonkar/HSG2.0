# Project Documentation Hub

This folder is the long-term documentation source for project strategy, delivery phases, logic, and operational learnings.

Use this as the first stop before planning or implementing major changes.

## Documentation Set

| Document | Purpose | Update Trigger |
| --- | --- | --- |
| `PROJECT_PHASES.md` | Delivery roadmap, phase scope, exit criteria, and current status. | Any phase progress, phase re-scope, or new milestone. |
| `LOGIC_AND_ARCHITECTURE.md` | Core business logic, module boundaries, and critical invariants. | New module, rule change, or cross-module integration update. |
| `CHALLENGES_AND_DECISIONS.md` | Problem register with root cause, impact, and mitigations. | Any blocker, production issue, or recurring defect pattern. |
| `STRATEGY_CHANGELOG.md` | Why strategy changed, expected impact, and rollback approach. | Product or technical strategy adjustments. |
| `DATABASE_SWITCHING.md` | Safe temporary DB switch and rollback runbook for local operations. | Any database environment switch or connection-strategy update. |
| `RENDER_DEPLOY.md` | Render deployment workflow, required env vars, and first-deploy verification steps. | Any Render topology or deployment-process update. |
| `templates/` | Standard templates for consistent documentation updates. | Use whenever adding a new entry. |

## Documentation Standards

1. Accuracy before completeness: Document confirmed facts, then add assumptions explicitly.
2. Date every decision: Always include absolute date in `YYYY-MM-DD` format.
3. Traceability: Link decisions to code, tickets, or PRs whenever available.
4. Actionability: Each challenge entry must include owner and next step.
5. Clarity: Prefer short sections, clear tables, and direct language.

## Update Workflow

1. Implement change in code.
2. Update one or more documents in this folder within the same change set.
3. Add one line to `STRATEGY_CHANGELOG.md` if the change affects roadmap or design direction.
4. Add one line to `CHALLENGES_AND_DECISIONS.md` if a non-trivial issue was discovered.
5. Validate that `PROJECT_PHASES.md` status still reflects reality.

## Current Maintainer Note

Created on `2026-02-24`.

As the project scales, this folder should remain compact and high-signal. Avoid duplicating API-level details that already exist in code-level docstrings.
