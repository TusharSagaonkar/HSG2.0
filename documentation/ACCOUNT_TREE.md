# Account Tree Structure

## Overview

The housing accounting system uses a hierarchical account structure with proper parent-child relationships. Each account has a unique code following the pattern `X`, `X.X`, or `X.X.X` (e.g., `1`, `1.1`, `1.1.1`).

## Account Code Pattern

- **Root accounts**: `1`, `2`, `3`, `4`, `5`
- **Level 1**: `1.1`, `1.2`, etc.
- **Level 2**: `1.1.1`, `1.1.2`, etc.
- **Level 3**: `1.4.2.1`, etc.

## Complete Account Tree

```
ROOT
│
├── 1. ASSETS (ASSET)
│   │
│   ├── 1.1 Fixed Assets (ASSET)
│   │   ├── 1.1.1 Building Structure
│   │   ├── 1.1.2 Lift & Elevator
│   │   ├── 1.1.3 Generator / DG Set
│   │   ├── 1.1.4 Water Pump & Motor
│   │   ├── 1.1.5 CCTV System
│   │   ├── 1.1.6 Furniture & Fixtures
│   │   ├── 1.1.7 Office Equipment
│   │   └── 1.1.8 Electrical Installations
│   │
│   ├── 1.2 Deposits (ASSET)
│   │   ├── 1.2.1 Electricity Deposit
│   │   ├── 1.2.2 Water Deposit
│   │   ├── 1.2.3 Security Deposit Given
│   │   └── 1.2.4 Other Utility Deposits
│   │
│   ├── 1.3 Investments (ASSET)
│   │   ├── 1.3.1 Fixed Deposits (FD)
│   │   ├── 1.3.2 Sinking Fund Investment
│   │   ├── 1.3.3 Repair Fund Investment
│   │   └── 1.3.4 Reserve Fund Investment
│   │
│   ├── 1.4 Bank & Cash (ASSET)
│   │   ├── 1.4.1 Cash-in-Hand
│   │   ├── 1.4.2 Bank Accounts (ASSET)
│   │   │   ├── 1.4.2.1 Bank – Maintenance Account
│   │   │   ├── 1.4.2.2 Bank – Sinking Fund
│   │   │   ├── 1.4.2.3 Bank – Repair Fund
│   │   │   └── 1.4.2.4 Bank – Parking Fund
│   │   └── 1.4.3 Fund Transfer Account [BANK][CONTRA]
│   │
│   ├── 1.5 Receivables (ASSET)
│   │   ├── 1.5.1 Member Receivable (ASSET)
│   │   │   ├── 1.5.1.1 Maintenance Due
│   │   │   ├── 1.5.1.2 Parking Due
│   │   │   ├── 1.5.1.3 Interest on Arrears
│   │   │   └── 1.5.1.4 Other Member Dues
│   │   ├── 1.5.2 Vendor Receivable
│   │   └── 1.5.3 Interest Receivable – Bank
│   │
│   ├── 1.6 Advances (ASSET)
│   │   ├── 1.6.1 Vendor Advance
│   │   ├── 1.6.2 Staff Advance
│   │   └── 1.6.3 Prepaid Expenses
│   │
│   ├── 1.7 GST Input (ASSET)
│   │   ├── 1.7.1 Input CGST
│   │   ├── 1.7.2 Input SGST
│   │   └── 1.7.3 Input IGST
│   │
│   └── 1.8 Other Assets (ASSET)
│       ├── 1.8.1 Accrued Income
│       └── 1.8.2 Suspense (Debit)
│
├── 2. LIABILITIES (LIABILITY)
│   │
│   ├── 2.1 Member Liabilities (LIABILITY)
│   │   ├── 2.1.1 Advance Maintenance
│   │   ├── 2.1.2 Member Advance
│   │   ├── 2.1.3 Member Refund Payable
│   │   └── 2.1.4 Security Deposit – Members
│   │
│   ├── 2.2 Vendor & Expense Payables (LIABILITY)
│   │   ├── 2.2.1 Vendor Payable
│   │   ├── 2.2.2 Expense Payable
│   │   └── 2.2.3 Audit Fees Payable
│   │
│   ├── 2.3 Statutory Liabilities (LIABILITY)
│   │   ├── 2.3.1 GST Payable (LIABILITY)
│   │   │   ├── 2.3.1.1 Output CGST
│   │   │   ├── 2.3.1.2 Output SGST
│   │   │   └── 2.3.1.3 Output IGST
│   │   ├── 2.3.2 TDS Payable
│   │   ├── 2.3.3 Professional Tax Payable
│   │   └── 2.3.4 GST TDS / Reverse Charge
│   │
│   ├── 2.4 Bank & Clearing (LIABILITY)
│   │   ├── 2.4.1 Cheque Issued but Not Cleared
│   │   ├── 2.4.2 Cheque Deposited but Not Cleared
│   │   └── 2.4.3 Payment Gateway Clearing
│   │
│   ├── 2.5 Provisions (LIABILITY)
│   │   ├── 2.5.1 Provision for Expenses
│   │   └── 2.5.2 Provision for Audit
│   │
│   └── 2.6 Other Liabilities (LIABILITY)
│       └── 2.6.1 Suspense Account
│
├── 3. INCOME (INCOME)
│   │
│   ├── 3.1 Member Income (INCOME)
│   │   ├── 3.1.1 Maintenance Charges
│   │   ├── 3.1.2 Service Charges
│   │   ├── 3.1.3 Parking Charges
│   │   ├── 3.1.4 Transfer Fees
│   │   ├── 3.1.5 Non-Occupancy Charges
│   │   ├── 3.1.6 Late Payment Penalty
│   │   └── 3.1.7 Interest Income – Member
│   │
│   ├── 3.2 Financial Income (INCOME)
│   │   ├── 3.2.1 Interest Income – Bank
│   │   └── 3.2.2 Interest on FD
│   │
│   ├── 3.3 Commercial Income (INCOME)
│   │   ├── 3.3.1 Rental Income – Common Area
│   │   ├── 3.3.2 Advertisement Income
│   │   └── 3.3.3 Mobile Tower Income
│   │
│   └── 3.4 Other Income (INCOME)
│       ├── 3.4.1 Other Income
│       ├── 3.4.2 Scrap Sale Income
│       └── 3.4.3 Donation Received
│
├── 4. EXPENSES (EXPENSE)
│   │
│   ├── 4.1 Administrative (EXPENSE)
│   │   ├── 4.1.1 Audit Fees
│   │   ├── 4.1.2 Printing & Stationery
│   │   ├── 4.1.3 Software Expense
│   │   ├── 4.1.4 Legal Fees
│   │   └── 4.1.5 Office Expenses
│   │
│   ├── 4.2 Utilities (EXPENSE)
│   │   ├── 4.2.1 Electricity Expense
│   │   ├── 4.2.2 Water Expense
│   │   ├── 4.2.3 Internet Charges
│   │   └── 4.2.4 Gas Charges
│   │
│   ├── 4.3 Maintenance (EXPENSE)
│   │   ├── 4.3.1 Civil Repairs
│   │   ├── 4.3.2 Plumbing Repairs
│   │   ├── 4.3.3 Lift Maintenance
│   │   ├── 4.3.4 Generator Maintenance
│   │   ├── 4.3.5 Electrical Repairs
│   │   └── 4.3.6 Garden Maintenance
│   │
│   ├── 4.4 Staff (EXPENSE)
│   │   ├── 4.4.1 Salary Expense
│   │   ├── 4.4.2 Bonus
│   │   ├── 4.4.3 Staff Welfare
│   │   └── 4.4.4 Uniform Expense
│   │
│   ├── 4.5 Security & Cleaning (EXPENSE)
│   │   ├── 4.5.1 Security Charges
│   │   ├── 4.5.2 Housekeeping Charges
│   │   └── 4.5.3 Pest Control
│   │
│   ├── 4.6 Financial (EXPENSE)
│   │   ├── 4.6.1 Bank Charges
│   │   ├── 4.6.2 Depreciation Expense
│   │   └── 4.6.3 Interest Expense
│   │
│   ├── 4.7 Compliance & Tax (EXPENSE)
│   │   ├── 4.7.1 GST Expense (non-creditable)
│   │   └── 4.7.2 Penalty & Late Fees
│   │
│   └── 4.8 Other (EXPENSE)
│       ├── 4.8.1 Miscellaneous Expense
│       └── 4.8.2 Rounding Off Account
│
└── 5. EQUITY / FUNDS (EQUITY)
    │
    ├── 5.1 Member Funds (EQUITY)
    │   ├── 5.1.1 Sinking Fund
    │   ├── 5.1.2 Repair & Maintenance Fund
    │   └── 5.1.3 Parking Fund
    │
    ├── 5.2 Capital & Reserves (EQUITY)
    │   ├── 5.2.1 Share Capital
    │   ├── 5.2.2 General Reserve
    │   └── 5.2.3 Opening Balance Fund
    │
    └── 5.3 Retained Earnings (EQUITY)
        └── 5.3.1 Surplus / Deficit
```

