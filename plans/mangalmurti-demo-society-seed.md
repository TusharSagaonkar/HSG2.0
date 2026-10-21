# Plan: Mangalmurti demo society with realistic seed data

## Goal

Create a fully populated, believable demo society named **Mangalmurti** so every core
screen (society setup, members, ownership/occupancy, shares, billing, receipts,
accounting, reports, reminders, reconciliation, parking) shows realistic, related data.

Scope confirmed with user: **Core app domain** + **create and run the command now**.

Out of scope: GateOps (~40 models), onboarding wizard/staging tables, and
low-value tenant-admin tables (`FieldVisibility`, `ApprovalRequest`,
`ImpersonationSession`, `TenantModel`).

## Deliverable

A single idempotent management command:

```
housing/management/commands/seed_mangalmurti.py
```

Modeled on the proven `housing/management/commands/seed_deepsagar.py` architecture
(society → standard accounts → FY/periods → structures/units → members →
ownership/occupancy → charge templates → bills → receipts → reconciliation →
late fees → reminders), extended to cover the remaining core domains and to use
realistic Indian/Marathi data instead of `Owner 001` / `example.com` placeholders.

Run with:

```bash
python manage.py seed_mangalmurti --society Mangalmurti
```

The command prints a per-table summary at the end.

## Table coverage (all core domain tables)

| Domain | Tables seeded |
| --- | --- |
| societies | `Society`, `SocietyConfig` (tuned), `Membership` |
| members | `Structure`, `Unit`, `Member`, `UnitOwnership`, `UnitOccupancy`, `Nominee` |
| shares | `ShareLedger`, `ShareCertificate`, `EventLog` |
| billing | `ChargeTemplate`, `Bill`, `BillLine` |
| receipts | `PaymentReceipt`, `ReceiptAllocation` |
| accounting | `AccountCategory`, `Account`, `AccountMapping`, `FinancialYear`, `AccountingPeriod`, `Voucher`, `LedgerEntry`, `VoucherTemplate`, `voucher_sequence` (auto) |
| notifications | `ReminderLog`, `EmailTemplate`, `SocietyEmailSettings` (light) |
| reconciliation | `BankStatementImport`, `BankTransaction`, `ReconciliationLink`/`ReconciliationHistory` (via matcher) |
| parking | `ParkingSlot`, `Vehicle`, `ParkingPermit` |
| auditlog | `AuditLog` |

## Realistic data design

- **Society**: `name="Mangalmurti"`, `registration_number="MH/PUN/HSG/2011/0784"`,
  address `"Mangalmurti Society, Survey No. 128, Baner Road, Pune 411045"`.
- **SocietyConfig**: `share_value=100`, `default_share_count=1`, `entrance_fee=5000`,
  `transfer_fee=2500`, `premium_amount=0`, `allow_multiple_nominees=True`,
  `require_approval=False`, `auto_generate_vouchers=True`.
- **Structures**: two wings as BUILDING → WING → FLOOR hierarchy
  (`Building A / Wing A1 / Floor 1..4`, plus `Building B`), like
  `seed_test_society_matrix` but larger.
- **Units**: flat numbers such as `A-101`, `A-102`, `B-201`; a few `SHOP`/`OFFICE`
  units; realistic `area_sqft`/`chargeable_area_sqft`.
- **Members**: realistic names (e.g. `Rahul Deshmukh`, `Sneha Kulkarni`,
  `Amit Joshi`, `Priya Patil`), Indian mobile numbers (`98xxxxxxxx`),
  emails `firstname.lastname@mangalmurti.in`. Owners + tenants + a couple of
  nominees; one ownership succession (primary → secondary) and one vacancy to
  exercise the matrix. Nominee data is seeded via the `members.Nominee` model
  attached to owner members — **not** as `Member` rows with
  `MemberRole.NOMINEE` (see gotcha G8).
- **Shares**: allot shares to owner members via `ShareTransactionService.allot_shares_to_member`
  (creates `ShareLedger` + certificate + `EventLog` + accounting voucher),
  plus one `transfer_shares` between owners and one `record_share_correction`.
- **ChargeTemplate**: Maintenance (₹2,500/mo, 10% late fee), Sinking Fund
  (₹500/mo), Parking (₹300/mo) — effective from the seeded FY.
- **Bills/Receipts**: reuse `billing.services.generate_bills_for_period` and
  `receipts.services.post_receipt_for_bill`; mix of fully paid, partial, overdue,
  and unpaid across two periods so aging/status/collection reports are non-trivial.
- **Accounting**: bootstrap via `ensure_standard_categories` +
  `create_default_accounts_for_society`; add opening-balance voucher plus a small
  set of realistic vouchers (electricity bill, housekeeping salary, lift
  maintenance, GST split, bank interest, bank charges) following the
  `seed_test_society_reports` pattern, so P&L/balance sheet/trial balance populate.
- **Notifications**: `ReminderLog` via `schedule_payment_reminders`; ensure a couple
  of `EmailTemplate` rows (reuse `bootstrap_email_templates` if present).
