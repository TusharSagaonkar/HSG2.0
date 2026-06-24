# Manual Bank Reconciliation Workspace V1

**Project:** Housing Accounting  
**App:** [`reconciliation/`](../reconciliation/)  
**Last Updated:** `2026-05-31`

## Objective

Build a keyboard-first manual bank reconciliation workspace for housing societies.

The primary use case is:

- A user has a printed bank statement, passbook, PDF printout, or handwritten statement.
- The user enters bank statement rows manually or pastes them from Excel.
- The system immediately searches accounting entries and suggests matches.
- The user confirms reconciliation with minimal mouse movement.
- Reconciliation should be significantly faster than traditional accounting software.

This feature must work without requiring a bank statement import file.

## Current Project Fit

This repository already has the core reconciliation engine in place. The V1 workspace should reuse and extend the existing app instead of introducing a parallel system.

Existing building blocks:

- `reconciliation.models.BankStatementImport`
- `reconciliation.models.BankTransaction`
- `reconciliation.models.BankTransactionNormalized`
- `reconciliation.models.ReconciliationLink`
- `reconciliation.models.ReconciliationHistory`
- `reconciliation.services.importer.StatementImportService`
- `reconciliation.services.manual_entry.ManualStatementImportService`
- `reconciliation.services.matcher.MatchingEngine`
- `reconciliation.services.adjustments.AdjustmentService`
- `reconciliation.views`
- `reconciliation.templates.reconciliation.workspace`
- `reconciliation.templates.reconciliation.manual_entry`

## Core Principles

1. Manual entry and imported statements must use the same `BankTransaction` model.
2. Never modify vouchers or ledger entries.
3. Reconciliation creates links only.
4. Keyboard-first operation.
5. Spreadsheet-style entry, not form-style entry.
6. The system should handle hundreds of statement rows efficiently.
7. Everything stays society-scoped.

## What V1 Should Do

V1 should focus on the fastest practical manual reconciliation flow for housing society operators:

- enter statement rows manually
- paste rows from Excel or WhatsApp-exported text
- save rows without full page refresh
- run matching immediately after save
- show top candidate matches in a side panel
- confirm or unmatch with keyboard shortcuts
- create adjustment entries when there is no book match

## What V1 Should Not Try To Do

To keep the first version shippable, V1 should not add:

- AI-assisted matching
- bank feed integrations
- PDF parsing as a new import pipeline
- mobile-first optimization
- React/Vue frontend
- a second transaction table for manual statements

## Existing Data Model

### `BankStatementImport`

Represents the parent record for a statement batch.

Relevant fields in this project:

- `society`
- `bank_account`
- `file_name`
- `file_hash`
- `raw_file`
- `uploaded_by`
- `uploaded_at`
- `statement_start_date`
- `statement_end_date`
- `import_status`
- `source_type`
- `error_log`
- `row_count`

For manual entry, `source_type` should be `MANUAL` or `COPY_PASTE` as appropriate.

### `BankTransaction`

Represents the raw bank row and must remain immutable after creation.

Relevant fields:

- `bank_statement_import`
- `source_row_index`
- `transaction_date`
- `value_date`
- `narration`
- `reference_no`
- `cheque_no`
- `amount`
- `dr_cr`
- `balance`
- `raw_row_data`
- `duplicate_hash`
- `is_duplicate`

### `BankTransactionNormalized`

Stores extracted text used for matching:

- `cleaned_narration`
- `extracted_utr`
- `extracted_flat_no`
- `extracted_reference`
- `extracted_amount_words`

### `ReconciliationLink`

Stores the reconciliation relationship between a bank transaction and a ledger entry.

Important fields:

- `society`
- `voucher_entry`
- `bank_transaction`
- `matched_amount`
- `match_type`
- `confidence_score`
- `matched_by`
- `matched_at`
- `is_manual`
- `remarks`
- `status`
- `exception_type`

### `ReconciliationHistory`

Keeps audit history for match, unmatch, exception, and adjustment actions.

## Workspace Layout

The workspace should be a single full-screen page with three panels:

### Left Panel: Filters And Progress

Show:

- bank account
- statement period
- unmatched only toggle
- reconciled toggle
- voucher search
- reconciliation progress

Display:

- total statement entries
- reconciled count
- pending count
- difference amount

### Center Panel: Manual Entry Grid

Spreadsheet-like grid with columns:

| Date | Narration | Ref No | Debit | Credit | Balance | Status |
|---|---|---|---|---|---|---|

Requirements:

- inline editing
- add row with keyboard
- paste multiple rows from Excel
- arrow key navigation
- Enter key moves to next cell
- Tab navigation
- bulk row entry support

Status values:

