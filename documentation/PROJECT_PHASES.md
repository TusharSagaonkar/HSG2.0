# Project Phases

Last updated: `2026-07-12`

## Phase Overview

| Phase | Objective | Status | Exit Criteria | Owner |
| --- | --- | --- | --- | --- |
| Phase 0: Platform Foundation | Baseline Django project, auth, settings, environments. | Completed | Local setup, auth flows, baseline test tooling. | Core Team |
| Phase 1: Core Housing Domain | Societies, structures, units, ownership, occupancy. | Completed | CRUD + core validations + domain tests stable. | Housing Team |
| Phase 2: Accounting Core | Chart of accounts, vouchers, posting, periods, policies. | In Progress | Double-entry integrity, posting controls, period locks verified. | Accounting Team |
| Phase 3: Billing and Receipts | Bill generation, collections, allocations, reminder workflows. | In Progress | End-to-end bill-to-receipt flows with reconciliation checks. | Billing Team |
| Phase 4: Governance and Observability | Auditability, reporting confidence, operational controls. | Planned | Operational dashboards, audit trails, incident runbooks. | Platform Team |
| Phase 5: Scale and Hardening | Performance, multi-tenant maturity, reliability at scale. | Planned | Load profile targets, rollback playbooks, SLO monitoring. | Engineering Leadership |

## Current Focus (Near-Term)

1. Stabilize frontend interaction consistency across all pages.
2. Tighten cross-domain workflow reliability (billing, receipts, accounting).
3. Increase precision of architecture and strategy documentation as system complexity grows.
4. Harden billing financial integrity with immutable, versioned charge-rule execution.

## Phase Quality Gates

| Gate | Description | Evidence Required |
| --- | --- | --- |
| Functional | Target user flow works end-to-end. | Test run evidence and manual verification notes. |
| Data Integrity | Business invariants preserved under edge cases. | Domain tests and rule validation artifacts. |
| Operability | Teams can monitor, support, and recover. | Logs, runbooks, and incident notes. |
| Documentation | Logic, decisions, and strategy are up to date. | Updated files in `documentation/`. |

## Update Rule

When status changes for any phase:

1. Update the phase row.
2. Add a matching note in `STRATEGY_CHANGELOG.md`.
3. Add any blocker in `CHALLENGES_AND_DECISIONS.md`.

---

## GateOps Phase 9: Contractor Management

**Status:** Completed

**Description:** Contractor management for gate operations — tracking contracting companies, contracts, workers, and work permits with expiry checks, attendance tracking, and labour count enforcement. This phase enables societies to register external contracting companies, commission work engagements under bounded date windows, enrol individual labourers against a labour-count ceiling, and issue time-bound work permits with safety documentation. Contractor entry/exit flows through the existing `GateEvent` lifecycle, and contract/permit expiry activates the pre-existing `CONTRACTOR_EXPIRY` rule condition.

### Models Created

| Model | File | Description |
| --- | --- | --- |
| [`Contractor`](gateops/models/model_Contractor.py:6) | [`gateops/models/model_Contractor.py`](gateops/models/model_Contractor.py) | Contracting company master record. Society FK, `company_name`, `supervisor_name`, `supervisor_phone`, `contact_person`, `contact_phone`, `gst_number`, `pan_number`, `address`. Conditional unique constraint on `(society, company_name)` where `is_active=True`. |
| [`Contract`](gateops/models/model_Contract.py:6) | [`gateops/models/model_Contract.py`](gateops/models/model_Contract.py) | Work engagement under a contractor. Contractor FK, `title`, `description`, `start_date`/`end_date` date range, `max_workers` (default 10), `status` (`ACTIVE`/`COMPLETED`/`SUSPENDED`). Conditional unique constraint on `(society, contractor, title)` where `is_active=True`. |
| [`Worker`](gateops/models/model_Worker.py:6) | [`gateops/models/model_Worker.py`](gateops/models/model_Worker.py) | Individual labourer linked to a `Contract` via `Person` composition. Contract FK, `person` FK with `PROTECT` (preserves the deduplicated person master record), `designation`, `id_type`/`id_number`. Conditional unique constraint on `(society, contract, person)` where `is_active=True`. Cross-society leak prevention in [`Worker.clean()`](gateops/models/model_Worker.py:81). |
| [`WorkPermit`](gateops/models/model_WorkPermit.py:6) | [`gateops/models/model_WorkPermit.py`](gateops/models/model_WorkPermit.py) | Time-bound work authorization with safety documentation. Contract FK, `permit_number`, `issued_at`/`expires_at` datetime window, `safety_docs_verified`, `safety_briefing_given`, `work_area`, `hazard_level` (`LOW`/`MEDIUM`/`HIGH`), `status` (`ACTIVE`/`EXPIRED`/`REVOKED`). Conditional unique constraint on `(society, permit_number)` where `is_active=True`. |

### Service

[`ContractorService`](gateops/services/contractor_service.py:83) — 28 methods covering CRUD, expiry checks, attendance, and labour count enforcement. Follows the established service pattern: all `@staticmethod`, keyword-only args, `@transaction.atomic`, race-safe updates via `QuerySet.update()`, and append-only audit logging via [`GateOpsAuditLog`](gateops/models/model_GateOpsAuditLog.py).

| Category | Methods |
| --- | --- |
| Contractor CRUD | `create_contractor`, `update_contractor`, `deactivate_contractor`, `list_contractors`, `get_contractor` |
| Contract CRUD | `create_contract`, `update_contract`, `deactivate_contract`, `list_contracts`, `get_contract` |
| Worker CRUD | `register_worker`, `deactivate_worker`, `list_workers`, `get_worker` |
| WorkPermit CRUD | `issue_work_permit`, `revoke_work_permit`, `list_work_permits`, `get_work_permit` |
| Expiry checks | `check_contract_expiry`, `check_work_permit_expiry`, `get_expired_contracts`, `get_expired_work_permits`, `process_expiries` |
| Attendance | `check_in_worker`, `check_out_worker`, `get_active_workers_on_site` |
| Labour enforcement | `get_labour_count`, `is_labour_limit_exceeded` |
| Internal helpers | `_serialize_contractor`, `_serialize_contract`, `_serialize_worker`, `_serialize_work_permit`, `_log_audit` |

### Rule Engine Integration

The [`CONTRACTOR_EXPIRY`](gateops/models/model_RuleCondition.py:36) condition field was pre-built in Phase 2 (Rule Engine) but remained inert — it resolved to `None` because no context key populated it. Phase 9 activates it via [`_build_contractor_expiry_context()`](gateops/services/gate_event_lifecycle.py:762) in [`GateEventLifecycleService._build_rule_context()`](gateops/services/gate_event_lifecycle.py:704), which populates the `contractor_expiry` context key. The context provides: `contract_expired`, `permit_expired`, `days_until_contract_expiry`, `days_until_permit_expiry`, `has_active_permit`.

### Migration

[`0008_contractor_management`](gateops/migrations/0008_contractor_management.py:8) — creates the four models and adds additive nullable FKs (`contractor`, `contract`, `work_permit` with `SET_NULL`) to [`GateEvent`](gateops/models/model_GateEvent.py:120). Depends on `0007_parcel_management`.

### Tests

86 tests in [`test_contractor_service.py`](gateops/tests/test_contractor_service.py):

| Test class | Count | Coverage |
| --- | --- | --- |
| [`ContractorModelTest`](gateops/tests/test_contractor_service.py:49) | 24 | Model creation, `__str__`, `clean()` validation, defaults, soft-delete for all 4 models |
| [`ContractorServiceTest`](gateops/tests/test_contractor_service.py:325) | 46 | CRUD, cross-society rejection, audit logging, expiry checks, `process_expiries`, attendance, rule engine context |
| [`ContractorViewTest`](gateops/tests/test_contractor_service.py:1007) | 16 | View auth, 200/404 responses, POST-only guards, create/deactivate flows, dashboard |

### Dependencies

| Phase | Dependency |
| --- | --- |
| Phase 1 (Foundation Models) | [`VisitorCategory.is_contractor`](gateops/models/model_VisitorCategory.py:24) flag identifies contractor visitor categories |
| Phase 2 (Rule Engine) | [`CONTRACTOR_EXPIRY`](gateops/models/model_RuleCondition.py:36) condition field + [`RuleEngineService`](gateops/services/rule_engine.py) context mapping |
| Phase 3 (Visitor Lifecycle) | [`Person`](gateops/models/model_Person.py) deduplicated master record + [`GateEvent`](gateops/models/model_GateEvent.py) lifecycle + [`GateEventLifecycleService`](gateops/services/gate_event_lifecycle.py) |

### Files Created/Updated

**Created:**

| File | Purpose |
| --- | --- |
| [`gateops/models/model_Contractor.py`](gateops/models/model_Contractor.py) | Contractor model |
| [`gateops/models/model_Contract.py`](gateops/models/model_Contract.py) | Contract model |
| [`gateops/models/model_Worker.py`](gateops/models/model_Worker.py) | Worker model |
| [`gateops/models/model_WorkPermit.py`](gateops/models/model_WorkPermit.py) | WorkPermit model |
| [`gateops/services/contractor_service.py`](gateops/services/contractor_service.py) | ContractorService (28 methods) |
| [`gateops/migrations/0008_contractor_management.py`](gateops/migrations/0008_contractor_management.py) | Schema migration |
| [`gateops/tests/test_contractor_service.py`](gateops/tests/test_contractor_service.py) | Test suite (86 tests) |
| `gateops/templates/gateops/contractor_list.html` | Contractor list view |
| `gateops/templates/gateops/contractor_detail.html` | Contractor detail view |
| `gateops/templates/gateops/contractor_form.html` | Contractor create/edit form |
| `gateops/templates/gateops/contractor_dashboard.html` | Contractor dashboard |
| `gateops/templates/gateops/contract_list.html` | Contract list view |
| `gateops/templates/gateops/contract_detail.html` | Contract detail view |
| `gateops/templates/gateops/contract_form.html` | Contract create/edit form |
| `gateops/templates/gateops/worker_list.html` | Worker list view |
| `gateops/templates/gateops/worker_detail.html` | Worker detail view |
| `gateops/templates/gateops/worker_form.html` | Worker register/edit form |
| `gateops/templates/gateops/work_permit_list.html` | Work permit list view |
| `gateops/templates/gateops/work_permit_detail.html` | Work permit detail view |
| `gateops/templates/gateops/work_permit_form.html` | Work permit issue/edit form |

**Updated:**

