# Plan: Redesign Onboarding Step 8 — Structure → Units → Member CRUD

## Goal
Replace the flat `members_json` JS-grid on Step 8 (`/onboarding/<wid>/step/8/`) with a two-pane UI:
- **Left pane**: flat list of the society's Structures (click to filter).
- **Right pane**: unit cards for the selected structure; each card shows its existing members with inline Edit/Delete, plus an **Add Member** button that opens a modal pre-filled with society + that unit.

Members persist immediately via AJAX (real CRUD). "Save & Continue" only advances the wizard.

## Confirmed decisions
1. **Persistence**: Immediate AJAX per member (create/update/delete hit the DB at once; revisit shows saved members).
2. **Structure tree**: Flat list, click-to-filter the right pane.
3. **Unit panel**: Unit cards with inline members + per-unit Add button.
4. **Modal fields (lean)**: `full_name`, `role` (OWNER/TENANT/NOMINEE), `email`, `phone`, `status` (ACTIVE/INACTIVE), `start_date`. Society + unit pre-filled & locked (hidden). Receivable account auto-set for owners (existing `MemberForm` behavior).
5. **AJAX endpoints**: New JSON endpoints in the `onboarding` app, scoped to the wizard.
6. **Delete**: Hard-delete the `Member` row **and** clean up the active `UnitOwnership`/`UnitOccupancy` rows created for that member's unit + user.

## Additional decisions (reasonable defaults — flag if you disagree)
7. **"Save & Continue" becomes a no-form advance**: members are already persisted via AJAX, so Step 8 no longer calls `SocietySetupService.assign_members`. Step 8 is removed from `STEP_FORMS` (like steps 5/9/10/11) and `_handle_no_form_step` advances it. **Server-side guard**: advance is blocked if no `Member` with `status=ACTIVE` exists for `wizard.society` (mirrors the Step 7 empty-grid guard at `onboarding/views.py:547-572`).
8. **GET context** mirrors Step 7's `_step7_structure_context`: pass `structures` (with `units` prefetched) and `members_by_unit` (dict `{unit_id: [member, ...]}`) to the template. This gives pre-fill on revisit and avoids an extra AJAX round-trip on load.
9. **Reuse `MemberForm`** (`housing/forms.py:525`) in the JSON create/update endpoints for validation + `sync_member_unit_lifecycle`, passing `society=wizard.society` and forcing `unit`. Keeps receivable-account auto-assignment and field validation consistent with the standalone member form.
10. **Lifecycle cleanup on delete**: end-date (set `end_date=today`) the active `UnitOwnership` and `UnitOccupancy` rows for the member's `unit` where `owner`/`occupant == member.user`, **then** hard-delete the `Member`. End-dating (not hard-deleting the lifecycle rows) is consistent with how `_sync_occupancy` already manages lifecycle (`membership_lifecycle.py:110-125` — it end-dates, never hard-deletes) and preserves audit history. `Member.receivable_account` (PROTECT) does not block deletion (the reference is on the member side).

## Affected files

### Backend — new
- `onboarding/views.py` — add 4 JSON view functions (create/update/delete/list member) + a GET-context helper `_step8_member_context(wizard)`.
- `onboarding/urls.py` — add 4 URL patterns under `<int:wizard_id>/step/8/api/members/...`.

### Backend — modify
- `onboarding/views.py`:
  - `wizard_step` (≈line 438-468): add `elif step_number == 8:` branch calling `_step8_member_context(wizard)`.
  - Remove `8` from `STEP_FORMS` dict (≈line 68-77) so step 8 is a no-form step.
  - `_handle_no_form_step` (≈line 599): add step-8 branch doing the ≥1-active-member guard.
  - `wizard_step_save` (≈line 574-580): drop the step-8 form-rendering branches (no longer needed since no form).
- `onboarding/forms.py`: `MemberAssignmentForm` (lines 753-817) becomes dead code — leave in place (out of scope to delete; not referenced once step 8 leaves `STEP_FORMS`). Optionally remove later.

### Templates — new
- `onboarding/templates/onboarding/steps/step_member_assignment.html` — full rewrite (two-pane + modal).
- `onboarding/templates/onboarding/partials/member_modal.html` (optional split) — the Bootstrap modal form. May also be inlined in the step template.

### No DB migration needed
All models exist; no schema changes.

## Endpoint contracts (all `@login_required`, JSON, scoped to `wizard.society`)

All endpoints load the wizard via `_get_wizard(wizard_id, request.user)` and reject if `wizard.status != IN_PROGRESS` (403 JSON). All require `wizard.society` to exist (else 400).

### `GET /onboarding/<wid>/step/8/api/members/`
Returns members grouped for the UI. Response:
```json
{
  "structures": [
    {"id": 1, "name": "Tower A", "structure_type": "BUILDING",
     "units": [{"id": 10, "identifier": "101", "unit_type": "FLAT", "area_sqft": "1200.00"}]}
  ],
  "members_by_unit": {"10": [{"id": 5, "full_name": "John", "role": "OWNER", "email": "...", "phone": "...", "status": "ACTIVE", "start_date": "2026-07-25"}]}
}
```

