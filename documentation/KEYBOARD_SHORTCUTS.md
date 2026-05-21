# Keyboard Shortcuts

The Housing Accounting System includes a database-driven keyboard shortcut engine that lets users navigate the application and trigger common actions without reaching for the mouse.

The architecture is already solid:

- database-driven
- role-aware
- page-aware
- normalized
- cached
- API-based
- modular
- HTMX compatible

The weak point is shortcut policy, not the engine itself. This document therefore serves as both the current reference and the recommended direction for the next shortcut design cycle.

## Shortcut Policy

### Principles

- Prefer browser-safe shortcuts that do not collide with tab management, address bar focus, refresh, dev tools, or fullscreen.
- Prefer sequential navigation for module switching, especially `g`-prefixed combos.
- Keep discovery centralized in a command palette.
- Use simple contextual action keys inside modules and grids.
- Avoid building core workflows around function keys or `Ctrl+Shift` chords.

### Recommended Structure

| Layer | Purpose | Recommended Pattern |
|---|---|---|
| Universal command palette | Search, actions, navigation, recent items | `Ctrl+K` |
| Sequential navigation | Module and page switching | `g h`, `g d`, `g a`, `g r`, etc. |
| Context actions | Create, edit, save, approve, print | `c`, `e`, `s`, `a`, `p` |
| Grid mode | Spreadsheet-style accounting work | arrows, `Enter`, `Tab`, `Ctrl+C`, `Ctrl+V` |

### Keys to Prefer

| Key | Use |
|---|---|
| `Ctrl+K` | Command palette |
| `g h` | Home |
| `g d` | Dashboard |
| `g a` | Accounting |
| `g r` | Reports |
| `g m` | Members |
| `g b` | Billing |
| `g p` | Parking |
| `/` | Search |
| `?` | Help |
| `Esc` | Close or cancel |
| `j` / `k` | Move down or up in lists |
| `c` | Create |
| `e` | Edit |
| `s` | Save |
| `a` | Approve |
| `p` | Print |

### Keys to Avoid

| Key Type | Why |
|---|---|
| Function keys | Browser, OS, laptop, and extension conflicts |
| `Ctrl+1` through `Ctrl+9` | Browser tab switching owns these keys |
| Most `Ctrl+Shift+<key>` combos | Dev tools, browser actions, and poor ergonomics |
| `Ctrl+R`, `Ctrl+W`, `Ctrl+L` | Refresh, close tab, and address bar collisions |

### Migration Note

