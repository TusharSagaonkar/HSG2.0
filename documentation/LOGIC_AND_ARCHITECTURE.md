# Logic and Architecture

Last updated: `2026-02-24`

## System Logic Model

The platform follows a domain-oriented structure where each app owns its data and business rules:

- `housing`: Society, structure, unit, ownership, occupancy foundations.
- `accounting`: Accounts, vouchers, posting workflow, period control.
- `billing`: Bill lifecycle and charge application.
- `receipts`: Payment capture and allocation.
- `notifications`: Reminder and communication workflows.
- `members`: Member profile and context usage.

## Cross-Domain Flow (High Level)

1. Housing defines valid society and unit context.
2. Accounting provides valid ledgers and period controls.
3. Billing raises receivables for members/units.
4. Receipts settle receivables through allocations.
5. Notifications drive follow-up and reminders.

## Core Business Invariants

| Domain | Invariant | Why It Matters |
| --- | --- | --- |
| Accounting | Voucher posting must preserve double-entry balance. | Prevents ledger corruption. |
| Accounting | Closed periods block posting changes. | Protects historical integrity. |
| Billing | Bill totals must equal sum of bill lines. | Financial accuracy and auditability. |
| Billing | Charge templates are versioned by effective dates, and used versions are immutable. | Preserves audit trail and reproducibility of prior periods. |
| Billing | Bill lines store calculation snapshots (rate, quantity, charge type, late-fee basis). | Prevents retroactive amount drift after rule or unit data changes. |
| Receipts | Allocation total cannot exceed receipt amount. | Prevents over-allocation errors. |
| Housing | Unit/society relationships must remain valid and consistent. | Avoids orphaned financial records. |

## Billing Rule Versioning Model

1. `ChargeTemplate` defines rule semantics (`charge_type`, `rate`, frequency, accounts).
2. `effective_from` and `effective_to` define the validity window of that version.
3. Rule changes create a new version; old versions are closed instead of edited.
4. Bill generation resolves template versions by billing period date.
5. `BillLine` stores execution snapshots so posted bills remain historically stable.

## UI Interaction Logic

Global layout interactions (sidebar/topbar toggles) must behave consistently across pages using shared base template hooks and defensive client-side handlers.
Sidebar navigation should separate business capabilities into distinct tabs/sections (Society, Structure & Units, Members, Billing, Receipts, Reminders, Accounting) to keep workflows discoverable as modules expand.
Low-frequency actions should be nested in collapsible submenus under each parent domain to keep the default navigation compact.
Parent menu click should route to the primary child view; submenu expansion should be controlled only by the caret toggle.

## Architecture Constraints

1. Avoid direct cross-app model coupling that bypasses domain boundaries.
2. Keep policy checks in the owning domain service/view logic.
3. Maintain deterministic behavior for posting, billing, and allocation operations.
4. Prefer explicit state transitions over implicit side effects.

## Documentation Rule for Logic Changes

For each logic change:

1. Update the affected invariant row or add a new one.
2. Record strategy impact in `STRATEGY_CHANGELOG.md` if architecture direction changes.
3. Record challenge details in `CHALLENGES_AND_DECISIONS.md` if issue-driven.
