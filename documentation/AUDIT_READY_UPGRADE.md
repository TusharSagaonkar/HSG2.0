# Audit-Ready Upgrade: Completion Record

Last updated: `2026-04-08`

## Objective

Upgrade the housing accounting system to be audit-ready by:

1. Expanding default chart of accounts
2. Supporting GST-compliant accounting
3. Improving voucher posting logic (double-entry with tax split)
4. Enabling accurate financial and GST reports

---

## Status Summary

- Part 1 (Default accounts expansion): `Completed`
- Part 2 (Account model classification fields): `Completed`
- Part 3 (Voucher structure enhancements): `Completed`
- Part 4 (GST-compliant voucher logic): `Completed`
- Part 5 (Report dependencies): `Completed`
- Part 6 (Validation rules): `Completed`
- Part 7 (Migration/backward compatibility): `Completed`

---

## Part 1: New Default Accounts

Implemented in:
- [standard_accounts.py](/home/tushar/Documents/Projects/housing_accounting/accounting/services/standard_accounts.py)

Added:
- GST: Output CGST/SGST/IGST, Input CGST/SGST/IGST
- Bank/Clearing: Bank Clearing Account, Cheque in Hand, Cheque Issued but Not Cleared
- Member control: Member Advance, Member Refund Payable
- Vendor/Expense control: Vendor Advance, Expense Payable
- Fund tracking: Sinking Fund Investment, Repair Fund Investment
- Depreciation: Depreciation Expense, Accumulated Depreciation
- Statutory: TDS Receivable, GST Receivable
- Adjustment/Control: Suspense Account, Rounding Off Account
- Transfers: Fund Transfer Account
- Prepaid: Prepaid Expenses

Also added metadata derivation for each default account (GST/bank/member/vendor flags, subtype).

---

## Part 2: Account Model Classification

Implemented in:
- [model_Account.py](/home/tushar/Documents/Projects/housing_accounting/accounting/models/model_Account.py)

Added fields:
- `account_type`
- `sub_type`
- `is_gst`
- `gst_type`
- `is_bank`
- `is_member_related`
- `is_vendor_related`

Validation added:
- Account type must match category type
- GST-type consistency checks (`is_gst` vs `gst_type`)

---

## Part 3: Voucher Structure

Already existed:
- Voucher header has `id`, `voucher_date`, `voucher_type`, `narration`, `society`

Enhanced:
- Voucher types now include `JOURNAL`, `BILL` (kept existing for backward compatibility)
- Voucher lines extended with:
  - `reference_type`
  - `reference_id`

Implemented in:
- [model_Voucher.py](/home/tushar/Documents/Projects/housing_accounting/accounting/models/model_Voucher.py)
- [model_LedgerEntry.py](/home/tushar/Documents/Projects/housing_accounting/accounting/models/model_LedgerEntry.py)

---

## Part 4: GST-Compliant Voucher Logic

Implemented reusable posting services:
- [gst_vouchers.py](/home/tushar/Documents/Projects/housing_accounting/accounting/services/gst_vouchers.py)

Implemented cases:
1. Maintenance billing with GST split
2. Expense booking with input GST split
3. Member receipt
4. Vendor payment

Added validation hardening:
- Direct posting to legacy `GST Payable` blocked
- Input GST must be debit-side only
- Output GST must be credit-side only

---

## Part 5: Report Dependencies

Already completed in prior reporting rollout:
- Balance Sheet / P&L / Cash Flow / BRS / Reconciliation / GST / TDS / Analytics / Control-Risk

Hardening done:
- GST report relies on GST-tagged account mapping (input/output)
- Balance sheet includes GST receivable/output liability naturally via ledger
- Member ledger references supported through voucher-line reference fields (`reference_type`, `reference_id`)

---

## Part 6: Validation Rules

Implemented:
- Voucher must balance (already existing invariant)
- GST posting direction constraints enforced
- Direct `GST Payable` posting blocked (deprecated path)

Note:
- “GST must never be merged into income/expense” is enforced by strict GST-account policy and default GST split services.

---

## Part 7: Migration and Backward Compatibility

Implemented migration:
- [0013_account_classification_and_ledger_references.py](/home/tushar/Documents/Projects/housing_accounting/accounting/migrations/0013_account_classification_and_ledger_references.py)

Migration actions:
- Adds account classification fields
- Adds ledger line reference fields
- Creates newly required default accounts for existing societies
- Backfills account metadata flags
- Maps legacy `GST Payable` ledger entries to `Output CGST` (compatibility bridge)
- Marks legacy `GST Payable` inactive

---

## Tests Added/Updated

- GST voucher policy tests extended:
  - [test_voucher_type_policy.py](/home/tushar/Documents/Projects/housing_accounting/accounting/tests/test_voucher_type_policy.py)
- New GST voucher service tests:
  - [test_gst_voucher_services.py](/home/tushar/Documents/Projects/housing_accounting/accounting/tests/test_gst_voucher_services.py)

---

## What Was Already Done Before This Upgrade

- Multi-phase reporting app and report templates
- Trial balance/P&L/balance sheet/cash-flow/BRS/reconciliation report stack
- Voucher modal detail integration in report pages
- Seed data and phase rollout documentation baseline
