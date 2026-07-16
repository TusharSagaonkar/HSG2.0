# Logic and Architecture

Last updated: `2026-07-11`

## System Logic Model

The platform follows a domain-oriented structure where each app owns its data and business rules:

- `housing`: Society, structure, unit, ownership, occupancy foundations.
- `accounting`: Accounts, vouchers, posting workflow, period control.
- `billing`: Bill lifecycle and charge application.
- `receipts`: Payment capture and allocation.
- `notifications`: Reminder and communication workflows.
- `members`: Member profile and context usage.
- `gateops`: Gate operations — visitor lifecycle, rule engine, vehicle, material, parcel, and contractor management.

## Cross-Domain Flow (High Level)

1. Housing defines valid society and unit context.
2. Accounting provides valid ledgers and period controls.
3. Billing raises receivables for members/units.
4. Receipts settle receivables through allocations.
5. Notifications drive follow-up and reminders.

## Core Business Invariants

| Domain | Invariant | Why It Matters |
| --- | --- | --- |
| Accounting | Voucher posting must preserve double-entry balance. | Prevents ledger corruption. |
| Accounting | Closed periods block posting changes. | Protects historical integrity. |
| Billing | Bill totals must equal sum of bill lines. | Financial accuracy and auditability. |
| Billing | Charge templates are versioned by effective dates, and used versions are immutable. | Preserves audit trail and reproducibility of prior periods. |
| Billing | Bill lines store calculation snapshots (rate, quantity, charge type, late-fee basis). | Prevents retroactive amount drift after rule or unit data changes. |
| Receipts | Allocation total cannot exceed receipt amount. | Prevents over-allocation errors. |
| Housing | Unit/society relationships must remain valid and consistent. | Avoids orphaned financial records. |
| GateOps | Active worker count on a contract cannot exceed `max_workers`. | Enforces labour-count ceiling and prevents unbounded contractor enrolment. |
| GateOps | Worker `person` must belong to the same society as the worker profile. | Prevents cross-society visitor data leaks via contractor enrolment. |
| GateOps | Expired contracts transition to `COMPLETED` and expired work permits to `EXPIRED` atomically. | Keeps engagement lifecycle consistent with date windows and drives rule-engine expiry conditions. |
| GateOps | Notification dispatch never blocks a gate operation. | Notifications are a side-effect of gate transitions, not a precondition — a failure must not prevent arrival/entry/exit. |
| GateOps | Notification routing honours per-society, per-visitor-category preferences (channel, trigger, silent, bundle window). | Enforces the "no spam" philosophy and lets each society tune notification behaviour per visitor type. |

## Billing Rule Versioning Model

1. `ChargeTemplate` defines rule semantics (`charge_type`, `rate`, frequency, accounts).
2. `effective_from` and `effective_to` define the validity window of that version.
3. Rule changes create a new version; old versions are closed instead of edited.
4. Bill generation resolves template versions by billing period date.
5. `BillLine` stores execution snapshots so posted bills remain historically stable.

## UI Interaction Logic

Global layout interactions (sidebar/topbar toggles) must behave consistently across pages using shared base template hooks and defensive client-side handlers.
Sidebar navigation should separate business capabilities into distinct tabs/sections (Society, Structure & Units, Members, Billing, Receipts, Reminders, Accounting) to keep workflows discoverable as modules expand.
Low-frequency actions should be nested in collapsible submenus under each parent domain to keep the default navigation compact.
Parent menu click should route to the primary child view; submenu expansion should be controlled only by the caret toggle.

## Architecture Constraints

1. Avoid direct cross-app model coupling that bypasses domain boundaries.
2. Keep policy checks in the owning domain service/view logic.
3. Maintain deterministic behavior for posting, billing, and allocation operations.
4. Prefer explicit state transitions over implicit side effects.

## Documentation Rule for Logic Changes

For each logic change:

1. Update the affected invariant row or add a new one.
2. Record strategy impact in `STRATEGY_CHANGELOG.md` if architecture direction changes.
3. Record challenge details in `CHALLENGES_AND_DECISIONS.md` if issue-driven.

---

## Contractor Management (GateOps Phase 9)

### Architecture Overview

Contractor management introduces a 4-model hierarchy for tracking external contracting companies and their labour engagements against the gate operations lifecycle:

