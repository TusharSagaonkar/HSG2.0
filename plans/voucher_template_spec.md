# Society‑Specific Voucher Template Configuration – Specification

## Overview
Enable each housing society to define custom voucher templates for the four common voucher types (Payment, Receipt, Contra, Journal). Templates store default values for narration, payment mode, reference number pattern, and pre‑defined ledger rows (accounts, units, sides). The voucher entry page will use these templates to pre‑fill the form when a user clicks a quick‑action button, significantly speeding up voucher creation.

## Goals
1. Allow society administrators to create, edit, and delete voucher templates.
2. Automatically create default templates when a new society is set up (along with its standard accounts).
3. Integrate templates with the existing voucher entry UI: buttons should pre‑fill the form using the society’s template for that voucher type.
4. Maintain backward compatibility – if no template exists for a voucher type, fall back to generic defaults.

## Database Design

### Model: `VoucherTemplate`
| Field | Type | Description |
|-------|------|-------------|
| `id` | AutoField | Primary key. |
| `society` | ForeignKey to `Society` | The society that owns this template. |
| `voucher_type` | CharField (choices=Voucher.VoucherType.choices) | Which voucher type this template is for (PAYMENT, RECEIPT, GENERAL, JOURNAL, etc.). |
| `name` | CharField (max_length=100, blank=True) | Human‑readable name (e.g., “Monthly Maintenance Receipt”). |
| `narration` | TextField (blank=True) | Default narration text for vouchers created from this template. |
| `payment_mode` | CharField (choices=Voucher.PaymentMode.choices, blank=True) | Default payment mode (if applicable). |
| `reference_number_pattern` | CharField (max_length=50, blank=True) | Optional pattern for generating reference numbers (e.g., “CHQ‑{seq}”). |
| `is_active` | BooleanField (default=True) | Whether the template is available for use. |
| `created_at` | DateTimeField (auto_now_add=True) | |
| `updated_at` | DateTimeField (auto_now=True) | |

**Constraints:**
- Unique together (`society`, `voucher_type`) – only one active template per voucher type per society (optional; could allow multiple with a “default” flag).
- Foreign key `on_delete=CASCADE` – templates are deleted when the society is deleted.

### Model: `VoucherTemplateRow`
| Field | Type | Description |
|-------|------|-------------|
| `id` | AutoField | Primary key. |
| `template` | ForeignKey to `VoucherTemplate` (related_name='rows') | The template this row belongs to. |
| `account` | ForeignKey to `Account` | The ledger account to pre‑fill. Must belong to the same society as the template. |
| `unit` | ForeignKey to `Unit` (null=True, blank=True) | Optional unit to link. |
| `side` | CharField (choices=[('DEBIT','Debit'), ('CREDIT','Credit')]) | Whether this row is a debit or credit entry. |
| `default_amount` | DecimalField (max_digits=12, decimal_places=2, null=True, blank=True) | Suggested amount (zero if left empty). |
| `order` | IntegerField (default=0) | Order of rows within the template. |

**Constraints:**
- `account.society` must equal `template.society`.
- `unit.structure.society` must equal `template.society` (if unit is provided).

## UI/UX Design

### 1. Template Management Interface
**Location:** Society administration panel (e.g., `/societies/<id>/voucher-templates/`) and Django admin.

**Features:**
- List of existing templates for the society, with columns: Voucher Type, Name, Active, Actions (Edit, Delete).
- “Add Template” button opens a form with:
  - Voucher type dropdown.
  - Name field.
  - Narration textarea.
  - Payment mode dropdown.
  - Reference number pattern field.
  - A table for ledger rows (similar to voucher entry) where the admin can add rows, select accounts, units, sides, and default amounts.
  - Save button.

**Edit/Delete:** Standard CRUD operations.

### 2. Integration with Voucher Entry Page
**Changes to `VoucherEntryView`:**
- In `get_context_data`, fetch all active templates for the selected society (or the society from query parameters).
- Pass a dictionary `voucher_templates` mapping voucher_type -> template object (or None) to the template context.

**Changes to `voucher_entry.html`:**
- Replace the static quick‑action buttons with dynamic buttons generated from the template dictionary.
- For each voucher type (PAYMENT, RECEIPT, GENERAL, JOURNAL), if a template exists, the button’s link includes a query parameter `template_id=<id>` (or `use_template=<voucher_type>`).
- If no template exists, fall back to the current hard‑coded defaults (same as before).
- Button labels can include the template name (e.g., “Payment – Electricity Bill”).

**New endpoint:** `GET /accounting/vouchers/entry/?template_id=<id>` – loads the voucher entry page with the template’s data pre‑filled in the form and ledger rows.

**Pre‑filling logic:**
- Set `voucher_type`, `narration`, `payment_mode`, `reference_number` (if any) from the template.
- For each row in the template, create a corresponding entry in the ledger entry formset with `account`, `unit`, `side` (debit/credit), and `amount` (if default_amount is not null).
- The formset should have exactly the number of rows defined in the template (plus extra empty rows if needed).

## Migration & Seeding

### 1. Database Migrations
Create two new migrations in the `accounting` app:
- `000X_create_voucher_template_models.py`
- `000Y_add_unique_constraint.py`

### 2. Default Template Creation
When a new society is created (or when standard accounts are bootstrapped), automatically create basic templates for that society.

**Approach:** Extend the existing society account bootstrap command (`seed_test_society_matrix` or `seed_deepsagar`) to also create voucher templates.

**Default templates:**
- **Payment:** Debit “Electricity Expense”, Credit “Cash Account” (if those accounts exist).
- **Receipt:** Debit “Cash Account”, Credit “Maintenance Receivable”.
- **Contra:** Debit “Bank Account”, Credit “Cash Account”.
- **Journal:** Debit “Depreciation”, Credit “Furniture Account”.

These defaults are illustrative; the actual accounts will be looked up by name within the society.

**Management command:** `python manage.py seed_voucher_templates --society=<id>` (optional).

## Implementation Phases

### Phase 1: Core Models & Admin
- Create `VoucherTemplate` and `VoucherTemplateRow` models.
- Register them in Django admin with appropriate inline for rows.
- Write basic validation (account/unit society matching).
- Run migrations.

### Phase 2: Template Management UI
- Create view(s) for society admins to manage templates (list, create, edit, delete).
- Templates are scoped to the currently selected society (via `get_selected_scope`).
- Use a formset for rows similar to voucher entry.

### Phase 3: Integration with Voucher Entry
- Modify `VoucherEntryView` to accept `template_id` parameter and pre‑fill the form.
- Update the template to generate buttons based on existing templates.
- Ensure the JavaScript that appends society parameter works with dynamic buttons.

### Phase 4: Seeding & Deployment
- Write migration to create default templates for existing societies (optional).
- Update society creation workflows to include template creation.

## Open Questions
1. Should we allow multiple templates per voucher type per society? (e.g., different payment templates for different expense categories). Initially we can limit to one per type; later add a “default” flag.
2. How to handle template rows where the referenced account becomes inactive? We can still pre‑fill but validation will fail; we can show a warning.
3. Should templates support variable placeholders (e.g., `{date}` in narration)? This is a future enhancement.

## Dependencies
- Existing society and account models.
- Voucher entry form and formset logic.

## Estimated Effort
- **Phase 1:** 1–2 days
- **Phase 2:** 2–3 days
- **Phase 3:** 1–2 days
- **Phase 4:** 1 day

Total: 5–8 days of development.

## Next Steps
1. Review this specification with stakeholders.
2. Prioritize phases based on business needs.
3. Begin implementation in Code mode.