### `POST /onboarding/<wid>/step/8/api/members/`
Body: `{"unit_id": 10, "full_name", "role", "email", "phone", "status", "start_date"}`.
Instantiates `MemberForm(data, society=wizard.society, current_user=request.user)`, forces `unit=Unit.objects.get(pk=unit_id, structure__society=wizard.society)`. On valid: save member, call `sync_member_unit_lifecycle(member)`, return `{ok: true, member: {...}}`. On invalid: 400 with `{errors: {field: [msg]}}`.

### `PATCH /onboarding/<wid>/step/8/api/members/<mid>/`
Body: subset of fields. Loads `Member` (must belong to `wizard.society`). Binds `MemberForm` with `instance=member`, forces society+unit. On valid: save; if role/unit/start_date changed, re-call `sync_member_unit_lifecycle` to reconcile. Return `{ok, member}` / 400 errors.

### `DELETE /onboarding/<wid>/step/8/api/members/<mid>/`
Loads `Member` (scoped to `wizard.society`). In a transaction:
1. If `member.user_id`: end-date active `UnitOwnership` rows for `unit=member.unit, owner=member.user, end_date__isnull=True` (set `end_date=today`); end-date active `UnitOccupancy` rows for `unit=member.unit, occupant=member.user, end_date__isnull=True`.
2. `member.delete()` (cascades to `Nominee` rows via `Nominee.member` CASCADE — acceptable in onboarding seed context).
Return `{ok: true}`. 404 if not found / not in society.

CSRF: all mutating endpoints require the `X-CSRFToken` header (JS reads `csrftoken` cookie, as `member_add_modal.html` does today).

## Template UI (Bootstrap 5 + KaiAdmin + custom `content-card` classes)

### Layout
```
content-card
  content-card__body
    [intro text]
    .row
      .col-md-3  → Structure list (list-group, click selects, active class)
      .col-md-9  → Units panel
                    - if no structure selected: "Select a structure" placeholder
                    - else: cards per unit
                        card: identifier + type + area badge
                        card-body: list of members (name, role badge, email/phone, Edit/Delete btns)
                                   [Add Member] btn (data-unit-id, data-unit-label)
                    - empty-state if structure has no units
    form-actions: [Save & Continue] [Back]
```

### Modal (`#memberModal`, Bootstrap modal)
- Hidden fields: `society` (locked), `unit` (locked, set by which Add/Edit button was clicked), `pk` (set on edit, blank on add).
- Visible: `full_name`*, `role`* (select), `email`, `phone`, `status` (select), `start_date` (date).
- Read-only banner: "Unit: 101 (Tower A)" so the user sees the pre-filled context.
- Submit → `fetch` to create (POST) or update (PATCH). On success: close modal, refresh that unit's member list (re-fetch `GET /api/members/` and re-render, or splice the returned member into the DOM). On error: show field errors inline.

### Inline JS (vanilla, in `{% block inline_javascript %}`)
- On load: fetch `GET /api/members/`, render structure list + default-select first structure, render its units + members.
- Structure click: re-render units panel.
- Add button: open modal blank, set hidden `unit_id`/`unit_label` from `data-*`.
- Edit button: open modal, fetch nothing extra (member data already in DOM via `data-*` on the row), populate fields.
- Delete button: `confirm()` → `DELETE` fetch → on success remove row; if unit now has no members, show empty member area (unit card stays).
- Save & Continue: posts to `wizard-step-save` (standard form post, no members_json). Server advances if ≥1 active member exists.

## Validation plan
- Create a member via modal for a unit → assert `Member` row + `UnitOwnership`/`UnitOccupancy` rows exist (for owner).
- Edit member role OWNER→TENANT → assert occupancy row flipped to TENANT.
- Delete member → assert `Member` gone and active ownership/occupancy end-dated.
- Revisit Step 8 after saving → existing members pre-filled under their units.
- "Save & Continue" with zero active members → blocked with message; with ≥1 → advances to Step 9.
- Cross-society isolation: member from society A not visible/editable in wizard for society B (404 on delete/edit).

## Risks / open notes
- **Member.user auto-provision**: `sync_member_unit_lifecycle` auto-creates a `User` from `member.email` when an owner has no user (`membership_lifecycle.py:14-33`). With repeated add/edit, this may create duplicate or stale users. The create path reuses `MemberForm` + `get_or_create`-like behavior, so duplicates are bounded by the `unique_together = (society, unit, full_name, role)` constraint — adding a duplicate (same unit+name+role) will raise an integrity/validation error surfaced in the modal. Acceptable for onboarding.
- **Nominee cascade on delete**: hard-deleting a `Member` cascades to its `Nominee` rows. In onboarding seed context this is acceptable; in production (post-onboarding) it would lose nominee history. Flagging — if nominees exist, the delete endpoint should still proceed but this is the documented trade-off.
- **`MemberAssignmentForm` dead code**: leaving it in `forms.py` is harmless. Removal is a separate cleanup, out of scope here.
- **No migrations**: confirmed — no schema change.

## Implementation task order
1. Add `_step8_member_context(wizard)` + wire into `wizard_step` GET (pass structures + members_by_unit).
2. Add the 4 JSON endpoints + URL patterns (create/update/delete/list).
3. Add the ≥1-active-member guard in `_handle_no_form_step` for step 8; remove `8` from `STEP_FORMS`.
4. Rewrite `step_member_assignment.html` (two-pane + modal + inline JS).
5. Manual verification per the validation plan above.
