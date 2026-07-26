### **Plan for HTMX and UI Upscaling of Nav Top Bar**
**Goal**: Enhance the nav top bar with HTMX for dynamic updates and improve UI aesthetics while preserving all existing functionality.

---

#### **1. HTMX Enhancements**
**a. Society and Financial Year Selection**
- Replace the current form submission with HTMX:
  - Add `hx-post="/users/selection/"` and `hx-trigger="change"` to the form.
  - Add `hx-target="#workspace"` and `hx-swap="innerHTML"` to reload the workspace dynamically.
  - Add `hx-indicator="#workspace-loading"` to show a loading state during requests.
- Remove `onchange="handleSocietySelection(this)"` and `onchange="this.form.submit()"` from the dropdowns.
- Delete the `handleSocietySelection` function from `project.js`.

**b. Periods Button**
- Replace the modal approach with HTMX:
  - Add `hx-get="/accounting/periods/"` and `hx-trigger="click"` to the button.
  - Add `hx-target="#periods-modal-body"` and `hx-swap="innerHTML"` to load periods dynamically into a modal body.
  - Add `hx-indicator="#workspace-loading"` to show a loading state.

---

#### **2. UI Improvements**
**a. Selection Shell**
- Replace the current `selection-shell` structure with a cleaner design:
  - Use a flexbox layout (`d-flex align-items-center gap-2`) for the society and financial year dropdowns.
  - Apply `background-color: var(--color-bg-secondary)` and `padding: var(--space-sm) var(--space-md)` for grouping.
  - Add `border-radius: var(--radius-md)` for rounded corners.

**b. Dropdown Styling**
- Replace `form-select-sm` with custom-styled dropdowns:
  - Use `btn btn-sm btn-outline-secondary` for the dropdown containers.
  - Apply `background-color: var(--color-bg-primary)` and `border: 1px solid var(--color-border-default)`.
  - Add hover and focus states using `var(--color-primary-hover)` and `var(--color-border-focus)`.

**c. Badge Styling**
- Enhance the `context-badge` styling:
  - Use `background-color: var(--color-primary-subtle)` and `color: var(--color-primary)` for the default state.
  - Apply `padding: var(--space-xs) var(--space-sm)` and `border-radius: var(--radius-full)`.
  - Add `font-weight: var(--font-weight-medium)` for better readability.

**d. Button Styling**
- Replace the `periods-chip` button with a modern design:
  - Use `btn btn-sm btn-outline-primary` for the button.
  - Apply `border-radius: var(--radius-md)` and `transition: all var(--transition-normal)`.
  - Add hover effects using `var(--color-primary-hover)` and `var(--color-primary-light)`.
- Ensure the `user-chip` buttons (profile and sign-out) are visually consistent.

**e. Accessibility**
- Verify all interactive elements have proper `aria-label` and `aria-live` attributes.
- Ensure dropdowns and buttons are keyboard-navigable.

---

#### **3. Implementation Steps**
1. **Update the HTML Template**:
   - Replace the form submission logic with HTMX attributes.
   - Apply UI improvements to the `selection-shell`, dropdowns, badges, and buttons.

2. **Remove Redundant JavaScript**:
   - Delete the `handleSocietySelection` function from `project.js`.

3. **Test**:
   - Verify society and financial year selection works without full page reloads.
   - Ensure the periods button loads data dynamically.
   - Validate UI improvements for visual consistency and accessibility.

---

#### **4. Validation**
- Test all dropdowns and buttons for functionality and responsiveness.
- Verify that the workspace reloads dynamically without full page reloads.
- Ensure the UI adheres to the design tokens and remains accessible.