- **Reconciliation**: import a small CSV bank statement (like
  `seed_deepsagar._ensure_reconciliation_demo`) and run `MatchingEngine` to create
  suggestions/links.
- **Parking**: slots (`B-01`.. `B-10`, `P-01`..`P-05`), vehicles with real-looking
  plate format (`MH-12-AB-1234`), active `ParkingPermit` per sold slot.
- **AuditLog**: a handful of append-only entries via `AuditLog.log(...)` for the
  member/bill/voucher creation actions.

## Idempotency / safety

- Natural-key upserts everywhere: society by name; structure by
  `(society, parent, name)`; unit by `(structure, identifier)`; member by
  `(society, unit, full_name, role)`; templates by `(society, name, effective_from)`.
- Bills/receipts/vouchers guarded by existence checks and a marker
  (`reference_number` / narration prefix) so re-runs do not duplicate.
- `--reset` flag (off by default) to hard-delete the existing Mangalmurti society
  and all cascading data before re-seeding, for a clean rebuild.
- Wrap the whole seed in `transaction.atomic()`.

## Command interface

```
python manage.py seed_mangalmurti
    --society Mangalmurti      # default
    --owners N                 # default ~40
    --tenants N                # default ~18
    --reset                    # wipe + rebuild Mangalmurti data
```

## Key implementation notes / risks

- `Society` post_save signal already creates `SocietyConfig` + `AccountMapping`,
  so do not duplicate those.
- Share services call `AccountingPeriod.is_period_open` / `FinancialYear.get_open_year_for_date`,
  so the FY and current period must be created first.
- Vouchers must be posted via `voucher.post()` (as existing seed commands do) to
  hit `LedgerEntry` reporting.
- Voucher `performed_by`/`created_by` for shares/events must be a real user; create
  an `admin@mangalmurti.in` superuser-backed user and give `Membership` rows
  (admin/accountant roles) before share seeding.
- DATABASE_URL is currently **remote** (`.env.local: DJANGO_LOCAL_DATABASE_MODE=remote`),
  so running the command writes to that database — confirm intent before execution.
- No new migrations are required; this only writes data.

## Execution steps