```
Contractor (company master)
   └── Contract (work engagement, date window, max_workers)
          ├── Worker (Person composition — labourer enrolment)
          └── WorkPermit (time-bound authorization, safety docs)
```

- **4-model hierarchy:** [`Contractor`](gateops/models/model_Contractor.py:6) → [`Contract`](gateops/models/model_Contract.py:6) → [`Worker`](gateops/models/model_Worker.py:6) / [`WorkPermit`](gateops/models/model_WorkPermit.py:6). A `Contractor` is the company master record; a `Contract` is a specific engagement; `Worker` and `WorkPermit` hang off a `Contract`.
- **Person composition:** [`Worker`](gateops/models/model_Worker.py:6) uses composition (FK to [`Person`](gateops/models/model_Person.py) with `on_delete=PROTECT`) rather than inheritance. A person may be a worker on one contract and a regular visitor on another; deleting a worker profile must not destroy the underlying deduplicated person master record.
- **GateEvent extension:** [`GateEvent`](gateops/models/model_GateEvent.py:120) is extended with additive nullable FKs (`contractor`, `contract`, `work_permit`) using `on_delete=SET_NULL` to preserve historical events even if a contractor/contract/permit is later deleted.
- **Service pattern:** [`ContractorService`](gateops/services/contractor_service.py:83) follows the established service contract — all `@staticmethod`, keyword-only args, `@transaction.atomic`, race-safe updates via `QuerySet.update()`, and append-only audit logging via [`GateOpsAuditLog`](gateops/models/model_GateOpsAuditLog.py). No caller mutates these models directly.

### Rule Engine Integration

The [`CONTRACTOR_EXPIRY`](gateops/models/model_RuleCondition.py:36) condition field was pre-built in Phase 2 (Rule Engine) but remained inert — it resolved to `None` because no context key populated it. Phase 9 activates it:

- [`GateEventLifecycleService._build_rule_context()`](gateops/services/gate_event_lifecycle.py:704) now populates the `contractor_expiry` context key by calling [`_build_contractor_expiry_context()`](gateops/services/gate_event_lifecycle.py:762).
- The context is populated when the event's visitor category is a contractor category (`VisitorCategory.is_contractor=True`) **or** a `contractor`/`contract` FK is directly linked. When no contractor context exists, the value is `None` so `IS_FALSE` conditions match (no expiry concern) and `IS_TRUE` conditions do not.
- The [`RuleEngineService`](gateops/services/rule_engine.py:105) maps `CONTRACTOR_EXPIRY` conditions to the `contractor_expiry` context key.

Context provided:

| Key | Type | Description |
| --- | --- | --- |
| `contract_expired` | `bool` | `contract.end_date < today` |
| `permit_expired` | `bool` | `work_permit.expires_at < now` |
| `days_until_contract_expiry` | `int\|None` | Days until contract expiry (negative if expired) |
| `days_until_permit_expiry` | `int\|None` | Days until permit expiry (negative if expired) |
| `has_active_permit` | `bool` | Whether an `ACTIVE` work permit is linked/found |

### Expiry Processing

[`ContractorService`](gateops/services/contractor_service.py:83) provides a layered expiry API:

| Method | File | Purpose |
| --- | --- | --- |
| [`process_expiries()`](gateops/services/contractor_service.py:673) | [`contractor_service.py`](gateops/services/contractor_service.py) | Atomically transitions expired contracts to `COMPLETED` and expired work permits to `EXPIRED` in a single `@transaction.atomic` block. Returns `{"contracts_marked_completed": int, "work_permits_marked_expired": int}`. |
| [`get_expired_contracts()`](gateops/services/contractor_service.py:640) | [`contractor_service.py`](gateops/services/contractor_service.py) | Queries `ACTIVE` contracts whose `end_date` has passed (society-scoped). |
| [`get_expired_work_permits()`](gateops/services/contractor_service.py:656) | [`contractor_service.py`](gateops/services/contractor_service.py) | Queries `ACTIVE` work permits whose `expires_at` has passed (society-scoped). |
| [`check_contract_expiry()`](gateops/services/contractor_service.py:603) | [`contractor_service.py`](gateops/services/contractor_service.py) | Individual contract expiry check — returns `is_expired`, `days_until_expiry`, `expiry_date`. |
| [`check_work_permit_expiry()`](gateops/services/contractor_service.py:621) | [`contractor_service.py`](gateops/services/contractor_service.py) | Individual work permit expiry check — returns `is_expired`, `days_until_expiry`, `expiry_datetime`. |