## Account Types

| Code | Type | Description |
|------|------|-------------|
| 1 | ASSET | Resources owned by the society |
| 2 | LIABILITY | Obligations of the society |
| 3 | INCOME | Revenue earned by the society |
| 4 | EXPENSE | Costs incurred by the society |
| 5 | EQUITY | Owner's equity and reserves |

## Account Sub-Types

| Sub-Type | Description | Used For |
|----------|-------------|----------|
| GST | GST accounts | Input/Output GST accounts |
| BANK | Bank accounts | All bank and cash accounts |
| MEMBER | Member-related | Member receivables, advances |
| FUND | Fund accounts | Sinking fund, reserve fund, etc. |
| EXPENSE | Expense accounts | All expense accounts |
| INCOME | Income accounts | All income accounts |
| GENERAL | General accounts | Default for other accounts |

## Key Accounts

### Critical for Billing Engine
- `1.5.1.1` - Maintenance Due
- `1.5.1.2` - Parking Due
- `1.5.1.3` - Interest on Arrears
- `1.5.1.4` - Other Member Dues

### GST Accounts
- `1.7.1` - Input CGST (Asset)
- `1.7.2` - Input SGST (Asset)
- `1.7.3` - Input IGST (Asset)
- `2.3.1.1` - Output CGST (Liability)
- `2.3.1.2` - Output SGST (Liability)
- `2.3.1.3` - Output IGST (Liability)

