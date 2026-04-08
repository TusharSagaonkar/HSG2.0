# Financial Reporting Phase Plan

Last updated: `2026-04-08`

## Status Checkpoint (2026-04-08)

Phase 1 status: `Completed`

Validated against Phase 1 exit criteria:
- Posted vouchers only in reports: satisfied (`posted_at__isnull=False` enforced in reporting queries).
- Trial balance debit=credit integrity: satisfied (trial balance integrity logic and tests in place).
- P&L and Balance Sheet from same ledger source: satisfied (both derive from trial-balance aggregation).
- AR/AP aging with controlled sample data: satisfied (seed command includes aging scenarios across buckets).
- Bank unmatched + duplicate visibility in exception view: satisfied (suspense-balance + duplicate-reference exceptions live).

Phase 2 status: `Completed`
Phase 3 status: `Completed`
Phase 4 status: `Completed`
Phase 5 status: `Completed`

Kickoff delivered:
- Cash Flow Statement moved from placeholder to live report view.
- Drill-down links added for traceability:
  - Trial Balance account rows link to account ledger.
  - Cash Flow voucher rows link to voucher detail.
- Bank Reconciliation Statement is live with reconciling-item drill-down.
- Fixed Assets Register is live with opening/addition/reduction/closing movement.
- Transaction Reconciliation is live with matched/unmatched/exception lifecycle buckets.
- GST and TDS report views are live with ledger-backed summaries.
- Management Analytics and Inventory/Costing views are live with accounting-driven metrics.
- Control/Risk and Advanced Regulatory views are live with suspense/duplicate/settlement monitoring summaries.

## Why This Plan Exists

Financial reporting is cumulative. If core statements are wrong, every downstream report is wrong.

This plan gives a practical rollout sequence so we can ship value in phases while preserving accounting integrity.

## Report Universe (Target State)

1. Core Financial Statements
- Balance Sheet
- Profit and Loss (Income Statement)
- Cash Flow Statement

2. Ledger and Book-Level Reports
- General Ledger (GL)
- Trial Balance
- Sub-ledgers: Accounts Receivable (AR), Accounts Payable (AP), Fixed Assets Register

3. Receivables and Payables
- AR outstanding and aging
- AP dues and aging
- Payment schedules

4. Bank and Reconciliation
- Bank Reconciliation Statement (BRS)
- Transaction reconciliation (IC vs Switch vs Host)
- Matched, unmatched, and exceptions

5. Tax and Compliance
- GST reports (GSTR-1, GSTR-3B, ITC)
- TDS reports and returns
- Audit and journal-change trail

6. Inventory and Costing (if needed)
- Inventory valuation
- Stock ledger
- Product costing and margins

7. Management Analytics
- Budget vs actual
- Variance analysis
- Profitability and KPI dashboards

8. Control and Risk
- Audit trail
- Suspense-account monitoring
- Fraud/anomaly and exception alerts

9. Advanced (Fintech/Banking Scale)
- Regulatory and settlement reports
- Liquidity forecasting
- Revenue leakage monitoring

## Delivery Phases

## Phase 1 (Build First)

Scope:
- General Ledger + Trial Balance
- Profit and Loss + Balance Sheet
- Bank reconciliation foundation + exception tagging
- AR/AP aging

Exit criteria:
- Posted vouchers only in reports
- Trial balance debits = credits for every selected date range
- P&L and Balance Sheet generated from same ledger source
- AR/AP aging buckets validated with controlled sample data
- Bank unmatched and duplicate scenarios visible in exception view

## Phase 2

Scope:
- Cash Flow Statement
- Drill-down from statements to voucher and ledger entries
- Reconciliation workflow hardening (matched/unmatched lifecycle)

Exit criteria:
- Cash flow sections (operating/investing/financing) reconcile to ledger movement
- Drill-down traceability available for every aggregate number

## Phase 3

Scope:
- GST and TDS reporting
- Audit and compliance extracts

Exit criteria:
- Compliance totals tie back to accounting books
- Audit trail is queryable by user/time/change type

## Phase 4

Scope:
- Management analytics (budget vs actual, variance, profitability)
- KPI dashboards

Exit criteria:
- Variance and KPI formulas versioned and documented
- Monthly management pack can be produced without manual spreadsheet logic

## Phase 5

Scope:
- Control/risk monitoring and alerts
- Enterprise-grade reconciliation and regulatory outputs

Exit criteria:
- Exception monitoring with SLA states
- Required advanced exports available for operational review

## Test Society Seed Data (Phase 1 Validation)

A dedicated command is added for repeatable test data:

```bash
python manage.py seed_test_society_reports --society "Test Society"
```

Seed design includes:
- Opening balances (cash, bank, equity)
- Maintenance billing and partial collections for AR aging
- Vendor bill and partial payment for AP aging
- Salary and utility expense postings for P&L validation
- Unmatched bank entry parked in suspense for reconciliation exceptions
- Duplicate reference-number receipts for exception-report testing

Expected use:
1. Run seed command.
2. Select `Test Society` and `FY 2025-26` in UI scope.
3. Validate GL, Trial Balance, P&L, Balance Sheet, AR/AP aging, and exception slices.

Related structural seed for housing-domain permutations:

```bash
python manage.py seed_test_society_matrix --society "Test Society"
```

This matrix dataset adds multiple buildings, nested structures, varied unit types,
multi-owner cases, primary-owner history, owner-to-tenant transitions, vacancy,
and nominee/member combinations for domain workflow testing.

## Guardrails

- Never mix draft vouchers in financial reports.
- Do not bypass posting and period-lock rules in seeded or production data.
- Every phase must pass integrity checks before next phase starts.
