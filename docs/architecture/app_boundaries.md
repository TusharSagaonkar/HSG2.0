# App Boundaries and Dependency Rules

This codebase is organized by business responsibility:

- `users`: identity, roles, authentication only
- `societies`: society scope and multi-tenant boundary
- `accounting`: chart, periods, vouchers, ledger, trial balance, close
- `members`: flats/units, owners/tenants, member status
- `billing`: bills, bill lines, recurring templates, penalties
- `receipts`: payment receipt capture and bill allocation
- `notifications`: reminder scheduling and delivery orchestration
- `auditlog`: immutable audit events (incremental rollout)
- `reconciliation`: bank matching workflows (incremental rollout)

## Strict Rules

1. `accounting` is the only app that can create ledger entries.
2. `billing` and `receipts` must create financial impact only through `Voucher.post()`.
3. `members` must never write accounting entries directly.
4. Cross-society data writes are forbidden.
5. Period lock checks are enforced in accounting posting flow.

## Allowed Dependency Direction

- `users` -> none
- `societies` -> none
- `accounting` -> `societies`, `members` (for scoped unit links)
- `members` -> `societies`
- `billing` -> `societies`, `members`, `accounting`
- `receipts` -> `societies`, `members`, `billing`, `accounting`
- `notifications` -> `societies`, `members`, `billing`, `receipts`
- `auditlog` -> all apps may emit events into it; it should not depend on domain logic
- `reconciliation` -> `societies`, `accounting`, `receipts`

## Transitional Note

Legacy `housing` modules remain as compatibility facades while call sites migrate to
app-scoped modules (`billing.*`, `receipts.*`, `societies.*`, `members.*`, `notifications.*`).
