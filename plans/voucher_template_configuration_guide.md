# Voucher Template Configuration Guide

## Overview
Voucher templates allow society administrators to pre‑configure common voucher types with default accounts, amounts, and narrations. When users click a template button on the voucher entry page, the form is automatically filled with the template's data.

## Accessing Template Configuration
1. Go to Django Admin (`/admin/`)
2. Navigate to **Accounting → Voucher Templates**
3. Click **Add Voucher Template** or edit an existing one

## Configuring a Payment Voucher Template

### Step 1: Basic Information
- **Society**: Select the society this template applies to
- **Voucher Type**: Choose `PAYMENT`
- **Name**: Descriptive name (e.g., "Electricity Bill Payment", "Vendor Payment")
- **Narration**: Default narration text for the voucher
- **Payment Mode**: Required for PAYMENT vouchers (CASH, CHEQUE, ONLINE, etc.)
- **Reference Number Pattern**: Optional pattern (e.g., "CHQ-{seq}" for cheque numbers)
- **Is Active**: Check to make the template available

### Step 2: Add Ledger Rows
Click **Add another Voucher Template Row** to add accounts:

#### Typical Payment Voucher Structure:
1. **Expense Account (Debit)**
   - Account: Select the expense account (e.g., Electricity Expense, Maintenance Expense)
   - Side: `DEBIT`
   - Default Amount: Optional (leave blank for user to fill)
   - Unit: Optional (if expense is allocated to a specific unit)

2. **Payment Account (Credit)**
   - Account: Select the payment account (e.g., Cash, Bank Account)
   - Side: `CREDIT`
   - Default Amount: Optional (should match debit amount if specified)
   - Unit: Usually left blank

### Example: Electricity Bill Payment
| Field | Value |
|-------|-------|
| Society | Deepsagar gruhasankul |
| Voucher Type | PAYMENT |
| Name | Electricity Bill Payment |
| Narration | Paid electricity bill for the month |
| Payment Mode | CASH |
| Reference Number Pattern | (leave blank) |

**Rows:**
1. Account: `Electricity Expense`, Side: `DEBIT`, Amount: (blank)
2. Account: `Cash`, Side: `CREDIT`, Amount: (blank)

### Example: Vendor Payment by Cheque
| Field | Value |
|-------|-------|
| Society | Test Society |
| Voucher Type | PAYMENT |
| Name | Vendor Payment |
| Narration | Payment to vendor for supplies |
| Payment Mode | CHEQUE |
| Reference Number Pattern | `CHQ-{seq}` |

**Rows:**
1. Account: `Purchases`, Side: `DEBIT`, Amount: (blank)
2. Account: `Bank Account`, Side: `CREDIT`, Amount: (blank)

## Validation Rules
1. **Payment Mode Required**: PAYMENT and RECEIPT vouchers must have a payment mode
2. **Debit/Credit Balance**: Template rows don't need to balance (user will fill amounts)
3. **Unique per Society/Voucher Type**: Each society can have only one template per voucher type
4. **Active/Inactive**: Only active templates appear on the voucher entry page

## Using Templates

### On Voucher Entry Page
1. Select a society from the dropdown
2. Available templates appear as colored buttons
3. Click a template button to:
   - Pre‑fill voucher type, payment mode, narration
   - Add ledger rows with pre‑selected accounts
   - Preset any default amounts

### Template Button Colors
- **PAYMENT**: Red outline (`btn-outline-danger`)
- **RECEIPT**: Green outline (`btn-outline-success`)
- **GENERAL (Contra)**: Blue outline (`btn-outline-info`)
- **JOURNAL**: Yellow outline (`btn-outline-warning`)

## Advanced Configuration

### Default Amounts
- Set `default_amount` on template rows to pre‑fill amounts
- Useful for fixed payments (e.g., monthly salaries, fixed fees)
- User can still modify the amount before saving

### Unit Allocation
- Assign a specific unit to a template row
- Useful for society‑level templates that allocate expenses to default units
- Leave blank for society‑level transactions

### Reference Number Patterns
- Use patterns like `CHQ-{seq}` or `ONL-{date}`
- The `{seq}` placeholder can be replaced with actual sequence numbers
- Currently displayed as-is; future enhancement could auto‑generate

## Troubleshooting

### Template Not Appearing
1. Check `is_active` is checked
2. Verify the template is for the selected society
3. Ensure the society has accounts matching those in the template

### Payment Mode Missing
- PAYMENT and RECEIPT templates require a payment mode
- Edit the template and select a payment mode

### Accounts Not Found
- Template rows reference accounts that must exist in the society
- Create the missing accounts in **Accounting → Accounts** first

## Best Practices
1. **Create Comprehensive Templates**: Cover all common transaction types
2. **Use Descriptive Names**: Help users identify the template purpose
3. **Leave Amounts Blank**: Let users fill amounts unless fixed
4. **Test Templates**: Create a test voucher to ensure pre‑filling works correctly
5. **Document Templates**: Add notes in the name or narration for clarity

## Default Templates
The system includes a seeding command that creates basic templates for each society:
- **Payment**: Electricity bill payment (Cash → Electricity Expense)
- **Receipt**: Maintenance collection (Cash ← Maintenance Receivable)
- **Contra**: Cash to bank transfer
- **Journal**: Depreciation entry

Run `python manage.py seed_voucher_templates` to create/recreate default templates.