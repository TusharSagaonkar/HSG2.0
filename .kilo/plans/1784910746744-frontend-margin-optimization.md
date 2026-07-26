# Frontend Margin Optimization (Global Chrome)

## Goal
Reclaim wasted screen space caused by compounding margins/padding in the global
layout chrome — without touching per-page templates or breaking htmx navigation,
print styles, or visual hierarchy.

## Scope (locked)
**Global chrome only.** Edits confined to `base.html` + shared CSS
(`project.css`, `components.css`). Per-template `mb-4`/`g-3` sweeps, row-gutter
global changes, fixed-header offset, and deep mobile rework are **out of scope**.

## Problem Analysis (margin-stacking chain, desktop ≥992px)
Reading order from `base.html` + kaiadmin.css:

1. `.main-panel > .container` → `margin-top: 69px` (fixed-header offset, leave alone).
2. `.page-inner` (kaiadmin) → `padding-right/left: 30px` at ≥576px
   (`project.css:1151` only overrides top/bottom). **60px horizontal waste.**
3. `.dashboard-content-card.card` wrapper (`base.html:365`, `project.css:858`)
   → `border + box-shadow + border-radius:18px + margin-bottom:1.5rem`.
4. `.card-body#workspace` (`base.html:366`) → ~1rem L/R + 1rem padding around
   **all** page content. Redundant: inner components (`.action-bar`,
   `.content-card`, `.stat-card`) already self-pad → **double padding**.
5. `dashboard_subtitle` footer (`base.html:400-404`) → `pt-3 pb-2` wrapper + `h6`
   rendered *below* content → ~40px wasted at page bottom, poor placement.

Net: ~92px horizontal + ~80px+ vertical lost per page before any content.

## Key Constraint
htmx nav links use `hx-target="#workspace" hx-select="#workspace" hx-swap="innerHTML"`.
`#workspace` element **must remain** (id + `tabindex="-1"`). Only its innerHTML
swaps on navigation; the outer wrapper classes persist across swaps. Flattening
the wrapper edits base.html once and holds.

## Decisions
1. **Flatten `#workspace` wrapper** — remove `.dashboard-content-card.card.card-round`
   and `.card-body` classes; keep `<div id="workspace" tabindex="-1" class="workspace">`.
   `.workspace { padding: 0 }` lets page components own their insets.
2. **Remove** `.dashboard-content-card.card` rule in `project.css:858-864`.
3. **Tighten `.page-inner`** (`project.css:1151`) — L/R `16px` (`--space-md`),
   top `0.5rem`, bottom `0.75rem`.
4. **Relocate subtitle (also fixes stale-on-nav bug)** — the `dashboard_subtitle`
   block currently renders at base.html:402, **outside** `#workspace`. Since nav
   links use `hx-select="#workspace"` + `hx-swap="innerHTML"`, only `#workspace`'s
   innerHTML swaps on navigation → subtitle is static/stale today. Move the block
   **inside** `#workspace` as its first child (before `{% block main %}`), wrapped
   in `<p class="page-subtitle">…</p>`. Delete the bottom `pt-3 pb-2` footer
   wrapper (lines 400-404). Because the subtitle is now part of the swapped
   fragment, each page's `{% block dashboard_subtitle %}` override flows in
   correctly on both full load and htmx nav. Follows the existing `.page-inner`
   chrome pattern (gateops_nav include sits in `.page-inner`).
5. **Compact shared components** via existing design tokens:
   - `.action-bar` (`components.css:73`): `margin-bottom` `--space-lg`→`--space-sm`;
     `padding` `--space-md`→`--space-sm --space-md`.
   - `.content-card__body` (`components.css:184`): `1rem 1.1rem 1.1rem`→`0.85rem 0.95rem 0.9rem`.
   - `.stat-card` (`components.css:198`): `1rem 1.05rem`→`0.85rem 0.9rem`.
   - `.page-inner .table > :not(caption) > * > *` (`project.css:1122`):
     `0.85rem 0.95rem`→`0.7rem 0.8rem`.
6. Add `.page-subtitle` + `.workspace` rules in `project.css` using tokens
   (`--color-text-secondary`, `--font-size-sm`, `--space-sm`). No `design-tokens.css` change.

## Files to Edit
- `housing_accounting/templates/base.html` — flatten `#workspace` wrapper (lines 365-366),
  relocate `dashboard_subtitle` block + remove bottom footer wrapper (lines 400-404).
- `housing_accounting/static/css/project.css` — tighten `.page-inner` (1151),
  remove `.dashboard-content-card.card` (858-864), add `.workspace` + `.page-subtitle`,
  denser table cells (1122).
- `housing_accounting/static/css/components.css` — compact `.action-bar`,
  `.content-card__body`, `.stat-card`.

## Risks & Mitigations
- **Pages with no inner card** (e.g. default `content` fallback quick-cards) lose
  the outer card frame → they sit on the body gradient. Acceptable; most pages
  have inner structure. `.workspace { padding:0 }` keeps content at the 16px
  page-inner inset (clean, not flush-to-viewport).
- **Subtitle line adds ~24px at top** vs ~40px removed at bottom → net reclaim + better placement.
  Keep it tight (single line, `font-size-sm`, `mb: --space-sm`).
- **Table density** slightly reduces row height → keep modest (`0.7rem 0.8rem`);
  no wrapping breakage expected.
- **Mobile** (≤991px): kaiadmin sets `.page-inner` 15px L/R; project.css override
  (16px) loads later and wins — consistent, no breakage. Existing mobile media
  queries in `project.css` (1884+) still apply.
- **Print**: `project.css:1429` `@media print` already flattens everything; unaffected.

## Validation
1. Run the project test command (pytest / `python manage.py test`) — confirm no
   template/CSS-dependent test regressions.
2. Start dev server and visually verify these representative pages at desktop +
   mobile widths:
   - Dashboard (`home`)
   - Societies list (`housing:society-list`) — stat cards + card grid
   - Form (`housing:form.html`) — action-bar + content-card nesting
   - Reconciliation manual workspace — dense table
   - Reports index — report cards
3. Verify htmx nav: click sidebar links → `#workspace` innerHTML swaps correctly,
   no layout jump, no content under the fixed header.
4. Verify subtitle renders at top (compact), bottom footer gone.
5. Verify print preview still clean.
6. Check `git diff` is limited to the 3 files above.