| File | Change |
| --- | --- |
| [`gateops/models/__init__.py`](gateops/models/__init__.py) | Exported 4 new models |
| [`gateops/models/model_GateEvent.py`](gateops/models/model_GateEvent.py:116) | Added additive nullable FKs: `contractor`, `contract`, `work_permit` (`SET_NULL`) |
| [`gateops/services/gate_event_lifecycle.py`](gateops/services/gate_event_lifecycle.py:751) | Added `_build_contractor_expiry_context()` + wired into `_build_rule_context()` |
| [`gateops/forms.py`](gateops/forms.py:752) | Added `ContractorForm`, `ContractForm`, `WorkerForm`, `WorkPermitForm` (society-scoped querysets) |
| [`gateops/views.py`](gateops/views.py:1940) | Added 21 view functions for contractor/contract/worker/work-permit/dashboard |
| [`gateops/urls.py`](gateops/urls.py:65) | Added 21 URL routes |
| [`gateops/admin.py`](gateops/admin.py:258) | Added `ContractorAdmin`, `ContractAdmin`, `WorkerAdmin`, `WorkPermitAdmin` |

---

## GateOps Phase 10: Smart Notification Engine

**Status:** Completed

**Description:** Smart notification routing for gate operations — preference-driven channel selection, host resolution, time-windowed bundling, repeat suppression, and silent entry. This phase wires the gate-event lifecycle to the notification engine so that every arrival, entry, and exit can notify the correct host (resident/owner/tenant) through the right channel (push/SMS/WhatsApp/email/voice), while honouring per-society, per-visitor-category preferences to enforce a "no spam" philosophy. Notification dispatch is fully non-blocking: a notification failure never prevents a gate transition. The engine also integrates with the rule engine so that `SEND_NOTIFICATION`, `NOTIFY_SECURITY`, and `ESCALATE` rule actions produce auditable notification bundles.

### Models Created

| Model | File | Description |
| --- | --- | --- |
| [`NotificationBundle`](gateops/models/model_NotificationBundle.py:9) | [`gateops/models/model_NotificationBundle.py`](gateops/models/model_NotificationBundle.py) | Audit record of a bundled set of gate notifications dispatched within a time window. Society FK (`CASCADE`), `visitor_category` FK (`CASCADE`), `host_unit` FK (`SET_NULL`), `gate_events` M2M (to [`GateEvent`](gateops/models/model_GateEvent.py:10)), `trigger` (`ARRIVAL`/`ENTRY`/`EXIT`/`NEVER`), `channel` (`PUSH`/`SMS`/`WHATSAPP`/`EMAIL`/`VOICE`/`NONE`), `status` (`PENDING`/`SENT`/`SKIPPED`), `recipient_email`, `bundle_window_minutes` (default 0), `dispatched_at`, `email_queue` FK (`SET_NULL`, to `housing.EmailQueue`), `is_active`, `deleted_at`, `created_at`, `updated_at`. 3 indexes: `notifbundle_soc_active_idx`, `nb_soc_unit_active_idx`, `notifbundle_soc_status_idx`. [`clean()`](gateops/models/model_NotificationBundle.py:130) enforces a cross-society guard (visitor category must belong to the same society). Soft-delete pattern (`is_active` + `deleted_at`). |

### Fields Added

| Model | Field | File | Description |
| --- | --- | --- | --- |
| [`GateEvent`](gateops/models/model_GateEvent.py:10) | `host_unit` | [`gateops/models/model_GateEvent.py:147`](gateops/models/model_GateEvent.py:147) | `ForeignKey("housing.Unit", on_delete=SET_NULL, null=True, blank=True, related_name="hosted_gate_events")`. The host unit (flat/shop) the visitor is visiting. Resolved at arrival time and cached on the event so notification routing and bundling can group events by unit without re-resolving the host. Nullable to preserve historical events when a unit is later deleted. |

### Service

[`NotificationEngineService`](gateops/services/notification_engine.py:65) — 14 methods covering host resolution, preference lookup, smart routing, email dispatch, bundling, repeat suppression, rule-action dispatch, audit logging, and bundle queries. Follows the established service contract: all `@staticmethod`, keyword-only args, `@transaction.atomic` on bundling/flush operations, race-safe bundle creation via `select_for_update()`, and append-only audit logging via [`GateOpsAuditLog`](gateops/models/model_GateOpsAuditLog.py). The main entry point ([`dispatch_for_event()`](gateops/services/notification_engine.py:255)) is wrapped in try/except so notification failures **never** block gate operations.

| Category | Methods |
| --- | --- |
| Host resolution | `resolve_host`, `_resolve_user_contact` |
| Preference resolution | `get_preferences`, `get_preference_for_trigger` |
| Smart routing | `dispatch_for_event` (main entry point) |
| Email dispatch | `_dispatch_email`, `_build_email_context`, `_select_template` |
| Bundling | `_find_or_create_bundle`, `flush_bundle`, `flush_pending_bundles` |
| Repeat suppression | `_is_duplicate_notification` |
| Rule action dispatch | `dispatch_for_rule_action`, `_dispatch_send_notification`, `_dispatch_notify_security`, `_dispatch_escalate`, `_create_bundle_for_channel` |
| Audit logging | `_log_audit` |
| Query methods | `list_bundles`, `get_bundle`, `get_pending_bundle_count` |
| Internal helpers | `_create_skipped_bundle`, `_infer_trigger_from_event`, `_serialize_bundle` |

### Host Resolution Chain

[`resolve_host()`](gateops/services/notification_engine.py:82) resolves who should be notified for a gate event, using a first-match-wins fallback chain anchored on `event.host_unit`:

1. **[`UnitOccupancy`](housing/models.py)** — active occupancy (`end_date__isnull=True`), excluding `VACANT`. The current occupant (tenant or owner-occupant) is the recipient.
2. **[`UnitOwnership`](housing/models.py)** — active `PRIMARY` owner (`end_date__isnull=True`).
3. **[`Member`](housing/models.py)** — active member (`status=ACTIVE`, `end_date__isnull=True`), ordered by role (`OWNER` first).

Returns `None` when `event.host_unit` is not set or no host can be resolved from any source. Contact details (email/phone/name) are resolved via [`_resolve_user_contact()`](gateops/services/notification_engine.py:178), which prefers an active `Member` row for the same user + unit and falls back to the `User`'s own attributes.

### Lifecycle Hooks

Notification dispatch is wired into [`GateEventLifecycleService`](gateops/services/gate_event_lifecycle.py:63) via the [`_notify()`](gateops/services/gate_event_lifecycle.py:647) helper. The helper is wrapped in try/except so notification failures never block gate operations, and resolves a `SecurityGuard` actor to its linked `User` for audit logging (since [`GateOpsAuditLog.actor`](gateops/models/model_GateOpsAuditLog.py) is a FK to `User`).

| Lifecycle method | Trigger | File |
| --- | --- | --- |
| [`record_arrival()`](gateops/services/gate_event_lifecycle.py:167) | `ARRIVAL` | [`gate_event_lifecycle.py:221`](gateops/services/gate_event_lifecycle.py:221) |
| [`approve()`](gateops/services/gate_event_lifecycle.py:343) | `ARRIVAL` | [`gate_event_lifecycle.py:379`](gateops/services/gate_event_lifecycle.py:379) |
| [`record_entry()`](gateops/services/gate_event_lifecycle.py:419) | `ENTRY` | [`gate_event_lifecycle.py:449`](gateops/services/gate_event_lifecycle.py:449) |
| [`record_exit()`](gateops/services/gate_event_lifecycle.py:455) | `EXIT` | [`gate_event_lifecycle.py:477`](gateops/services/gate_event_lifecycle.py:477) |
| [`auto_close()`](gateops/services/gate_event_lifecycle.py:483) | `EXIT` | [`gate_event_lifecycle.py:517`](gateops/services/gate_event_lifecycle.py:517) |

### Rule Action Integration

[`evaluate_rules()`](gateops/services/gate_event_lifecycle.py:227) dispatches notifications for three rule-action types via [`dispatch_for_rule_action()`](gateops/services/notification_engine.py:675) in its else branch (the "safe middle ground" that still surfaces to a human approver):

| Rule action | Behaviour |
| --- | --- |
| `SEND_NOTIFICATION` | Dispatches via each channel in `parameters["notify_channels"]` (if present), otherwise falls back to the default preference. Honours `parameters["template"]` override. |
| `NOTIFY_SECURITY` | Creates a `PENDING` bundle with `channel=SMS` (full guard resolution not yet implemented; bundle is logged). |
| `ESCALATE` | Creates a `PENDING` bundle with `channel=PUSH` and logs the escalation target (full supervisor notification not yet implemented). |

### Default NotificationPreferences

Seeded in [`gateops/signals.py`](gateops/signals.py:84) when a society is created:

| Visitor category | Override | Rationale |
| --- | --- | --- |
| `DELIVERY` | `bundle_window_minutes=30`, `trigger=ARRIVAL` | High-frequency, low-urgency — bundle so a resident gets one digest instead of a push per parcel. |
| `EMERGENCY` | `channel=SMS`, `is_silent=False` | Urgent — higher open rate than push, never silenced. |
| `CONTRACTOR` | `trigger=ENTRY` | Notify on entry (not arrival) so the host knows work has actually started. |
| Others | `trigger=ARRIVAL`, `channel=PUSH` | Default — notify on arrival via push. |

### Gate Email Templates

6 templates added to [`notifications/services.py`](notifications/services.py):

| Template | Subject | Trigger |
| --- | --- | --- |
| `gateops.visitor_arrival` | Visitor Arrival: {{ visitor_name }} at {{ society_name }} | `ARRIVAL` (no approval required) |
| `gateops.visitor_entry` | Visitor Entered: {{ visitor_name }} at {{ society_name }} | `ENTRY` |
| `gateops.visitor_exit` | Visitor Exited: {{ visitor_name }} at {{ society_name }} | `EXIT` (explicit scan) |
| `gateops.approval_request` | Approval Required: {{ visitor_name }} at {{ society_name }} | `ARRIVAL` (approval required) |
| `gateops.parcel_ready` | Parcel Ready for Collection: {{ tracking_number }} at {{ society_name }} | Parcel dispatch |
| `gateops.auto_close` | Auto-Closed Visit: {{ visitor_name }} at {{ society_name }} | `EXIT` (auto-closed) |

Template selection is handled by [`_select_template()`](gateops/services/notification_engine.py:1138), which distinguishes `approval_request` from `visitor_arrival` based on the event's status, and `auto_close` from `visitor_exit` based on the event's `AUTO_CLOSED` status / `AUTO_CLOSE` event type.

### Migration

[`0009_notification_engine`](gateops/migrations/0009_notification_engine.py:8) — creates the `NotificationBundle` table (with the `gate_events` M2M) and adds the `host_unit` FK to [`GateEvent`](gateops/models/model_GateEvent.py:147). Depends on `0008_contractor_management` and `housing.0010_add_share_fields_to_member`.

### Tests

91 tests in [`test_notification_engine.py`](gateops/tests/test_notification_engine.py):