The current implementation still contains legacy shortcuts such as `Ctrl+Shift+...`, function keys, and `Ctrl+1` through `Ctrl+9` on one page. Those remain documented below because they are active today, but the policy recommendation is to phase them out in favor of the safer structure above.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Viewing Available Shortcuts](#viewing-available-shortcuts)
3. [Shortcut Engine Architecture](#shortcut-engine-architecture)
4. [Global Shortcuts](#global-shortcuts)
5. [Page-Specific Shortcuts](#page-specific-shortcuts)
6. [Role-Based Access Control](#role-based-access-control)
7. [Managing Shortcuts](#managing-shortcuts)
8. [Database Schema](#database-schema)
9. [Default Shortcut Reference Table](#default-shortcut-reference-table)

---

## Quick Start

Press `Ctrl+Q` on any page to open the Shortcut Help modal. It lists every shortcut available to the current role on the current page, grouped into Global and Page-Specific sections.

---

## Viewing Available Shortcuts

The built-in help modal is always available:

| Shortcut | Action |
|---|---|
| `Ctrl+Q` | Open the keyboard shortcuts help modal |
| `Ctrl+Shift+?` | Same as above |

The help modal displays:

- the current page identifier, or `Global`
- all global shortcuts available everywhere
- all page-specific shortcuts for the current page
- each shortcut's key combination, name, action type, and action value

If no shortcuts are available for the user's role on the current page, a message is shown: `No shortcuts available for your role on this page.`

---

## Shortcut Engine Architecture

### Component Overview

| Component | File | Purpose |
|---|---|---|
| Client engine | [`housing_accounting/static/js/shortcut_engine.js`](../housing_accounting/static/js/shortcut_engine.js) | Global key listener, key normalization, action execution, help modal |
| Model | [`administration/models.py`](../administration/models.py) | `Shortcut` model for key, action type, value, scope, role, and priority |
| API view | [`administration/views.py`](../administration/views.py) | `GET /administration/api/shortcuts/` returns filtered shortcuts as JSON |
| URL route | [`administration/urls.py`](../administration/urls.py) | Maps `api/shortcuts/` to the API view |
| Seeder | [`administration/management/commands/seed_shortcuts.py`](../administration/management/commands/seed_shortcuts.py) | `python manage.py seed_shortcuts` creates, updates, and deactivates defaults |
| Base template | [`housing_accounting/templates/base.html`](../housing_accounting/templates/base.html) | Loads the engine script and provides the `data-page` attribute |

### Request Lifecycle

1. The base template loads `shortcut_engine.js` with `defer`.
2. When the DOM is ready, the engine initializes via `initShortcutEngine()`.
3. The engine reads `document.body.dataset.page`.
4. It calls `GET /administration/api/shortcuts/?page=<page_id>`.
5. The API filters active shortcuts by role and page.
6. The response is cached for 5 minutes.
7. The client stores the shortcuts in a `SHORTCUTS` object, keyed by normalized key combination.
8. The built-in `Ctrl+Q` help shortcut is injected.
9. A single `keydown` listener handles key matching and execution.

### Key Normalization

Key combinations are normalized on both the server and client.

Server-side examples:

```text
Input:  "ctrl + r"    ->  Output: "CTRL+R"
Input:  "Ctrl+Alt+R"  ->  Output: "CTRL+ALT+R"
Input:  "f9"          ->  Output: "F9"
```

The server function:

1. Converts to uppercase and removes spaces
2. Standardizes separators to `+`
3. Removes duplicate `+` characters
4. Replaces synonyms such as `CONTROL` -> `CTRL`, `COMMAND` -> `CMD`, `OPTION` -> `ALT`, `WINDOWS` -> `WIN`

The client function builds the combo from a live `KeyboardEvent`:

1. Appends modifier keys in order: `CTRL`, `ALT`, `SHIFT`, `META`
2. Ignores modifier keys pressed alone
3. Maps special keys such as `Escape` -> `ESC`, arrow keys, space, and `/` -> `?`
4. Joins all parts with `+`

### Action Types

| Type | Constant | Behavior | Example `action_value` |
|---|---|---|---|
| URL | `URL` | Sets `window.location.href` to the value | `/accounting/vouchers/entry/` |
| MODAL | `MODAL` | Uses HTMX to fetch into `#modal-container`, then shows a Bootstrap modal | `/members/search/modal/` |
| JS | `JS` | Executes JavaScript via global function lookup, then `eval()` fallback | `showShortcutHelp()` |

### Browser Shortcut Protection

The engine blocks browser-default shortcuts to avoid accidental tab/window closures or navigation away from the application.

Blocked shortcuts include:

| Category | Blocked Keys |
|---|---|
| Tab or window management | `Ctrl+N`, `Ctrl+T`, `Ctrl+W`, `Ctrl+Shift+T` |
| Tab switching | `Ctrl+Tab`, `Ctrl+Shift+Tab`, `Ctrl+1` through `Ctrl+9` |
| Page control | `Ctrl+R`, `Ctrl+Shift+R`, `Ctrl+S`, `Ctrl+P` |
| Find or view | `Ctrl+F`, `Ctrl+G`, `Ctrl+U`, `Ctrl+Shift+I` |
| Bookmarks | `Ctrl+D`, `Ctrl+Shift+D`, `Ctrl+Shift+B`, `Ctrl+Shift+O` |
| Address bar | `Ctrl+L`, `Ctrl+E`, `F6` |
| History or downloads | `Ctrl+J`, `Ctrl+Shift+Delete` |
| Function keys | `F1`, `F3`, `F5`, `F11`, `F12` |

Special handling:

- `Ctrl+H` is explicitly allowed and is used by the app for Home.
- `Ctrl+K` is reserved in the recommended policy for the command palette, but the current engine still blocks the browser address-bar shortcut pattern.
- `Ctrl+Shift+<key>` combinations are blocked by default, except for the keys the app currently uses.
- Alt-only and Alt+function combinations are blocked because they trigger browser menus.

### Input Field Safety

The global key handler ignores keypresses when focus is on:

- `input`
- `textarea`
- `select`
- any element with `contenteditable="true"`

### Page Detection

The engine determines the current page via the `data-page` HTML attribute on the `body` tag:

```html
<body data-page="{% block page_id %}{% endblock %}">
```

If no page is set, only global shortcuts are returned.

### Caching

The API view uses two layers of caching:

1. Django view-level cache for the full HTTP response
2. Application-level cache for the serialized JSON data

Shortcut changes may take up to 5 minutes to reach all users unless cache is cleared manually.

### Client-Side API

```javascript
window.ShortcutEngine.reload();
const shortcuts = window.ShortcutEngine.getShortcuts();
const success = window.ShortcutEngine.execute('CTRL+SHIFT+H');
window.ShortcutEngine.showHelp();
```

The engine also exports its functions for ES6 module usage.

---

## Global Shortcuts

Global shortcuts are available on every page. They are stored with `scope = 'GLOBAL'` and `page = ''`.

The table below reflects the current implementation. The recommended future direction is to replace `Ctrl+Shift` navigation with `g`-prefixed sequences.

### Navigation

| Shortcut | Name | Action | Destination |
|---|---|---|---|
| `Ctrl+H` | Go to Home | URL redirect | `/` |
| `Ctrl+Shift+H` | Open Housing Module | URL redirect | `/housing/` |
| `Ctrl+Shift+A` | Open Accounting Module | URL redirect | `/accounting/` |
| `Ctrl+Shift+R` | Open Reports Module | URL redirect | `/reports/` |
| `Ctrl+Shift+B` | Open Billing Module | URL redirect | `/billing/` |
| `Ctrl+Shift+P` | Open Parking Module | URL redirect | `/parking/` |
| `Ctrl+Shift+M` | Open Members Module | URL redirect | `/members/` |

Recommended future mapping:

| Future Shortcut | Intended Action |
|---|---|
| `g h` | Home |
| `g d` | Dashboard |
| `g a` | Accounting |
| `g r` | Reports |
| `g b` | Billing |
| `g m` | Members |
| `g p` | Parking |

### Actions

| Shortcut | Name | Action Type | Description |
|---|---|---|---|
| `F2` | Quick Search | JS (`focusSearchField()`) | Focuses the global search field |
| `F4` | Create New Item | JS (`openCreateModal()`) | Opens a creation modal for the current context |
| `F7` | View Dashboard | URL redirect | Navigates to `/accounting/dashboard/` |
| `F8` | Print/Export | JS (`triggerPrint()`) | Triggers the browser's print dialog |
| `Ctrl+Shift+V` | Open Voucher Entry | URL redirect | Navigates to `/accounting/vouchers/entry/` |

### Modals

| Shortcut | Name | Action | Modal Content URL |
|---|---|---|---|
| `Ctrl+Shift+F` | Find Member | MODAL | `/members/search/modal/` |
| `Ctrl+Shift+E` | Email Compose | MODAL | `/notifications/compose/modal/` |
| `Ctrl+Shift+N` | New Notification | MODAL | `/notifications/create/modal/` |

### System

| Shortcut | Name | Action Type | Description |
|---|---|---|---|
| `Ctrl+Q` | Show Keyboard Shortcuts Help | JS (`showShortcutHelp()`) | Opens the help modal |
| `Ctrl+Shift+?` | Show All Shortcuts | JS (`showShortcutHelp()`) | Same as `Ctrl+Q` |
| `Ctrl+Shift+S` | Quick Society Switch | JS (`focusSocietySelector()`) | Focuses the society selector in the top bar |

---

## Page-Specific Shortcuts

Page-specific shortcuts are stored with `scope = 'PAGE'` and a page identifier. They are only returned when the current page's `data-page` attribute matches.

### Accounting Module

**Page identifier:** `accounting`

These shortcuts use function keys `F9` through `F12` to create different voucher types.

| Shortcut | Name | Action Type | Destination |
|---|---|---|---|
| `F9` | Create Purchase Voucher | URL redirect | `/accounting/voucher/entry/?type=PURCHASE` |
| `F10` | Create Receipt Voucher | URL redirect | `/accounting/voucher/entry/?type=RECEIPT` |
| `F11` | Create Payment Voucher | URL redirect | `/accounting/voucher/entry/?type=PAYMENT` |
| `F12` | Create Journal Voucher | URL redirect | `/accounting/voucher/entry/?type=JOURNAL` |

Policy note: this is the clearest example of the legacy pattern that should eventually move to `g v` plus contextual actions or a command-palette entry.

### Voucher Entry Page

**File:** [`accounting/templates/accounting/voucher_entry.html`](../accounting/templates/accounting/voucher_entry.html)

The voucher entry page has an additional inline shortcut handler in its inline JavaScript block. It provides quick template selection.

| Shortcut | Action |
|---|---|
| `Ctrl+1` | Click the 1st voucher template quick-button |
| `Ctrl+2` | Click the 2nd voucher template quick-button |
| `Ctrl+3` | Click the 3rd voucher template quick-button |
| ... | ... |
| `Ctrl+9` | Click the 9th voucher template quick-button |

Important: this handler uses its own `keydown` listener, not the global shortcut engine. The global engine would normally block `Ctrl+1` through `Ctrl+9` because browsers reserve tab switching, but the inline handler stops propagation before the global engine sees the events.

Policy note: this is a strong candidate for replacement with in-page letter shortcuts or a command-palette action.

---

## Role-Based Access Control

The shortcut system supports optional role-based restrictions through the `role` field on the `Shortcut` model.

- If `role` is `NULL` or empty string, the shortcut is available to all authenticated users.
- If `role` is set, the shortcut is only available to users whose `user.role` matches.
- Anonymous users only see shortcuts where `role` is blank or `NULL`.

Current configuration: all default shortcuts have `role = ''`, so they are available to all users. No role-restricted shortcuts are configured by default.

---

## Managing Shortcuts

### Via Django Admin

Administrators can:

- create new shortcuts with any key combination, action type, scope, and role
- edit existing shortcuts
- deactivate shortcuts by unchecking `is_active`
- delete shortcuts permanently
- reorder using the `priority` field

### Via Management Command

The seeder command resets shortcuts to the defaults defined in the codebase:

```bash
python manage.py seed_shortcuts
```

It:

1. creates shortcuts that do not exist
2. updates existing shortcuts that match by normalized key
3. deactivates shortcuts not in the default set

### Programmatically

```python
from administration.models import Shortcut

Shortcut.objects.create(
    name='Go to Settings',
    key_combination='CTRL+ALT+S',
    action_type=Shortcut.ActionType.URL,
    action_value='/settings/',
    scope=Shortcut.Scope.GLOBAL,
    role='',
    is_active=True,
    priority=10,
)
```

### Adding Page-Specific Shortcuts

1. Set the page identifier in the template.
2. Create the shortcut with `scope='PAGE'` and the matching `page`.
3. Run the seeder or create via admin.

If you are adding a new page, prefer a browser-safe mapping and avoid depending on a function key as the primary interaction.

---

## Database Schema

| Field | Type | Description |
|---|---|---|
| `name` | CharField(100) | Human-readable name |
| `key_combination` | CharField(50) | Raw key combo as entered |
| `action_type` | CharField(20) | One of `URL`, `MODAL`, `JS` |
| `action_value` | CharField(255) | URL or JS function name |
| `scope` | CharField(20) | One of `GLOBAL`, `PAGE` |
| `page` | CharField(100) | Page identifier for page-specific shortcuts |
| `role` | CharField(50) | Optional role restriction |
| `is_active` | BooleanField | Whether the shortcut is currently served |
| `priority` | IntegerField | Higher values take precedence |
| `created_at` | DateTimeField | Auto-set on creation |
| `normalized_key` | CharField(50) | Auto-computed normalized key |

Database indexes:

- `(normalized_key, is_active)`
- `(scope, page, is_active)`
- `(role, is_active)`

Validation rules:

- `key_combination` is required
- if `scope = PAGE`, `page` must be non-empty
- if `scope = GLOBAL`, `page` must be empty

---

## Default Shortcut Reference Table

The following table lists the currently seeded shortcuts.

| # | Name | Key | Type | Value | Scope | Page | Role |
|---|---|---|---|---|---|---|---|
| 1 | Go to Home | `Ctrl+H` | URL | `/` | GLOBAL |  | all |
| 2 | Open Housing Module | `Ctrl+Shift+H` | URL | `/housing/` | GLOBAL |  | all |
| 3 | Open Accounting Module | `Ctrl+Shift+A` | URL | `/accounting/` | GLOBAL |  | all |
| 4 | Open Reports Module | `Ctrl+Shift+R` | URL | `/reports/` | GLOBAL |  | all |
| 5 | Open Billing Module | `Ctrl+Shift+B` | URL | `/billing/` | GLOBAL |  | all |
| 6 | Open Parking Module | `Ctrl+Shift+P` | URL | `/parking/` | GLOBAL |  | all |
| 7 | Open Members Module | `Ctrl+Shift+M` | URL | `/members/` | GLOBAL |  | all |
| 8 | Quick Search | `F2` | JS | `focusSearchField()` | GLOBAL |  | all |
| 9 | Create New Item | `F4` | JS | `openCreateModal()` | GLOBAL |  | all |
| 10 | View Dashboard | `F7` | URL | `/accounting/dashboard/` | GLOBAL |  | all |
| 11 | Open Voucher Entry | `Ctrl+Shift+V` | URL | `/accounting/vouchers/entry/` | GLOBAL |  | all |
| 12 | Print/Export | `F8` | JS | `triggerPrint()` | GLOBAL |  | all |
| 13 | Quick Help | `Ctrl+Q` | JS | `showShortcutHelp` | GLOBAL |  | all |
| 14 | Create Purchase Voucher | `F9` | URL | `/accounting/voucher/entry/?type=PURCHASE` | PAGE | `accounting` | all |
| 15 | Create Receipt Voucher | `F10` | URL | `/accounting/voucher/entry/?type=RECEIPT` | PAGE | `accounting` | all |
| 16 | Create Payment Voucher | `F11` | URL | `/accounting/voucher/entry/?type=PAYMENT` | PAGE | `accounting` | all |
| 17 | Create Journal Voucher | `F12` | URL | `/accounting/voucher/entry/?type=JOURNAL` | PAGE | `accounting` | all |
| 18 | Find Member Modal | `Ctrl+Shift+F` | MODAL | `/members/search/modal/` | GLOBAL |  | all |
| 19 | Email Compose Modal | `Ctrl+Shift+E` | MODAL | `/notifications/compose/modal/` | GLOBAL |  | all |
| 20 | New Notification Modal | `Ctrl+Shift+N` | MODAL | `/notifications/create/modal/` | GLOBAL |  | all |
| 21 | Show All Shortcuts | `Ctrl+Shift+?` | JS | `showShortcutHelp` | GLOBAL |  | all |
| 22 | Quick Society Switch | `Ctrl+Shift+S` | JS | `focusSocietySelector()` | GLOBAL |  | all |

Built-in shortcut, not in the database:

| Name | Key | Type | Value | Scope |
|---|---|---|---|---|
| Show Keyboard Shortcuts Help | `Ctrl+Q` | JS | `showShortcutHelp` | GLOBAL |

> Note: Item 13 and the built-in `Ctrl+Q` are functionally identical. The built-in injection ensures the help modal stays available even if the database shortcut is deactivated.