### Labour Count Enforcement

The `max_workers` field on [`Contract`](gateops/models/model_Contract.py:39) sets the labour ceiling:

- [`register_worker()`](gateops/services/contractor_service.py:372) validates `active_count < max_workers` before creating a `Worker` row, raising `ValidationError` when the limit is reached. It also rejects enrolment on non-`ACTIVE` contracts.
- [`is_labour_limit_exceeded()`](gateops/services/contractor_service.py:911) provides a pre-check returning `True` when the active worker count has reached `max_workers`.
- [`get_labour_count()`](gateops/services/contractor_service.py:906) returns the active worker count for a contract.

### Attendance

Worker attendance flows through the existing [`GateEvent`](gateops/models/model_GateEvent.py) lifecycle, delegating to [`GateEventLifecycleService`](gateops/services/gate_event_lifecycle.py):

| Method | File | Behaviour |
| --- | --- | --- |
| [`check_in_worker()`](gateops/services/contractor_service.py:761) | [`contractor_service.py`](gateops/services/contractor_service.py) | Creates a `GateEvent` via `GateEventLifecycleService.create_invitation()` + `record_arrival()`, then attaches the `contractor`, `contract`, and `work_permit` FKs (derived from `worker.contract`) so the rule engine and attendance queries can resolve the contractor context. |
| [`check_out_worker()`](gateops/services/contractor_service.py:844) | [`contractor_service.py`](gateops/services/contractor_service.py) | Finds the worker's most recent `ENTERED` gate event (not yet exited) and transitions it to `EXITED` via `GateEventLifecycleService.record_exit()`. Raises `ValidationError` if no active on-site event exists. |
| [`get_active_workers_on_site()`](gateops/services/contractor_service.py:886) | [`contractor_service.py`](gateops/services/contractor_service.py) | Queries `GateEvent` rows where `contractor` is set, `status=ENTERED`, and `exited_at` is null — i.e. workers currently on site. |

### Multi-Tenancy

All four models are society-scoped with `society = ForeignKey("housing.Society", on_delete=CASCADE)`, following the established multi-tenancy convention (see [`societies/middleware.py`](societies/middleware.py)):

- **Conditional unique constraints** enforce uniqueness only among `is_active=True` rows, so soft-deleted names/titles/permit numbers can be reused:
  - `unique_active_contractor_name_per_society` on `(society, company_name)`
  - `unique_active_contract_title_per_society` on `(society, contractor, title)`
  - `unique_active_worker_per_contract` on `(society, contract, person)`
  - `unique_active_workpermit_number_per_society` on `(society, permit_number)`
- **Cross-society data leak prevention** in [`Worker.clean()`](gateops/models/model_Worker.py:81): the `person` must belong to the same society as the worker profile. The service layer mirrors this check in [`register_worker()`](gateops/services/contractor_service.py:395).
- **Compound indexes** on `(society, ...)` follow the existing pattern (see [`societies/models/model_Membership.py`](societies/models/model_Membership.py)).
- **Form querysets** in [`ContractorForm`](gateops/forms.py:752), [`ContractForm`](gateops/forms.py:802), [`WorkerForm`](gateops/forms.py:856), and [`WorkPermitForm`](gateops/forms.py:898) are narrowed to the current society's active rows in `__init__` so cross-tenant data cannot leak via dropdowns.

### URL Routes

21 routes under [`gateops/urls.py`](gateops/urls.py:65), grouped by resource:

| Prefix | Routes | Names |
| --- | --- | --- |
| `/contractors/` | list, detail, create, edit, deactivate | `contractor-list`, `contractor-detail`, `contractor-create`, `contractor-edit`, `contractor-deactivate` |
| `/contracts/` | list, detail, create, edit, deactivate | `contract-list`, `contract-detail`, `contract-create`, `contract-edit`, `contract-deactivate` |
| `/workers/` | list, detail, register, edit, deactivate | `worker-list`, `worker-detail`, `worker-register`, `worker-edit`, `worker-deactivate` |
| `/work-permits/` | list, detail, issue, edit, revoke | `work-permit-list`, `work-permit-detail`, `work-permit-issue`, `work-permit-edit`, `work-permit-revoke` |
| `/contractor-dashboard/` | dashboard | `contractor-dashboard` |