| Test class | Count | Coverage |
| --- | --- | --- |
| [`NotificationBundleModelTest`](gateops/tests/test_notification_engine.py:50) | 18 | Model creation, defaults, `__str__`, `clean()` cross-society guard, soft-delete, status/channel/trigger choices, M2M linking, index presence, ordering |
| [`NotificationEngineServiceTest`](gateops/tests/test_notification_engine.py:246) | 59 | Host resolution chain (occupancy → ownership → member, vacant skip, no-host), preference lookup, `dispatch_for_event` (no-preference, NONE channel, NEVER trigger, silent, no-host, immediate email, bundling, audit log, non-email PENDING, never-raises), bundling (create/reuse/zero-window), flush (dispatch/skip/no-events), `flush_pending_bundles` (count/society-scoped), repeat suppression (no-person/no-prior/duplicate/within-window), rule-action dispatch (SEND_NOTIFICATION with channels/fallback, NOTIFY_SECURITY, ESCALATE, unknown, never-raises), query methods (list/filter/inactive/get/404/count), template selection, trigger inference, email context |
| [`NotificationEngineViewTest`](gateops/tests/test_notification_engine.py:1029) | 14 | Lifecycle hooks (arrival/approve/entry/exit/auto-close trigger correct notification), notification failure does not block arrival, rule-action dispatch (SEND_NOTIFICATION/NOTIFY_SECURITY/ESCALATE), exception swallowing, no-preference/no-host-unit graceful degradation, audit log creation, audit-log failure does not block notification |

### Bug Fix

During test development, a bug was found and fixed in [`_notify()`](gateops/services/gate_event_lifecycle.py:647): the `SecurityGuard` was being passed directly as the `actor` to [`dispatch_for_event()`](gateops/services/notification_engine.py:255), but [`GateOpsAuditLog.actor`](gateops/models/model_GateOpsAuditLog.py) is a FK to `User` (not `SecurityGuard`). The fix resolves the guard's linked `user` via `getattr(actor, "user", None)` before passing it as the audit actor, so the audit log accepts the value instead of raising a foreign-key type error.

### Dependencies

| Phase | Dependency |
| --- | --- |
| Phase 1 (Foundation Models) | [`NotificationPreference`](gateops/models/model_NotificationPreference.py) drives channel, trigger, silent mode, and bundling window |
| Phase 3 (Visitor Lifecycle) | [`GateEvent`](gateops/models/model_GateEvent.py:10) lifecycle + [`GateEventLifecycleService`](gateops/services/gate_event_lifecycle.py:63) hooks |
| Phase 2 (Rule Engine) | [`RuleAction`](gateops/models/model_RuleAction.py) action types (`SEND_NOTIFICATION`, `NOTIFY_SECURITY`, `ESCALATE`) |
| Existing `notifications` app | [`EmailQueue`](notifications/models/model_EmailQueue.py) + [`queue_email()`](notifications/services.py) for email dispatch |

### Files Created/Updated

**Created:**

| File | Purpose |
| --- | --- |
| [`gateops/models/model_NotificationBundle.py`](gateops/models/model_NotificationBundle.py) | NotificationBundle model |
| [`gateops/services/notification_engine.py`](gateops/services/notification_engine.py) | NotificationEngineService (14 methods) |
| [`gateops/migrations/0009_notification_engine.py`](gateops/migrations/0009_notification_engine.py) | Schema migration |
| [`gateops/tests/test_notification_engine.py`](gateops/tests/test_notification_engine.py) | Test suite (91 tests) |

**Updated:**

| File | Change |
| --- | --- |
| [`gateops/models/__init__.py`](gateops/models/__init__.py) | Exported `NotificationBundle` |
| [`gateops/models/model_GateEvent.py`](gateops/models/model_GateEvent.py:147) | Added `host_unit` FK (`SET_NULL`) |
| [`gateops/services/gate_event_lifecycle.py`](gateops/services/gate_event_lifecycle.py:647) | Added `_notify()` helper + wired 5 lifecycle hooks + rule-action dispatch in `evaluate_rules()` |
| [`gateops/signals.py`](gateops/signals.py:84) | Added `_DEFAULT_NOTIFICATION_PREFERENCES` seeding (DELIVERY/EMERGENCY/CONTRACTOR) |
| [`notifications/services.py`](notifications/services.py) | Added 6 gate email templates |

---

## GateOps Phase 11: AI Recommendation Engine

**Status:** ✅ Completed

**Description:** AI-powered analysis of historical [`GateEvent`](gateops/models/model_GateEvent.py:10) data for gate operations — visitor pattern detection, anomaly detection, composite risk scoring, and peak-hour prediction. This phase introduces an [`AIRecommendationService`](gateops/services/ai_recommendation_service.py:138) that analyzes a society's gate-event history to (1) build aggregated [`VisitorPattern`](gateops/models/model_VisitorPattern.py:7) rows capturing each person's visit frequency, typical schedule, and risk score; (2) scan for eight categories of suspicious activity (forgotten exits, after-hours entries, frequency spikes, blacklist bypasses, off-pattern visits, duplicate entries, abnormally long stays, and suspicious risk-level crossings) and record each as an immutable [`AnomalyDetection`](gateops/models/model_AnomalyDetection.py:7) audit row with a `OPEN` → `ACKNOWLEDGED` → `RESOLVED`/`FALSE_POSITIVE` lifecycle; (3) compute an 8-factor weighted composite risk score (0.0–1.0) per person and inject it into the [`RuleEngineService`](gateops/services/rule_engine.py) as a new `RISK_SCORE` condition field; and (4) generate [`PeakHourPrediction`](gateops/models/model_PeakHourPrediction.py:7) rows per `(society, day_of_week, hour)` slot using an exponentially weighted moving average (EWMA, decay=0.85) for staffing optimization. All analysis runs non-blocking: a failure in the AI engine never prevents a gate transition, matching the [`NotificationEngineService`](gateops/services/notification_engine.py:65) robustness philosophy. Batch analysis is triggered via the [`gateops_ai_analysis`](gateops/management/commands/gateops_ai_analysis.py:31) management command; real-time anomaly checks fire from [`GateEventLifecycleService.record_entry()`](gateops/services/gate_event_lifecycle.py:461).

### Models Created

| Model | File | Description |
| --- | --- | --- |
| [`VisitorPattern`](gateops/models/model_VisitorPattern.py:7) | [`gateops/models/model_VisitorPattern.py`](gateops/models/model_VisitorPattern.py) | Aggregated visit-history pattern for a person. Society FK (`CASCADE`), `person` FK (`PROTECT`), `gate_vehicle` FK (`SET_NULL`), `visitor_category` FK (`PROTECT`), `suggested_category` FK (`SET_NULL`), `visit_count`, `first_visit_at`/`last_visit_at`, `last_event` FK (`SET_NULL`), `avg_visit_duration_minutes`, `typical_visit_days` (JSONField), `typical_time_window` (JSONField), `is_frequent`, `frequency_score` (0.0–1.0), `risk_score` (0.0–1.0), `risk_level` (`LOW`/`MEDIUM`/`HIGH`/`CRITICAL`), `last_analyzed_at`. Conditional unique constraint on `(society, person)` where `is_active=True`. 4 indexes. [`clean()`](gateops/models/model_VisitorPattern.py:115) enforces cross-society guards (person, visitor_category, suggested_category) and validates `risk_score`/`frequency_score` ranges and `risk_level`/`risk_score` consistency. [`_risk_level_for_score()`](gateops/models/model_VisitorPattern.py:161) static helper maps a score to its `RiskLevel`. |
| [`AnomalyDetection`](gateops/models/model_AnomalyDetection.py:7) | [`gateops/models/model_AnomalyDetection.py`](gateops/models/model_AnomalyDetection.py) | Immutable audit record for a detected anomaly. Society FK (`CASCADE`), `anomaly_type` (8 choices: `FORGOTTEN_EXIT`/`AFTER_HOURS_ENTRY`/`UNUSUAL_FREQUENCY`/`BLACKLIST_BYPASS`/`OFF_PATTERN_VISIT`/`DUPLICATE_ENTRY`/`LONG_STAY`/`SUSPICIOUS_PATTERN`), `severity` (`LOW`/`MEDIUM`/`HIGH`/`CRITICAL`, default `MEDIUM`), `gate_event` FK (`SET_NULL`), `person` FK (`PROTECT`), `gate_vehicle` FK (`SET_NULL`), `description`, `context` (JSONField), `status` (`OPEN`/`ACKNOWLEDGED`/`RESOLVED`/`FALSE_POSITIVE`, default `OPEN`, indexed), `detected_at`, `resolved_at`, `resolved_by` FK (`SET_NULL`), `resolution_notes`. 5 indexes. [`clean()`](gateops/models/model_AnomalyDetection.py:124) enforces cross-society guard and `resolved_at`/`status` consistency (resolved_at requires RESOLVED/FALSE_POSITIVE and vice-versa). |
| [`PeakHourPrediction`](gateops/models/model_PeakHourPrediction.py:7) | [`gateops/models/model_PeakHourPrediction.py`](gateops/models/model_PeakHourPrediction.py) | Predicted visitor counts per `(society, day_of_week, hour)` slot. Society FK (`CASCADE`), `day_of_week` (0–6, Monday=0), `hour` (0–23), `predicted_count`, `confidence_score` (0.0–1.0), `actual_count` (nullable, filled post-hoc for accuracy tracking), `analysis_date`. Conditional unique constraint on `(society, day_of_week, hour, analysis_date)` where `is_active=True` so re-running prediction on the same day upserts rather than duplicates. 3 indexes. [`clean()`](gateops/models/model_PeakHourPrediction.py:74) validates `day_of_week` (0–6), `hour` (0–23), and `confidence_score` (0.0–1.0) ranges. |

### Service

[`AIRecommendationService`](gateops/services/ai_recommendation_service.py:138) — the single authority over [`VisitorPattern`](gateops/models/model_VisitorPattern.py:7), [`AnomalyDetection`](gateops/models/model_AnomalyDetection.py:7), and [`PeakHourPrediction`](gateops/models/model_PeakHourPrediction.py:7) lifecycle. Follows the established service contract: all `@staticmethod`, keyword-only args, `@transaction.atomic` on write operations, race-safe upserts via `update_or_create()`, race-safe status transitions via `QuerySet.update()`, and append-only audit logging via [`GateOpsAuditLog`](gateops/models/model_GateOpsAuditLog.py) wrapped in `try/except` so logging failures never block operations. Critical-anomaly notifications are dispatched via [`NotificationEngineService`](gateops/services/notification_engine.py:65) and are also non-blocking.