1. Add `housing/management/commands/seed_mangalmurti.py`.
2. `python manage.py check`.
3. `python manage.py seed_mangalmurti --society Mangalmurti`.
4. Verify with a counts query per table (built into the command's summary) and
   spot-check the society in the UI.

## Verification

- Command summary reports non-zero counts for every listed table.
- `Bill` statuses include PAID/PARTIAL/OVERDUE; `PaymentReceipt` allocations tie to bills.
- `LedgerEntry` debits == credits for every seeded `Voucher`.
- Society appears in the navbar society selector and onboarding-independent flows.

## Verified implementation notes (codebase-checked)

These were confirmed by reading the referenced models/services during planning and
must be respected by the implementation.

- **G1 — SocietyConfig defaults.** The `Society` `post_save` signal
  (`societies/signals.py`) already creates `SocietyConfig` (with
  `require_approval=True`, `allow_multiple_nominees=False`) and `AccountMapping`.
  The plan's tuned config must be applied with an `update_or_create` / attribute
  update, not `get_or_create(defaults=...)` (defaults are ignored on existing rows).
- **G2 — Admin/accountant user.** `ShareVoucherService` / `EventLog` require a real
  `performed_by` / `created_by` user. Create `admin@mangalmurti.in`
  (`is_superuser=True`, usable-vs-unusable password) and `Membership` rows
  (ADMIN + ACCOUNTANT) before share seeding. `EventLog.save()` runs `full_clean()`,
  so `performed_by` must be non-null.
- **G3 — Voucher posting window.** `Voucher.post()` requires an open
  `FinancialYear` **and** an open `AccountingPeriod` covering `voucher_date`;
  `Voucher.clean()` rejects future dates. `FinancialYear.save()` auto-creates
  monthly periods and opens every period whose `start_date <= today`. Therefore
  seed vouchers/bills/receipts/shares with dates inside the created FY, or
  explicitly open the needed historical period (`_ensure_period_open` pattern in
  `seed_test_society_reports`). Share allotment also calls
  `AccountingPeriod.is_period_open` via `ShareVoucherService._validate_accounting_period`.
- **G4 — GST voucher rules.** `Voucher._validate_gst_policy` requires at least one
  non-GST taxable base line, forbids posting to a `GST Payable` account by name,
  requires INPUT GST debited and OUTPUT GST credited, and forbids GST on
  INCOME/EXPENSE accounts. Follow the `seed_test_society_reports` GST voucher shapes.
- **G5 — Charge templates.** `ChargeTemplate.save()` runs `full_clean()` and rejects
  overlapping effective periods for the same `(society, name)`; `delete()` is blocked
  once used by a `BillLine`. Guard creation accordingly.
- **G6 — Billing scope.** `generate_bills_for_period` bills **every** `Member` with
  `status=ACTIVE` overlapping the period, regardless of role. Owners and tenants are
  billed; do not add billed roles unintentionally (see G8). `Bill` is unique per
  `(society, member, bill_period_start, bill_period_end)`.
- **G7 — Receipts.** `post_receipt_for_bill` requires a reference number for all
  non-`CASH` modes and caps the amount at `bill.outstanding_amount`; CASH receipts
  use an empty reference.
- **G8 — Nominee vs Member-role-NOMINEE.** `Nominee` model is versioned
  (`is_active`/`deactivated_at`), enforces `unique_active_priority_per_member`, caps
  summed active percentage at 100, and range 0–100. Create nominees via
  `Nominee.objects.create(member=<owner member>, ...)` so they are not picked up by
  billing (G6).
- **G9 — Ownership/occupancy invariants (matrix).** Only one active
  `UnitOwnership` PRIMARY per unit (`clean()`), so succession closes the old row
  (`end_date = new_start - 1 day`) before/while inserting the new one. Only one
  active `UnitOccupancy` per unit, and `VACANT` rows must have `occupant=None`.
  Prefer `full_clean()` (as `seed_test_society_matrix` does) when building the matrix.
- **G10 — Members uniqueness.** `Member` unique on
  `(society, unit, full_name, role)`; multiple owners on one unit need distinct
  `full_name`s and PRIMARY/SECONDARY roles. `share_balance` is denormalized from
  `ShareLedger` by `ShareLedger.save()`.
- **G11 — Share certificates.** `ShareCertificate.certificate_no` is **globally**
  unique. `ShareTransactionService.generate_share_certificate` auto-numbers, but
  allotment does not itself create a certificate — call it explicitly (or insert)
  after allotment, using the same `reference` as the ledger `reference_id`.
- **G12 — Parking.** `ParkingSlot.clean()` requires `owned_unit` iff
  `parking_model == SOLD` (and no unit otherwise). `ParkingPermit.save()` runs
  `full_clean()`: a `SOLD` permit needs `slot.owned_unit == unit` and
  `vehicle.unit == unit`; only one ACTIVE permit per slot. `Vehicle` needs a unit in
  the same society and its `save()` triggers rule-status recalculation (safe, but
  slower — create slots/units first).
- **G13 — Email templates/settings.** Use `ensure_default_email_templates()`
  (idempotent `update_or_create`) for `EmailTemplate`. `SocietyEmailSettings`
  requires non-blank `smtp_host` + `default_from_email`; the SMTP password is
  write-only via `set_smtp_password`; `is_active` defaults `False` (leave inactive
  for a demo so no real mail is attempted).
- **G14 — Reconciliation API.** Reuse `seed_deepsagar._ensure_reconciliation_demo`
  exactly: `BankStatementImport` + `BankTransaction`, then
  `NormalizerService(society).normalize_transaction(tx)` and
  `MatchingEngine(society).run_matching(bank_transactions=..., auto_confirm=False,
  create_suggestions=True)`. `BankTransaction` is immutable (updates rejected,
  `delete()` raises) — guard with an existence check on the import `file_name`.
- **G15 — Reminders.** `schedule_payment_reminders` dedupes on
  `scheduled_for__date == as_of_date` and only creates rows for bills due within
  `upcoming_days`; run it after bills/receipts so `ReminderLog` is non-empty.
- **G16 — AuditLog.** Append-only: `save()` rejects updates and `delete()` raises
  `PermissionError`. Use `AuditLog.log(society=..., action=..., entity_type=...,
  entity_id=...)` for a handful of entries; do not attempt to update/delete them.
- **G17 — `--reset` deletion order (important).** A bare `society.delete()` will
  raise `ProtectedError` because several FKs are `PROTECT`
  (e.g. `EventLog.society`, `EventLog.member`, `ShareLedger.member`,
  `ShareCertificate.member`, `PaymentReceipt.member`, `ReceiptAllocation.bill`,
  `Bill.member/unit/receivable_account`, `LedgerEntry`/`BillLine` account refs).
  `--reset` must hard-delete children in dependency order first (audit logs →
  bank transactions/imports/reconciliation → reminders → receipts/allocations →
  bills/lines → vouchers/ledger entries → share certs/ledger/events → nominees →
  vehicles/permits → slots → members/ownership/occupancy → units → structures →
  charge templates → periods → FY → accounts/categories → account mapping →
  config → memberships → society), or wrap in a collector-safe order. Note
  `AuditLog.delete()`/`BankTransaction.delete()` raise, so delete those via
  `QuerySet.delete()` (which bypasses instance `delete()`).
- **G18 — Idempotency marker.** Echo the existing narration-prefix convention
  (e.g. a `[MANGALMURTI-SEED]` prefix or `MG-` reference markers) so re-runs reuse
  rather than duplicate vouchers/receipts, as `seed_test_society_reports` does.
- **G19 — Remote DB.** `DJANGO_LOCAL_DATABASE_MODE=remote` currently points writes
  at the remote database. Confirm intent before executing step 3; the command is
  otherwise safe/idempotent.