---

## Smart Notification Engine (GateOps Phase 10)

### Architecture Overview

The Smart Notification Engine introduces [`NotificationEngineService`](gateops/services/notification_engine.py:65) as the single authority over gate-event notification routing, bundling, and dispatch. It sits alongside [`GateEventLifecycleService`](gateops/services/gate_event_lifecycle.py:63) in the gateops service layer and is invoked as a non-blocking side-effect of lifecycle transitions — never as a precondition. No caller creates [`NotificationBundle`](gateops/models/model_NotificationBundle.py:9) rows directly; every notification operation flows through the service so that multi-tenant safety, host resolution, preference honouring, repeat suppression, bundling, and audit logging are consistently applied.

```
GateEventLifecycleService (state machine)
   │
   ├── record_arrival() ──┐
   ├── approve()          ├──> _notify(event, trigger, actor)
   ├── record_entry()    │         │
   ├── record_exit()     │         └──> NotificationEngineService.dispatch_for_event()
   └── auto_close()     │                   │
                        │                   ├── resolve_host() (occupancy → ownership → member)
                        │                   ├── get_preference_for_trigger()
                        │                   ├── _is_duplicate_notification() (5-min window)
                        │                   ├── _find_or_create_bundle() (race-safe, select_for_update)
                        │                   ├── _dispatch_email() → queue_email() → EmailQueue
                        │                   └── _log_audit() → GateOpsAuditLog
                        │
   └── evaluate_rules() else branch
              │
              └──> NotificationEngineService.dispatch_for_rule_action()
                        ├── SEND_NOTIFICATION (per-channel or preference fallback)
                        ├── NOTIFY_SECURITY (SMS bundle, PENDING)
                        └── ESCALATE (PUSH bundle, PENDING)
```

- **Service pattern:** [`NotificationEngineService`](gateops/services/notification_engine.py:65) follows the established service contract — all `@staticmethod`, keyword-only args, `@transaction.atomic` on bundling/flush operations, race-safe bundle creation via `select_for_update()`, and append-only audit logging via [`GateOpsAuditLog`](gateops/models/model_GateOpsAuditLog.py). No shared mutable state.
- **Failure resilience:** the main entry point ([`dispatch_for_event()`](gateops/services/notification_engine.py:255)) and the rule-action dispatcher ([`dispatch_for_rule_action()`](gateops/services/notification_engine.py:675)) are both wrapped in try/except so notification failures **never** block gate operations. The lifecycle helper ([`_notify()`](gateops/services/gate_event_lifecycle.py:647)) adds a second layer of try/except so even import-time or signature errors cannot propagate into the lifecycle. Audit-log writes ([`_log_audit()`](gateops/services/notification_engine.py:927)) are similarly wrapped so a logging failure never blocks a legitimate notification operation.
- **Channel readiness:** only `Channel.EMAIL` has delivery infrastructure (via [`queue_email()`](notifications/services.py) → [`EmailQueue`](notifications/models/model_EmailQueue.py)). Push/SMS/WhatsApp/Voice channels create a `PENDING` bundle and log a warning — a placeholder until the transport layer is built.

### Host Resolution Chain

[`resolve_host()`](gateops/services/notification_engine.py:82) resolves who should be notified for a gate event, using a first-match-wins fallback chain anchored on `event.host_unit` (the unit the visitor is visiting, cached on [`GateEvent`](gateops/models/model_GateEvent.py:147) at arrival time):

1. **[`UnitOccupancy`](housing/models.py)** — active occupancy (`end_date__isnull=True`), excluding `VACANT`. The current occupant (tenant or owner-occupant) is the recipient.
2. **[`UnitOwnership`](housing/models.py)** — active `PRIMARY` owner (`end_date__isnull=True`).
3. **[`Member`](housing/models.py)** — active member (`status=ACTIVE`, `end_date__isnull=True`), ordered by role (`OWNER` first).