| Category | Methods |
| --- | --- |
| Pattern detection | `analyze_visitor_patterns`, `get_visitor_pattern`, `list_visitor_patterns` |
| Anomaly detection | `detect_anomalies`, `get_anomalies`, `get_anomaly`, `acknowledge_anomaly`, `resolve_anomaly` |
| Risk scoring | `calculate_risk_score`, `get_risk_assessment` |
| Peak-hour prediction | `predict_peak_hours`, `get_peak_hour_predictions` |
| Batch analysis | `run_full_analysis` |
| Real-time hooks | `_check_entry_anomalies`, `_get_cached_risk_score` |
| Anomaly detectors (8) | `_detect_forgotten_exits`, `_detect_after_hours_entries`, `_detect_unusual_frequency`, `_detect_blacklist_bypass`, `_detect_off_pattern_visits`, `_detect_duplicate_entries`, `_detect_long_stays`, `_detect_suspicious_patterns` |
| Pattern metrics | `_compute_frequency_score`, `_compute_risk_factors`, `_compute_typical_days`, `_compute_typical_time_window`, `_compute_avg_duration`, `_update_or_create_pattern` |
| Anomaly creation & notification | `_create_anomaly` (with deduplication), `_notify_anomaly` |
| Configuration | `_get_night_mode_hours`, `_get_frequent_visitor_threshold`, `_is_night_hour` |
| Serialization & audit | `_serialize_pattern`, `_serialize_anomaly`, `_log_audit` |

#### Key Algorithms

- **[`analyze_visitor_patterns()`](gateops/services/ai_recommendation_service.py:158)** — Batch pattern detection. Queries all persons with gate events in the last `days` (default 90), processes in batches of 100, filters to completed visits (`EXITED`/`AUTO_CLOSED`), computes metrics (visit_count, first/last_visit_at, avg_duration, typical_visit_days, typical_time_window, frequency_score, risk_score, is_frequent, suggested_category), and upserts via `update_or_create()`. Returns `{"patterns_updated", "patterns_created", "errors"}`.
- **[`detect_anomalies()`](gateops/services/ai_recommendation_service.py:260)** — Runs all 8 anomaly detectors sequentially (default window: 24h). Each detector returns a list of anomaly dicts; the service creates [`AnomalyDetection`](gateops/models/model_AnomalyDetection.py:7) rows from them with deduplication (skips if an open anomaly of the same type already exists for the same gate_event/person). Per-detector and per-anomaly exception isolation. Returns `{"anomalies_created", "by_type", "errors"}`.
- **[`calculate_risk_score()`](gateops/services/ai_recommendation_service.py:430)** — 8-factor weighted composite scoring. Each factor is scored 0.0–1.0, multiplied by its weight (summing to 1.0), and summed: visit_frequency_anomaly (0.20), time_pattern_deviation (0.15), blacklist_watchlist_proximity (0.20), incomplete_exit_history (0.15), duration_anomaly (0.10), cross_category_visits (0.05), night_time_activity (0.10), id_verification_gaps (0.05). Final score clamped to [0.0, 1.0] and mapped to `RiskLevel`. Returns `{"risk_score", "risk_level", "factors"}`.
- **[`predict_peak_hours()`](gateops/services/ai_recommendation_service.py:527)** — EWMA-based forecasting (decay=0.85 per week). Queries last 90 days of `ENTERED`/`EXITED`/`AUTO_CLOSED` events, groups by `(weekday, hour, week_index)`, computes exponentially weighted moving average (more recent weeks weighted higher), and a confidence score (`min(1.0, data_points/12)`, 0.0 if <3 data points). Generates `forecast_days` (default 7) × 24 prediction rows via `update_or_create()`. Returns `{"predictions_created", "analysis_date", "errors"}`.
- **[`run_full_analysis()`](gateops/services/ai_recommendation_service.py:662)** — Orchestrator for the management command. Executes pattern detection, anomaly detection, and peak-hour prediction in order, with per-step exception isolation so a failure in one does not abort the others. Returns a combined summary dict.

### Integration Points

| Integration point | File | Description |
| --- | --- | --- |
| Post-entry anomaly check | [`GateEventLifecycleService.record_entry()`](gateops/services/gate_event_lifecycle.py:461) | Calls [`AIRecommendationService._check_entry_anomalies()`](gateops/services/ai_recommendation_service.py:712) after a successful entry transition. Non-blocking (wrapped in `try/except`). Checks after-hours entry, duplicate entry, and blacklist bypass in real time. |
| Risk score context injection | [`GateEventLifecycleService._build_rule_context()`](gateops/services/gate_event_lifecycle.py:866) | Injects a `risk_score` key into the rule-evaluation context via [`AIRecommendationService._get_cached_risk_score()`](gateops/services/ai_recommendation_service.py:817) — a lightweight read from [`VisitorPattern.risk_score`](gateops/models/model_VisitorPattern.py:78) (no recomputation). Defaults to `0.0` on any failure so rule evaluation always proceeds. |
| `RISK_SCORE` condition field | [`RuleEngineService._FIELD_CONTEXT_KEYS`](gateops/services/rule_engine.py:108) | New `RISK_SCORE` entry maps to the `("risk_score",)` context key, allowing societies to create rules like "if `risk_score >= 0.75`, action = `REJECT`". |
| `ANOMALY` notification trigger | [`NotificationEngineService`](gateops/services/notification_engine.py:65) | [`AIRecommendationService._notify_anomaly()`](gateops/services/ai_recommendation_service.py:1805) calls `dispatch_for_event()` with `trigger=ANOMALY` for `CRITICAL` anomalies that have a linked gate event. Non-blocking. |
| 3 new audit actions | [`GateOpsAuditLog.Action`](gateops/models/model_GateOpsAuditLog.py:33) | `ANOMALY_DETECTED`, `PATTERN_UPDATED`, `PREDICTION_GENERATED` — used by the service for append-only audit logging. |

### Management Command

[`gateops_ai_analysis`](gateops/management/commands/gateops_ai_analysis.py:31) — batch analysis command following the pattern established by [`gateops_auto_close`](gateops/management/commands/gateops_auto_close.py:21).

| Flag | Description |
| --- | --- |
| `--society` | Limit analysis to a specific society (name or ID). Default: all societies. |
| `--dry-run` | Show what would be analyzed without persisting results. |
| `--skip` | Skip specific analysis types: `patterns`, `anomalies`, `predictions` (space-separated). |

Per-society exception isolation: a failure for one society does not abort processing of others. Recommended schedule: hourly (`--skip patterns predictions`) for anomaly detection, daily at 02:00 (no `--skip`) for full analysis.

### Migration

[`0010_ai_recommendation_engine`](gateops/migrations/0010_ai_recommendation_engine.py:8) — creates the three new models and applies 4 `AlterField` operations for the choice additions to [`RuleCondition.field`](gateops/models/model_RuleCondition.py:43) (`RISK_SCORE`), [`GateOpsAuditLog.action`](gateops/models/model_GateOpsAuditLog.py:33) (3 new actions), [`NotificationPreference.trigger`](gateops/models/model_NotificationPreference.py:27) (`ANOMALY`), and [`NotificationBundle.trigger`](gateops/models/model_NotificationBundle.py) (`ANOMALY`). Depends on `0009_notification_engine`. The `AlterField` operations are schema-level no-ops for `CharField` (the DB column is already `VARCHAR`) but update Django's migration state so the new choices are recognized. No data migration needed — all new models start empty and are populated by the first run of `gateops_ai_analysis`.

### Tests

307 tests across 7 test files:

| File | Test classes | Coverage |
| --- | --- | --- |
| [`test_ai_models.py`](gateops/tests/test_ai_models.py) | [`VisitorPatternModelTest`](gateops/tests/test_ai_models.py:36), [`AnomalyDetectionModelTest`](gateops/tests/test_ai_models.py:340), [`PeakHourPredictionModelTest`](gateops/tests/test_ai_models.py:537) | Model creation, `__str__`, `clean()` validation (cross-society guards, range checks, status/resolved_at consistency), defaults, soft-delete, unique constraints, `_risk_level_for_score()` boundary mapping |
| [`test_ai_pattern_analysis.py`](gateops/tests/test_ai_pattern_analysis.py) | [`AnalyzeVisitorPatternsTest`](gateops/tests/test_ai_pattern_analysis.py:26) | Pattern creation/update, typical_days/time_window/avg_duration computation, is_frequent threshold, society scoping, audit logging, skip-no-events |
| [`test_ai_anomaly_detection.py`](gateops/tests/test_ai_anomaly_detection.py) | [`DetectAnomaliesOrchestrationTest`](gateops/tests/test_ai_anomaly_detection.py:214), [`ForgottenExitDetectorTest`](gateops/tests/test_ai_anomaly_detection.py:304), [`AfterHoursEntryDetectorTest`](gateops/tests/test_ai_anomaly_detection.py:370), [`UnusualFrequencyDetectorTest`](gateops/tests/test_ai_anomaly_detection.py:461), [`BlacklistBypassDetectorTest`](gateops/tests/test_ai_anomaly_detection.py:542), [`OffPatternVisitDetectorTest`](gateops/tests/test_ai_anomaly_detection.py:609), [`DuplicateEntryDetectorTest`](gateops/tests/test_ai_anomaly_detection.py:710), [`LongStayDetectorTest`](gateops/tests/test_ai_anomaly_detection.py:782), [`SuspiciousPatternDetectorTest`](gateops/tests/test_ai_anomaly_detection.py:850), [`AnomalyDeduplicationTest`](gateops/tests/test_ai_anomaly_detection.py:938), [`AnomalyNotificationTest`](gateops/tests/test_ai_anomaly_detection.py:1012), [`AnomalyLifecycleTest`](gateops/tests/test_ai_anomaly_detection.py:1078), [`CheckEntryAnomaliesTest`](gateops/tests/test_ai_anomaly_detection.py:1272) | All 8 detectors (detection, severity, context, negatives), deduplication, critical-anomaly notification dispatch (non-blocking), anomaly lifecycle (acknowledge/resolve/false-positive), real-time entry hook, society scoping |
| [`test_ai_risk_scoring.py`](gateops/tests/test_ai_risk_scoring.py) | [`CalculateRiskScoreStructureTest`](gateops/tests/test_ai_risk_scoring.py:151), [`RiskFactorBlacklistTest`](gateops/tests/test_ai_risk_scoring.py:218), [`RiskFactorIncompleteExitTest`](gateops/tests/test_ai_risk_scoring.py:271), [`RiskFactorCrossCategoryTest`](gateops/tests/test_ai_risk_scoring.py:309), [`RiskFactorNightTimeActivityTest`](gateops/tests/test_ai_risk_scoring.py:361), [`RiskFactorIdVerificationGapsTest`](gateops/tests/test_ai_risk_scoring.py:410), [`RiskFactorVisitFrequencyTest`](gateops/tests/test_ai_risk_scoring.py:445), [`RiskFactorDurationAnomalyTest`](gateops/tests/test_ai_risk_scoring.py:492), [`RiskFactorTimePatternDeviationTest`](gateops/tests/test_ai_risk_scoring.py:533), [`RiskScoreClampingTest`](gateops/tests/test_ai_risk_scoring.py:596), [`GetRiskAssessmentTest`](gateops/tests/test_ai_risk_scoring.py:653), [`RiskScoreSocietyScopedTest`](gateops/tests/test_ai_risk_scoring.py:742) | All 8 risk factors individually, score clamping (max 1.0), risk-level mapping, cached vs computed assessment, society scoping |
| [`test_ai_peak_hours.py`](gateops/tests/test_ai_peak_hours.py) | [`PredictPeakHoursStructureTest`](gateops/tests/test_ai_peak_hours.py:118), [`PredictPeakHoursGenerationTest`](gateops/tests/test_ai_peak_hours.py:170), [`PredictPeakHoursConfidenceTest`](gateops/tests/test_ai_peak_hours.py:321), [`PredictPeakHoursEWMATest`](gateops/tests/test_ai_peak_hours.py:399), [`PredictPeakHoursUpsertTest`](gateops/tests/test_ai_peak_hours.py:481), [`PredictPeakHoursSocietyScopedTest`](gateops/tests/test_ai_peak_hours.py:535), [`GetPeakHourPredictionsTest`](gateops/tests/test_ai_peak_hours.py:585), [`RunFullAnalysisTest`](gateops/tests/test_ai_peak_hours.py:647) | Prediction generation, EWMA weighting (recent weeks higher), confidence scoring (data-volume based), upsert on re-run, society scoping, `run_full_analysis()` pipeline |
| [`test_ai_command.py`](gateops/tests/test_ai_command.py) | [`AICommandBasicTest`](gateops/tests/test_ai_command.py:59), [`AICommandSocietyFilterTest`](gateops/tests/test_ai_command.py:118), [`AICommandDryRunTest`](gateops/tests/test_ai_command.py:166), [`AICommandSkipTest`](gateops/tests/test_ai_command.py:229), [`AICommandErrorTest`](gateops/tests/test_ai_command.py:322), [`AICommandOutputTest`](gateops/tests/test_ai_command.py:368) | Command execution, `--society` filter (ID/name), `--dry-run` (no persistence), `--skip` flags, per-society error isolation, output formatting |
| [`test_ai_integration.py`](gateops/tests/test_ai_integration.py) | [`EntryAnomalyHookTest`](gateops/tests/test_ai_integration.py:172), [`RuleContextRiskScoreTest`](gateops/tests/test_ai_integration.py:359), [`RiskScoreConditionTest`](gateops/tests/test_ai_integration.py:432), [`FullLifecycleIntegrationTest`](gateops/tests/test_ai_integration.py:627) | End-to-end: entry → real-time anomaly creation, risk_score injection into rule context, `RISK_SCORE` condition evaluation in rule engine, full lifecycle (entry → anomaly + rule evaluation) |

