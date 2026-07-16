# Enterprise Security & Access Control Architecture — Plan Analysis

> **Document Type:** Analytical Assessment of a Proposed Architecture Plan
> **Scope:** 20-part Enterprise Security & Access Control Architecture for a Django-based Housing Society ERP
> **Analysis Date:** 2026-07-07
> **Target System:** Housing Accounting Platform (`housing_accounting`)
> **Framework:** Django 5.x + django-allauth, PostgreSQL shared-schema multi-tenant
> **Grounding:** This analysis is informed by the completed RBAC audit ([`RBAC_ACCESS_MANAGEMENT_AUDIT.md`](documentation/RBAC_ACCESS_MANAGEMENT_AUDIT.md:1)) and direct codebase inspection.

---

## Table of Contents

1. [Plan Overview & Assessment](#1-plan-overview--assessment)
   - 1.1 [Overall Assessment](#11-overall-assessment)
   - 1.2 [Coverage of the 13 Audit Vulnerabilities](#12-coverage-of-the-13-audit-vulnerabilities)
   - 1.3 [Alignment with the 10 Guiding Principles vs Codebase Reality](#13-alignment-with-the-10-guiding-principles-vs-codebase-reality)
2. [Section-by-Section Analysis (Parts 1–20)](#2-section-by-section-analysis-parts-120)
   - 2.1 [Part 1 — Security Philosophy](#21-part-1--security-philosophy)
   - 2.2 [Part 2 — Complete Administration Hierarchy](#22-part-2--complete-administration-hierarchy)
   - 2.3 [Part 3 — User Identity](#23-part-3--user-identity)
   - 2.4 [Part 4 — Complete Role Hierarchy](#24-part-4--complete-role-hierarchy)
   - 2.5 [Part 5 — Permission Architecture](#25-part-5--permission-architecture)
   - 2.6 [Part 6 — Permission Matrix](#26-part-6--permission-matrix)
   - 2.7 [Part 7 — Data Isolation](#27-part-7--data-isolation)
   - 2.8 [Part 8 — Object Level Security](#28-part-8--object-level-security)
   - 2.9 [Part 9 — Field Level Security](#29-part-9--field-level-security)
   - 2.10 [Part 10 — Workflow Security](#210-part-10--workflow-security)
   - 2.11 [Part 11 — Session Management](#211-part-11--session-management)
   - 2.12 [Part 12 — Super Admin](#212-part-12--super-admin)
   - 2.13 [Part 13 — Audit](#213-part-13--audit)
   - 2.14 [Part 14 — API Security](#214-part-14--api-security)
   - 2.15 [Part 15 — Background Jobs](#215-part-15--background-jobs)
   - 2.16 [Part 16 — AI Security](#216-part-16--ai-security)
   - 2.17 [Part 17 — Notification Security](#217-part-17--notification-security)
   - 2.18 [Part 18 — Reports](#218-part-18--reports)
   - 2.19 [Part 19 — Database Security](#219-part-19--database-security)
   - 2.20 [Part 20 — Things To Avoid](#220-part-20--things-to-avoid)
3. [Critical Gaps in the Proposed Plan](#3-critical-gaps-in-the-proposed-plan)
4. [Implementation Order Analysis](#4-implementation-order-analysis)
5. [Codebase-Specific Recommendations](#5-codebase-specific-recommendations)
6. [Effort & Scope Assessment](#6-effort--scope-assessment)
7. [Recommendations for the Full Document](#7-recommendations-for-the-full-document)

---

## 1. Plan Overview & Assessment

### 1.1 Overall Assessment

The proposed 20-part Enterprise Security & Access Control Architecture is **highly ambitious, comprehensive in conceptual coverage, and correctly oriented toward defense-in-depth**. It transitions the platform from a broken, single-layer, ordinal-role RBAC model (documented in the audit as having decorators that are defined but never applied — see [`role_required`](societies/decorators.py:25) and [`society_access_required`](societies/decorators.py:10)) to a full enterprise-grade authorization system spanning field-level, object-level, workflow-level, and API-level controls.

**Strengths of the plan:**

- **Correctly identifies the systemic root cause.** The audit's headline finding — that the RBAC primitive layer is inert in production — is implicitly addressed by Parts 5, 6, 8, and 20, which mandate permission-based authorization on every request and every object access.
- **Defense-in-depth orientation.** The plan does not rely on a single enforcement layer. Parts 7 (data isolation), 8 (object security), 9 (field security), and 13 (audit) collectively build multiple independent barriers.
- **Anti-pattern catalog (Part 20).** The "Things To Avoid" section is unusually valuable — it directly targets the codebase's actual failure modes (trusting IDs, scattering checks, hardcoding role names, depending on developer discipline).
- **Future-proofing.** Parts 14 (API), 15 (background jobs), and 16 (AI) anticipate infrastructure the platform does not yet have (no DRF, no Celery, no AI), which is the correct sequencing for a document intended to guide multi-year evolution.

**Weaknesses of the plan:**

- **Scope explosion risk.** ~30 roles, a Module × Action matrix across 20 modules with 14 actions each (280 permission cells), field-level security, and workflow chains represent an order-of-magnitude increase in complexity over the current 5-role ordinal system. The plan does not acknowledge the migration burden.
- **Django-specific mechanics are underspecified.** The plan speaks in enterprise-architecture abstractions (Platform → Regional → Society → Department → Module → Permission → Object → Record → Field → Action) but does not map these to Django's ORM, managers, middleware, signals, or template tags. This is the single largest gap (see §3).
- **No migration strategy.** The plan describes the end state but not the path from the current broken state. With 13 active vulnerabilities (2 CRITICAL, 5 HIGH), a "big bang" rewrite is infeasible; a phased migration is essential but unspecified.
- **Performance implications unaddressed.** Field-level security and per-object authorization checks on every request, combined with the proposed audit-everything model, will have measurable query-count and latency costs that the plan does not acknowledge.
- **Over-engineering for current scale.** A Housing Society ERP with server-rendered templates and no API layer does not need Government Auditor, AI Agent, and Developer as first-class identity types on day one. The plan conflates the eventual target state with the immediate need.

**Verdict:** The plan is a strong *target architecture* but a weak *implementation blueprint*. It should be treated as the north star for the eventual 150–250 page document, with this analysis providing the gap-bridging analysis the plan itself lacks.

### 1.2 Coverage of the 13 Audit Vulnerabilities

The table below maps each vulnerability from the RBAC audit ([`RBAC_ACCESS_MANAGEMENT_AUDIT.md`](documentation/RBAC_ACCESS_MANAGEMENT_AUDIT.md:202)) to the proposed plan part(s) that address it.

| Vuln ID | Severity | Summary | Plan Coverage | Assessment |
|---|---|---|---|---|
| V-1 | CRITICAL | Unscoped [`Voucher`](accounting/models/model_Voucher.py:14) fetch by PK in [`VoucherPostView`](accounting/views.py:986) et al. | Part 8 (Object Level Security), Part 20 (Never trust IDs) | ✅ Addressed conceptually; needs Django manager/scoped-lookup implementation |
| V-2 | CRITICAL | [`SocietyAdminView`](housing/views.py:1433) / [`SocietyUserCreateView`](housing/views.py:1490) lack authorization | Part 8, Part 5 (permission architecture) | ✅ Addressed; requires per-view permission binding |
| V-3 | HIGH | [`role_required`](societies/decorators.py:25) / [`society_access_required`](societies/decorators.py:10) unused in production | Parts 5, 6, 8 (replace with permission registry) | ✅ Addressed by superseding the decorator model |
| V-4 | HIGH | Inline role checks with string literals in [`UpdateMembershipView`](housing/views.py:1729) | Part 20 (Never hardcode role names) | ✅ Addressed; needs `can_user_do_x()` primitive |
| V-5 | HIGH | Super-admin bypass is tenant-agnostic, no audit binding | Part 12 (Super Admin impersonation), Part 13 (Audit) | ✅ Strongly addressed; Part 12 is the best-matched section |
| V-6 | HIGH | Authentication used in place of authorization across all apps | Parts 5, 6, 8 | ✅ Addressed; largest mechanical effort |
| V-7 | HIGH | [`VoucherTemplateScopeMixin`](accounting/views.py:450) resolves society from GET/POST without membership check | Part 8, Part 20 (Never trust IDs) | ✅ Addressed |
| V-8 | MEDIUM | Session-based tenant selection causes concurrent-window confusion | Part 11 (Session Management) | ⚠️ Partially addressed; Part 11 focuses on auth sessions, not tenant-selection sessions |
| V-9 | MEDIUM | [`MemberDetailView`](members/views.py:26) post-hoc validation | Part 8 (Object Level Security) | ✅ Addressed |
| V-10 | MEDIUM | [`Member.user`](members/models/model_Member.py:27) not validated against [`Membership`](societies/models/model_Membership.py:4) | Part 7 (Data Isolation), Part 8 | ⚠️ Implicit; plan does not explicitly address identity-vs-access decoupling |
| V-11 | LOW | [`SocietyListView`](housing/views.py:378) shows only selected society | Not explicitly addressed | ❌ UX gap; minor |
| V-12 | LOW | Linear role hierarchy, no feature scoping | Parts 4, 5, 6 (full role + permission matrix) | ✅ Directly addressed |
| V-13 | LOW | [`is_super_admin`](housing_accounting/users/models.py:33) has no guardrail | Part 12 (Super Admin) | ✅ Addressed |

**Coverage summary:** 10 of 13 vulnerabilities are directly and adequately addressed by the plan. Two (V-8, V-10) are partially addressed. One (V-11) is not addressed but is a low-severity UX issue. The plan's coverage of the CRITICAL and HIGH vulnerabilities is strong, which is the correct priority.

### 1.3 Alignment with the 10 Guiding Principles vs Codebase Reality

| Principle | Plan Alignment | Current Codebase Reality | Gap |
|---|---|---|---|
| Default Deny | ✅ Part 1, Part 20 | ❌ Default is *allow* — [`LoginRequiredMixin`](accounting/views.py:986) is the only gate | Critical |
| Automatic Tenant Isolation | ✅ Part 7 | ❌ Manual per-view `.filter(society=...)`; no manager-level scoping | Critical |
| Permission-Based Authorization | ✅ Parts 5, 6 | ❌ Ordinal role hierarchy only ([`has_role_or_above()`](societies/permissions.py:4)) | Critical |
| Defense in Depth | ✅ Parts 7, 8, 9, 13 | ❌ Single layer (view queryset filtering) | Critical |
| Immutable Audit Trail | ✅ Part 13 | ⚠️ [`GateOpsAuditLog`](gateops/models/model_GateOpsAuditLog.py:6) is immutable but gateops-only; [`auditlog/`](auditlog/models.py:1) is a placeholder | High |
| Least Privilege | ✅ Parts 4, 5, 6 | ❌ No feature-level grants; `VIEWER` can access accounting | High |
| Separation of Duties | ✅ Part 10 (Maker-Checker) | ❌ No workflow authorization at all | High |
| Secure by Default | ✅ Part 20 | ❌ Developers must remember to filter; forgetting = breach | Critical |
| Single Source of Truth | ✅ Part 5 (permission registry) | ❌ Logic scattered across decorators, services, inline checks | High |
| Future Compatibility | ✅ Parts 14, 15, 16 | ⚠️ No API/Celery/AI yet, so no constraints to violate | Low |

**Key insight:** The plan's principles are sound, but the codebase violates 6 of 10 principles *critically* today. The migration is not an enhancement — it is a remediation of a fundamentally insecure baseline.

---

## 2. Section-by-Section Analysis (Parts 1–20)

### 2.1 Part 1 — Security Philosophy

**Current State:** No documented security philosophy exists. The codebase's *de facto* philosophy is "authenticate then trust" — [`LoginRequiredMixin`](accounting/views.py:986) is the dominant gate, with society filtering applied inconsistently by convention. The [`societies/`](societies/roles.py:1) module encodes a partial "role hierarchy" philosophy but it is inert (audit V-3).

**Gap Analysis:** The plan's 11 principles (Default Deny, Least Privilege, Zero Trust, Immutable Audit, No Hidden Access, Everything Logged, No Cross-Society Access, Every Request Verified, Every Action Authorized, Everything Recoverable, Nothing Deleted Permanently) are comprehensive. The codebase satisfies exactly zero of these as enforced invariants. The "Nothing Deleted Permanently" principle directly conflicts with the current [`on_delete=models.CASCADE`](societies/models/model_Membership.py:14) patterns on membership/society FKs — cascading hard deletes are the norm.

**Feasibility:** Low technical difficulty (it is a document, not code). High organizational difficulty — adopting Default Deny means every existing view must be explicitly opened.

**Dependencies:** None (this is the foundational section).

**Risks:** Philosophy without enforcement is theater. The risk is documenting principles that remain unimplemented, exactly as the current [`role_required`](societies/decorators.py:25) decorator is documented and tested but unused.

**Recommendations:**
- Pair each principle with a concrete enforcement mechanism (e.g., "Default Deny → enforced via [`TenantManager`](core/db_router.py:1) auto-scoping + permission registry gate in middleware").
- Add a 12th principle: **"Permissions are Code, Not Configuration"** — the permission registry should be defined in Python and version-controlled, not stored in a database admin-editable table, to prevent runtime privilege escalation.

### 2.2 Part 2 — Complete Administration Hierarchy

**Current State:** The hierarchy is flat: Platform ([`is_super_admin`](housing_accounting/users/models.py:33)) → Society ([`Society`](societies/models/model_Society.py:4)) → Membership role ([`Membership.role`](societies/models/model_Membership.py:22)). There is no Department, Module, Permission, Object, Record, Field, or Action granularity. The [`DatabaseRouter`](core/db_router.py:3) provides a notional "Platform → analytics/archive" split but this is data partitioning, not administration hierarchy.

**Gap Analysis:** The plan proposes a 10-level hierarchy (Platform → Regional → Society → Department → Module → Permission → Object → Record → Field → Action). The codebase has 3 of 10 levels. The "Regional" level is explicitly future — there is no regional concept in the data model. "Department" does not exist. "Module" maps roughly to Django apps ([`accounting/`](accounting/models/model_Voucher.py:1), [`gateops/`](gateops/views.py:1), etc.) but is not modeled.

**Feasibility:** High difficulty. Modeling Department and Regional levels requires new models, FKs on every tenant-scoped model, and migration of existing data. The Object/Record/Field/Action levels are enforcement layers, not data models — they require the permission registry (Part 5) and field-level security (Part 9).

**Dependencies:** Part 5 (Permission Architecture) must define Module/Action before the hierarchy can be populated.

**Risks:** Over-modeling. A 10-level hierarchy for a Housing Society ERP may introduce administrative overhead disproportionate to the threat model. The "Regional" level in particular has no current or near-term justification.

**Recommendations:**
- Implement levels 1 (Platform), 3 (Society), 5 (Module), 6 (Permission), 7 (Object), 9 (Field), 10 (Action) now.
- Defer levels 2 (Regional) and 4 (Department) until a concrete business requirement emerges.
- Level 8 (Record) is an enforcement concept, not a model — document it as "every row access is authorized," implemented via Part 8.

### 2.3 Part 3 — User Identity

**Current State:** A single [`User`](housing_accounting/users/models.py:14) model (email-based, extends `AbstractUser`) with `is_super_admin` and `is_superuser` flags. [`Membership`](societies/models/model_Membership.py:4) binds User ↔ Society with a role. There is no Profile model (the `name` field on User is the only profile data), no Delegation, no Temporary Access, no Family Member / Vendor / Auditor / API User / AI Agent identity types. [`Member`](members/models/model_Member.py:1) is a *resident* identity decoupled from the *access* identity (audit V-10).

**Gap Analysis:** The plan proposes ~16 user types. The codebase has 1 (User) + 1 (Member, decoupled). The gap is vast. Notably, the plan's "Family Member," "Vendor," "Auditor," "Government Auditor," "Support Engineer," "Developer," "API User," and "AI Agent" types do not exist and have no near-term implementation path (no API layer, no AI, no support workflow).

**Feasibility:** Medium for the core types (User, Profile, Membership, Delegation, Temporary Access, Inactive/Blocked/Deleted). High for the extended types (Vendor, Auditor, API User, AI Agent) due to missing infrastructure.

**Dependencies:** Part 4 (roles) and Part 5 (permissions) must define what each identity type can do.

**Risks:** Conflating *identity type* with *role*. A "Vendor" is not a role — it is an identity that may hold a role. The plan should clarify that identity type determines *authentication method* and *available role set*, while role determines *authorization*.

**Recommendations:**
- Implement a `Profile` model (1:1 with User) to externalize personal data from the auth model.
- Add `Delegation` and `TemporaryAccess` models (with expiry) — these directly enable Part 10 workflows.
- Defer Vendor/Auditor/API User/AI Agent until the corresponding infrastructure (Part 14 API, Part 16 AI) is built.
- Resolve the [`Member`](members/models/model_Member.py:1) ↔ [`Membership`](societies/models/model_Membership.py:4) decoupling (V-10): a `Member.user` should imply a `Membership` in the same society, or be explicitly unlinked (guest resident).

### 2.4 Part 4 — Complete Role Hierarchy

**Current State:** 5 roles in a linear ordinal hierarchy ([`ROLE_HIERARCHY`](societies/roles.py:8)): owner=100, admin=80, accountant=60, member=40, viewer=20. Authorization is exclusively "role ≥ X" via [`has_role_or_above()`](societies/permissions.py:4). There is no platform-level role distinction beyond [`is_super_admin`](housing_accounting/users/models.py:33).

**Gap Analysis:** The plan proposes ~30 roles across Platform (7) and Society (18+) levels. The codebase has 5 society-level roles and 1 platform flag. The plan introduces functional specialization (Secretary, Treasurer, Billing Operator, Collection Operator, Parking Manager, Visitor Manager, etc.) that the current ordinal system cannot express — a "Billing Operator" cannot be granted billing permissions without also being ≥ accountant (60), which would grant accounting permissions.

**Feasibility:** High difficulty. Moving from ordinal to capability-based roles is the single most disruptive change. It requires the permission registry (Part 5), migration of all existing [`Membership.role`](societies/models/model_Membership.py:22) values, and replacement of every [`has_role_or_above()`](societies/permissions.py:4) call.

**Dependencies:** Part 5 (Permission Architecture) and Part 6 (Permission Matrix) are prerequisites — roles are meaningless without defined permissions.

**Risks:**
- **Role explosion.** 30 roles × 280 permission cells = 8,400 permission assignments to manage. Without a default-permission-set-per-role mechanism, this is unmaintainable.
- **Migration ambiguity.** Existing `accountant` memberships — do they map to "Accountant," "Assistant Accountant," "Billing Operator," or "Collection Operator"? The migration requires a business decision per society.
- **Backward compatibility.** Any code calling [`has_role_or_above()`](societies/permissions.py:4) (currently only the unused decorators) will break.

**Recommendations:**
- Implement a `Role` model with `default_permissions` (M2M to `Permission`) so each role ships with a sane default capability set, overridable per-society.
- Phase the role introduction: keep the current 5 roles mapped to default permission sets first, then add specialized roles (Secretary, Treasurer, etc.) as opt-in.
- Provide a migration command that maps existing ordinal roles to default permission sets, with manual review for edge cases.

### 2.5 Part 5 — Permission Architecture

**Current State:** No permission model exists. The only "permission" primitive is [`has_role_or_above()`](societies/permissions.py:4), which is a role comparison, not a capability check. There is no `Permission` model, no `RolePermission` join, and no module/action taxonomy.

**Gap Analysis:** The plan proposes a Module × Action matrix: 20 modules × 14 actions (Create, Edit, Delete, Reverse, Approve, Cancel, Post, Lock, Unlock, Export, Print, Restore, Archive) = 280 permission codes. The codebase has 0. This is the foundational gap — without a permission registry, Parts 6, 8, 9, 10, 14, 15, 16, 17, and 18 cannot be implemented.

**Feasibility:** Medium difficulty for the model (a `Permission` model with `code` and `description`, plus a `RolePermission` M2M). High difficulty for the *population* — defining which of 280 permissions each of 30 roles gets, and wiring every view to check the correct permission.

**Dependencies:** None (this is a prerequisite for Parts 4, 6, 8, 9, 10).

**Risks:**
- **Permission code sprawl.** Without a naming convention, permission codes will become inconsistent (e.g., `accounting.voucher.post` vs `voucher_post` vs `accounting_post_voucher`).
- **Granularity vs usability.** 280 permissions is too many to manage manually. A grouping/namespace mechanism is needed.

**Recommendations:**
- Define permission codes as dot-namespaced strings: `<module>.<entity>.<action>` (e.g., `accounting.voucher.post`, `gateops.pass.create`).
- Implement a `PermissionRegistry` as a Python class (not a DB table) that declares all valid permission codes at startup, with a system check that validates the DB against the registry. This prevents orphaned/typo permissions.
- Map modules to Django apps for consistency: `accounting` → [`accounting/`](accounting/models/model_Voucher.py:1), `gateops` → [`gateops/`](gateops/views.py:1), etc.
- Add a `has_permission(user, permission_code, society)` function that replaces [`has_role_or_above()`](societies/permissions.py:4) as the canonical authorization primitive.

### 2.6 Part 6 — Permission Matrix

**Current State:** No permission matrix exists. The closest artifact is the audit's recommendation table ([`RBAC_ACCESS_MANAGEMENT_AUDIT.md`](documentation/RBAC_ACCESS_MANAGEMENT_AUDIT.md:388)) proposing per-app minimum roles, which is ordinal, not capability-based.

**Gap Analysis:** The plan proposes a Role × Permission × Allowed × Conditions × Approval Required × Audit Required matrix. With 30 roles × 280 permissions = 8,400 cells, each with 4 attributes (Allowed, Conditions, Approval, Audit), this is a 33,600-cell configuration problem. The codebase has nothing analogous.

**Feasibility:** High difficulty. The matrix itself is documentation, but *enforcing* it requires the permission registry (Part 5), workflow engine (Part 10), and audit system (Part 13) to all be operational.

**Dependencies:** Part 4 (roles), Part 5 (permissions), Part 10 (workflow for "Approval Required"), Part 13 (audit for "Audit Required").

**Risks:** The matrix becomes a static document that drifts from the implemented permission registry. Without a single source of truth, the matrix and the code will diverge.

**Recommendations:**
- Generate the matrix *from* the permission registry (Part 5) as a computed artifact, not a hand-maintained document. A management command (`dump_permission_matrix`) should produce the matrix from the `RolePermission` table.
- The "Conditions," "Approval Required," and "Audit Required" columns should be modeled as attributes on the `RolePermission` join (or a `PermissionPolicy` model), not as free-text documentation.

### 2.7 Part 7 — Data Isolation

**Current State:** Tenant isolation is via a `society` FK on tenant-scoped models (e.g., [`Voucher.society`](accounting/models/model_Voucher.py:32), [`Membership.society`](societies/models/model_Membership.py:17)). [`Society`](societies/models/model_Society.py:4) has `created_by` and `created_at` but no `updated_by`, `deleted_by`, `financial_year`, `uuid`, `version`, `status`, or audit fields. [`Membership`](societies/models/model_Membership.py:4) has `joined_at` and `invited_by` but no update/delete tracking. There is no soft-delete, no UUID PK, no version field on any model. The [`DatabaseRouter`](core/db_router.py:3) routes analytics/archive to separate DBs but does not isolate tenants.

**Gap Analysis:** The plan requires every model to have: Society, Created By, Updated By, Deleted By, Financial Year, UUID, Version, Tenant, Status, Audit fields. The codebase has `society` (on most models) and `created_by` (on some). Everything else is absent. This is a schema migration of significant magnitude — every tenant-scoped model needs 7+ new fields.

**Feasibility:** High difficulty. Adding nullable fields to existing models is mechanically straightforward (Django migrations handle this), but *populating* them (backfilling UUIDs, versions, statuses) and *enforcing* them (making them non-nullable, requiring them on create) is complex. The "Financial Year" field is problematic — not every model is financial-year-scoped (e.g., [`Gate`](gateops/views.py:96), [`VisitorCategory`](gateops/views.py:100)).

**Dependencies:** None for the schema; Part 8 (object security) and Part 13 (audit) depend on these fields existing.

**Risks:**
- **Over-scoping.** Requiring `financial_year` on non-financial models (gateops, parking, notifications) is semantically wrong.
- **Migration size.** Adding 7 fields × ~40 models = 280 column additions. Each requires a migration. A single mega-migration is risky; per-app migrations are safer.
- **UUID migration.** Switching PKs from integer to UUID is destructive (FKs, indexes, URLs). Better to add a `uuid` field alongside the integer PK and use it for external references (addresses V-1's sequential ID enumeration).

**Recommendations:**
- Create an abstract `TenantModel` base class with `society`, `created_by`, `updated_by`, `created_at`, `updated_at`, `uuid`, `version`, `is_deleted`, `deleted_by`, `deleted_at`. All tenant-scoped models inherit from it.
- Make `financial_year` optional (on a `FinancialModel` mixin) for models that are FY-scoped ([`Voucher`](accounting/models/model_Voucher.py:14), [`Bill`](housing/views.py:1), [`LedgerEntry`](accounting/models/model_LedgerEntry.py:1)).
- Add UUID as a supplementary field (not PK replacement) to avoid FK/index migration.
- Implement soft-delete via a custom manager that filters `is_deleted=False` by default, with a `including_deleted()` escape hatch.

### 2.8 Part 8 — Object Level Security

**Current State:** Object-level security is inconsistent. The [`gateops/`](gateops/views.py:1) module demonstrates the correct pattern with scoped `_or_404()` helpers (e.g., [`_pass_or_404()`](gateops/views.py:981), [`_gate_vehicle_or_404()`](gateops/views.py:1200), [`_material_movement_or_404()`](gateops/views.py:1452), [`_parcel_or_404()`](gateops/views.py:1726)). The [`accounting/`](accounting/views.py:986) module has the vulnerable pattern: [`get_object_or_404(Voucher, pk=pk)`](accounting/views.py:988) with no society filter (V-1). [`housing/views.py`](housing/views.py:1433) has [`SocietyAdminView`](housing/views.py:1433) with no `get_queryset()` override (V-2).

**Gap Analysis:** The plan requires every object access to pass society check, permission check, financial year check, archive check, and visibility check. The codebase has society check in gateops only, permission check nowhere, and the rest absent. The gateops pattern is the correct template but is not generalized.

**Feasibility:** Medium difficulty. The gateops helpers prove the pattern works. Generalizing it requires a `TenantScopeMixin` (CBV mixin overriding `get_queryset()` and `get_object()`) and a `get_tenant_object_or_404()` utility function.

**Dependencies:** Part 7 (data isolation fields, especially `is_deleted` for archive check), Part 5 (permission registry for permission check).

**Risks:**
- **Performance.** Adding 5 checks per object fetch increases query count. The society and permission checks can be cached on `request`; the FY and archive checks add filter conditions (cheap if indexed).
- **Incomplete adoption.** If even one view bypasses the mixin, isolation breaks (the current V-1 problem). Enforcement must be structural (manager-level), not conventional.

**Recommendations:**
- Implement a `TenantScopeMixin` that overrides `get_queryset()` to filter by `request.current_society` and `is_deleted=False`, and `get_object()` to apply the same scoping.
- Implement `get_tenant_object_or_404(model, society, **kwargs)` as the standard utility, replacing raw [`get_object_or_404()`](accounting/views.py:988) calls.
- **Critical:** Also implement a `TenantManager` that auto-filters by the current tenant when accessed in a request context, so that even raw `Model.objects.filter()` calls are scoped. This is the only way to make isolation the default rather than a convention (audit R-5).
- Add a system check or lint rule that flags `get_object_or_404(Model, pk=...)` without a `society` kwarg on tenant-scoped models.

### 2.9 Part 9 — Field Level Security

**Current State:** No field-level security exists. All fields of a model are visible to any user who can access the object. Templates render fields unconditionally. There is no per-field visibility configuration.

**Gap Analysis:** The plan proposes per-field visibility based on role (e.g., Accountant sees Amount but not PAN/Salary/Bank Password). The codebase has zero field-level controls. This is the most technically novel part of the plan for a Django server-rendered application.

**Feasibility:** High difficulty. Django's ORM and template system do not natively support field-level security. Options:
1. **Serializer-level:** Define field visibility in a serializer-like layer and filter `values()`/`only()` accordingly. Requires an API layer or a custom template context processor.
2. **Template-level:** Use template tags (`{% if user|can_see:"field" %}`) — fragile, depends on developer discipline (violates Part 20's "Never trust frontend").
3. **Manager-level:** Override `values()`/`values_list()` to strip fields — complex, breaks introspection.

**Dependencies:** Part 5 (permission registry must support field-level permissions), Part 7 (models must have identifiable sensitive fields).

**Risks:**
- **Performance.** Field-level filtering on every query adds overhead.
- **Complexity.** Defining field visibility for ~40 models × ~15 fields each = 600 field-permission entries.
- **Template bypass.** Server-rendered templates are the primary rendering layer. If field-level security is enforced only at the ORM level, a template that accesses `{{ object.salary }}` will still render the value unless the attribute is stripped.

**Recommendations:**
- Defer field-level security to Phase 3 (after object-level security is solid). It is a defense-in-depth enhancement, not a primary control.
- Implement via a `FieldVisibility` model (`permission_code`, `field_path`, `visible_to_roles`) and a `visible_fields(model, user)` utility that returns the set of visible field names.
- Enforce at the *serialization* layer (a `to_secure_dict()` method on models or a context processor that strips fields before template rendering).
- For the immediate term, mark sensitive fields (PAN, salary, bank details) and exclude them from default querysets/templates, adding them only when the user has the explicit permission.

### 2.10 Part 10 — Workflow Security

**Current State:** No workflow authorization exists. [`Voucher`](accounting/models/model_Voucher.py:14) has a `posted_at` field and a `post()` method, but posting is not gated by a maker-checker workflow — any user who can access the view can post (V-1, V-6). There is no approval chain, no digital signature, no OTP/eSign, no escalation, no timeout, no delegation.

**Gap Analysis:** The plan proposes Maker-Checker, Maker-Checker-Checker, approvals, digital signatures, OTP, eSign, approval chains, escalation, timeout, and delegation. The codebase has none of these. The closest analog is the gateops approval workflow ([`GateEventApproval`](gateops/views.py:43), [`ApprovalType`](gateops/views.py:40)) which implements a basic approval type with escalation timeout — a useful reference pattern.

**Feasibility:** High difficulty. Workflow engines are complex state machines. The gateops approval pattern is a starting point but is gateops-specific and does not generalize to accounting (where maker-checker on voucher posting is the critical use case).

**Dependencies:** Part 5 (permissions for "approve" action), Part 13 (audit for workflow events), Part 3 (Delegation/TemporaryAccess models for delegation).

**Risks:**
- **Workflow rigidity.** Over-engineered workflows (Maker-Checker-Checker) can paralyze operations if approvers are unavailable.
- **State management.** Workflow state must be persisted and recoverable. A crashed transaction mid-approval must not leave the system in an inconsistent state.

**Recommendations:**
- Start with Maker-Checker on voucher posting (the highest-value workflow): a draft voucher is created by a maker, posted only after a checker approves. This directly addresses the audit's V-1 concern (unauthorized posting).
- Generalize the [`gateops`](gateops/views.py:43) approval pattern into a reusable `ApprovalRequest` model with `approver`, `status`, `timeout`, `escalation_to`.
- Defer digital signature / eSign / OTP until a regulatory requirement emerges — these add significant complexity (crypto, integration with eSign providers) for uncertain value.

### 2.11 Part 11 — Session Management

**Current State:** Django's default session management (database-backed sessions) with django-allauth for authentication. [`SocietyMiddleware`](societies/middleware.py:5) populates `request.current_society` from the session. There is no multiple-device management, no concurrent login control, no session timeout configuration beyond Django defaults, no IP/browser change detection, no device trust, no 2FA/MFA. The tenant-selection session (V-8) is a separate concern from the auth session.

**Gap Analysis:** The plan proposes comprehensive session management including 2FA/MFA, device trust, concurrent login control, and IP/browser change detection. The codebase has basic session management only. django-allauth supports some of these (e.g., email verification is already mandatory per [`config/settings/base.py`](config/settings/base.py:369)).

**Feasibility:** Medium difficulty. 2FA/MFA can be added via `django-otp` or `django-allauth-2fa`. Device trust and concurrent login control require custom session tracking. IP/browser change detection requires middleware.

**Dependencies:** None (independent of the permission system).

**Risks:**
- **2FA friction.** Mandatory 2FA for all users (including residents) may be excessive. Consider 2FA for high-privilege roles (admin, accountant) only.
- **Concurrent login control** can break legitimate multi-device usage. Make it configurable per role.

**Recommendations:**
- Implement 2FA for roles ≥ accountant (the roles that can mutate financial data).
- Add session device tracking (a `SessionDevice` model logging user-agent, IP, last-seen) and surface it in a "active sessions" UI.
- **Address V-8 separately:** the tenant-selection session confusion is not an auth-session problem. Recommend moving the active tenant from session state to a per-request header or URL namespace (audit R-9), which Part 11 does not explicitly address.

### 2.12 Part 12 — Super Admin

**Current State:** [`is_super_admin`](housing_accounting/users/models.py:33) is a plain `BooleanField` with an unconditional bypass in every access check ([`get_accessible_societies_qs()`](societies/services.py:15), [`user_has_society_access()`](societies/services.py:26), [`role_required`](societies/decorators.py:29), [`society_access_required`](societies/decorators.py:14)). There is no impersonation workflow, no reason recording, no temporary session, no auto-exit, and no audit binding (V-5, V-13). The super-admin operates entirely outside the tenant boundary.

**Gap Analysis:** The plan proposes a full impersonation workflow: Login → Choose Society → Reason → Impersonation Token → Temporary Session → Everything Logged → Auto Exit. The codebase has a bare boolean flag with no workflow. This is one of the plan's best-matched sections — it directly addresses V-5 and V-13.

**Feasibility:** Medium difficulty. Django has impersonation packages (`django-impersonate`) but they do not integrate with the tenant-selection mechanism. A custom `ImpersonationSession` model with `impersonator`, `target_user`, `society`, `reason`, `started_at`, `expires_at`, and an audit log is needed.

**Dependencies:** Part 13 (audit — every impersonation action must be logged), Part 11 (session — impersonation creates a temporary session).

**Risks:**
- **Privilege escalation via impersonation.** If the impersonation token is not tightly scoped (time-limited, society-scoped), it becomes a permanent backdoor.
- **Audit gaps.** If any code path bypasses the impersonation audit, super-admin actions become invisible.

**Recommendations:**
- Replace the unconditional [`is_super_admin`](housing_accounting/users/models.py:33) bypass with a scoped impersonation model. Super-admins must explicitly enter an impersonation session with a reason and target society.
- Auto-expire impersonation sessions after a configurable timeout (e.g., 1 hour).
- Log every impersonation session start, every action within it, and the session end to an immutable audit log (Part 13).
- Remove the `is_super_admin` bypass from [`get_accessible_societies_qs()`](societies/services.py:15) and [`user_has_society_access()`](societies/services.py:26) — super-admins should access societies *through* impersonation, not by bypass.

### 2.13 Part 13 — Audit

**Current State:** Audit logging is fragmented. [`GateOpsAuditLog`](gateops/models/model_GateOpsAuditLog.py:6) is a well-designed immutable append-only log (enforced via `save()` rejecting updates and `delete()` raising `PermissionError`) but is gateops-only. The [`auditlog/`](auditlog/models.py:1) app is a placeholder (the model file contains only a docstring). There is no platform-wide audit log. The audit (V-5, C-5) notes that tenant switches are not logged.

**Gap Analysis:** The plan proposes a comprehensive audit capturing Who, When, IP, Browser, Session, Old, New, Reason, API, Device, Location, Tenant, Module, Object, Duration, Request ID — immutable. The [`GateOpsAuditLog`](gateops/models/model_GateOpsAuditLog.py:6) model captures most of these (society, actor, action, entity_type, entity_id, before_value, after_value, ip_address, device_info, gps) and is the correct template. The gap is generalizing it platform-wide.

**Feasibility:** Medium difficulty. The [`GateOpsAuditLog`](gateops/models/model_GateOpsAuditLog.py:6) pattern is proven. Generalizing requires a platform-wide `AuditLog` model (or evolving [`auditlog/`](auditlog/models.py:1) from placeholder to implementation) and instrumenting every state-changing operation.

**Dependencies:** Part 7 (models need `society` FK for tenant scoping of audit entries).

**Risks:**
- **Performance.** Audit logging on every write doubles write volume. Asynchronous logging (via signals + queue) mitigates but introduces consistency risk.
- **Immutability enforcement.** The [`GateOpsAuditLog`](gateops/models/model_GateOpsAuditLog.py:6) enforces immutability at the model level (`save()` rejects updates), but this is bypassable via `QuerySet.update()` or raw SQL. Database-level triggers or append-only table permissions are needed for true immutability.

**Recommendations:**
- Evolve [`auditlog/`](auditlog/models.py:1) from placeholder to a platform-wide `AuditLog` model modeled on [`GateOpsAuditLog`](gateops/models/model_GateOpsAuditLog.py:6).
- Use Django signals (`post_save`, `post_delete`) to auto-generate audit entries for audited models, with a decorator/mixin to opt in.
- Add a `request_id` (correlation ID) propagated via middleware to link all audit entries in a single request.
- For true immutability, use PostgreSQL row-level security or a separate append-only role with no UPDATE/DELETE grants.
- Log tenant switches (audit C-5) in [`GlobalSelectionUpdateView`](housing_accounting/users/views.py:56).

### 2.14 Part 14 — API Security

**Current State:** No API layer exists. The application is entirely server-rendered templates. There is no DRF, no JWT, no API keys, no rate limiting, no webhook signatures. The [`config/urls.py`](config/settings/base.py:1) routes only to template-serving views.

**Gap Analysis:** The plan proposes JWT, scopes, refresh, expiry, rate limiting, client secrets, webhook signatures, idempotency, audit, API keys, and service accounts. The codebase has none of this infrastructure. This part is entirely greenfield.

**Feasibility:** High difficulty (new infrastructure). Medium difficulty if scoped to DRF + `djangorestframework-simplejwt` + `django-ratelimit`.

**Dependencies:** Part 5 (permission registry — API scopes map to permissions), Part 13 (audit — API calls must be audited).

**Risks:**
- **Premature build.** Building API security before there is an API use case may produce a system that does not match eventual requirements.
- **Dual authorization paths.** If the API and the template views use different authorization code, one will drift. The permission registry (Part 5) must be the single source for both.

**Recommendations:**
- Defer Part 14 until a concrete API consumer (mobile app, integration) is identified.
- When built, use DRF's `DjangoModelPermissions` extended to use the Part 5 permission registry, so API and template views share authorization logic.
- Use JWT with short-lived access tokens (15 min) and refresh tokens (7 days), scoped to the tenant (`society` claim in the JWT).

### 2.15 Part 15 — Background Jobs

**Current State:** No background job infrastructure exists. There is no Celery, no task queue, no scheduled jobs. The [`reconciliation/management/commands/`](reconciliation/management/commands/run_reconciliation.py:1) module has management commands run manually.

**Gap Analysis:** The plan proposes that Celery tasks must not bypass permissions and must execute as a System User → Society Context → Permission Context → Audit. The codebase has no background jobs to secure. This is a future-state concern.

**Feasibility:** Low difficulty *conceptually* (the pattern is clear); High difficulty *infrastructurally* (introducing Celery, Redis, task monitoring).

**Dependencies:** Part 5 (permissions), Part 13 (audit), Part 3 (System User identity).

**Risks:** Background jobs that bypass the permission system (e.g., a Celery task that calls `Voucher.objects.all()` without scoping) would reintroduce the exact vulnerability class the architecture is designed to prevent.

**Recommendations:**
- Defer until Celery is introduced.
- When introduced, mandate that all tasks accept a `society_id` and `user_id` parameter, set the tenant context via a task decorator, and audit via the Part 13 system.
- The `TenantManager` (Part 7/8) should support a context manager (`with tenant_context(society):`) that scopes ORM calls within background jobs.

### 2.16 Part 16 — AI Security

**Current State:** No AI integration exists.

**Gap Analysis:** The plan proposes AI Assistant scoped to Current User → Current Society → Current Permission → AI Response. Entirely greenfield.

**Feasibility:** Not assessable against the current codebase (no AI infrastructure).

**Dependencies:** Part 5 (permissions), Part 14 (API — AI likely consumes an API), Part 3 (AI Agent identity type).

**Risks:** AI agents that can read/write data must be scoped identically to human users. An AI agent with `is_super_admin` would be catastrophic.

**Recommendations:** Defer entirely. Document the principle (AI inherits the calling user's permissions) but do not build until AI is introduced.

### 2.17 Part 17 — Notification Security

**Current State:** The [`notifications/`](notifications/views.py:1) app has a `ReminderLogListView` (V-6 — no authorization). The [`housing/`](housing/services/reminders.py:1) services module has reminder logic. Email queue and templates exist in [`housing/migrations/0006_email_delivery_models.py`](housing/migrations/0006_email_delivery_models.py:1). There is no per-user notification scoping — notifications are society-scoped, not user-scoped.

**Gap Analysis:** The plan proposes users receive only their own bills, complaints, visitors, parking, notices. The codebase has society-level notifications, not user-level. A member can currently see all reminder logs for their society (V-6).

**Feasibility:** Medium difficulty. Requires adding a `recipient` FK to notification models and filtering by `request.user`.

**Dependencies:** Part 8 (object-level security — notifications are objects), Part 5 (permissions for notification access).

**Risks:** Notification routing errors (wrong recipient) are privacy violations. The routing logic must be centralized and tested.

**Recommendations:**
- Add a `recipient` (FK to User) or `membership` (FK to Membership) field to notification models.
- Filter notification lists by `recipient=request.user` in addition to `society=current_society`.
- Apply Part 8 object-level security to notification models.

### 2.18 Part 18 — Reports

**Current State:** The [`reports/`](reports/views.py:33) app has `ReportsHomeView` and report views with no authorization (V-6 — any authenticated user can access all reports). [`reports/services.py`](reports/services.py:1) contains report generation logic. There are no separate permissions for generate/export/schedule/email/share/print/archive.

**Gap Analysis:** The plan proposes 7 distinct report permissions (Generate, Export, Schedule, Email, Share, Print, Archive). The codebase has 0 — reports are all-or-nothing.

**Feasibility:** Low difficulty (add permission checks to report views). Medium difficulty if scheduling/emailing requires background jobs (Part 15).

**Dependencies:** Part 5 (permission registry), Part 15 (for scheduling — requires Celery).

**Risks:** Over-granular permissions may frustrate users (e.g., a user who can generate but not export a report).

**Recommendations:**
- Implement Generate and Export permissions immediately (high value, low effort).
- Defer Schedule/Email/Share/Print/Archive until the corresponding infrastructure exists.

### 2.19 Part 19 — Database Security

**Current State:** Single PostgreSQL database (shared-schema multi-tenant). [`DatabaseRouter`](core/db_router.py:3) routes `analytics`/`archive` app labels to separate DBs. [`ATOMIC_REQUESTS`](config/settings/base.py:108) is enabled. There is no encryption at rest (beyond PostgreSQL defaults), no secrets management (settings likely contain secrets in env vars), no PITR configuration, no immutable backup, no soft-delete, no versioning, no partitioning, no audit tables (beyond gateops).

**Gap Analysis:** The plan proposes encryption, secrets management, backup, restore, PITR, immutable backup, soft-delete, versioning, partitioning, and audit tables. The codebase has basic backup (implied by PostgreSQL) and atomic requests. Everything else is absent.

**Feasibility:** High difficulty. Most of these are infrastructure/ops concerns, not application code. Encryption at rest, PITR, and immutable backup are PostgreSQL/cloud-provider configurations. Soft-delete and versioning are application-level (Part 7). Partitioning is a migration concern.

**Dependencies:** Part 7 (soft-delete, versioning fields), Part 13 (audit tables).

**Risks:**
- **Encryption performance.** Column-level encryption (for sensitive fields like PAN) adds CPU overhead.
- **Partitioning complexity.** Partitioning by society or financial year requires careful migration and affects all queries.
- **Backup immutability.** Requires infrastructure (e.g., AWS S3 Object Lock) beyond the application.

**Recommendations:**
- Implement soft-delete and versioning at the application level (Part 7).
- Use environment variables / a secrets manager (e.g., AWS Secrets Manager, HashiCorp Vault) for secrets — never hardcode in [`config/settings/`](config/settings/base.py:1).
- Defer partitioning until data volume warrants it.
- Document PITR and immutable backup as ops requirements, not application code.

### 2.20 Part 20 — Things To Avoid

**Current State:** The codebase violates nearly every anti-pattern listed:
- **"Never trust IDs"** — violated by [`get_object_or_404(Voucher, pk=pk)`](accounting/views.py:988) (V-1).
- **"Never hardcode role names"** — violated by [`role not in ['owner', 'admin']`](housing/views.py:1729) (V-4).
- **"Never use role hierarchy in business logic"** — the entire authorization system *is* role hierarchy ([`has_role_or_above()`](societies/permissions.py:4)).
- **"Never scatter permission checks"** — checks are scattered across decorators, services, and inline code (V-4).
- **"Never bypass permissions"** — [`is_super_admin`](housing_accounting/users/models.py:33) bypasses everything (V-5).
- **"Never expose sequential IDs"** — all models use integer PKs exposed in URLs.
- **"Never depend on developers remembering filters"** — the entire isolation model depends on this (V-1, V-3).
- **"Never give Admin unrestricted power"** — [`is_super_admin`](housing_accounting/users/models.py:33) is unrestricted.
- **"Never mix auth and authz"** — [`LoginRequiredMixin`](accounting/views.py:986) is used as authorization (V-6).

**Gap Analysis:** The plan's anti-pattern catalog is a direct diagnosis of the codebase's current failure modes. This is the most accurate section of the plan.

**Feasibility:** Low difficulty (it is a list of prohibitions). High difficulty to *enforce* — each anti-pattern requires a structural mechanism (manager, registry, lint rule) to prevent.

**Dependencies:** All other parts (the anti-patterns are enforced by the mechanisms built in Parts 5–13).

**Risks:** Documenting anti-patterns without enforcement tools is insufficient. Developers will reintroduce them.

**Recommendations:**
- For each anti-pattern, specify the enforcement mechanism:
  - "Never trust IDs" → `TenantManager` auto-scoping (Part 7/8).
  - "Never hardcode role names" → permission registry (Part 5), ban raw role strings via lint.
  - "Never use role hierarchy in business logic" → `has_permission()` replaces `has_role_or_above()`.
  - "Never scatter permission checks" → central middleware/decorator gate.
  - "Never expose sequential IDs" → UUID field for external references.
- Add a `flake8`/`ruff` custom lint rule that flags `get_object_or_404(Model, pk=...)` without a `society` kwarg.

---

## 3. Critical Gaps in the Proposed Plan

The plan is conceptually comprehensive but underspecifies the *Django-specific mechanics* and the *migration path*. The following gaps are critical:

### 3.1 Django ORM and Manager-Level Isolation

The plan mandates "Automatic Tenant Isolation" but does not specify the Django mechanism. The correct approach is a custom `QuerySet`/`Manager` (`TenantManager`) that auto-filters by `request.current_society` when a request context is active. This is the *only* way to make isolation the default rather than a per-view convention (audit R-5). The plan should specify:
- A `TenantManager` class that reads the current tenant from a thread-local or context variable set by [`SocietyMiddleware`](societies/middleware.py:5).
- A `TenantModel` abstract base class that assigns `TenantManager` as the default manager.
- A `tenant_context()` context manager for background jobs (Part 15) and management commands.

### 3.2 Migration Strategy from Broken RBAC

The plan describes the end state but not the migration. With 13 active vulnerabilities, a "big bang" rewrite is unacceptable — the CRITICAL vulnerabilities (V-1, V-2) must be fixed *immediately*, before the full architecture is built. The plan should include:
- **Phase 0 (Immediate):** Fix V-1 and V-2 by applying society scoping to the four voucher views and adding authorization to [`SocietyAdminView`](housing/views.py:1433). This is a patch, not architecture.
- **Phase 1 (Foundation):** Build the permission registry (Part 5), `TenantManager` (Part 7/8), and platform-wide audit log (Part 13).
- **Phase 2 (Authorization):** Apply permission checks to all views, replacing [`LoginRequiredMixin`](accounting/views.py:986)-only gates.
- **Phase 3 (Advanced):** Field-level security (Part 9), workflows (Part 10), session hardening (Part 11), super-admin impersonation (Part 12).
- **Phase 4 (Future):** API (Part 14), background jobs (Part 15), AI (Part 16).

### 3.3 Performance Implications

The plan does not acknowledge the performance cost of:
- **Field-level security:** Per-query field filtering adds overhead, especially with 600+ field-permission entries.
- **Per-object authorization:** 5 checks per object fetch (society, permission, FY, archive, visibility) increase query count unless cached.
- **Audit-everything:** Doubling write volume for audit entries.
- **Permission registry lookups:** Checking `has_permission()` on every request requires caching the user's permission set (e.g., in the session or Redis).

The plan should specify a caching strategy (permission set cached on `request.user` after first lookup) and benchmark targets.

### 3.4 Testing Strategy

The audit ([`RBAC_ACCESS_MANAGEMENT_AUDIT.md`](documentation/RBAC_ACCESS_MANAGEMENT_AUDIT.md:441)) notes that the current RBAC tests exercise decorators in isolation but no production view's authorization path. The plan does not specify a testing strategy for the new system. Required:
- A cross-tenant IDOR test suite asserting `user_a` cannot access `society_b` objects via every production URL.
- A permission matrix test suite asserting each role can/cannot perform each action.
- A test base class (extending [`core/`](core/db_router.py:1) test base) that auto-generates cross-tenant access tests for all tenant-scoped models.

### 3.5 Backward Compatibility During Migration

The plan does not address how to migrate without breaking existing functionality. Key concerns:
- Existing [`Membership.role`](societies/models/model_Membership.py:22) values must map to new roles/permissions.
- Existing views must continue to function while permission checks are incrementally added.
- The [`has_role_or_above()`](societies/permissions.py:4) function is referenced by the (unused) decorators — replacing it must not break imports.

The plan should specify a compatibility shim: `has_role_or_above()` delegates to `has_permission()` with a default role-to-permission mapping during migration.

### 3.6 Database Migration Complexity

Adding 7+ fields (Part 7) to ~40 models is a large migration. The plan does not address:
- Migration ordering (per-app vs single mega-migration).
- Backfill strategy (UUIDs, versions, statuses for existing rows).
- Downtime (adding non-nullable fields to large tables locks them).
- The [`DatabaseRouter`](core/db_router.py:3) — migrations for `analytics`/`archive` apps must be handled separately.

### 3.7 Django Admin Interface Security

The plan does not address the Django admin. [`is_super_admin`](housing_accounting/users/models.py:33) is exposed in [`housing_accounting/users/admin.py`](housing_accounting/users/admin.py:30) with no guardrail (V-13). The Django admin bypasses application-level authorization (it uses `is_superuser`/`is_staff`). The plan should specify:
- Whether the Django admin is enabled in production.
- If enabled, how tenant isolation is enforced (the admin does not use `TenantManager`).
- Guardrails on `is_super_admin`/`is_superuser` assignment (break-glass procedure, audit).

### 3.8 Template-Level Permission Checks

The plan mentions "Never trust frontend" (Part 20) but does not specify how server-rendered templates handle permissions. Templates need:
- A `{% has_permission %}` template tag for conditional rendering (hide buttons the user cannot use).
- A `FieldVisibility`-aware context processor that strips inaccessible fields before rendering.
- Recognition that template-level checks are *UX*, not *security* — the view/manager must enforce independently.

### 3.9 WebSocket / Real-Time Security

The plan does not address WebSocket/real-time security. If the platform adds real-time features (e.g., live gate event updates), the authorization model must extend to WebSocket consumers. This is a future concern but should be noted.

### 3.10 Multi-Database Considerations

The [`DatabaseRouter`](core/db_router.py:3) routes `analytics`/`archive` to separate DBs. The plan's `TenantManager` and audit log must account for this:
- Cross-database queries are not possible in Django ORM.
- Audit entries for `analytics`/`archive` models must be stored in the same DB as the model.
- The `TenantManager` must not attempt to scope cross-database relations.

---

## 4. Implementation Order Analysis

### 4.1 Dependency Graph

```
Part 1 (Philosophy) ─── no deps
Part 5 (Permission Registry) ─── no deps (foundational)
Part 7 (Data Isolation / TenantModel) ─── no deps (foundational)
Part 13 (Audit Log) ─── depends on Part 7 (society FK)
   │
   ├── Part 8 (Object Security / TenantManager) ─── depends on Part 7, Part 5
   │      │
   │      ├── Part 6 (Permission Matrix) ─── depends on Part 5, Part 4
   │      ├── Part 4 (Role Hierarchy) ─── depends on Part 5
   │      ├── Part 17 (Notification Security) ─── depends on Part 8
   │      ├── Part 18 (Reports) ─── depends on Part 5
   │      └── Part 20 (Anti-patterns) ─── enforced by Parts 5, 7, 8
   │
   ├── Part 12 (Super Admin Impersonation) ─── depends on Part 13, Part 11
   ├── Part 10 (Workflow Security) ─── depends on Part 5, Part 13, Part 3
   ├── Part 9 (Field-Level Security) ─── depends on Part 5, Part 8
   ├── Part 11 (Session Management) ─── no deps (independent)
   ├── Part 3 (User Identity) ─── depends on Part 4, Part 5
   ├── Part 2 (Admin Hierarchy) ─── depends on Part 4, Part 5
   │
   └── Part 14 (API Security) ─── depends on Part 5, Part 13 (DEFERRED)
         ├── Part 15 (Background Jobs) ─── depends on Part 5, Part 13, Part 3 (DEFERRED)
         └── Part 16 (AI Security) ─── depends on Part 14, Part 5 (DEFERRED)

Part 19 (Database Security) ─── partially depends on Part 7, Part 13; mostly ops
```

### 4.2 Parallelizable Work

The following can be built in parallel (no inter-dependencies):
- **Track A:** Part 5 (Permission Registry) + Part 4 (Roles)
- **Track B:** Part 7 (TenantModel / Data Isolation fields)
- **Track C:** Part 11 (Session Management / 2FA)
- **Track D:** Part 13 (Audit Log — can start once Part 7's `society` FK exists, which it already does on most models)

### 4.3 Prerequisites

- **Part 5 is the critical path.** Parts 4, 6, 8, 9, 10, 14, 15, 16, 17, 18 all depend on the permission registry. It must be built first.
- **Part 7 is the second critical path.** Parts 8, 13 depend on the `TenantModel` base class and audit fields.
- **Part 8 depends on both Part 5 and Part 7.** It is the convergence point where authorization and isolation meet.

### 4.4 Recommended Phased Approach

| Phase | Parts | Milestone | Effort |
|---|---|---|---|
| **Phase 0 — Immediate Patch** | Fix V-1, V-2, V-7 | CRITICAL vulnerabilities closed | S |
| **Phase 1 — Foundation** | Part 5, Part 7, Part 13 | Permission registry, TenantModel, platform audit log | L |
| **Phase 2 — Authorization** | Part 8, Part 4, Part 6, Part 20 | All views permission-gated; TenantManager active | L |
| **Phase 3 — Hardening** | Part 12, Part 10, Part 11, Part 9, Part 17, Part 18 | Impersonation, workflows, 2FA, field security | XL |
| **Phase 4 — Future** | Part 14, Part 15, Part 16, Part 2 (full), Part 3 (full) | API, Celery, AI, full hierarchy | XL |
| **Continuous** | Part 1, Part 19 | Philosophy document, ops hardening | M |

---

## 5. Codebase-Specific Recommendations

### 5.1 Mapping the Plan to the Existing Django App Structure

| Plan Module | Django App | Notes |
|---|---|---|
| Accounting | [`accounting/`](accounting/models/model_Voucher.py:1) | Vouchers, ledger, accounts, FY |
| Billing | [`billing/`](billing/views.py:1), [`housing/services/billing.py`](housing/services/billing.py:1) | Split between billing app and housing services |
| Members | [`members/`](members/models/model_Member.py:1) | Members, nominees |
| Visitors | [`gateops/`](gateops/views.py:1) | Gate operations |
| Parking | [`parking/`](parking/views/main.py:1) | Parking slots, vehicles |
| Complaints | *(does not exist)* | New app needed |
| Reports | [`reports/`](reports/views.py:1) | Reporting engine |
| Assets | *(does not exist)* | New app needed |
| Bank | [`reconciliation/`](reconciliation/views.py:1) | Bank reconciliation |
| Reconciliation | [`reconciliation/`](reconciliation/views.py:1) | Same as Bank — consolidate |
| Documents | *(does not exist)* | New app needed |
| Notice Board | *(does not exist)* | New app needed |
| Meetings | *(does not exist)* | New app needed |
| Inventory | *(does not exist)* | New app needed |
| Vendor | *(does not exist)* | New app needed |
| Payroll | *(does not exist)* | New app needed |
| Settings | [`societies/`](societies/models/model_SocietyConfig.py:1) | Society config |
| Analytics | *(analytics DB via [`DatabaseRouter`](core/db_router.py:3))* | Separate DB |
| AI | *(does not exist)* | Future |
| API | *(does not exist)* | Future |

**Recommendation:** The plan's 20 modules map to ~12 existing apps and ~8 future apps. The permission registry should namespace by *existing* app labels first, adding future modules as they are built.

### 5.2 Files Needing Modification vs New Files

**Modify:**
- [`societies/roles.py`](societies/roles.py:1) — expand role catalog or replace with `Role` model.
- [`societies/permissions.py`](societies/permissions.py:1) — add `has_permission()`, deprecate `has_role_or_above()`.
- [`societies/decorators.py`](societies/decorators.py:1) — replace with permission-based decorator or remove (middleware enforces).
- [`societies/middleware.py`](societies/middleware.py:5) — add permission context, request ID, audit instrumentation.
- [`societies/services.py`](societies/services.py:1) — remove `is_super_admin` bypass, integrate with permission registry.
- [`housing_accounting/users/models.py`](housing_accounting/users/models.py:14) — add Profile relation, deprecate bare `is_super_admin`.
- [`core/db_router.py`](core/db_router.py:3) — ensure audit log routing is consistent.
- [`config/settings/base.py`](config/settings/base.py:210) — add audit/permission middleware.
- Every tenant-scoped model (add `TenantModel` inheritance, audit fields).
- Every view (add permission checks / `TenantScopeMixin`).

**New:**
- `societies/permissions_registry.py` — the `PermissionRegistry` class.
- `societies/managers.py` — `TenantManager`, `TenantQuerySet`.
- `societies/models/model_Permission.py` — `Permission`, `RolePermission` models.
- `societies/models/model_Role.py` — `Role` model (if replacing ordinal roles).
- `societies/models/model_Delegation.py` — Delegation, TemporaryAccess.
- `societies/models/model_ImpersonationSession.py` — Super-admin impersonation.
- `auditlog/models/model_AuditLog.py` — platform-wide audit log (evolve placeholder).
- `societies/mixins.py` — `TenantScopeMixin`, `PermissionRequiredMixin`.
- `societies/utils.py` — `get_tenant_object_or_404()`, `has_permission()`, `tenant_context()`.

### 5.3 Handling the Existing `societies/` RBAC Module

**Recommendation: Evolve, do not replace.** The [`societies/`](societies/roles.py:1) module has the correct *structure* (roles, permissions, decorators, middleware, services) but incomplete *implementation*. Evolving it:
- [`roles.py`](societies/roles.py:1) → expand to a `Role` model with default permissions, keeping the existing constants as aliases during migration.
- [`permissions.py`](societies/permissions.py:1) → add `has_permission()` alongside `has_role_or_above()` (deprecated but functional).
- [`decorators.py`](societies/decorators.py:1) → replace `role_required(min_role)` with `permission_required(code)`, keeping the old decorator as a compatibility shim.
- [`middleware.py`](societies/middleware.py:5) → extend to populate `request.user_permissions` (cached permission set).
- [`services.py`](societies/services.py:1) → remove `is_super_admin` bypass from `get_accessible_societies_qs()` and `user_has_society_access()`.

### 5.4 Handling the Existing `auditlog/` App

**Recommendation: Evolve from placeholder to platform-wide audit log.** The [`auditlog/models.py`](auditlog/models.py:1) file is currently a docstring placeholder. It should be replaced with an `AuditLog` model modeled on the proven [`GateOpsAuditLog`](gateops/models/model_GateOpsAuditLog.py:6) pattern:
- Same immutability enforcement (`save()` rejects updates, `delete()` raises `PermissionError`).
- Same field set (society, actor, action, entity_type, entity_id, before_value, after_value, ip_address, device_info) plus `request_id`, `session_id`, `user_agent`, `module`, `duration`.
- A `log()` classmethod for ergonomic creation.
- Signal-based auto-logging for audited models (opt-in via a mixin or `AuditOptions`).

The existing [`GateOpsAuditLog`](gateops/models/model_GateOpsAuditLog.py:6) should either be migrated to the platform `AuditLog` (with a `module='gateops'` discriminator) or retained as a gateops-specific log with a foreign key to the platform log. Consolidation is cleaner but requires a gateops migration.

### 5.5 Database Migration Strategy for Tenant/Audit Fields

1. **Create the `TenantModel` abstract base class** with all new fields as nullable initially.
2. **Per-app migrations:** Each app gets a migration that adds the `TenantModel` fields to its models (nullable). This avoids a single mega-migration.
3. **Backfill:** A data migration populates `uuid` (via `uuid4()`), `version` (set to 1), `is_deleted` (set to False), `status` (set to 'active') for existing rows. `created_by` is backfilled from `society.created_by` where available.
4. **Enforce:** A follow-up migration makes critical fields non-nullable (`uuid`, `is_deleted`, `version`).
5. **Manager:** Swap the default manager to `TenantManager` once all models have the fields.
6. **Index:** Add indexes on `(society, is_deleted)`, `(society, uuid)`.

### 5.6 Introducing the Permission Registry Without Breaking Functionality

1. **Define the registry** in `societies/permissions_registry.py` as a Python class with all permission codes declared as constants.
2. **Create `Permission` and `RolePermission` models** but do not enforce them yet.
3. **Populate default permissions** for existing roles (owner → all, admin → most, accountant → financial, member → read, viewer → read-limited) via a data migration.
4. **Add `has_permission()`** but do not call it from any view yet.
5. **Incrementally apply** `permission_required(code)` to views, app by app, starting with the CRITICAL vulnerability views (V-1, V-2).
6. **Add a system check** that validates all `RolePermission` entries reference valid registry codes.
7. **Remove `has_role_or_above()`** calls only after all views use `has_permission()`.

---

## 6. Effort & Scope Assessment

### 6.1 Per-Section Effort Estimates

| Part | Description | Effort | Rationale |
|---|---|---|---|
| 1 | Security Philosophy | S | Documentation only |
| 2 | Administration Hierarchy | L | New models, FKs, migration; defer Regional/Department |
| 3 | User Identity | L | Profile, Delegation, TemporaryAccess models; defer extended types |
| 4 | Role Hierarchy | L | 30 roles, migration from 5 ordinal roles |
| 5 | Permission Architecture | L | Registry, Permission/RolePermission models, 280 codes |
| 6 | Permission Matrix | M | Generated from registry; policy model for conditions |
| 7 | Data Isolation | XL | TenantModel, 7 fields × ~40 models, migrations, backfill |
| 8 | Object Level Security | L | TenantManager, TenantScopeMixin, refactor all views |
| 9 | Field Level Security | XL | Novel for Django; 600+ field-permission entries; template integration |
| 10 | Workflow Security | XL | Maker-checker engine, approval chains, state machines |
| 11 | Session Management | M | 2FA via django-otp, device tracking |
| 12 | Super Admin | M | ImpersonationSession model, audit integration |
| 13 | Audit | L | Evolve auditlog, signal-based logging, immutability enforcement |
| 14 | API Security | XL | New DRF infrastructure, JWT, rate limiting (DEFERRED) |
| 15 | Background Jobs | L | Celery, Redis, task tenant context (DEFERRED) |
| 16 | AI Security | S | Documentation only until AI exists (DEFERRED) |
| 17 | Notification Security | M | Add recipient FK, filter views |
| 18 | Reports | M | Permission checks on report views |
| 19 | Database Security | L | Mostly ops; soft-delete/versioning overlap with Part 7 |
| 20 | Things To Avoid | S | Documentation + lint rules |

**Legend:** S = Small (days), M = Medium (1–2 weeks), L = Large (3–6 weeks), XL = Extra Large (6+ weeks).

### 6.2 Total Estimated Effort

- **Phase 0 (Immediate Patch):** S (1–2 days) — fixes V-1, V-2, V-7.
- **Phase 1 (Foundation):** L (8–10 weeks) — Parts 5, 7, 13.
- **Phase 2 (Authorization):** L (8–10 weeks) — Parts 8, 4, 6, 20.
- **Phase 3 (Hardening):** XL (12–16 weeks) — Parts 12, 10, 11, 9, 17, 18.
- **Phase 4 (Future):** XL (16+ weeks) — Parts 14, 15, 16, 2 (full), 3 (full).
- **Total (Phases 0–3):** ~30–38 weeks (7–9 months) of focused engineering effort.

### 6.3 Deferrable vs Critical for Immediate Security

**Critical for immediate security (must be in Phase 0–1):**
- Fix V-1 (unscoped voucher fetch) and V-2 (SocietyAdminView) — Phase 0.
- Permission registry (Part 5) — without it, no granular authorization is possible.
- TenantManager (Part 7/8) — without it, isolation remains convention-based.
- Platform audit log (Part 13) — without it, breaches are undetectable.

**Deferrable (Phase 3+):**
- Field-level security (Part 9) — defense-in-depth, not primary control.
- Workflow security (Part 10) — valuable but not a vulnerability fix.
- 2FA (Part 11) — hardening, not remediation.
- API/AI/Background jobs (Parts 14, 15, 16) — no infrastructure exists.

### 6.4 Minimum Viable Security Architecture (MVSA)

The *minimum* architecture to close all 13 vulnerabilities and establish a defensible baseline:

1. **Fix V-1, V-2, V-7** (scoped lookups, authorization on admin views) — Phase 0.
2. **Permission registry** with 5 existing roles mapped to default permission sets — Part 5 (minimal).
3. **TenantManager** auto-scoping all tenant-scoped models — Part 7/8 (minimal).
4. **`permission_required()` decorator** applied to all state-changing views — Part 8 (minimal).
5. **Platform audit log** on all writes — Part 13 (minimal, modeled on [`GateOpsAuditLog`](gateops/models/model_GateOpsAuditLog.py:6)).
6. **Remove `is_super_admin` bypass** from [`get_accessible_societies_qs()`](societies/services.py:15) and [`user_has_society_access()`](societies/services.py:26) — Part 12 (minimal).

This MVSA closes all CRITICAL and HIGH vulnerabilities, establishes defense-in-depth (manager + view + audit), and provides the foundation for the full architecture. Estimated effort: 4–6 weeks.

---

## 7. Recommendations for the Full Document

### 7.1 Suggested Structure Adjustments for the 150–250 Page Document

The proposed 20-part structure is conceptually sound but should be reorganized for the full document to reflect *implementation order* and *dependency depth*:

**Volume I — Foundations (Parts 1, 5, 7, 13)**
- Security Philosophy
- Permission Registry & Architecture
- Data Isolation & TenantModel
- Audit Trail

**Volume II — Authorization (Parts 4, 6, 8, 20)**
- Role Hierarchy
- Permission Matrix
- Object-Level Security & TenantManager
- Anti-Patterns & Enforcement

**Volume III — Advanced Controls (Parts 9, 10, 11, 12)**
- Field-Level Security
- Workflow Security
- Session Management
- Super-Admin Impersonation

**Volume IV — Domain Security (Parts 2, 3, 17, 18)**
- Administration Hierarchy
- User Identity
- Notification Security
- Reports

**Volume V — Future & Infrastructure (Parts 14, 15, 16, 19)**
- API Security
- Background Jobs
- AI Security
- Database Security

### 7.2 What to Consolidate

- **Parts 5 and 6** (Permission Architecture + Permission Matrix) should be one chapter — the matrix is a view of the architecture, not a separate concern.
- **Parts 7 and 8** (Data Isolation + Object Security) should be one chapter — they are the same enforcement layer (manager + mixin).
- **Parts 14, 15, 16** (API, Background Jobs, AI) should be one chapter on "Non-Interactive Access" — they share the same pattern (system identity + tenant context + permission check + audit).

### 7.3 What to Expand

- **Migration Strategy** — the plan has no migration section. The full document needs a dedicated chapter on migrating from the current broken RBAC, including the phased approach (§4.4), the compatibility shim, and per-app migration order.
- **Django-Specific Mechanics** — each part should include a "Django Implementation" subsection specifying the manager, mixin, signal, middleware, or template tag mechanism.
- **Testing Strategy** — a dedicated chapter on the cross-tenant IDOR test suite, permission matrix tests, and the test base class.
- **Performance** — a chapter on caching the permission set, audit log async strategies, and benchmark targets.

### 7.4 Additional Sections to Consider

- **Threat Model** — the plan jumps to controls without documenting the threats. A threat model (STRIDE or similar) per module would justify the controls.
- **Django Admin Security** — the plan ignores the admin. A section on admin hardening (or disabling) is essential.
- **Template-Level Security** — the plan mentions "never trust frontend" but does not specify template tag patterns.
- **Operational Runbooks** — incident response for audit anomalies, impersonation abuse, permission drift.
- **Compliance Mapping** — if the platform targets Indian housing societies, map controls to relevant regulations (GST, society bye-laws, data protection).

### 7.5 Format Recommendations for AI Ingestion

- **Semantic anchors:** Every code reference as a clickable link with file path and line number (e.g., [`has_role_or_above()`](societies/permissions.py:4)), consistent with the audit document's convention.
- **Structured headings:** Hierarchical `##`/`###`/`####` with stable IDs for cross-referencing.
- **Tables for matrices:** Permission matrix, role hierarchy, vulnerability mapping — all as Markdown tables.
- **Code blocks:** Django code examples (models, managers, mixins) in fenced Python blocks with file path comments.
- **Decision records:** Each architectural decision as an ADR (Architecture Decision Record) with Context, Decision, Consequences.
- **Machine-readable appendix:** A YAML/JSON export of the permission registry and role-permission matrix for tooling consumption.

---

*End of Analysis Document.*
