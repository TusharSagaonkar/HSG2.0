# Voucher Entry Requirements

## Overview
The voucher entry form at `/accounting/vouchers/entry/` allows users to create a draft voucher with ledger entries. This document outlines the required fields, validation rules, and steps to successfully submit a voucher.

## Required Fields

### Voucher Header
| Field | Required | Type | Description | Notes |
|-------|----------|------|-------------|-------|
| Society | Yes | ModelChoice | The society for which the voucher is being created. | Dropdown filtered by user's accessible societies. |
| Voucher Type | Yes | Choice | Type of voucher. | Options: General, Receipt, Payment, Adjustment, Opening, Journal, Bill. |
| Voucher Date | Yes | Date | Date of the voucher. | Cannot be a future date. Defaults to today. |
| Payment Mode | Conditional | Choice | Mode of payment. | Required for Receipt and Payment vouchers. Options: Cash, Bank Transfer, Cheque, UPI, Card, Other. |
| Reference Number | Conditional | Text | Reference number (e.g., cheque number, transaction ID). | Required for non‑cash payment modes (i.e., any mode other than “Cash”). |
| Narration | Conditional | TextArea | Description of the voucher. | Required for Receipt and Payment vouchers. |

### Ledger Entries
At least **two** ledger rows must be provided.

Each row contains:

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| Account | Conditional | ModelChoice | The ledger account to debit or credit. | Required if either debit or credit is non‑zero. Dropdown filtered by the selected society. |
| Unit | Optional | ModelChoice | The housing unit (flat, shop, etc.) linked to this entry. | Dropdown filtered by the selected society. |
| Debit | Conditional | Decimal | Positive amount to debit. | Exactly one of Debit or Credit must be filled per row. |
| Credit | Conditional | Decimal | Positive amount to credit. | Exactly one of Debit or Credit must be filled per row. |

## Validation Rules

### General Voucher Rules
1. **Date Validation** – Voucher date cannot be in the future.
2. **Minimum Rows** – At least two ledger entries must be present.
3. **Double‑Entry Balance** – Total debit across all rows must equal total credit.
4. **Same‑Account Restriction** – An account cannot appear both debited and credited in the same voucher.
5. **Active Account** – All selected accounts must be active.
6. **Society Consistency** – Accounts and units must belong to the selected society.

### Voucher‑Type Specific Rules
- **Receipt Voucher**
  - Must debit a cash or bank account.
  - Payment mode is required.
  - Narration is required.
  - Reference number is required for non‑cash payment modes.

- **Payment Voucher**
  - Must credit a cash or bank account.
  - Payment mode is required.
  - Narration is required.
  - Reference number is required for non‑cash payment modes.

- **General, Adjustment, Opening, Journal, Bill Vouchers**
  - No cash/bank requirement.
  - Payment mode, reference number, and narration are optional.

### GST‑Related Rules (if applicable)
- Direct posting to “GST Payable” is prohibited.
- GST accounts must be mapped as INPUT or OUTPUT.
- Input GST accounts can only be debited; output GST accounts only credited.
- GST lines must be accompanied by at least one non‑GST taxable base line (income, expense, receivable, payable).

### Unit‑Linked Accounts
For certain member‑related accounts (e.g., “Maintenance Receivable”, “Advance Receivable”, “Advance from Members”, “Security Deposit Payable”), a unit must be selected.

## Voucher Type Examples and Accounting Entries

The system supports several voucher types, each serving a distinct accounting purpose. Below are the four most commonly used types with typical examples and double‑entry patterns.

### 1. Payment Voucher
**Purpose:** Record money going out (expenses, vendor payments, salaries, etc.).

**Examples:**
- Paid ₹10,000 electricity bill via bank transfer.
- Paid vendor for plumbing work.
- Society paid security guard salary.

**Accounting Entry:**
```
Electricity Expense A/c    Dr  10,000
    To Bank A/c                    10,000
```

**Key Points:**
- Must credit a cash or bank account.
- Payment mode and narration are required.
- Reference number required for non‑cash payments.

### 2. Receipt Voucher
**Purpose:** Record money coming in (maintenance collections, rent, interest, etc.).

**Examples:**
- Member paid maintenance ₹3,000.
- Rent received from commercial unit.
- Interest received from bank.

**Accounting Entry:**
```
Bank A/c                  Dr  3,000
    To Maintenance A/c            3,000
```

**Key Points:**
- Must debit a cash or bank account.
- Payment mode and narration are required.
- Reference number required for non‑cash payments.

### 3. Contra Voucher
**Purpose:** Transfer between own cash and bank accounts (no external party).

**Examples:**
- Deposited cash ₹5,000 into bank.
- Withdrawn cash from bank ATM.

**Accounting Entry:**
```
Bank A/c                  Dr  5,000
    To Cash A/c                   5,000
```

**Key Points:**
- Involves only cash/bank accounts (both sides).
- No payment mode or reference number required.
- Narration optional but recommended.

### 4. Journal Voucher
**Purpose:** Adjustments that do not involve cash/bank (depreciation, provisions, corrections).

**Examples:**
- Depreciation on furniture ₹2,000.
- Provision for doubtful debts.
- Correction of earlier mis‑posting.

**Accounting Entry:**
```
Depreciation A/c         Dr  2,000
    To Furniture A/c              2,000
```

**Key Points:**
- No cash/bank account required.
- Payment mode, reference number, and narration are optional.
- This is the most flexible voucher type, used for all non‑cash adjustments.

### Other Voucher Types
- **General Voucher** – Default type for any double‑entry transaction.
- **Adjustment Voucher** – Similar to journal but often used for member‑ledger adjustments.
- **Opening Voucher** – For opening balances at the start of a financial year.
- **Bill Voucher** – For recording supplier invoices (payable later).

## UI Behavior
- The society dropdown triggers a page reload to filter account and unit options.
- Clicking “Add Row” inserts a new empty ledger row.
- Debit and credit fields are mutually exclusive; entering one clears the other (client‑side validation).
- Validation errors are displayed inline below each field and as a global warning.

## Submission Workflow
1. Select a society (or rely on the currently selected scope).
2. Choose a voucher type.
3. Fill in the voucher date (defaults to today).
4. If voucher type is Receipt or Payment, select a payment mode and provide narration.
5. If payment mode is not Cash, enter a reference number.
6. Add at least two ledger rows:
   - Pick an account for each row.
   - Optionally link a unit.
   - Enter either a debit or credit amount (positive).
7. Ensure the total debit equals total credit.
8. Click “Save Draft Voucher”.

## Post‑Submission
- The voucher is saved as a draft (posted_at = NULL).
- It appears in the Voucher Posting Menu for final review and posting.
- Once posted, the voucher receives a sequential voucher number and becomes immutable.

## Common Pitfalls
- **Single‑row vouchers** – rejected with “Voucher must contain at least two ledger entry rows.”
- **Unbalanced totals** – rejected with “Total debit and credit must be equal.”
- **Future date** – rejected with “Voucher date cannot be in the future.”
- **Inactive account** – rejected with “Selected account is inactive.”
- **Missing cash/bank account** – for Receipt/Payment vouchers, the error “Receipt voucher must debit a cash/bank account” (or credit for Payment) appears.

## Next Steps
After creating a draft voucher, navigate to the Voucher Posting Menu (`/accounting/vouchers/posting/`) to review, post, delete, or reverse vouchers.

---

*This document is based on code analysis of `accounting/forms.py`, `accounting/models/model_Voucher.py`, `accounting/views.py`, and `accounting/templates/accounting/voucher_entry.html`.*