### Updated Models

| Model | File | Addition |
| --- | --- | --- |
| [`RuleCondition`](gateops/models/model_RuleCondition.py:21) | [`gateops/models/model_RuleCondition.py:43`](gateops/models/model_RuleCondition.py:43) | `RISK_SCORE = "risk_score"` added to `ConditionField` TextChoices |
| [`NotificationPreference`](gateops/models/model_NotificationPreference.py:21) | [`gateops/models/model_NotificationPreference.py:27`](gateops/models/model_NotificationPreference.py:27) | `ANOMALY = "anomaly"` added to `Trigger` TextChoices |
| [`GateOpsAuditLog`](gateops/models/model_GateOpsAuditLog.py:20) | [`gateops/models/model_GateOpsAuditLog.py:33`](gateops/models/model_GateOpsAuditLog.py:33) | `ANOMALY_DETECTED`, `PATTERN_UPDATED`, `PREDICTION_GENERATED` added to `Action` TextChoices |
| [`NotificationBundle`](gateops/models/model_NotificationBundle.py) | [`gateops/migrations/0010_ai_recommendation_engine.py:147`](gateops/migrations/0010_ai_recommendation_engine.py:147) | `ANOMALY` trigger choice added (reuses `NotificationPreference.Trigger.choices`) |

### Dependencies

| Phase | Dependency |
| --- | --- |
| Phase 3 (Visitor Lifecycle) | [`GateEvent`](gateops/models/model_GateEvent.py:10) lifecycle + [`GateEventLifecycleService`](gateops/services/gate_event_lifecycle.py) hooks + [`Person`](gateops/models/model_Person.py) deduplicated master record |
| Phase 2 (Rule Engine) | [`RuleCondition.ConditionField`](gateops/models/model_RuleCondition.py:21) + [`RuleEngineService._FIELD_CONTEXT_KEYS`](gateops/services/rule_engine.py:108) context mapping |
| Phase 10 (Notification Engine) | [`NotificationEngineService.dispatch_for_event()`](gateops/services/notification_engine.py:255) for critical-anomaly alerts |
| Phase 1 (Foundation Models) | [`GateOpsSocietyConfig`](gateops/models/model_GateOpsSocietyConfig.py:6) night-mode hours + [`GateOpsAuditLog`](gateops/models/model_GateOpsAuditLog.py) append-only audit + [`VisitorCategory`](gateops/models/model_VisitorCategory.py:6) + [`GateVehicle`](gateops/models/model_GateVehicle.py:7) watchlist flag |

### Files Created/Updated

**Created:**

| File | Purpose |
| --- | --- |
| [`gateops/models/model_VisitorPattern.py`](gateops/models/model_VisitorPattern.py) | VisitorPattern model |
| [`gateops/models/model_AnomalyDetection.py`](gateops/models/model_AnomalyDetection.py) | AnomalyDetection model |
| [`gateops/models/model_PeakHourPrediction.py`](gateops/models/model_PeakHourPrediction.py) | PeakHourPrediction model |
| [`gateops/services/ai_recommendation_service.py`](gateops/services/ai_recommendation_service.py) | AIRecommendationService |
| [`gateops/migrations/0010_ai_recommendation_engine.py`](gateops/migrations/0010_ai_recommendation_engine.py) | Schema migration |
| [`gateops/management/commands/gateops_ai_analysis.py`](gateops/management/commands/gateops_ai_analysis.py) | Batch analysis management command |
| [`gateops/tests/test_ai_models.py`](gateops/tests/test_ai_models.py) | Model tests (3 classes) |
| [`gateops/tests/test_ai_pattern_analysis.py`](gateops/tests/test_ai_pattern_analysis.py) | Pattern analysis tests |
| [`gateops/tests/test_ai_anomaly_detection.py`](gateops/tests/test_ai_anomaly_detection.py) | Anomaly detection tests (13 classes) |
| [`gateops/tests/test_ai_risk_scoring.py`](gateops/tests/test_ai_risk_scoring.py) | Risk scoring tests (12 classes) |
| [`gateops/tests/test_ai_peak_hours.py`](gateops/tests/test_ai_peak_hours.py) | Peak-hour prediction tests (8 classes) |
| [`gateops/tests/test_ai_command.py`](gateops/tests/test_ai_command.py) | Management command tests (6 classes) |
| [`gateops/tests/test_ai_integration.py`](gateops/tests/test_ai_integration.py) | Integration tests (4 classes) |

**Updated:**

| File | Change |
| --- | --- |
| [`gateops/models/__init__.py`](gateops/models/__init__.py) | Exported `VisitorPattern`, `AnomalyDetection`, `PeakHourPrediction` |
| [`gateops/models/model_RuleCondition.py`](gateops/models/model_RuleCondition.py:43) | Added `RISK_SCORE` to `ConditionField` TextChoices |
| [`gateops/models/model_GateOpsAuditLog.py`](gateops/models/model_GateOpsAuditLog.py:33) | Added `ANOMALY_DETECTED`, `PATTERN_UPDATED`, `PREDICTION_GENERATED` to `Action` TextChoices |
| [`gateops/models/model_NotificationPreference.py`](gateops/models/model_NotificationPreference.py:27) | Added `ANOMALY` to `Trigger` TextChoices |
| [`gateops/services/rule_engine.py`](gateops/services/rule_engine.py:108) | Added `RISK_SCORE` entry to `_FIELD_CONTEXT_KEYS` dict |
| [`gateops/services/gate_event_lifecycle.py`](gateops/services/gate_event_lifecycle.py:461) | Added non-blocking `_check_entry_anomalies()` hook in `record_entry()` + `risk_score` context injection in `_build_rule_context()` |
| [`documentation/PHASE_11_AI_RECOMMENDATION_ENGINE.md`](documentation/PHASE_11_AI_RECOMMENDATION_ENGINE.md) | Detailed design document (architecture, algorithms, migration plan, test plan) |

---

## GateOps Phase 12: Exit Management

**Status:** ✅ Completed

**Description:** Fast, auditable, shift-aware explicit exit for gate operations — one-tap exit by UUID/PK, QR-code exit (Pass code or GateEvent UUID), an enhanced "Currently Inside" screen with filtering/pagination, and guard-to-guard shift handover with immutable snapshots. This phase completes the "visit session" loop of the "Everything is a Gate Event" philosophy: Phases 1–11 built the full entry pipeline (invitation → arrival → rule evaluation → approval → entry) plus the auto-close safety net for forgotten exits; Phase 12 makes the explicit exit fast and shift-aware. The exit transition itself is delegated to the existing [`GateEventLifecycleService.record_exit()`](gateops/services/gate_event_lifecycle.py:470) so the state machine, audit log, and host notification are reused — never duplicated. Two new models — [`ShiftHandover`](gateops/models/model_ShiftHandover.py:9) and [`ShiftHandoverItem`](gateops/models/model_ShiftHandoverItem.py:6) — record guard-to-guard handovers with a `PENDING` → `ACKNOWLEDGED` / `DISPUTED` state machine and per-person immutable snapshots. The "Currently Inside" screen is enhanced with filtering (gate, visitor category, duration, overstay, host unit, search), pagination, and a cached count (60s TTL).

### Models Created

| Model | File | Description |
| --- | --- | --- |
| [`ShiftHandover`](gateops/models/model_ShiftHandover.py:9) | [`gateops/models/model_ShiftHandover.py`](gateops/models/model_ShiftHandover.py) | Guard-to-guard handover record. Society FK (`CASCADE`), `handover_uuid` (UUIDField, `unique=True`, `db_index=True`, `editable=False`), `outgoing_guard`/`incoming_guard` FKs to `SecurityGuard` (`PROTECT`), `gate` FK (`PROTECT`), `shift` FK (`PROTECT`, nullable), `outgoing_assignment`/`incoming_assignment` FKs to `GuardShiftAssignment` (`SET_NULL`, nullable), `status` ([`Status`](gateops/models/model_ShiftHandover.py:23): `PENDING`/`ACKNOWLEDGED`/`DISPUTED`, default `PENDING`, `db_index=True`), `inside_count`, `pending_items_count`, `pending_items_summary` (JSONField), `outgoing_notes`/`incoming_notes`/`dispute_reason`, `handed_over_at` (`auto_now_add`), `acknowledged_at`/`disputed_at` (nullable), `created_by`/`acknowledged_by` FKs to `User` (`SET_NULL`), `is_active`/`deleted_at` soft-delete. 7 indexes. [`clean()`](gateops/models/model_ShiftHandover.py:146) enforces cross-society guards on all FKs, rejects self-handover (`outgoing_guard != incoming_guard`), and validates `acknowledged_at`/`disputed_at`/`dispute_reason` consistency with `status`. |
| [`ShiftHandoverItem`](gateops/models/model_ShiftHandoverItem.py:6) | [`gateops/models/model_ShiftHandoverItem.py`](gateops/models/model_ShiftHandoverItem.py) | Immutable per-person snapshot row linking a [`ShiftHandover`](gateops/models/model_ShiftHandover.py:9) to each inside [`GateEvent`](gateops/models/model_GateEvent.py:10). Society FK (`CASCADE`, denormalized), `handover` FK (`CASCADE`), `gate_event` FK (`SET_NULL`, nullable), `person` FK (`PROTECT`, denormalized), `visitor_category` FK (`PROTECT`, denormalized), `entered_at` (denormalized), `duration_minutes_at_handover`, `gate` FK (`PROTECT`, denormalized), `is_overstay`, `notes`. No soft-delete — items are immutable snapshots deleted only via parent `CASCADE`. 3 indexes + unique constraint on `(handover, gate_event)` where `gate_event__isnull=False`. [`clean()`](gateops/models/model_ShiftHandoverItem.py:98) enforces cross-society guards on all FKs. |

