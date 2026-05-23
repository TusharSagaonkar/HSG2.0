# Bank Reconciliation Engine — Enterprise Design Document

**For:** Housing Accounting / ERP / Cooperative Banking Systems  
**Last Updated:** `2026-05-23`  
**Status:** Design Phase — Implementation Pending  
**App:** [`reconciliation/`](../reconciliation/)

---

## 1. Objective

Build a future-proof, auditable, scalable bank reconciliation engine that supports:

- manual reconciliation
- automated reconciliation
- partial reconciliation
- split transactions
- duplicate detection
- exception handling
- AI-assisted matching
- multi-bank support
- payment gateway reconciliation
- UPI reconciliation
- continuous reconciliation

The system must support housing societies initially but should be architected such that the same engine can later support:

- SME accounting
- ERP systems
- cooperative banks
- NBFC reconciliation
- payment systems
- fintech products

---

## 2. Core Philosophy

### 2.1 Reconciliation Is NOT Accounting

Accounting system and bank system are separate realities.

Reconciliation is a **mapping layer** between them.

DO NOT tightly couple reconciliation with accounting posting.

### 2.2 Never Modify Original Accounting Entries

Original vouchers and ledger entries must remain immutable.

Reconciliation should:
- reference accounting entries
- never rewrite them
- never alter historical accounting data

### 2.3 Never Modify Imported Bank Data

Imported bank statements are raw financial evidence.

- Store them permanently.
- Never overwrite narration, amount, dates, or reference numbers.
- Normalization should happen separately.

### 2.4 Everything Must Be Auditable

Every action must have:

| Audit Field | Description |
|---|---|
| `created_by` | Who initiated the action |
| `modified_by` | Who last changed the record |
| `created_at` / `modified_at` | Timestamps |
| Reconciliation history | Full match/unmatch/override lineage |

---

## 3. Functional Requirements

| Feature | Required |
|---|---|
| Manual reconciliation | YES |
| Statement upload | YES |
| CSV import | YES |
| XLSX import | YES |
| PDF import (future) | YES |
| Auto matching | YES |
| Partial matching | YES |
| Split matching | YES |
| Many-to-many matching | YES |
| Duplicate detection | YES |
| Exception management | YES |
| Unmatch/re-match | YES |
| Adjustment entries | YES |
| Multi-bank support | YES |
| Reconciliation reports | YES |
| Audit trail | YES |
| AI-assisted matching | FUTURE |
| Realtime bank feeds | FUTURE |

---

## 4. High-Level Architecture

```
Accounting System
        +
Bank Statement System
        ↓
Normalization Layer
        ↓
Matching Engine
        ↓
Review Workspace
        ↓
Reconciliation Layer
        ↓
Exception Management
        ↓
Reporting Engine
```

---

## 5. Core Data Model

### 5.1 Voucher (Existing — Minimal Additions)

The existing [`Voucher`](../accounting/models/model_Voucher.py) model already provides:

| Field | Purpose |
|---|---|
| `voucher_type` | RECEIPT, PAYMENT, GENERAL, etc. |
| `voucher_date` | Transaction date |
| `narration` | Description |
| `payment_mode` | CASH, BANK_TRANSFER, CHEQUE, UPI, CARD, OTHER |
| `reference_number` | UTR, cheque number, transaction reference |

DO NOT redesign the entire voucher architecture. Only minimal additions required.

### 5.2 LedgerEntry (Existing)

Represents debit/credit ledger rows. Only bank-related entries participate in reconciliation.

Existing model at [`accounting/models/model_LedgerEntry.py`](../accounting/models/model_LedgerEntry.py):

| Field | Purpose |
|---|---|
| `voucher` | FK to Voucher |
| `account` | FK to Account (cash/bank accounts) |
| `debit` | Debit amount |
| `credit` | Credit amount |

### 5.3 BankStatementImport

Represents an uploaded statement file.

| Field | Type | Purpose |
|---|---|---|
| `bank_account` | FK → Account | Which bank account |
| `file_name` | CharField | Original filename |
| `file_hash` | CharField | SHA-256 for duplicate detection |
| `uploaded_by` | FK → User | Who uploaded |
| `uploaded_at` | DateTime | Upload timestamp |
| `statement_start_date` | DateField | Statement period start |
| `statement_end_date` | DateField | Statement period end |
| `import_status` | CharField | PENDING / PROCESSING / COMPLETED / FAILED |

### 5.4 BankTransaction

Represents raw bank statement rows. **This table is immutable.**

| Field | Type | Purpose |
|---|---|---|
| `bank_statement_import` | FK → BankStatementImport | Parent import |
| `transaction_date` | DateField | Bank date |
| `value_date` | DateField | Value date (if available) |
| `narration` | TextField | Raw bank narration |
| `reference_no` | CharField | Bank reference |
| `cheque_no` | CharField | Cheque number |
| `amount` | DecimalField | Transaction amount |
| `dr_cr` | CharField | DEBIT or CREDIT |
| `balance` | DecimalField | Running balance |
| `raw_row_data` | JSONField | Complete original row |