Returns `None` when `event.host_unit` is not set or no host can be resolved from any source. Contact details (email/phone/name) are resolved via [`_resolve_user_contact()`](gateops/services/notification_engine.py:178), which prefers an active `Member` row for the same user + unit and falls back to the `User`'s own attributes.

### Smart Routing

[`dispatch_for_event()`](gateops/services/notification_engine.py:255) is the main entry point, called by lifecycle hooks. It applies an 8-step pipeline:

1. Resolve the [`NotificationPreference`](gateops/models/model_NotificationPreference.py) for the event's society + visitor_category + trigger. If none exists, no notification is sent.
2. Suppress if the preference channel is `NONE` or the trigger is `NEVER` (silent entry).
3. If the preference is in silent mode (`is_silent=True`), create a `SKIPPED` bundle for traceability and return it.
4. Repeat suppression: if a notification was already sent for this person + trigger within the duplicate window, skip silently.
5. Resolve the host (recipient). If no host can be resolved, create a `SKIPPED` bundle and return it.
6. Find or create a bundle (honouring the bundling window).
7. Dispatch via the appropriate channel. For `EMAIL` with no bundling window, dispatch immediately. For `EMAIL` with a bundling window, leave the bundle `PENDING` (flushed later by [`flush_pending_bundles()`](gateops/services/notification_engine.py:603)). For other channels, leave `PENDING` and log a warning.
8. Audit the dispatch.

Channel selection is preference-driven, not hard-coded. Each society configures a [`NotificationPreference`](gateops/models/model_NotificationPreference.py) per visitor category, choosing from `PUSH`/`SMS`/`WHATSAPP`/`EMAIL`/`VOICE`/`NONE`. The `NONE` channel and `NEVER` trigger both suppress notification entirely — the former for "this category should not notify on this channel", the latter for "this category should not notify at all" (silent entry).

### Bundling

When a [`NotificationPreference`](gateops/models/model_NotificationPreference.py) has a non-zero `bundle_window_minutes`, individual gate events for the same society + visitor_category + host_unit + trigger + channel are accumulated into a single [`NotificationBundle`](gateops/models/model_NotificationBundle.py:9) and dispatched together once the window elapses. This is the primary "no spam" mechanism for high-frequency, low-urgency categories (e.g. `DELIVERY` defaults to a 30-minute window so a resident gets one digest instead of a push per parcel).

- [`_find_or_create_bundle()`](gateops/services/notification_engine.py:477) looks for an existing `PENDING` bundle within the window using `select_for_update()` for race-safety. If found, the event is added to the bundle's `gate_events` M2M; otherwise a new `PENDING` bundle is created.
- [`flush_bundle()`](gateops/services/notification_engine.py:532) dispatches a single `PENDING` bundle (resolves the host from the first event, queues the email, sets `status=SENT` + `dispatched_at`).
- [`flush_pending_bundles()`](gateops/services/notification_engine.py:603) flushes all `PENDING` bundles whose window has elapsed for a society and returns the count.
- A window of `0` means "dispatch immediately" — no bundling, the email is queued inline during `dispatch_for_event()`.

### Repeat Suppression

[`_is_duplicate_notification()`](gateops/services/notification_engine.py:642) prevents burst notifications for the same visitor. It queries for a `PENDING` or `SENT` bundle linked to an event with the same `person`, within a suppression window (default 5 minutes, [`DEFAULT_DUPLICATE_WINDOW_MINUTES`](gateops/services/notification_engine.py:62)). When `event.person` is `None`, the check is skipped — without a person anchor, deduplication is impossible.

### Silent Entry

A visitor category can be configured with `trigger=NEVER` to suppress notification entirely (silent entry). This is distinct from `channel=NONE` (suppress on a specific channel) and `is_silent=True` (create a `SKIPPED` bundle for traceability without dispatching). The three mechanisms compose:

| Mechanism | Effect | Bundle created? |
| --- | --- | --- |
| `trigger=NEVER` | No notification for this trigger | No |
| `channel=NONE` | No notification on any channel | No |
| `is_silent=True` | `SKIPPED` bundle for traceability, no dispatch | Yes (`SKIPPED`) |

### Rule Action Integration

