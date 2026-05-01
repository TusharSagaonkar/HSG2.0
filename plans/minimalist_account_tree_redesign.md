# Minimalist Account Tree Page Redesign - Implementation Complete

## Objective
Redesign the account tree page (`/accounting/accounts/tree/`) with a complete minimalist approach focusing on:
- Simplified visual design (remove excess borders, shadows, decorations)
- Reduced information density (fewer badges/metadata by default)
- Streamlined tree structure (simpler indentation, clean collapse/expand)
- Read-only focus (remove/minimize action buttons)

## Implementation Status: ✅ COMPLETE

### Files Modified

1. **`accounting/templates/accounting/account_tree.html`** - Simplified markup
2. **`accounting/templates/accounting/partials/account_tree_node.html`** - Minimalist node
3. **`staticfiles/css/project.css`** - Added minimalist styles (lines ~930-1080)

## Changes Made

### 1. Page Layout (`account_tree.html`)

**Before:**
- Used `.quick-card` wrapper with border/shadow for summary
- Page actions as prominent buttons with `.btn` classes
- Society groups wrapped in `.quick-card` with badge counts

**After:**
```html
{% extends "base.html" %}
{% load i18n %}

{% block content %}
  <!-- Minimal page actions - subtle links -->
  <div class="tree-page-actions">
    <a href="{% url 'accounting:account-list' %}" class="tree-link">...</a>
    <a href="{% url 'accounting:dashboard' %}" class="tree-link">...</a>
  </div>

  <!-- Inline summary text, no card -->
  <div class="tree-summary">
    {{ total_accounts }} {% translate "accounts" %}
    {% if tree_groups|length > 1 %}
      {% translate "across" %} {{ tree_groups|length }} {% translate "societies" %}
    {% endif %}
  </div>

  <!-- Society groups - minimal headers, no cards -->
  {% for group in tree_groups %}
    <div class="tree-group">
      <div class="tree-group-header">
        <span class="tree-group-name">{{ group.society.name }}</span>
        <span class="tree-group-count">{{ group.nodes|length }} {% translate "root" %}</span>
      </div>
      <div class="tree-container">
        {% for node in group.nodes %}
          {% include "accounting/partials/account_tree_node.html" with node=node level=0 %}
        {% endfor %}
      </div>
    </div>
  {% endfor %}
{% endblock content %}
```

### 2. Tree Node Design (`account_tree_node.html`)

**Before:**
- Card-based design with `.account-tree-card` (border, background, shadow)
- Multiple colored badges (type, sub_type, is_active, system_protected, is_gst, is_bank, etc.)
- Always-visible "Ledger" button with `.btn` class
- FontAwesome icons for toggle and file/folder icons

**After:**
```html
<div class="tree-node level-{{ level }}{% if not node.children %} leaf{% endif %}">
  <div class="tree-node-content">
    {% if node.children %}
      <details class="tree-details" open>
        <summary class="tree-summary">
          <!-- Small text toggle instead of icon -->
          <span class="tree-toggle">›</span>
          <span class="tree-node-name">{{ node.account.name }}</span>
          <span class="tree-node-code">{{ node.account.code }}</span>
          <span class="tree-node-type">{{ node.account.get_account_type_display }}</span>
          <!-- Ledger link appears on hover only -->
          <div class="tree-node-actions">
            <a href="{% url 'accounting:account-ledger' node.account.pk %}" class="tree-action-link">
              {% translate "Ledger" %}
            </a>
          </div>
        </summary>
        <div class="tree-children">
          {% for child in node.children %}
            {% include "accounting/partials/account_tree_node.html" with node=child level=level|add:1 %}
          {% endfor %}
        </div>
      </details>
    {% else %}
      <div class="tree-leaf-content">
        <span class="tree-leaf-icon">•</span>
        <!-- Same minimal structure for leaves -->
      </div>
    {% endif %}
  </div>
</div>
```