### Services

[`ExitManagementService`](gateops/services/exit_management_service.py:51) — thin orchestration layer for one-tap exit, QR exit, and the "currently inside" query. The actual exit transition is delegated to [`GateEventLifecycleService.record_exit()`](gateops/services/gate_event_lifecycle.py:470) — no state-machine, audit, or notification logic is duplicated. Follows the established service contract: all `@staticmethod`, keyword-only args, `@transaction.atomic` on writes.

| Category | Methods |
| --- | --- |
| Exit processing | [`process_quick_exit()`](gateops/services/exit_management_service.py:67) (one-tap by UUID/PK), [`process_qr_exit()`](gateops/services/exit_management_service.py:101) (QR = GateEvent UUID or Pass.code) |
| Currently inside query | [`get_currently_inside()`](gateops/services/exit_management_service.py:131) (filtered/paginated), [`get_currently_inside_count()`](gateops/services/exit_management_service.py:194) (cached 60s) |
| Internal helpers | [`_resolve_event()`](gateops/services/exit_management_service.py:230), [`_resolve_qr()`](gateops/services/exit_management_service.py:250), [`_validate_inside()`](gateops/services/exit_management_service.py:287), [`_serialize_inside_event()`](gateops/services/exit_management_service.py:295) |

[`ShiftHandoverService`](gateops/services/shift_handover_service.py:53) — create, acknowledge, dispute, list, and retrieve shift handovers. Snapshots currently-inside persons into [`ShiftHandoverItem`](gateops/models/model_ShiftHandoverItem.py:6) rows with denormalized fields. Race-safe transitions via `QuerySet.update()`. Sends direct `EmailQueue` notifications to guards (not via [`NotificationEngineService`](gateops/services/notification_engine.py:65) — design decision: handover is not a `GateEvent`, so the event-centric notification engine does not fit). Follows the established service contract: all `@staticmethod`, keyword-only args, `@transaction.atomic` on writes, append-only audit logging via [`GateOpsAuditLog`](gateops/models/model_GateOpsAuditLog.py).

| Category | Methods |
| --- | --- |
| Handover lifecycle | [`create_shift_handover()`](gateops/services/shift_handover_service.py:70) (snapshots inside persons), [`acknowledge_handover()`](gateops/services/shift_handover_service.py:223), [`dispute_handover()`](gateops/services/shift_handover_service.py:296) |
| Queries | [`list_handovers()`](gateops/services/shift_handover_service.py:360), [`get_handover()`](gateops/services/shift_handover_service.py:387), [`get_handover_items()`](gateops/services/shift_handover_service.py:397), [`get_pending_handovers_for_guard()`](gateops/services/shift_handover_service.py:407), [`get_guards_needing_handover()`](gateops/services/shift_handover_service.py:422) |
| Internal helpers | [`_compute_pending_items()`](gateops/services/shift_handover_service.py:477), [`_log_audit()`](gateops/services/shift_handover_service.py:534), [`_notify_incoming_guard()`](gateops/services/shift_handover_service.py:568), [`_notify_outgoing_guard()`](gateops/services/shift_handover_service.py:605), [`_serialize_handover()`](gateops/services/shift_handover_service.py:511) |

### Views and URLs

8 new routes added to [`gateops/urls.py`](gateops/urls.py:87), plus 1 enhanced existing route. All views are function-based, `@login_required`, server-rendered — matching the existing pattern in [`gateops/views.py`](gateops/views.py).

| Route | View | Method | Description |
| --- | --- | --- | --- |
| `currently-inside/` | [`currently_inside_view`](gateops/views.py:960) | GET | **Enhanced** (existing URL at [`gateops/urls.py:30`](gateops/urls.py:30)) — filtering, pagination, cached count |
| `exits/quick/` | [`quick_exit_view`](gateops/views.py:2629) | POST | One-tap exit by GateEvent UUID/PK |
| `exits/qr/` | [`qr_exit_view`](gateops/views.py:2668) | POST | QR-code exit (Pass code or GateEvent UUID) |
| `exits/qr/scan/` | [`qr_exit_scan_view`](gateops/views.py:2654) | GET | QR scan form page |
| `handovers/` | [`handover_list_view`](gateops/views.py:2693) | GET | List handovers with status/gate filters |
| `handovers/create/` | [`handover_create_view`](gateops/views.py:2723) | GET/POST | Create a shift handover |
| `handovers/<uuid:uuid>/` | [`handover_detail_view`](gateops/views.py:2757) | GET | Handover detail with snapshot items |
| `handovers/<uuid:uuid>/acknowledge/` | [`handover_acknowledge_view`](gateops/views.py:2784) | POST | Acknowledge a pending/disputed handover |
| `handovers/<uuid:uuid>/dispute/` | [`handover_dispute_view`](gateops/views.py:2815) | POST | Dispute a pending handover |

### Forms

6 new forms added to [`gateops/forms.py`](gateops/forms.py:960):

| Form | File | Description |
| --- | --- | --- |
| [`QuickExitForm`](gateops/forms.py:960) | [`gateops/forms.py:960`](gateops/forms.py:960) | Single `gate_event_id` field (UUID or PK) |
| [`QrExitForm`](gateops/forms.py:979) | [`gateops/forms.py:979`](gateops/forms.py:979) | Single `qr_code` field |
| [`ShiftHandoverForm`](gateops/forms.py:1000) | [`gateops/forms.py:1000`](gateops/forms.py:1000) | `outgoing_guard`, `incoming_guard`, `gate`, `shift`, `outgoing_notes` — society-scoped querysets |
| [`HandoverAcknowledgeForm`](gateops/forms.py:1064) | [`gateops/forms.py:1064`](gateops/forms.py:1064) | `notes` field (optional) |
| [`HandoverDisputeForm`](gateops/forms.py:1083) | [`gateops/forms.py:1083`](gateops/forms.py:1083) | `reason` field (required) |
| [`CurrentlyInsideFilterForm`](gateops/forms.py:1105) | [`gateops/forms.py:1105`](gateops/forms.py:1105) | GET-bound filter form (gate, visitor category, duration, overstay, search) |

### Templates

5 templates under `gateops/templates/gateops/`:

| Template | Status | Description |
| --- | --- | --- |
| `currently_inside.html` | Enhanced | Filter form, pagination controls, duration column, overstay badge, quick-exit button per row |
| `qr_exit_scan.html` | New | QR scan/entry form page |
| `handover_list.html` | New | Handover list with status/gate filters |
| `handover_form.html` | New | Handover create form |
| `handover_detail.html` | New | Handover detail with items table + acknowledge/dispute forms |

### Integration Points

| Integration point | File | Description |
| --- | --- | --- |
| Exit transition delegation | [`GateEventLifecycleService.record_exit()`](gateops/services/gate_event_lifecycle.py:470) | [`ExitManagementService`](gateops/services/exit_management_service.py:51) delegates all exit transitions — state machine (`entered → exited`), audit log (`EXIT` action), and host notification (`EXIT` trigger) are reused, not duplicated. |
| Handover guard notifications | [`ShiftHandoverService._notify_incoming_guard()`](gateops/services/shift_handover_service.py:568) | Sends direct `EmailQueue` notifications to guards — not via [`NotificationEngineService`](gateops/services/notification_engine.py:65) (design decision: handover is not a `GateEvent`, so the event-centric notification engine does not fit). Non-blocking (`try/except`). |
| Auto-close interaction | [`ShiftHandoverItem`](gateops/models/model_ShiftHandoverItem.py:6) | Immutable handover snapshots survive auto-close — `ShiftHandoverItem` rows retain `entered_at` and `duration_minutes_at_handover` from handover time even if the `GateEvent` later transitions to `AUTO_CLOSED`. The handover records state at handover time, not current state. |

### Migration

[`0011_exit_management`](gateops/migrations/0011_exit_management.py:10) — creates the [`ShiftHandover`](gateops/migrations/0011_exit_management.py:21) and [`ShiftHandoverItem`](gateops/migrations/0011_exit_management.py:66) tables (2 `CreateModel` operations) and applies 1 [`AlterField`](gateops/migrations/0011_exit_management.py:102) adding 3 new choices (`HANDOVER_CREATED`, `HANDOVER_ACKNOWLEDGED`, `HANDOVER_DISPUTED`) to [`GateOpsAuditLog.action`](gateops/models/model_GateOpsAuditLog.py:20). Depends on `0010_ai_recommendation_engine`. The `AlterField` is a schema-level no-op for `CharField` (the DB column is already `VARCHAR`) but updates Django's migration state — matching the [`0010`](gateops/migrations/0010_ai_recommendation_engine.py:8) pattern. No data migration needed — all new tables start empty.

### Tests

191 tests across 5 test files:

