# Phase 10 Test Suite — Fix Plan

## Current State
- **285 tests** across 8 files
- **test_models.py**: 28/48 passed so far (fixing in progress)
- **Other 7 files**: untested yet

## Build vs Create Pattern (Recurring Root Cause)
`factory.build()` creates unsaved instances → FK chains are null → `full_clean()` or `.save()` fails with:
- `ValueError: save() prohibited to prevent data loss due to unsaved related object 'category'`
- `ValueError: save() prohibited to prevent data loss due to unsaved related object 'society'`

**Rule**: Use `factory.create()` for any test that calls `full_clean()` or `.save()` unless the test is specifically testing validation on unsaved instances (then ensure FK chain is populated).

## ReconciliationLinkFactory EXACT Constraint (Recurring Root Cause)
Model's `clean()` at [model_ReconciliationLink.py:128](reconciliation/models/model_ReconciliationLink.py:128):
```python
if self.match_type == self.MatchType.EXACT and self.status not in {
    self.Status.MATCHED,
    self.Status.FORCE_MATCHED,
}:
    raise ValidationError("Exact match links must have status MATCHED or FORCE_MATCHED.")
```

**Rule**: Any test creating a `ReconciliationLink` with `status=SUGGESTED` or `status=PENDING` must also set `match_type="PARTIAL"` (not the default `EXACT`).

## Strategy: Batch Per File

For each of the 8 test files:
1. Run pytest on that file **without `-x`** → capture ALL failures
2. Analyze failures → batch-fix all in one diff
3. Re-run to confirm all pass
4. Move to next file

This avoids incremental 60-120s runs per single failure. Instead: 1 run → see all failures → fix all → 1 verification run.

---

## File-by-File Plan

### 1. test_models.py (48 tests)
Status: ~28/48 pass. Remaining fixes needed:

| Test | Issue | Fix |
|------|-------|-----|
| `test_confirm_match_from_suggested` | EXACT+SUGGESTED rejected | Add `match_type="PARTIAL"` |
| `test_confirm_match_from_pending` | EXACT+PENDING rejected | Add `match_type="PARTIAL"` |
| `test_unmatch_raises_if_suggested` | EXACT+SUGGESTED rejected | Add `match_type="PARTIAL"` |
| `test_voucher_entry_society_mismatch` | `.build()` on ReconciliationLinkFactory | Lazy attr fix already in place; should work |
| `test_bank_transaction_society_mismatch` | `.build()` on ReconciliationLinkFactory | Lazy attr fix already in place; should work |
| Any other build-based test | `.build()` with unsaved FK chain | Convert to `.create()` |
| Signal tests | Depends on link creation | Same match_type fix |

### 2. test_parsers.py (18 tests)
Likely few issues — tests parser logic, not DB models.

### 3. test_normalizer.py (13 tests)
Likely few issues — normalization logic.

### 4. test_importer.py (10 tests)
May have `.build()` issues in factory chains.

### 5. test_matcher.py (22 tests)
Creates vouchers/entries via helpers, may have posted-voucher conflicts.

### 6. test_reports.py (30 tests)
View + report logic, likely uses `.create()`.

### 7. test_adjustments.py (19 tests)
Creates adjustment vouchers, may have posted-voucher issues.

### 8. test_views.py (125 tests)
Largest file. View tests that use factories. May have login/SocietyMiddleware issues.

---

## Execution Order
1. Complete test_models.py fixes
2. Run ALL 8 files to get full failure picture
3. Batch-fix remaining files
4. Final verification run (all 285 pass)