### 3. CSS Architecture (Minimalist)

**Key CSS Classes Added to `project.css`:**

```css
/* Page Actions - subtle links instead of buttons */
.tree-page-actions {
  display: flex;
  gap: 1rem;
  margin-bottom: 1rem;
  font-size: 0.85rem;
}

.tree-link {
  color: var(--color-text-secondary);
  text-decoration: none;
}

/* Summary text - no card wrapper */
.tree-summary {
  font-size: 0.85rem;
  color: var(--color-text-muted);
  margin-bottom: 1.5rem;
}

/* Society group - minimal header with subtle border-bottom */
.tree-group-header {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border-bottom: 1px solid var(--color-border-subtle);
}

/* Tree node - no card, just padding and hover */
.tree-node {
  padding: 0.375rem 0;
}

.tree-node-content {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.9rem;
}

/* Toggle - small text character instead of icon */
.tree-toggle {
  width: 1rem;
  height: 1rem;
  font-size: 0.85rem;
  color: var(--color-text-muted);
}

.tree-details[open] > .tree-summary .tree-toggle {
  transform: rotate(90deg);
}

/* Text-only indicators instead of badges */
.tree-node-code {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  font-family: monospace;
}

.tree-node-type {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

/* Indentation without left border */
.tree-children {
  padding-left: 1.5rem;
}

/* Ledger link - show on hover only */
.tree-node-actions {
  margin-left: auto;
  opacity: 0;
  transition: opacity 0.15s ease;
}

.tree-node:hover .tree-node-actions {
  opacity: 1;
}

.tree-action-link {
  font-size: 0.75rem;
  color: var(--color-primary);
  text-decoration: none;
}

/* Minimal hover state */
.tree-node:hover {
  background: var(--color-bg-hover, rgba(0, 0, 0, 0.02));
  border-radius: 4px;
}
```

## Comparison: Before vs After

| Element | Before | After |
|---------|--------|-------|
| Container | `.quick-card` with border/shadow | No container, just padding |
| Node | `.account-tree-card` with border/bg | Plain div with padding |
| Toggle | FontAwesome `fa-chevron-right` icon | Small `›` text character |
| Name | Bold in card | Medium weight text |
| Code | Badge with background | Monospace text, muted color |
| Type | Colored badge | Uppercase text, muted color |
| Status badges | 6+ colored badges | **Removed entirely** |
| Actions | Always visible button | Show on hover only |
| Indentation | Left border + margin | Padding-left only |
| Children | Left border line | No border, just indent |
| Icons | `fa-folder`, `fa-file` | **Removed** |

## View Context

**No changes needed** - The `AccountTreeView` already provides minimal context:
- `tree_groups` - list of society groups with nodes
- `total_accounts` - count for summary

## Testing Checklist

- [ ] Page loads at `/accounting/accounts/tree/`
- [ ] No visual clutter (borders, shadows, excessive badges)
- [ ] Tree structure is immediately scannable
- [ ] Indentation clearly shows hierarchy
- [ ] Hover states work (background appears, ledger link shows)
- [ ] Expand/collapse works via details/summary
- [ ] Page works on mobile (320px+ width)
- [ ] Ledger links accessible on hover
- [ ] Multiple societies display correctly

## Responsive Behavior

- Tree indentation reduces on mobile: `padding-left: 1rem`
- Node content wraps on small screens
- Ledger links always visible on mobile (hover not available)

## Performance

- **No JavaScript required** - uses native `<details>`/`<summary>` for expand/collapse
- **No additional CSS files** - styles added to existing `project.css`
- **Minimal DOM** - removed badge elements, icon elements, card wrappers

## Future Enhancements (Optional)

1. Add keyboard navigation (arrow keys, Enter to toggle)
2. Add search/filter functionality
3. Add "copy code to clipboard" on click
4. Add right-click context menu for actions
5. Persist expand/collapse state in localStorage