| File | Tests | Test classes | Coverage |
| --- | --- | --- | --- |
| [`test_exit_models.py`](gateops/tests/test_exit_models.py) | 31 | [`ShiftHandoverModelTest`](gateops/tests/test_exit_models.py:45), [`ShiftHandoverItemModelTest`](gateops/tests/test_exit_models.py:262) | Model creation, `__str__`, `clean()` cross-society guards, defaults, soft-delete, unique constraints, state machine validation |
| [`test_exit_management_service.py`](gateops/tests/test_exit_management_service.py) | 38 | [`ExitManagementServiceTest`](gateops/tests/test_exit_management_service.py:54) | `process_quick_exit` (UUID, PK, cross-society, not-inside, success), `process_qr_exit` (Pass code, UUID, invalid, cross-society, not-inside), `get_currently_inside` (filters, pagination, search, overstay), `get_currently_inside_count` (cached) |
| [`test_shift_handover_service.py`](gateops/tests/test_shift_handover_service.py) | 53 | [`ShiftHandoverServiceTest`](gateops/tests/test_shift_handover_service.py:57) | `create_shift_handover` (snapshot, pending items, duplicate rejection, cross-society, self-handover, audit), `acknowledge_handover` (success, wrong guard, not-pending, race safety), `dispute_handover` (success, missing reason, not-pending), list/get/items/queries, `get_guards_needing_handover` |
| [`test_exit_views.py`](gateops/tests/test_exit_views.py) | 45 | [`CurrentlyInsideViewTest`](gateops/tests/test_exit_views.py:143), [`QuickExitViewTest`](gateops/tests/test_exit_views.py:200), [`QrExitScanViewTest`](gateops/tests/test_exit_views.py:265), [`QrExitViewTest`](gateops/tests/test_exit_views.py:289), [`HandoverListViewTest`](gateops/tests/test_exit_views.py:331), [`HandoverCreateViewTest`](gateops/tests/test_exit_views.py:382), [`HandoverDetailViewTest`](gateops/tests/test_exit_views.py:486), [`HandoverAcknowledgeViewTest`](gateops/tests/test_exit_views.py:554), [`HandoverDisputeViewTest`](gateops/tests/test_exit_views.py:613) | View auth, 200/404 responses, POST-only guards, filter/pagination, create/acknowledge/dispute flows |
| [`test_exit_integration.py`](gateops/tests/test_exit_integration.py) | 24 | [`ExitIntegrationTestBase`](gateops/tests/test_exit_integration.py:55) | End-to-end: entry → quick exit → EXITED + audit + host notified; entry → QR exit; entry → handover create → acknowledge → snapshot correct; handover snapshot survives auto-close; cross-society isolation |

### Updated Models

| Model | File | Addition |
| --- | --- | --- |
| [`GateOpsAuditLog`](gateops/models/model_GateOpsAuditLog.py:20) | [`gateops/models/model_GateOpsAuditLog.py:37`](gateops/models/model_GateOpsAuditLog.py:37) | `HANDOVER_CREATED`, `HANDOVER_ACKNOWLEDGED`, `HANDOVER_DISPUTED` added to `Action` TextChoices |

### Bug Fixes

During test development, 5 bugs were found and fixed:

1. **Missing `Q` import** in [`ShiftHandoverService.list_handovers()`](gateops/services/shift_handover_service.py:360) — the method used `Q` objects for filtering but `Q` was not imported in `shift_handover_service.py`.
2. **`quick_exit_view` passing `actor=`** to [`process_quick_exit()`](gateops/services/exit_management_service.py:67) — the view passed `actor=` but the service method did not accept it; added `actor=None` parameter.
3. **`qr_exit_view` passing `actor=`** to [`process_qr_exit()`](gateops/services/exit_management_service.py:101) — same issue as above; added `actor=None` parameter.
4. **`currently_inside_view` missing `@login_required`** — the enhanced [`currently_inside_view`](gateops/views.py:960) was missing the `@login_required` decorator; added it.
5. **`ShiftHandoverForm` not setting `instance.society`** before `full_clean()` — the form did not set `instance.society` before calling `full_clean()`, causing cross-society validation to fail; fixed by setting `instance.society` before validation.

### Dependencies

| Phase | Dependency |
| --- | --- |
| Phase 3 (Visitor Lifecycle) | [`GateEvent`](gateops/models/model_GateEvent.py:10) lifecycle + [`GateEventLifecycleService.record_exit()`](gateops/services/gate_event_lifecycle.py:470) for exit transitions + [`Person`](gateops/models/model_Person.py) deduplicated master record |
| Phase 4 (Pass Management) | [`Pass`](gateops/models/model_Pass.py:62) for QR code resolution (`process_qr_exit` resolves `Pass.code`) |
| Phase 10 (Notification Engine) | [`NotificationEngineService`](gateops/services/notification_engine.py:65) — host exit notification reused via `record_exit()`; handover notifications use direct `EmailQueue` instead (design decision) |
| Phase 1 (Foundation Models) | [`GateOpsAuditLog`](gateops/models/model_GateOpsAuditLog.py) append-only audit + [`SecurityGuard`](gateops/models/model_SecurityGuard.py:33) + [`Gate`](gateops/models/model_Gate.py) + [`GuardShift`](gateops/models/model_GuardShift.py:21) + [`GuardShiftAssignment`](gateops/models/model_GuardShiftAssignment.py:63) |
| Existing `notifications` app | [`EmailQueue`](notifications/models/model_EmailQueue.py) for direct handover guard notifications |

### Files Created/Updated

**Created:**

| File | Purpose |
| --- | --- |
| [`gateops/models/model_ShiftHandover.py`](gateops/models/model_ShiftHandover.py) | ShiftHandover model |
| [`gateops/models/model_ShiftHandoverItem.py`](gateops/models/model_ShiftHandoverItem.py) | ShiftHandoverItem model |
| [`gateops/services/exit_management_service.py`](gateops/services/exit_management_service.py) | ExitManagementService |
| [`gateops/services/shift_handover_service.py`](gateops/services/shift_handover_service.py) | ShiftHandoverService |
| [`gateops/migrations/0011_exit_management.py`](gateops/migrations/0011_exit_management.py) | Schema migration |
| [`gateops/tests/test_exit_models.py`](gateops/tests/test_exit_models.py) | Model tests (2 classes, 31 tests) |
| [`gateops/tests/test_exit_management_service.py`](gateops/tests/test_exit_management_service.py) | ExitManagementService tests (38 tests) |
| [`gateops/tests/test_shift_handover_service.py`](gateops/tests/test_shift_handover_service.py) | ShiftHandoverService tests (53 tests) |
| [`gateops/tests/test_exit_views.py`](gateops/tests/test_exit_views.py) | View tests (9 classes, 45 tests) |
| [`gateops/tests/test_exit_integration.py`](gateops/tests/test_exit_integration.py) | Integration tests (24 tests) |
| `gateops/templates/gateops/qr_exit_scan.html` | QR exit scan form |
| `gateops/templates/gateops/handover_list.html` | Handover list view |
| `gateops/templates/gateops/handover_form.html` | Handover create form |
| `gateops/templates/gateops/handover_detail.html` | Handover detail with items + acknowledge/dispute |

**Updated:**

| File | Change |
| --- | --- |
| [`gateops/models/__init__.py`](gateops/models/__init__.py:57) | Exported `ShiftHandover`, `ShiftHandoverItem` + added to `__all__` |
| [`gateops/models/model_GateOpsAuditLog.py`](gateops/models/model_GateOpsAuditLog.py:37) | Added `HANDOVER_CREATED`, `HANDOVER_ACKNOWLEDGED`, `HANDOVER_DISPUTED` to `Action` TextChoices |
| [`gateops/forms.py`](gateops/forms.py:960) | Added `QuickExitForm`, `QrExitForm`, `ShiftHandoverForm`, `HandoverAcknowledgeForm`, `HandoverDisputeForm`, `CurrentlyInsideFilterForm` |
| [`gateops/views.py`](gateops/views.py:960) | Enhanced `currently_inside_view` with filtering/pagination; added 8 new view functions (quick exit, QR exit/scan, handover list/create/detail/acknowledge/dispute) |
| [`gateops/urls.py`](gateops/urls.py:87) | Added 8 Phase 12 URL routes |
| `gateops/templates/gateops/currently_inside.html` | Enhanced with filter form, pagination, duration column, overstay badge, quick-exit button |
| [`documentation/PHASE_12_EXIT_MANAGEMENT.md`](documentation/PHASE_12_EXIT_MANAGEMENT.md) | Detailed design document (architecture, models, services, migration plan, test plan) |

---

## GateOps Phase 13: Analytics

**Status:** ✅ Completed

**Description:** Read-only analytics and reporting for gate operations — live visitor counts, peak-hour traffic distribution with AI-prediction overlay, guard performance metrics, a filterable custom report, rule-violation statistics, anomaly statistics, visitor trends, CSV export, and pre-computed snapshot generation. This phase introduces an [`AnalyticsService`](gateops/services/analytics_service.py:58) that performs society-scoped, read-only aggregation queries against [`GateEvent`](gateops/models/model_GateEvent.py:10), [`RuleEvaluation`](gateops/models/model_RuleEvaluation.py), [`AnomalyDetection`](gateops/models/model_AnomalyDetection.py:7), [`VisitorPattern`](gateops/models/model_VisitorPattern.py:7), and [`PeakHourPrediction`](gateops/models/model_PeakHourPrediction.py:7). The only write path is snapshot generation, which operates exclusively on the new [`AnalyticsSnapshot`](gateops/models/model_AnalyticsSnapshot.py:19) table. All analytics views are gated behind a `can_view_analytics` permission on [`GateOpsRole`](gateops/models/model_GateOpsRole.py) and are society-scoped. The analytics dashboard is linked from the main [`gateops:dashboard`](gateops/templates/gateops/dashboard.html) so societies can reach the analytics suite from the gate-operations landing page.

### Model Created

| Model | File | Description |
| --- | --- | --- |
| [`AnalyticsSnapshot`](gateops/models/model_AnalyticsSnapshot.py:19) | [`gateops/models/model_AnalyticsSnapshot.py`](gateops/models/model_AnalyticsSnapshot.py) | Pre-computed aggregate metrics cached per society per date. Society FK (`CASCADE`), `date` (DateField, `db_index=True`), `snapshot_type` ([`SnapshotType`](gateops/models/model_AnalyticsSnapshot.py:33): `DAILY`/`WEEKLY`/`MONTHLY`/`CUSTOM`, default `DAILY`), `metrics` (JSONField, schema evolves without migrations), `generated_at` (`auto_now_add`), `is_active`/`deleted_at` soft-delete. 2 indexes: `analytics_snap_soc_date_idx`, `anlsnap_soc_type_date_idx`. Conditional unique constraint on `(society, date, snapshot_type)` where `is_active=True` so re-generation upserts rather than duplicates. [`clean()`](gateops/models/model_AnalyticsSnapshot.py:92) validates weekly snapshots land on a Monday and monthly snapshots on the 1st. Soft-delete via overridden [`delete()`](gateops/models/model_AnalyticsSnapshot.py:105) (idempotent, retains rows for historical trend queries). |

### Service

[`AnalyticsService`](gateops/services/analytics_service.py:58) — read-only analytics queries plus snapshot generation. Follows the established service contract: all `@staticmethod`, keyword-only args, `@transaction.atomic` on the snapshot write path, and multi-tenant safety (every method accepts `*, society` as its first keyword argument). Methods return zeros / empty dicts / empty lists — never `None` — so callers can render dashboards without null-checks. The only write path is snapshot generation, which operates exclusively on [`AnalyticsSnapshot`](gateops/models/model_AnalyticsSnapshot.py:19).

