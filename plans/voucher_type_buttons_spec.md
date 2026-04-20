# Voucher Type Buttons – Implementation Specification

## Objective
Add four quick‑action buttons on the voucher entry page (`/accounting/vouchers/entry/`) that pre‑fill the form with default data for the four most common voucher types: **Payment**, **Receipt**, **Contra**, and **Journal**.

## User Experience
1. Above the existing voucher entry form, display a horizontal row of four buttons, each labeled with the voucher type and a short description.
2. Clicking a button reloads the page with query parameters that cause the form to be pre‑filled with:
   - Voucher type set to the corresponding value.
   - Payment mode set to a sensible default (CASH for Receipt/Payment, empty for Contra/Journal).
   - Narration pre‑filled with a placeholder text describing the typical use case.
   - Reference number left empty (user can fill if needed).
   - Ledger rows remain empty (user must add accounts and amounts).
3. The user can then adjust any field, add ledger rows, and save the draft as usual.

## Technical Design

### 1. Extend `VoucherEntryView`
**File:** `accounting/views.py`

Modify the `get_context_data` method to read additional query parameters and set initial values on the `VoucherForm`.

Suggested parameters:
- `voucher_type` – matches `Voucher.VoucherType` choices.
- `payment_mode` – matches `Voucher.PaymentMode` choices.
- `narration` – string.
- `reference_number` – string.

If these parameters are present in `request.GET`, they should be passed as `initial` dict to `VoucherForm`. The existing logic for `society` should remain unchanged.

**Example changes:**
```python
def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)
    voucher_form = kwargs.get("voucher_form")
    entry_formset = kwargs.get("entry_formset")

    if voucher_form is None or entry_formset is None:
        selected_society = self._resolve_society(self.request.GET.get("society"))
        if selected_society is None:
            selected_society, _ = get_selected_scope(self.request)

        # Build initial dict from query parameters
        initial = {"society": selected_society} if selected_society else {}
        for param in ("voucher_type", "payment_mode", "narration", "reference_number"):
            value = self.request.GET.get(param)
            if value is not None:
                initial[param] = value

        voucher_form = voucher_form or VoucherForm(initial=initial)
        entry_formset = entry_formset or self._build_row_formset(society=selected_society)

    context["voucher_form"] = voucher_form
    context["entry_formset"] = entry_formset
    return context
```

### 2. Update Voucher Entry Template
**File:** `accounting/templates/accounting/voucher_entry.html`

Add a new section between the `page-actions` div and the `<form>` element.

**Button definitions:**
- **Payment Voucher** – `?voucher_type=PAYMENT&payment_mode=CASH&narration=Payment+for+expenses`
- **Receipt Voucher** – `?voucher_type=RECEIPT&payment_mode=CASH&narration=Receipt+from+member`
- **Contra Voucher** – `?voucher_type=GENERAL&narration=Transfer+cash+to+bank` (Note: Contra is not a separate voucher type; use GENERAL with a cash/bank pair.)
- **Journal Voucher** – `?voucher_type=JOURNAL&narration=Adjustment+entry`

We may decide to treat Contra as a separate type later, but for now we can keep it as GENERAL with a hint.

**UI Layout:**
```html
<div class="voucher-quick-actions mb-4">
  <h3 class="h6">Quick Start</h3>
  <p class="text-muted small">Click a button to pre‑fill common voucher details.</p>
  <div class="d-flex flex-wrap gap-2">
    <a href="?voucher_type=PAYMENT&payment_mode=CASH&narration=Payment+for+expenses"
       class="btn btn-outline-danger">
       <i class="fas fa-money-bill-wave me-1"></i> Payment
    </a>
    <a href="?voucher_type=RECEIPT&payment_mode=CASH&narration=Receipt+from+member"
       class="btn btn-outline-success">
       <i class="fas fa-receipt me-1"></i> Receipt
    </a>
    <a href="?voucher_type=GENERAL&narration=Transfer+cash+to+bank"
       class="btn btn-outline-info">
       <i class="fas fa-exchange-alt me-1"></i> Contra
    </a>
    <a href="?voucher_type=JOURNAL&narration=Adjustment+entry"
       class="btn btn-outline-warning">
       <i class="fas fa-book me-1"></i> Journal
    </a>
  </div>
</div>
```

**JavaScript Enhancement:**
To preserve the currently selected society, we can add a small script that appends `&society=...` to each button’s href if a society is already chosen.

```html
<script>
  document.addEventListener('DOMContentLoaded', function() {
    const societyField = document.getElementById('id_society');
    if (!societyField) return;
    const societyId = societyField.value;
    if (!societyId) return;

    document.querySelectorAll('.voucher-quick-actions a').forEach(link => {
      const url = new URL(link.href, window.location.origin);
      url.searchParams.set('society', societyId);
      link.href = url.toString();
    });
  });
</script>
```

### 3. Styling
Use Bootstrap’s outline button variants with distinct colors:
- Payment: `btn-outline-danger` (red)
- Receipt: `btn-outline-success` (green)
- Contra: `btn-outline-info` (blue)
- Journal: `btn-outline-warning` (yellow)

Add appropriate icons from Font Awesome (already included in the project).

### 4. Validation Considerations
- The pre‑filled data is only initial; the user must still satisfy all validation rules (e.g., at least two ledger rows, balanced totals).
- If the user changes the society after clicking a button, the page will reload (existing behavior) and the pre‑filled values will be retained because they are in the URL. That’s acceptable.

### 5. Future Enhancements (Optional)
- Store voucher templates per society (e.g., default accounts for each type) and pre‑fill ledger rows.
- Add a “Clear” button to reset the form to empty.
- Make the buttons more prominent with larger size and icons.

## Testing
- Verify that each button correctly sets the voucher type, payment mode, and narration.
- Ensure that the society parameter is preserved when present.
- Confirm that the form can be submitted after pre‑fill (with appropriate ledger entries added).
- Test that validation errors are still displayed correctly.

## Files to Modify
1. `accounting/views.py` – `VoucherEntryView.get_context_data`
2. `accounting/templates/accounting/voucher_entry.html` – add quick‑actions section and JavaScript.

## Dependencies
- Bootstrap 5 (already used)
- Font Awesome (already included)

## Timeline
This is a small feature that can be implemented in a single development session (approx. 2‑3 hours).

## Approval
Once this specification is approved, the implementation can be carried out in Code mode.