# Project Phases

Last updated: `2026-02-24`

## Phase Overview

| Phase | Objective | Status | Exit Criteria | Owner |
| --- | --- | --- | --- | --- |
| Phase 0: Platform Foundation | Baseline Django project, auth, settings, environments. | Completed | Local setup, auth flows, baseline test tooling. | Core Team |
| Phase 1: Core Housing Domain | Societies, structures, units, ownership, occupancy. | Completed | CRUD + core validations + domain tests stable. | Housing Team |
| Phase 2: Accounting Core | Chart of accounts, vouchers, posting, periods, policies. | In Progress | Double-entry integrity, posting controls, period locks verified. | Accounting Team |
| Phase 3: Billing and Receipts | Bill generation, collections, allocations, reminder workflows. | In Progress | End-to-end bill-to-receipt flows with reconciliation checks. | Billing Team |
| Phase 4: Governance and Observability | Auditability, reporting confidence, operational controls. | Planned | Operational dashboards, audit trails, incident runbooks. | Platform Team |
| Phase 5: Scale and Hardening | Performance, multi-tenant maturity, reliability at scale. | Planned | Load profile targets, rollback playbooks, SLO monitoring. | Engineering Leadership |

## Current Focus (Near-Term)

1. Stabilize frontend interaction consistency across all pages.
2. Tighten cross-domain workflow reliability (billing, receipts, accounting).
3. Increase precision of architecture and strategy documentation as system complexity grows.
4. Harden billing financial integrity with immutable, versioned charge-rule execution.

## Phase Quality Gates

| Gate | Description | Evidence Required |
| --- | --- | --- |
| Functional | Target user flow works end-to-end. | Test run evidence and manual verification notes. |
| Data Integrity | Business invariants preserved under edge cases. | Domain tests and rule validation artifacts. |
| Operability | Teams can monitor, support, and recover. | Logs, runbooks, and incident notes. |
| Documentation | Logic, decisions, and strategy are up to date. | Updated files in `documentation/`. |

## Update Rule

When status changes for any phase:

1. Update the phase row.
2. Add a matching note in `STRATEGY_CHANGELOG.md`.
3. Add any blocker in `CHALLENGES_AND_DECISIONS.md`.