| Category | Methods |
| --- | --- |
| Live visitors | [`get_live_visitors()`](gateops/services/analytics_service.py:71) |
| Peak hours | [`get_peak_hours()`](gateops/services/analytics_service.py:160) |
| Guard performance | [`get_guard_performance()`](gateops/services/analytics_service.py:240) |
| Custom report | [`get_custom_report()`](gateops/services/analytics_service.py:395) |
| Rule violations | [`get_rule_violation_stats()`](gateops/services/analytics_service.py:562) |
| Anomaly stats | [`get_anomaly_stats()`](gateops/services/analytics_service.py:642) |
| Visitor trends | [`get_visitor_trends()`](gateops/services/analytics_service.py:705) |
| Snapshot generation | [`generate_snapshot()`](gateops/services/analytics_service.py:813), [`get_or_create_snapshot()`](gateops/services/analytics_service.py:888) |
| Internal helpers | [`_apply_date_range()`](gateops/services/analytics_service.py:916), [`_apply_filters()`](gateops/services/analytics_service.py:931), [`_compute_metrics()`](gateops/services/analytics_service.py:958) |

### Views and URLs

7 new routes added to [`gateops/urls.py`](gateops/urls.py:97). All views are function-based, `@login_required`, server-rendered — matching the existing pattern in [`gateops/views.py`](gateops/views.py). Each view is gated behind [`_check_analytics_permission()`](gateops/views.py:2861), which consults the `can_view_analytics` flag on the society's active [`GateOpsRole`](gateops/models/model_GateOpsRole.py) (superusers / super-admins always pass).

| Route | View | Method | Description |
| --- | --- | --- | --- |
| `analytics/` | [`analytics_dashboard_view`](gateops/views.py:2893) | GET | Analytics landing page with summary cards (live count, open anomalies, peak hour) + Chart.js hourly distribution |
| `analytics/live-visitors/` | [`analytics_live_visitors_view`](gateops/views.py:2924) | GET (AJAX) | JSON of visitors currently inside (polled by the dashboard live counter) |
| `analytics/peak-hours/` | [`analytics_peak_hours_view`](gateops/views.py:2945) | GET | Hourly traffic distribution chart with predicted overlay |
| `analytics/guard-performance/` | [`analytics_guard_performance_view`](gateops/views.py:2971) | GET | Per-guard throughput metrics table and chart |
| `analytics/custom-report/` | [`analytics_custom_report_view`](gateops/views.py:2997) | GET | Filterable custom report of gate events with summary table + optional `group_by` |
| `analytics/rule-violations/` | [`analytics_rule_violations_view`](gateops/views.py:3045) | GET | Rule violation statistics with action distribution and daily trend |
| `analytics/export/` | [`analytics_export_view`](gateops/views.py:3079) | POST | CSV export of analytics data (events / violations / anomalies) |

### Forms

3 new forms added to [`gateops/forms.py`](gateops/forms.py:1132):

| Form | File | Description |
| --- | --- | --- |
| [`AnalyticsDateRangeForm`](gateops/forms.py:1132) | [`gateops/forms.py:1132`](gateops/forms.py:1132) | GET-bound date-range selector for analytics views |
| [`AnalyticsCustomReportForm`](gateops/forms.py:1179) | [`gateops/forms.py:1179`](gateops/forms.py:1179) | Filter form (date range, gate, visitor category, guard, status, metrics, `group_by`) for the custom report |
| [`AnalyticsExportForm`](gateops/forms.py:1277) | [`gateops/forms.py:1277`](gateops/forms.py:1277) | POST form for CSV export of analytics data |

### Templates

6 templates under `gateops/templates/gateops/`:

| Template | Description |
| --- | --- |
| `analytics_dashboard.html` | Landing page with summary cards, Chart.js hourly distribution, AJAX-polled live-visitor table |
| `analytics_live_visitors.html` | Full live-visitor list (gate / category filters) |
| `analytics_peak_hours.html` | Hourly traffic distribution chart with predicted overlay + date-range form |
| `analytics_guard_performance.html` | Per-guard throughput metrics table and chart |
| `analytics_custom_report.html` | Filterable custom report with summary cards, grouped-results table, and event-details table |
| `analytics_rule_violations.html` | Rule violation statistics with action distribution and daily trend |

### Key Features

- **Live visitors** — real-time count and list of persons currently inside (`status=ENTERED`), AJAX-polled on the dashboard, with `by_category` and `by_gate` breakdowns.
- **Peak hours** — hourly (0–23) and daily (0–6) traffic distribution derived from `GateEvent` timestamps, with [`PeakHourPrediction`](gateops/models/model_PeakHourPrediction.py:7) (Phase 11 AI engine) overlaid as a predicted series.
- **Guard performance** — per-guard throughput metrics (entries processed, average processing time from [`RuleEvaluation`](gateops/models/model_RuleEvaluation.py), violation count), filterable to a single guard.
- **Custom reports** — user-filtered report of gate events with selectable metrics, dimension filters (gate, visitor category, guard, status), and optional `group_by` (gate / category / guard / hour / day / status) producing a grouped series.
- **Rule violations** — violation counts aggregated by action, rule, and gate, with a daily trend series. Violations are any [`RuleEvaluation.ActionTaken`](gateops/models/model_RuleEvaluation.py) other than a clean auto-approve or no-match.
- **Anomaly stats** — breakdown of [`AnomalyDetection`](gateops/models/model_AnomalyDetection.py:7) records by type, severity, and status, with a resolution rate (`(resolved + false_positive) / total * 100`).
- **CSV export** — POST-only CSV export of analytics data (events / violations / anomalies), gated behind `can_view_analytics`.
- **Snapshot generation** — pre-computed aggregate metrics cached per `(society, date, snapshot_type)` in [`AnalyticsSnapshot`](gateops/models/model_AnalyticsSnapshot.py:19) to avoid expensive live `GROUP BY` queries on large `GateEvent` tables for historical trend analysis. Re-generation soft-deletes the previous active snapshot and creates a new one (get-or-create is idempotent).

### Migration

[`0012_analytics`](gateops/migrations/0012_analytics.py:1) — creates the [`AnalyticsSnapshot`](gateops/migrations/0012_analytics.py:16) table (1 `CreateModel` operation) with 2 indexes and 1 conditional unique constraint. Depends on `0011_exit_management`. No data migration needed — the table starts empty and is populated by snapshot generation.

### Tests

28 tests in [`test_analytics.py`](gateops/tests/test_analytics.py):

| Test class | Count | Coverage |
| --- | --- | --- |
| [`AnalyticsServiceTest`](gateops/tests/test_analytics.py:52) | 20 | Live visitors (count, by_category, by_gate, cross-society isolation, empty), peak hours (hourly distribution, predictions overlay, date-range filter), guard performance (metrics, avg processing time, single-guard filter), custom report (group_by series), rule-violation stats (by action/rule/gate), anomaly stats (by type/severity/status, resolution rate), visitor trends (daily granularity), snapshot generation (create, soft-delete-old, get-or-create idempotent, get-or-create new) |
| [`AnalyticsViewTest`](gateops/tests/test_analytics.py:589) | 8 | Dashboard permission enforcement (403 on revoked `can_view_analytics`), dashboard cross-society scoping, live-visitors AJAX JSON, CSV export (attachment + permission denial), peak-hours view, guard-performance view, custom-report view (GET form submission) |

### Bug Fix

During final integration, a template bug was found and fixed in [`analytics_custom_report.html`](gateops/templates/gateops/analytics_custom_report.html): the grouped-results block applied the `first`/`last` filters to dict rows, which raises `KeyError: 0` because Django's `first`/`last` filters index by integer position (`[0]` / `[-1]`) and the `data.grouped` context variable is a list of dicts (e.g. `{"gate__name": "Main", "count": 5}`), not a list of lists. The fix iterates each row's `.items()` and renders the `count` key in the count column and all other keys in the label column, so the grouped table renders correctly for every `group_by` dimension.

### Dependencies

| Phase | Dependency |
| --- | --- |
| Phase 3 (Visitor Lifecycle) | [`GateEvent`](gateops/models/model_GateEvent.py:10) is the primary data source for all live and historical analytics queries |
| Phase 2 (Rule Engine) | [`RuleEvaluation`](gateops/models/model_RuleEvaluation.py) drives guard-performance and rule-violation stats |
| Phase 11 (AI Recommendation Engine) | [`AnomalyDetection`](gateops/models/model_AnomalyDetection.py:7), [`VisitorPattern`](gateops/models/model_VisitorPattern.py:7), and [`PeakHourPrediction`](gateops/models/model_PeakHourPrediction.py:7) feed anomaly stats, risk context, and the peak-hours predicted overlay |
| Phase 1 (Foundation Models) | [`GateOpsRole`](gateops/models/model_GateOpsRole.py) `can_view_analytics` permission gates all analytics views + [`Gate`](gateops/models/model_Gate.py) / [`VisitorCategory`](gateops/models/model_VisitorCategory.py) / [`SecurityGuard`](gateops/models/model_SecurityGuard.py) for dimension filters |

### Files Created/Updated

**Created:**

| File | Purpose |
| --- | --- |
| [`gateops/models/model_AnalyticsSnapshot.py`](gateops/models/model_AnalyticsSnapshot.py) | AnalyticsSnapshot model |
| [`gateops/services/analytics_service.py`](gateops/services/analytics_service.py) | AnalyticsService (11 methods) |
| [`gateops/migrations/0012_analytics.py`](gateops/migrations/0012_analytics.py) | Schema migration |
| [`gateops/tests/test_analytics.py`](gateops/tests/test_analytics.py) | Test suite (28 tests) |
| `gateops/templates/gateops/analytics_dashboard.html` | Analytics landing page |
| `gateops/templates/gateops/analytics_live_visitors.html` | Live visitors list |
| `gateops/templates/gateops/analytics_peak_hours.html` | Peak hours chart |
| `gateops/templates/gateops/analytics_guard_performance.html` | Guard performance table/chart |
| `gateops/templates/gateops/analytics_custom_report.html` | Custom report with grouped results |
| `gateops/templates/gateops/analytics_rule_violations.html` | Rule violation statistics |

**Updated:**

| File | Change |
| --- | --- |
| [`gateops/models/__init__.py`](gateops/models/__init__.py) | Exported `AnalyticsSnapshot` |
| [`gateops/forms.py`](gateops/forms.py:1132) | Added `AnalyticsDateRangeForm`, `AnalyticsCustomReportForm`, `AnalyticsExportForm` |
| [`gateops/views.py`](gateops/views.py:2861) | Added `_check_analytics_permission()` helper + 7 analytics view functions |
| [`gateops/urls.py`](gateops/urls.py:97) | Added 7 Phase 13 URL routes |
| [`gateops/templates/gateops/dashboard.html`](gateops/templates/gateops/dashboard.html) | Added Analytics link to the action-bar |
| [`gateops/signals.py`](gateops/signals.py:111) | `can_view_analytics` permission seeded on `GATE_ADMIN` and `SECURITY_SUPERVISOR` roles |
| [`documentation/PHASE_13_ANALYTICS.md`](documentation/PHASE_13_ANALYTICS.md) | Detailed design document (architecture, service, migration plan, test plan) |
