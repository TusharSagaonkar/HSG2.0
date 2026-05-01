# Voucher Entry Page - Compact Layout Redesign Plan

## Current Layout Issues

1. **Quick Start section** uses a full `content-card` wrapper taking significant vertical space
2. **Voucher form** uses `col-12 col-md-6` (2 columns) wasting space on larger screens
3. **Vertical spacing** is too generous in content-cards, form-groups, and table cells
4. **Template info** uses a full alert box for simple informational text
5. **Form actions** (Add Row, Save) could be better positioned
6. **Ledger entries table** has standard spacing that could be tighter

## Proposed Compact Layout

### Layout Structure (Mermaid Diagram)

```mermaid
graph TB
    subgraph Header["Header Area (Inline)"]
        Title[Voucher Entry Title]
        QuickStart[Quick Start Buttons - Compact Row]
    end
    
    subgraph VoucherForm["Voucher Details - 4 Column Grid"]
        Society[Society]
        Date[Voucher Date]
        Type[Voucher Type]
        PaymentMode[Payment Mode]
        RefNum[Reference Number]
        Narration[Narration - Spans 2 cols]
    end
    
    subgraph LedgerEntries["Ledger Entries - Compact Table"]
        Table[Account | Unit | Debit | Credit - Tight Spacing]
        AddRow[Add Row - Inline]
        Save[Save Draft - Primary]
    end
    
    Header --> VoucherForm
    VoucherForm --> LedgerEntries
```

### Detailed Changes

#### 1. Header Area Integration
- Move Quick Start buttons from content-card to inline with page title
- Use a flexbox row: Title on left, Quick Start buttons on right
- On mobile: stack vertically

**Before:**
```html
<div class="action-bar">
  <a href="...">Voucher Posting Menu</a>
</div>
<div class="content-card mb-4">
  <div class="content-card__header">
    <h3>Quick Start</h3>
  </div>
  <div class="content-card__body">
    <!-- buttons -->
  </div>
</div>
```

**After:**
```html
<div class="page-header-actions d-flex justify-content-between align-items-center flex-wrap gap-2 mb-3">
  <div>
    <h3 class="fw-bold mb-0">{% translate "Voucher Entry" %}</h3>
  </div>
  <div class="d-flex flex-wrap gap-1">
    <!-- Quick Start buttons - compact -->
    <!-- Voucher Posting link -->
  </div>
</div>
```

#### 2. Voucher Form - 4 Column Layout
Change from `col-12 col-md-6` to `col-12 col-md-4 col-lg-3` for smaller fields.

**Fields Layout:**
- Row 1: Society (col-md-4), Voucher Date (col-md-4), Voucher Type (col-md-4)
- Row 2: Payment Mode (col-md-4), Reference Number (col-md-4), (empty col-md-4)
- Row 3: Narration (col-md-8), (empty col-md-4)

#### 3. Remove Content-Card Wrapper
Remove the `content-card` wrapper from Quick Start since it's now in the header.
Keep form without extra card wrapper to save space.

#### 4. Compact Form Styling
- Reduce form-group margins: `mb-2` instead of default
- Use `form-control-sm` and `form-select-sm` for smaller input fields
- Reduce label margins

#### 5. Ledger Entries Table - Compact
- Add `table-sm` class for tighter row spacing
- Reduce cell padding with custom CSS
- Make the table more compact while maintaining readability

#### 6. Template Info - Inline
Replace the alert box with a small inline message:
```html
{% if selected_template %}
  <small class="text-info">
    <i class="fas fa-info-circle"></i> 
    {% translate "Pre-filled from template" %}: {{ selected_template.name }}
  </small>
{% endif %}
```

#### 7. Form Actions - Inline
Position Add Row and Save buttons in a compact row with proper alignment:
```html
<div class="d-flex justify-content-between align-items-center mt-3">
  <button type="button" id="add-ledger-row" class="btn-action btn-action--sm btn-action--outline-secondary">
    <i class="fas fa-plus-circle me-1"></i>{% translate "Add Row" %}
  </button>
  <button type="submit" class="btn-action btn-action--primary">
    <i class="fas fa-save me-1"></i>{% translate "Save Draft Voucher" %}
  </button>
</div>
```

## Responsive Behavior

| Screen Size | Layout |
|------------|--------|
| XL (>1200px) | 4-column form, inline header buttons |
| LG (992-1200px) | 3-column form, inline header buttons |
| MD (768-992px) | 2-column form, stacked header |
| SM/XS (<768px) | Single column, fully stacked |

## Files to Modify

1. `accounting/templates/accounting/voucher_entry.html` - Main template redesign
2. Potentially add custom CSS in a new file or existing CSS file for compact styling

## Implementation Steps

1. Redesign the header area with inline Quick Start buttons
2. Update voucher form to use multi-column layout
3. Remove unnecessary content-card wrappers
4. Apply compact styling to form elements
5. Make ledger entries table more compact
6. Replace alert with inline template info
7. Position form actions compactly
8. Test responsive behavior