- `Unmatched`
- `Suggested`
- `Matched`
- `Partial`
- `Exception`

Color indicators are allowed but should not be required.

### Right Panel: Match Suggestions

When the user selects a statement row, the app should automatically search possible voucher matches.

Show the top 10 suggestions sorted by confidence descending.

Suggested columns:

| Score | Voucher Date | Voucher No | Member | Amount |

Selecting a suggestion should not open a modal. Single-key confirmation is preferred.

## Keyboard Shortcuts

V1 should support:

- `M` = match selected suggestion
- `U` = unmatch
- `N` = new statement row
- `S` = save current row
- `F` = focus search
- `A` = create adjustment entry
- `D` = mark exception
- arrow keys = grid navigation
- `Enter` = next cell
- `Ctrl+V` = paste Excel rows
- `Esc` = close popup
- `Space` = select current suggestion

## Manual Statement Entry Flow

The user enters:

- date
- narration
- reference number
- debit
- credit
- balance

The system creates:

- `BankStatementImport`
- `BankTransaction`
- `BankTransactionNormalized` when needed for matching

There should be no separate manual transaction table.

## Realtime Matching

After the user leaves a row or saves it, the matching engine should run immediately.

Matching order:

1. exact reference match
2. exact amount match
3. amount + date match
4. narration similarity
5. flat number extraction

The engine should return a confidence score and up to 10 suggestions.

Implementation should use HTMX partial updates and avoid full page refreshes.

## Reconciliation Workflow

1. User enters a statement row.
2. System saves the row.
3. System generates suggestions.
4. User presses `M` to confirm the selected match.
5. System creates `ReconciliationLink`.
6. Grid updates instantly.
7. Next row becomes active automatically.

Target workflow: reconcile an entire printed statement without touching the mouse.

## Bulk Paste Workflow

This is a critical V1 feature.

Users may paste rows copied from Excel into the first grid row.

The system should:

- create multiple rows
- validate dates
- validate amounts
- detect debit and credit values
- preserve pasted content as raw row data
- save rows in one action when possible

This should also work for simple tabular text copied from email or WhatsApp exports.

## Adjustment Workflow

When no match exists, the user can press `A`.

The side drawer should pre-fill:

- date
- amount
- narration

Allow creation of adjustments for:

- bank charges
- interest credit
- direct deposit
- misc adjustment

After posting an adjustment, refresh suggestions automatically.

## Performance Requirements

- No full page refreshes for normal matching actions.
- Use HTMX partial updates.
- Keep the interaction keyboard-first.
- Support 1000+ statement rows.
- Keep cursor movement minimal.
- Mobile support is not required in V1.

## Technical Stack For This Project

Backend:

- Django
- PostgreSQL

Frontend:

- HTMX
- Alpine.js
- Bootstrap 5

Rules:

- no React
- no Vue
- keep it server-rendered

## Project Files To Use Or Extend

The following files are the natural extension points for this feature:

- [`reconciliation/views.py`](../reconciliation/views.py)
- [`reconciliation/urls.py`](../reconciliation/urls.py)
- [`reconciliation/forms.py`](../reconciliation/forms.py)
- [`reconciliation/services/manual_entry.py`](../reconciliation/services/manual_entry.py)
- [`reconciliation/services/matcher.py`](../reconciliation/services/matcher.py)
- [`reconciliation/services/adjustments.py`](../reconciliation/services/adjustments.py)
- [`reconciliation/templates/reconciliation/workspace.html`](../reconciliation/templates/reconciliation/workspace.html)
- [`reconciliation/templates/reconciliation/manual_entry.html`](../reconciliation/templates/reconciliation/manual_entry.html)

## Suggested Deliverables For This Repo

If we implement the full V1 workspace in this codebase, the concrete deliverables should be:

- `reconciliation/views.py` additions for workspace and HTMX actions
- `reconciliation/templates/reconciliation/manual_workspace.html`
- `reconciliation/templates/reconciliation/partials/`
- `reconciliation/services/manual_entry.py` refinements for paste-based entry
- `reconciliation/static/reconciliation/manual_workspace.js`
- `reconciliation/forms/manual_statement_forms.py` or a refactor of the existing forms module
- `reconciliation/urls.py` routes for workspace, save row, match, unmatch, and adjustments

## Implementation Notes

- Keep reconciliation society-scoped using the project’s existing selected-scope pattern.
- Reuse the existing matching engine rather than creating a separate matching algorithm.
- Keep `BankTransaction` immutable after creation.
- Use `ReconciliationHistory` for auditability.
- Prefer partial renders over JSON where HTMX is already the transport.
- Preserve the current accounting boundary: reconciliation links entries, it does not post ledger changes directly.