[`evaluate_rules()`](gateops/services/gate_event_lifecycle.py:227) dispatches notifications for three rule-action types via [`dispatch_for_rule_action()`](gateops/services/notification_engine.py:675) in its else branch (the "safe middle ground" that still surfaces to a human approver):

| Rule action | Behaviour |
| --- | --- |
| `SEND_NOTIFICATION` | Dispatches via each channel in `parameters["notify_channels"]` (if present), otherwise falls back to the default preference. Honours `parameters["template"]` override. |
| `NOTIFY_SECURITY` | Creates a `PENDING` bundle with `channel=SMS` (full guard resolution not yet implemented; bundle is logged). |
| `ESCALATE` | Creates a `PENDING` bundle with `channel=PUSH` and logs the escalation target (full supervisor notification not yet implemented). |

Each dispatch is individually wrapped in try/except so a single rule-action failure does not block the others or the gate operation.

### Lifecycle Hooks

Notification dispatch is wired into [`GateEventLifecycleService`](gateops/services/gate_event_lifecycle.py:63) via the [`_notify()`](gateops/services/gate_event_lifecycle.py:647) helper. The helper is wrapped in try/except so notification failures never block gate operations, and resolves a `SecurityGuard` actor to its linked `User` for audit logging (since [`GateOpsAuditLog.actor`](gateops/models/model_GateOpsAuditLog.py) is a FK to `User`).

| Lifecycle method | Trigger | Rationale |
| --- | --- | --- |
| [`record_arrival()`](gateops/services/gate_event_lifecycle.py:167) | `ARRIVAL` | Notify the host that a visitor has arrived. Fired after rule evaluation so the event's final status (auto-approved, pending approval) is reflected in template selection. |
| [`approve()`](gateops/services/gate_event_lifecycle.py:343) | `ARRIVAL` | Notify the host of the approval (part of the arrival flow). Template selection distinguishes `approval_request` from `visitor_arrival`. |
| [`record_entry()`](gateops/services/gate_event_lifecycle.py:419) | `ENTRY` | Notify the host that the visitor has entered. |
| [`record_exit()`](gateops/services/gate_event_lifecycle.py:455) | `EXIT` | Notify the host that the visitor has exited. |
| [`auto_close()`](gateops/services/gate_event_lifecycle.py:483) | `EXIT` | Notify the host of the auto-close (an exit-like event). The engine selects the `auto_close` template based on the event's `AUTO_CLOSED` status. |

### Audit Logging

[`_log_audit()`](gateops/services/notification_engine.py:927) writes an append-only [`GateOpsAuditLog`](gateops/models/model_GateOpsAuditLog.py) entry for every notification dispatch (create, state transition, escalate). It is wrapped in try/except so a logging failure never blocks a legitimate notification operation; the error is logged at `ERROR` level instead. The `bundle` argument may be `None` for aggregate actions not tied to a single bundle row. The serialized bundle state (via [`_serialize_bundle()`](gateops/services/notification_engine.py:1208)) is recorded as the `before_value`/`after_value` so the audit trail captures status transitions (`PENDING` → `SENT` / `SKIPPED`).

### Multi-Tenancy

[`NotificationBundle`](gateops/models/model_NotificationBundle.py:9) is society-scoped with `society = ForeignKey("housing.Society", on_delete=CASCADE)`, following the established multi-tenancy convention (see [`societies/middleware.py`](societies/middleware.py)):

- **Cross-society guard** in [`NotificationBundle.clean()`](gateops/models/model_NotificationBundle.py:130): the `visitor_category` must belong to the same society as the bundle.
- **`host_unit` and `email_queue` use `SET_NULL`** so historical bundles survive even if the unit or email-queue row is later deleted.
- **Compound indexes** on `(society, is_active)`, `(society, host_unit, is_active)`, and `(society, status, is_active)` follow the existing pattern.
- **Query methods** ([`list_bundles()`](gateops/services/notification_engine.py:968), [`get_bundle()`](gateops/services/notification_engine.py:991), [`get_pending_bundle_count()`](gateops/services/notification_engine.py:1002)) are all society-scoped so a soft-deleted or cross-tenant bundle is never returned.
- **Soft-delete** follows the established `is_active` + `deleted_at` pattern; the `status` field drives a small state machine (`PENDING` → `SENT` / `SKIPPED`).