### Bank Accounts
- `1.4.2.1` - Bank – Maintenance Account
- `1.4.2.2` - Bank – Sinking Fund
- `1.4.2.3` - Bank – Repair Fund
- `1.4.2.4` - Bank – Parking Fund
- `1.4.3` - Fund Transfer Account (Contra)

## Usage in Code

### Looking Up Accounts by Code (Preferred)

```python
from accounting.models import Account

# Get account by code (most reliable)
account = Account.objects.get(society=society, code="1.5.1.1")

# Get account by name (fallback, less reliable)
account = Account.objects.get(society=society, name="Maintenance Due")
```

### Using AccountCodes Constants

```python
from accounting.services.gst_vouchers import AccountCodes

# Use constants for reliable lookups
account = Account.objects.get(society=society, code=AccountCodes.MAINTENANCE_DUE)
```

## Management Commands

### Rebuild Account Tree for Existing Societies

```bash
# Rebuild for all societies
python manage.py rebuild_account_tree --all

# Rebuild for specific society by ID
python manage.py rebuild_account_tree --society-id 1

# Rebuild for specific society by name
python manage.py rebuild_account_tree --society-name "Deepsagar"

# Dry run (show what would be done)
python manage.py rebuild_account_tree --all --dry-run

# Force rebuild even if transactions exist (DANGEROUS)
python manage.py rebuild_account_tree --all --force
```

### Print Account Tree

```bash
# Print tree for all societies
python manage.py print_account_tree

# Print tree with codes displayed
python manage.py print_account_tree --all

# Print as CSV
python manage.py print_account_tree --format csv
```

## Validation Rules

1. **Code Format**: Must match pattern `^\d+(\.\d+)*$`
2. **Parent-Child Relationship**: Child code must start with parent code + `.`
3. **Sibling Uniqueness**: Same parent cannot have duplicate codes
4. **Account Type Consistency**: Account type must match category type

## Data Structure

The account tree is defined in `accounting/services/standard_accounts.py` in the `NEW_ACCOUNT_TREE` list.

Each entry has the format:
```python
(code, name, account_type, parent_code, sub_type, is_gst, gst_type, is_bank, is_contra, is_clearing, is_member_related, is_vendor_related)
```

## Migration from Old Structure

The old structure used flat account names without proper hierarchy. The new structure:

1. Adds hierarchical codes to all accounts
2. Creates proper parent-child relationships
3. Separates GST Input (Assets) from GST Output (Liabilities)
4. Places fund accounts under Equity (5.x)
5. Adds missing accounts for audit compliance

To migrate existing societies, run:
```bash
python manage.py rebuild_account_tree --all
```

**WARNING**: This will delete all existing accounts and recreate them. Ensure no transactions exist, or use `--force` with caution.