### 5.5 BankTransactionNormalized

Optional normalized version used for matching.

| Field | Type | Purpose |
|---|---|---|
| `bank_transaction` | FK → BankTransaction | Source |
| `cleaned_narration` | TextField | Normalized description |
| `extracted_utr` | CharField | Parsed UTR |
| `extracted_flat_no` | CharField | Parsed flat number |
| `extracted_reference` | CharField | Extracted reference |

### 5.6 ReconciliationLink

**MOST IMPORTANT TABLE.** Creates the reconciliation mapping.

| Field | Type | Purpose |
|---|---|---|
| `voucher_entry` | FK → LedgerEntry | Accounting side |
| `bank_transaction` | FK → BankTransaction | Bank side |
| `matched_amount` | DecimalField | How much matched |
| `match_type` | CharField | EXACT / PARTIAL / SPLIT / FORCE |
| `confidence_score` | IntegerField | 0–100 |
| `matched_by` | FK → User | Who matched |
| `matched_at` | DateTime | When matched |
| `is_manual` | BooleanField | Manual vs auto |
| `remarks` | TextField | Notes |

---

## 6. Relationship Design

**Correct architecture:**

```
LedgerEntry
      ↕
ReconciliationLink
      ↕
BankTransaction
```

DO NOT directly link voucher to bank transaction.

**Wrong approach:**
```
voucher.bank_transaction = bank_row
```

This fails in:
- split transactions
- partial reconciliation
- many-to-many mapping

---

## 7. Reconciliation Status Design

DO NOT use `reconciled = yes/no`.

Use lifecycle states:

| Status | Meaning |
|---|---|
| `PENDING` | Not yet reviewed |
| `SUGGESTED` | Auto-match candidate |
| `MATCHED` | Confirmed match |
| `PARTIAL` | Partial amount matched |
| `DUPLICATE` | Flagged as duplicate |
| `EXCEPTION` | Needs investigation |
| `REVERSED` | Previously matched, now unmatched |
| `FORCE_MATCHED` | Manual override match |
| `IGNORED` | Deliberately excluded |

---

## 8. Reconciliation Lifecycle

### Phase 1 — Voucher Creation
Accounting voucher created.

Example:
```
Receipt Voucher
Date: 1 May
Amount: ₹5,000
Flat: A302
Status: PENDING
```

### Phase 2 — Bank Statement Import
User uploads CSV, XLSX, or (future) PDF.

- Raw file stored permanently.
- Hash generated to prevent duplicate imports.

### Phase 3 — Normalization
System extracts:
- UTR
- cheque number
- flat number
- transaction reference
- cleaned narration

### Phase 4 — Matching Engine
Matching rules execute (see §9).

### Phase 5 — Human Review
User validates:
- suggested matches
- duplicates
- exceptions
- partial matches

### Phase 6 — Reconciliation Link Creation
System creates mapping records.

**Accounting entries remain unchanged.**

### Phase 7 — Reconciliation Reporting
System generates:
- bank reconciliation statement
- unmatched report
- pending clearance report
- duplicate report

---

## 9. Matching Engine Design

### 9.1 Exact Match Rules (Highest Priority)

| Rule | Example |
|---|---|
| UTR match | `reference_number` == extracted UTR |
| Cheque number match | `reference_number` == `cheque_no` |
| Exact reference match | Full reference string equality |

### 9.2 Rule-Based Match

| Rule | Example |
|---|---|
| Amount + near date | Same amount within ±3 days |
| Narration similarity | Fuzzy match on cleaned narration |
| Flat number extraction | Extract unit identifier from narration |
| Account mapping | Bank account → known cash/bank accounts |

### 9.3 AI-Assisted Matching (Future)

AI suggests probable matches for:
- messy narration
- OCR statements
- typo handling
- merged transactions

AI must **NEVER** auto-confirm financial reconciliation without approval.

---

## 10. Matching Confidence System

Each suggestion should have a confidence score:

| Rule | Confidence |
|---|---|
| Exact UTR | 99 |
| Exact Cheque | 98 |
| Amount + Date | 85 |
| Narration Similarity | 70 |

---

## 11. Duplicate Detection

### 11.1 Statement Duplicate
Same file uploaded twice. Use **file hash**.

### 11.2 Bank Transaction Duplicate
Duplicate transaction in imported statement. Use `date + amount + narration + reference` hashing strategy.

### 11.3 Voucher Duplicate
Duplicate accounting entries. System should flag suspicious duplicates.

---

## 12. Partial Reconciliation

Example:

| Side | Amount |
|---|---|
| Book | ₹5,000 |
| Bank | ₹4,998 |
| Difference | ₹2 (gateway charge) |

System should support:
- partial matching
- adjustment suggestions

---

## 13. Split Transaction Support

Example: One bank deposit of ₹15,000 represents:

| Flat | Amount |
|---|---|
| A101 | ₹5,000 |
| A102 | ₹5,000 |
| A103 | ₹5,000 |

System must support:
- one bank entry → many vouchers
- many vouchers → one bank entry

---

## 14. Exception Management

### Book Side Exceptions
Present in accounting, absent in bank:
- uncleared cheque
- pending NEFT
- accounting error

### Bank Side Exceptions
Present in bank, absent in books:
- bank charges
- interest credit
- direct deposit
- fraud/suspicious debit

---

## 15. Adjustment Entry Workflow

System should support quick creation of:
- bank charges
- interest entries
- rounding adjustments

Example workflow:
```
Bank charge ₹17 detected.
→ Create expense voucher?
```

---

## 16. Reconciliation Reports

### 16.1 Bank Reconciliation Statement

| Description | Amount |
|---|---|
| Book Balance | 1,00,000 |
| Add Pending Deposits | 5,000 |
| Less Uncleared Cheques | 2,000 |
| Less Bank Charges | 500 |
| Final Bank Balance | 1,02,500 |

### 16.2 Unmatched Book Entries
Entries pending in books.

### 16.3 Unmatched Bank Entries
Bank transactions missing in accounting.

### 16.4 Duplicate Report
Possible duplicate transactions.

### 16.5 Exception Report
Suspicious or failed reconciliation cases.

---

## 17. User Interface Design

DO NOT build traditional accounting-style reconciliation. Build an **operational workspace**.

### 17.1 Dashboard
Show:
- unmatched entries
- suggested matches
- duplicates
- exceptions
- reconciliation progress

### 17.2 Reconciliation Workspace

Keyboard-driven workflow preferred.

| Shortcut | Action |
|---|---|
| `M` | Match |
| `U` | Unmatch |
| `D` | Duplicate |
| `A` | Adjustment |
| `S` | Split |

### 17.3 Suggested Layout

| Panel | Content |
|---|---|
| **Left** | Filters, Status, Date range |
| **Center** | Transactions grid |
| **Right** | Suggested matches, AI recommendations |

---

## 18. Best Practices

### DO
- Store raw bank data permanently
- Use append-only reconciliation history
- Maintain audit logs
- Separate accounting from reconciliation
- Support manual override
- Support re-match/unmatch
- Keep reconciliation reversible
- Use many-to-many mapping
- Maintain immutable accounting entries

### DO NOT
- Modify original vouchers
- Overwrite imported bank data
- Use single yes/no reconciliation flag
- Force one-to-one transaction matching
- Auto-delete imported statements
- Couple reconciliation tightly with ledger posting
- Auto-confirm AI matches without review

---

## 19. Future Enhancements

| Enhancement | Description |
|---|---|
| **Realtime Bank API Integration** | Automatic statement fetching |
| **AI Learning Engine** | Learns payer behavior, narration patterns, recurring payments |
| **Continuous Reconciliation** | Realtime reconciliation instead of month-end process |
| **Payment Gateway Reconciliation** | Razorpay, Paytm, PhonePe, UPI PSPs |
| **Multi-Entity Reconciliation** | Multiple societies, multiple banks, multiple branches |

---

## 20. Scalability Considerations

System should support:
- millions of transactions
- async matching engine
- batch processing
- background reconciliation jobs

**Recommended:**
- Celery for async tasks
- PostgreSQL for data integrity
- Indexed reference fields
- Partitioned bank transaction tables

---

## 21. Security Considerations

- Audit every reconciliation action
- Prevent silent deletion
- Role-based permissions
- Maker-checker workflow for overrides
- Encrypted bank file storage
- Checksum validation

---

## 22. Final Design Principle

```
Import Statement
        ↓
System Auto Matches 80–90%
        ↓
Operator Reviews Remaining Transactions
        ↓
Balances Reconcile
        ↓
Full Audit Trail Preserved
```

This is enterprise-grade reconciliation architecture.

---

## 23. Integration with Existing Codebase

### Current State
The [`reconciliation/`](../reconciliation/) app is a placeholder with:
- [`reconciliation/apps.py`](../reconciliation/apps.py) — AppConfig registered as `reconciliation`
- [`reconciliation/models.py`](../reconciliation/models.py) — Empty, placeholder docstring

### Key Integration Points

| Existing Component | How Reconciliation Integrates |
|---|---|
| [`Voucher`](../accounting/models/model_Voucher.py) | References vouchers via `LedgerEntry`; no modification needed |
| [`LedgerEntry`](../accounting/models/model_LedgerEntry.py) | `ReconciliationLink.voucher_entry` FK points here |
| [`Account`](../accounting/models/model_Account.py) | `is_bank` flag identifies bank accounts eligible for reconciliation |
| [`AccountingPeriod`](../accounting/models/model_AccountingPeriod.py) | Imported statements must fall within open periods |
| [`Society`](../societies/models/__init__.py) | All reconciliation is society-scoped |

### Migration Path
1. Add reconciliation models to [`reconciliation/models.py`](../reconciliation/models.py)
2. Create `reconciliation/migrations/` with initial schema
3. Add `reconciliation/services/` for matching engine, normalization, importers
4. Add `reconciliation/management/commands/` for reconciliation jobs
5. Add `reconciliation/views.py` and templates for the review workspace
6. Register in [`config/settings/base.py`](../config/settings/base.py) `INSTALLED_APPS` (already present)