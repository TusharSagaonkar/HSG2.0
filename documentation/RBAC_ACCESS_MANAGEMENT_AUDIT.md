# Role-Based Access Control & Access Management — Technical Audit Report

> **Document Type:** Analytical Audit Report (AI-Optimized)
> **Scope:** Role-Based Access Control (RBAC), Access Management, Multi-Tenant Isolation
> **Audit Date:** 2026-07-07
> **Target System:** Housing Accounting Platform (`housing_accounting`)
> **Framework:** Django 5.x + django-allauth
> **Tenant Model:** Single-database, shared-schema, society-scoped multi-tenancy

---

## 0. Document Conventions

This report is structured for deterministic downstream ingestion, parsing, and analysis by an artificial intelligence system. The following conventions apply:

- **Semantic Anchors:** Every code reference is expressed as a clickable link with a relative file path and line number, e.g. [`declaration()`](relative/file/path.ext:line).
- **Severity Taxonomy:** `CRITICAL` (direct data breach / privilege escalation), `HIGH` (exploitable with constraints), `MEDIUM` (defense-in-depth gap), `LOW` (maintainability / consistency).
- **Identifier Scheme:** Findings are keyed `F-<n>`; vulnerabilities `V-<n>`; recommendations `R-<n>`.
- **Evidence Format:** Each finding cites the exact source artifact and the operative lines.

---

## 1. Executive Summary

The platform implements a **session-scoped, role-hierarchical RBAC model** layered on top of a **shared-schema multi-tenant architecture**. Tenant isolation is achieved *predominantly through application-level queryset filtering* keyed on a `society` foreign key, with the active tenant resolved per-request via a custom middleware and a session-persisted selection mechanism.

The audit identifies a **fundamental architectural tension**: a robust, well-tested tenant-selection and role-hierarchy *primitive layer* exists in the [`societies`](societies/roles.py:1) app, but it is **systematically under-applied** across the production view layer. The canonical decorators [`role_required()`](societies/decorators.py:25) and [`society_access_required()`](societies/decorators.py:10) are **defined and unit-tested but never applied to any production view** — they appear exclusively in [`societies/tests/test_rbac.py`](societies/tests/test_rbac.py:120). Consequently, the majority of the application enforces only authentication (`LoginRequiredMixin` / `@login_required`) and relies on *convention-based* society filtering, producing a broad surface of **Insecure Direct Object Reference (IDOR)** vulnerabilities and **missing authorization** gaps.

Cross-tenant access for users belonging to multiple societies is supported structurally (a `User` may hold multiple `Membership` rows), but the runtime enforcement of *which* tenant a request operates within is **session-stateful and mutable**, introducing a class of cross-tenant confusion risks.

**Headline Vulnerability Counts:** 2 `CRITICAL`, 5 `HIGH`, 4 `MEDIUM`, 3 `LOW`.

---

## 2. System Architecture — Access Management Components

### 2.1 Component Inventory

| Component | Artifact | Responsibility |
|---|---|---|
| User Identity | [`User`](housing_accounting/users/models.py:14) | Custom user model; email-based; carries `is_super_admin` flag |
| Tenant Entity | [`Society`](societies/models/model_Society.py:4) | Root tenant aggregate; owns all society-scoped data |
| Tenant Binding | [`Membership`](societies/models/model_Membership.py:4) | Join table `User ↔ Society` with `role` + `is_active` |
| Role Catalog | [`societies/roles.py`](societies/roles.py:1) | Five-level role hierarchy with numeric weights |
| Permission Primitives | [`societies/permissions.py`](societies/permissions.py:1) | `has_role_or_above()`, `can_assign_role()` |
| Access Service | [`societies/services.py`](societies/services.py:1) | Tenant queryset scoping, society access checks, user provisioning |
| Tenant Resolution | [`housing_accounting/selection.py`](housing_accounting/selection.py:1) | Session-persisted active society + financial year |
| Request Middleware | [`SocietyMiddleware`](societies/middleware.py:5) | Populates `request.current_society` / `request.current_membership` |
| Decorators | [`societies/decorators.py`](societies/decorators.py:1) | `role_required()`, `society_access_required()` (unused in prod) |
| Selection Mutation | [`GlobalSelectionUpdateView`](housing_accounting/users/views.py:56) | POST endpoint to switch active society/financial year |
| Context Processor | [`global_selection()`](housing_accounting/users/context_processors.py:17) | Exposes selection state to templates |
| Database Router | [`DatabaseRouter`](core/db_router.py:3) | Routes `analytics`/`archive` app labels to separate DBs |

### 2.2 Authentication Layer

Authentication is delegated to **django-allauth** with email-only login (`ACCOUNT_LOGIN_METHODS = {"email"}`), mandatory email verification ([`ACCOUNT_EMAIL_VERIFICATION = "mandatory"`](config/settings/base.py:369)), and Argon2 as the primary password hasher ([`config/settings/base.py`](config/settings/base.py:190)). Two authentication backends are registered: Django's `ModelBackend` and allauth's `AuthenticationBackend` ([`config/settings/base.py`](config/settings/base.py:176)).

The custom user model ([`User`](housing_accounting/users/models.py:14)) extends `AbstractUser`, removes the `username` field (email becomes `USERNAME_FIELD`), and adds two privilege flags:

- `is_superuser` — inherited from `AbstractUser`; Django admin / ORM superuser.
- `is_super_admin` — custom application-level platform administrator flag ([`housing_accounting/users/models.py`](housing_accounting/users/models.py:33)).

These two flags are treated as **equivalent bypass tokens** throughout the access layer (see §4.1).

---

## 3. Role-Based Access Control — Existing Implementation

### 3.1 Role Hierarchy

The role system is a **fixed, five-level linear hierarchy** defined in [`societies/roles.py`](societies/roles.py:1):

```python
ROLE_HIERARCHY = {
    ROLE_OWNER: 100,
    ROLE_ADMIN: 80,
    ROLE_ACCOUNTANT: 60,
    ROLE_MEMBER: 40,
    ROLE_VIEWER: 20,
}
```

Roles are stored as `TextChoices` on [`Membership.Role`](societies/models/model_Membership.py:5) (`owner`, `admin`, `accountant`, `member`, `viewer`). The hierarchy is **ordinal and total** — there is no concept of role separation, scoped permissions, or feature-level grants. Authorization is expressed exclusively as *"minimum role X or above"*.

### 3.2 Permission Primitives

Two pure functions in [`societies/permissions.py`](societies/permissions.py:1) implement the core logic:

- [`has_role_or_above(user_role, required_role)`](societies/permissions.py:4) — numeric comparison against `ROLE_HIERARCHY`.
- [`can_assign_role(assigner_role, target_role)`](societies/permissions.py:8) — strict greater-than (`assigner > target`); an `admin` (80) cannot assign `owner` (100), and cannot assign another `admin` (80 ≯ 80).

### 3.3 Membership Model

[`Membership`](societies/models/model_Membership.py:4) is the tenant-binding aggregate:

- **Uniqueness:** `UniqueConstraint(fields=("user", "society"))` — a user may hold at most one membership row per society ([`societies/models/model_Membership.py`](societies/models/model_Membership.py:36)). This is the structural enabler for cross-tenant membership.
- **Soft deactivation:** `is_active` boolean; deactivated memberships are excluded from access checks but retained for audit.
- **Provenance:** `invited_by` FK records the inviting user.
- **Indexing:** Three indexes optimize the common lookup paths (`society`, `user+society`, `society+is_active`).

### 3.4 Access Service Functions

[`societies/services.py`](societies/services.py:1) provides four service functions:

1. [`get_accessible_societies_qs(user)`](societies/services.py:11) — returns all societies for super-admin/superuser; otherwise societies where the user has an active membership. This is the **canonical tenant-scope queryset** and is correctly applied in the context processor ([`global_selection()`](housing_accounting/users/context_processors.py:44)) and the selection update view.

2. [`user_has_society_access(user, society)`](societies/services.py:23) — boolean membership existence check with super-admin bypass.

3. [`create_society()`](societies/services.py:36) — atomically creates a `Society` and grants the creator an `OWNER` membership.

4. [`create_user_by_admin()`](societies/services.py:54) — enforces `can_assign_role()` before provisioning a new user with a society membership. This is one of the few places where role-assignment authorization is correctly enforced.

5. [`transfer_ownership()`](societies/services.py:79) — verifies the caller is the current owner before demoting them and promoting the new owner.

### 3.5 Decorators (Defined but Unused in Production)

[`societies/decorators.py`](societies/decorators.py:1) defines two view-level authorization decorators:

- [`society_access_required`](societies/decorators.py:10) — verifies the user has *any* active membership in `request.current_society`.
- [`role_required(min_role)`](societies/decorators.py:25) — verifies the user's role in `request.current_society` meets the minimum threshold.

Both decorators implement a **super-admin bypass** (`getattr(request.user, "is_super_admin", False)`). Both depend on `request.current_society` being populated by [`SocietyMiddleware`](societies/middleware.py:5).

**Critical Finding F-1:** A repository-wide search confirms these decorators are **referenced only in test code** ([`societies/tests/test_rbac.py`](societies/tests/test_rbac.py:120), [`societies/tests/test_rbac.py`](societies/tests/test_rbac.py:147)). No production view applies them. The RBAC primitive layer is therefore **inert in the running application**.

---

## 4. Multi-Tenant Architecture — Tenant Isolation Mechanics

### 4.1 Tenant Resolution Pipeline

The active tenant is resolved per-request through a stateful, session-backed pipeline:

1. **Middleware** ([`SocietyMiddleware.__call__()`](societies/middleware.py:9)): For authenticated users, calls [`get_selected_scope(request, persist=True)`](housing_accounting/selection.py:78) and stores the result as `request.current_society` and `request.current_membership` (via [`get_user_membership()`](societies/utils.py:4)).

2. **Scope Loader** ([`_load_scope_from_session()`](housing_accounting/selection.py:46)): Reads `selected_society_id` from the session, validates it against [`get_accessible_societies_qs()`](societies/services.py:11) (so a stale/unauthorized society ID is silently ignored), and falls back to `accessible_societies.first()`. The financial year is resolved similarly.

3. **Scope Cache** ([`get_selected_scope()`](housing_accounting/selection.py:78)): Caches the resolved `(society, financial_year)` tuple on `request._selection_scope_cache` to avoid repeated DB hits within a single request.

4. **Selection Mutation** ([`GlobalSelectionUpdateView`](housing_accounting/users/views.py:56)): A POST endpoint that accepts `selected_society_id`, validates it against the accessible queryset, and writes it to the session. This is the **only sanctioned mechanism** for switching tenants.

**Positive Finding F-2:** The selection loader correctly defends against session tampering. If a user's session contains a `selected_society_id` for a society they cannot access, [`_load_scope_from_session()`](housing_accounting/selection.py:46) filters it through `accessible_societies` and silently falls back. This is verified by [`test_selected_scope_ignores_unauthorized_society_in_session`](societies/tests/test_rbac.py:83) and [`test_selection_update_rejects_unauthorized_society`](societies/tests/test_rbac.py:96).

### 4.2 Data-Level Tenant Isolation

Tenant isolation is **not enforced at the database or ORM manager level**. There is no custom `QuerySet` manager that auto-filters by `society`, and no database-level row-level security. Isolation is achieved **per-view** by explicitly calling `.filter(society=selected_society)` on querysets.

**Pattern A — Correct Society Scoping (List Views):** Most list views follow the convention of resolving `selected_society` via [`get_selected_scope()`](housing_accounting/selection.py:78) and filtering. Examples: [`AccountListView.get_queryset()`](accounting/views.py:100), [`MemberListView.get_queryset()`](housing/views.py:863), [`VoucherListView.get_queryset()`](accounting/views.py:409), [`ShareLedgerListView`](shares/views.py:131).

**Pattern B — Correct Society-Scoped Object Fetch (GateOps):** The gateops module establishes a strong convention with helper functions that scope single-object fetches by society, raising `Http404` on cross-tenant access:
- [`_gate_event_or_404()`](gateops/views.py:732) — `get_object_or_404(_gate_event_queryset(society), event_uuid=uuid)`
- [`_pass_or_404()`](gateops/views.py:981) — `get_object_or_404(Pass, pk=pk, society=society)`
- [`_gate_vehicle_or_404()`](gateops/views.py:1200) — `get_object_or_404(GateVehicle, pk=pk, society=society, is_active=True)`
- [`_material_movement_or_404()`](gateops/views.py:1452) — `get_object_or_404(MaterialMovement, pk=pk, society=society, is_active=True)`
- [`_parcel_or_404()`](gateops/views.py:1726) — `get_object_or_404(Parcel, pk=pk, society=society, is_active=True)`

**Pattern C — Post-Hoc Society Validation (Accounting Detail Views):** Some views fetch the object by PK alone, then compare `account.society_id != selected_society.id` and raise `Http404`. Examples: [`AccountLedgerView`](accounting/views.py:213), [`AccountLedgerExportCsvView`](accounting/views.py:281), [`MemberDetailView`](members/views.py:26). This is functionally safe but less efficient and more error-prone than scoping the lookup.

**Pattern D — Unscoped Object Fetch (VULNERABLE):** Several views fetch objects by PK with **no society filter at all**. See §6 (V-1, V-2).

### 4.3 Model-Level Tenant Integrity

Tenant integrity is partially enforced at the model `clean()` layer:

- [`Member.clean()`](members/models/model_Member.py:63) validates that `unit.structure.society_id == self.society_id` and `receivable_account.society_id == self.society_id`.
- [`LedgerEntry.clean()`](accounting/models/model_LedgerEntry.py:67) validates that `account.society_id == voucher.society_id` and `unit.structure.society_id == voucher.society_id`.

These are **model-level cross-tenant referential integrity guards** — they prevent a row in society A from referencing a row in society B. However, `clean()` is only invoked on `full_clean()` / `save()`; bulk operations and raw queries bypass it.

### 4.4 Database Router

[`DatabaseRouter`](core/db_router.py:3) routes the `analytics` and `archive` app labels to separate physical databases. This is **not** a tenant-isolation mechanism — it is an analytics/archive data-partitioning strategy. All tenant-scoped apps (`housing`, `accounting`, `gateops`, `members`, `shares`, `billing`, `receipts`, `parking`, `reconciliation`, `reports`, `notifications`) share the `default` database.

---

## 5. Cross-Tenant Access — Multi-Society User Support

### 5.1 Structural Support

The data model natively supports users belonging to multiple societies:

- A `User` may hold N `Membership` rows (one per society), constrained by `UniqueConstraint("user", "society")` ([`societies/models/model_Membership.py`](societies/models/model_Membership.py:36)).
- [`get_accessible_societies_qs()`](societies/services.py:11) returns all societies where the user has an active membership, enabling a society switcher UI.
- The session-persisted selection ([`SESSION_SELECTED_SOCIETY_ID`](housing_accounting/selection.py:6)) tracks the *currently active* tenant.

### 5.2 Operational Mechanics

A multi-society user operates within **exactly one tenant context per session**. The active tenant is:

1. Resolved by [`SocietyMiddleware`](societies/middleware.py:5) on every request.
2. Mutable via [`GlobalSelectionUpdateView`](housing_accounting/users/views.py:56) (POST with `selected_society_id`).
3. Exposed to templates via [`global_selection()`](housing_accounting/users/context_processors.py:17) context processor.

The role of the user is **per-tenant**: [`get_user_role(user, society)`](societies/utils.py:18) returns the role from the `Membership` row matching the *currently selected* society. A user may be `OWNER` in society A and `VIEWER` in society B.

### 5.3 Isolation Guarantee Under Multi-Tenancy

The intended guarantee: *a request operating under society A must never read or mutate data belonging to society B*. This guarantee is **upheld by Pattern A/B/C views** and **violated by Pattern D views** (§6). Because the active tenant is session-stateful, the guarantee is only as strong as the weakest view — a single unscoped object fetch breaks isolation for that resource class.

---

## 6. Vulnerability Register

### V-1 — CRITICAL: Unscoped Voucher Object Fetch (Cross-Tenant IDOR)

**Affected Views:**
- [`VoucherPostView`](accounting/views.py:986): `get_object_or_404(Voucher, pk=pk)` — no `society` filter.
- [`VoucherDeleteDraftView`](accounting/views.py:1000): `get_object_or_404(Voucher, pk=pk)` — no `society` filter.
- [`VoucherReverseView`](accounting/views.py:1016): `get_object_or_404(Voucher.objects..., pk=pk)` — no `society` filter.
- [`VoucherDetailView`](accounting/views.py:1067): `get_object_or_404(Voucher.objects..., pk=pk)` — no `society` filter.

**Impact:** Any authenticated user can post, delete (draft), reverse, or view any voucher in the system by its numeric primary key, **regardless of which society the voucher belongs to or which society the user has selected**. This is a direct cross-tenant financial data breach and mutation primitive. A user with membership only in society A can reverse a posted voucher in society B by guessing/enumerating the sequential `pk`.

**Root Cause:** These views resolve `selected_society` only for list/dashboard scoping but omit it for single-object operations. The `Voucher` model has a `society` FK ([`accounting/models/model_Voucher.py`](accounting/models/model_Voucher.py:32)) that is simply not used in the filter.

**Severity:** `CRITICAL`

### V-2 — CRITICAL: SocietyAdminView / SocietyUserCreateView Lack Authorization

**Affected Views:**
- [`SocietyAdminView`](housing/views.py:1433): A `DetailView` on `Society` with **no `get_queryset()` override and no role check**. Any authenticated user can view the membership roster (users, emails, roles, verification status) of *any* society by its `pk`.
- [`SocietyUserCreateView`](housing/views.py:1490): [`get_society()`](housing/views.py:1496) performs `Society.objects.get(pk=self.kwargs["pk"])` with **no membership/role check**. The downstream [`create_user_by_admin()`](societies/services.py:54) enforces role-assignment hierarchy, but a user with no membership in the target society can still *attempt* user creation; the `can_assign_role()` check uses [`get_user_role()`](societies/utils.py:18) which returns `None` for non-members, and `ROLE_HIERARCHY.get(None, 0)` = 0, so the `PermissionDenied` branch fires — but only *after* the view has already rendered the creation form and accepted a POST.

**Impact:** Information disclosure of every society's user roster and role assignments. The creation path is defended by the service layer but the **read path is fully open**.

**Root Cause:** `DetailView.get_object()` uses the default manager with no scoping; no `role_required` / `society_access_required` decorator is applied.

**Severity:** `CRITICAL`

### V-3 — HIGH: RBAC Decorators Defined but Never Applied

**Evidence:** [`role_required`](societies/decorators.py:25) and [`society_access_required`](societies/decorators.py:10) appear only in [`societies/tests/test_rbac.py`](societies/tests/test_rbac.py:120). No production URL is protected by them.

**Impact:** The entire authorization primitive layer is dead code in production. Views rely on `LoginRequiredMixin` (authentication) plus ad-hoc, inconsistent society filtering. There is no centralized, auditable authorization gate. Any view that forgets to filter by society (see V-1, V-2) has no fallback protection.

**Severity:** `HIGH`

### V-4 — HIGH: Inconsistent Role Checks in Membership Management

**Affected View:** [`UpdateMembershipView`](housing/views.py:1729) performs an inline role check:
```python
if not request.user.is_superuser:
    updater_membership = Membership.objects.get(user=request.user, society=society)
    if updater_membership.role not in ['owner', 'admin']:
        raise PermissionDenied(...)
```

**Flaws:**
1. Uses raw string literals `['owner', 'admin']` instead of `Membership.Role.OWNER` / `ADMIN` — fragile to renaming.
2. Bypasses the canonical [`has_role_or_above()`](societies/permissions.py:4) primitive, duplicating logic.
3. The `is_superuser` bypass omits `is_super_admin` (inconsistent with the decorators and services, which check both).
4. [`ResendVerificationEmailView`](housing/views.py:1629) uses the same ad-hoc pattern with `request.user.is_superuser` only.

**Impact:** Authorization logic is scattered and inconsistent; maintenance changes to the role hierarchy will not propagate to these inline checks.

**Severity:** `HIGH`

### V-5 — HIGH: Super-Admin Bypass Is Unconditional and Tenant-Agnostic

**Evidence:** Every access check short-circuits on `is_super_admin` or `is_superuser`:
- [`get_accessible_societies_qs()`](societies/services.py:15): returns *all* societies.
- [`user_has_society_access()`](societies/services.py:26): returns `True` for any society.
- [`role_required`](societies/decorators.py:29): returns the view result immediately.
- [`society_access_required`](societies/decorators.py:14): returns the view result immediately.

**Impact:** A super-admin operates **outside the tenant boundary entirely** — `request.current_society` may be `None` (see [`test_super_admin_bypass_works`](societies/tests/test_rbac.py:114)), and the view executes without any tenant context. For views that fall back to `get_selected_scope()` when `current_society` is `None`, the super-admin's data access is determined by session state, not by a declared scope. There is no audit trail binding super-admin actions to a specific tenant.

**Severity:** `HIGH`

### V-6 — HIGH: No Authorization on Most List/Detail Views (Authentication ≠ Authorization)

**Evidence:** The dominant pattern across all apps is `LoginRequiredMixin` with *no role gate*:
- [`AccountListView`](accounting/views.py:95), [`VoucherListView`](accounting/views.py:404), [`TrialBalanceView`](accounting/views.py:251), [`VoucherEntryView`](accounting/views.py:638)
- [`ReceiptListView`](receipts/views.py:12), [`ReceiptDetailView`](receipts/views.py:51)
- [`BillListView`](billing/views.py:126), [`BillDetailView`](billing/views.py:170)
- [`ReportsHomeView`](reports/views.py:33), all report views
- [`ParkingDashboardView`](parking/views/main.py:52), all parking views
- [`ReminderLogListView`](notifications/views.py:9)
- All gateops views use `@login_required` only.

**Impact:** Any authenticated user with *any* role (including `VIEWER`) in *any* society can access the full accounting, billing, receipts, reports, parking, and gateops interfaces for their selected society. There is no enforcement that, e.g., only `ACCOUNTANT`+ may post vouchers, or only `ADMIN`+ may manage parking policies. The role hierarchy is effectively **read-only advisory** in production.

**Severity:** `HIGH`

### V-7 — HIGH: VoucherTemplateScopeMixin Accepts Society via GET/POST Without Membership Check

**Evidence:** [`VoucherTemplateScopeMixin.get_selected_society()`](accounting/views.py:450):
```python
society_id = self.request.GET.get("society") or self.request.POST.get("society")
if society_id:
    return get_object_or_404(Society, pk=society_id)
```

**Impact:** A user can pass an arbitrary `?society=<pk>` query parameter and the mixin will resolve that society **without verifying the user has a membership in it**. Subsequent template queries are scoped to this society, leaking voucher template names, structures, and account references of other tenants. The fallback to `get_selected_scope()` is only reached when no `society` param is supplied.

**Severity:** `HIGH`

### V-8 — MEDIUM: Session-Based Tenant Selection Is Vulnerable to Concurrent-Window Confusion

**Evidence:** The active tenant is stored in `request.session[SESSION_SELECTED_SOCIETY_ID]` ([`housing_accounting/selection.py`](housing_accounting/selection.py:6)). A user with two browser tabs operating on different societies shares one session; switching in one tab silently re-scopes the other tab's subsequent requests.

**Impact:** A user performing a financial operation in society A who switches to society B in another tab may, on the next request in tab A, operate against society B — leading to accidental cross-tenant data mutation. The per-request `SCOPE_CACHE_ATTR` mitigates within a single request but not across the user's concurrent interactions.

**Severity:** `MEDIUM`

### V-9 — MEDIUM: `MemberDetailView` Uses Post-Hoc Validation Instead of Scoped Lookup

**Evidence:** [`MemberDetailView.get_object()`](members/views.py:31) fetches the member via `super().get_object()` (unscoped), then checks `member.society_id != selected_society.id` and raises `Http404`. While functionally safe, this pattern is fragile — a refactor that removes the check (or a view that copies the pattern without the check) immediately leaks data.

**Severity:** `MEDIUM`

### V-10 — MEDIUM: No Object-Level Permission on `Member.user` Linkage

**Evidence:** [`Member.user`](members/models/model_Member.py:27) is a nullable FK to `User`. A `Member` row may be linked to a `User` who has no `Membership` in that society. There is no validation in [`Member.clean()`](members/models/model_Member.py:63) that `self.user` has a `Membership` in `self.society`. This decouples the *resident* identity (`Member`) from the *access* identity (`Membership`), allowing a user to be associated as a member of a society's unit without holding an access membership.

**Severity:** `MEDIUM`

### V-11 — MEDIUM: `SocietyListView` Filters to Selected Society Only

**Evidence:** [`SocietyListView.get_queryset()`](housing/views.py:378) filters to `pk=selected_society.pk` when a society is selected, meaning the list view shows **only the currently selected society**, not all societies the user can access. This is a UX inconsistency rather than a security issue, but it means the "list" view cannot be used to enumerate accessible societies (the switcher in the context processor serves that role).

**Severity:** `LOW`

### V-12 — LOW: Role Hierarchy Is Linear with No Feature Scoping

**Evidence:** The five roles map to a single ordinal axis ([`societies/roles.py`](societies/roles.py:8)). There is no permission matrix (e.g., `can_post_voucher`, `can_manage_parking`). Consequently, the only authorization question the system can answer is "is the user's role ≥ X". Granular feature authorization (e.g., "viewers can see reports but not vouchers") is impossible without adding a parallel permission system.

**Severity:** `LOW`

### V-13 — LOW: `is_super_admin` Has No Admin UI or Guardrail

**Evidence:** `is_super_admin` is a plain `BooleanField` ([`housing_accounting/users/models.py`](housing_accounting/users/models.py:33)) exposed in the Django admin ([`housing_accounting/users/admin.py`](housing_accounting/users/admin.py:30)) with no additional guardrail, audit log, or break-glass procedure. Any user with admin access can grant platform-wide super-admin privileges.

**Severity:** `LOW`

---

## 7. Cross-Tenant Isolation — Deep Analysis

### 7.1 Isolation Enforcement Layers (Defense-in-Depth Assessment)

| Layer | Mechanism | Status |
|---|---|---|
| Database | Row-level security / separate schemas | ❌ Absent |
| ORM Manager | Auto-scoping queryset manager | ❌ Absent |
| Model `clean()` | Cross-FK society validation | ⚠️ Partial (Member, LedgerEntry only) |
| View queryset | Explicit `.filter(society=...)` | ⚠️ Inconsistent (Patterns A–D) |
| View object fetch | `get_object_or_404(..., society=...)` | ⚠️ Inconsistent (Pattern B in gateops only) |
| Decorator | `society_access_required` / `role_required` | ❌ Defined, unused |
| Middleware | `request.current_society` population | ✅ Present |
| Session validation | Selection filtered through accessible QS | ✅ Present |

The isolation model is **single-layer** (view queryset filtering) with **no fallback**. A single missing `.filter(society=...)` is a complete breach for that resource.

### 7.2 Strict Society-Level Isolation — Requirements for Enforcement

To enforce **strict society-level data isolation** while supporting cross-tenant users, the following invariants must hold:

1. **I-1 (Read Isolation):** Every queryset that returns tenant-scoped data must be filtered by the resolved `current_society`. No unscoped `Model.objects.all()` should reach a tenant-scoped model.

2. **I-2 (Write Isolation):** Every create/update/delete on a tenant-scoped model must be bound to `current_society` and validated against the user's membership in that society.

3. **I-3 (Object Fetch Isolation):** Every single-object lookup by PK/UUID must include `society=current_society` in the filter, returning 404 on mismatch.

4. **I-4 (Cross-FK Integrity):** Every FK from a tenant-scoped model to another tenant-scoped model must be validated to belong to the same society (enforced in `clean()` for `Member` and `LedgerEntry`; absent elsewhere).

5. **I-5 (Authorization Context):** The user's role in `current_society` must be checked before any state-changing operation.

**Current Compliance:** I-1 ⚠️, I-2 ⚠️, I-3 ❌ (V-1), I-4 ⚠️, I-5 ❌ (V-3, V-6).

### 7.3 Cross-Tenant Access Support — Requirements

To support users requiring entry to multiple societies:

1. **C-1 (Multi-Membership):** A user may hold multiple `Membership` rows. ✅ Enforced structurally.
2. **C-2 (Per-Tenant Role):** The user's role is resolved per `current_society`. ✅ Enforced via [`get_user_role()`](societies/utils.py:18).
3. **C-3 (Tenant Switching):** A sanctioned mechanism exists to change `current_society`. ✅ [`GlobalSelectionUpdateView`](housing_accounting/users/views.py:56).
4. **C-4 (Switch Validation):** The target society is validated against accessible societies. ✅ [`get_accessible_societies_qs()`](societies/services.py:11).
5. **C-5 (Switch Audit):** Tenant switches are logged. ❌ Absent — no audit entry is written when `current_society` changes.
6. **C-6 (Concurrency Safety):** Concurrent sessions/tabs do not interfere. ❌ Absent (V-8).

---

## 8. Recommendations

### R-1 — Apply `role_required` / `society_access_required` to All Production Views (Addresses V-3, V-6)

Migrate every `LoginRequiredMixin` / `@login_required` view to also apply `society_access_required` (minimum) and `role_required(<min_role>)` where appropriate. Establish a per-app role matrix:

| App | Min Role (List/Detail) | Min Role (Create/Update/Delete) |
|---|---|---|
| accounting | `VIEWER` | `ACCOUNTANT` |
| billing | `VIEWER` | `ACCOUNTANT` |
| receipts | `VIEWER` | `ACCOUNTANT` |
| reports | `VIEWER` | `ACCOUNTANT` |
| shares | `VIEWER` | `ACCOUNTANT` |
| members | `VIEWER` | `ACCOUNTANT` |
| housing (admin) | `ADMIN` | `ADMIN` |
| gateops | `MEMBER` | `ADMIN` |
| parking | `VIEWER` | `ADMIN` |
| reconciliation | `VIEWER` | `ACCOUNTANT` |

### R-2 — Scope All Single-Object Fetches by Society (Addresses V-1, V-9)

Replace every `get_object_or_404(Model, pk=pk)` on a tenant-scoped model with `get_object_or_404(Model, pk=pk, society=current_society)`. Prioritize the four voucher views in V-1 immediately. Adopt the gateops helper-function pattern ([`_pass_or_404()`](gateops/views.py:981)) as the project standard.

### R-3 — Add Authorization to SocietyAdminView and SocietyUserCreateView (Addresses V-2)

Override `get_queryset()` on `SocietyAdminView` to filter by `get_accessible_societies_qs(request.user)`, or apply `society_access_required`. Add a `role_required(ROLE_ADMIN)` gate to `SocietyUserCreateView` and `UpdateMembershipView`.

### R-4 — Fix VoucherTemplateScopeMixin Society Resolution (Addresses V-7)

Replace the raw `get_object_or_404(Society, pk=society_id)` in [`get_selected_society()`](accounting/views.py:450) with `get_accessible_societies_qs(request.user).filter(pk=society_id).first()` and reject if `None`.

### R-5 — Introduce a Tenant-Scoped Queryset Manager (Defense-in-Depth)

Create a custom `Manager` (e.g., `TenantManager`) that auto-filters by `request.current_society` when accessed within a request context, and a `TenantScopeMixin` for CBVs that overrides `get_queryset()` centrally. This makes isolation the default rather than a per-view convention.

### R-6 — Centralize Role Checks (Addresses V-4)

Replace all inline role checks (`role not in ['owner', 'admin']`) with calls to [`has_role_or_above()`](societies/permissions.py:4). Standardize the super-admin bypass to always check both `is_super_admin` and `is_superuser`.

### R-7 — Audit Tenant Switches (Addresses C-5)

Write an `AuditLog` entry in [`GlobalSelectionUpdateView`](housing_accounting/users/views.py:56) recording the actor, previous society, and new society on every switch.

### R-8 — Add Cross-FK Society Validation to All Tenant-Scoped Models (Addresses I-4)

Extend the `clean()` pattern from [`Member`](members/models/model_Member.py:63) and [`LedgerEntry`](accounting/models/model_LedgerEntry.py:67) to all models with inter-tenant FKs (e.g., `Bill → member`, `PaymentReceipt → member`, `ShareLedger → member`, parking models).

### R-9 — Consider Per-Request Tenant Token Over Session State (Addresses V-8)

Evaluate moving the active tenant from session state to a per-request header/cookie (e.g., `X-Society-Id`) validated against accessible societies, eliminating cross-tab interference. Alternatively, embed the society in the URL namespace (e.g., `/societies/<pk>/accounting/...`).

### R-10 — Introduce a Permission Matrix (Addresses V-12)

For granular feature authorization, introduce a `Permission` model (`code`, `description`) and a `RolePermission` join table, replacing the pure ordinal hierarchy with a capability-based system. Migrate the role hierarchy to a default permission set per role.

---

## 9. Test Coverage Assessment

The RBAC test suite ([`societies/tests/test_rbac.py`](societies/tests/test_rbac.py:1)) covers:

| Test | Invariant |
|---|---|
| [`test_create_society_assigns_owner_membership`](societies/tests/test_rbac.py:34) | Society creation grants OWNER |
| [`test_admin_cannot_assign_owner_role`](societies/tests/test_rbac.py:46) | Role-assignment hierarchy |
| [`test_accountant_cannot_create_admin`](societies/tests/test_rbac.py:62) | Role-assignment hierarchy |
| [`test_selected_scope_ignores_unauthorized_society_in_session`](societies/tests/test_rbac.py:83) | Session tampering defense |
| [`test_selection_update_rejects_unauthorized_society`](societies/tests/test_rbac.py:96) | Switch validation |
| [`test_super_admin_bypass_works`](societies/tests/test_rbac.py:114) | Super-admin bypass |
| [`test_django_superuser_gets_all_societies_in_access_queryset`](societies/tests/test_rbac.py:129) | Superuser queryset bypass |
| [`test_cross_society_access_blocked`](societies/tests/test_rbac.py:139) | Cross-society role_required denial |

**Coverage Gap:** All tests exercise the *decorators in isolation* (via `RequestFactory` with manually set `current_society`). **No test exercises a production view's authorization path**, because no production view applies the decorators. The tests prove the primitive layer works; they do not prove the application is secured. A dedicated cross-tenant IDOR test suite against production URLs (e.g., asserting `user_a` cannot `POST /accounting/voucher/<pk>/post/` for a voucher in `society_b`) is absent.

---

## 10. Findings Summary Matrix

| ID | Severity | Component | One-Line Summary |
|---|---|---|---|
| V-1 | CRITICAL | accounting/views.py | Voucher post/delete/reverse/detail fetch by PK without society scope |
| V-2 | CRITICAL | housing/views.py | SocietyAdminView/SocietyUserCreateView lack membership/role authorization |
| V-3 | HIGH | societies/decorators.py | role_required/society_access_required unused in production |
| V-4 | HIGH | housing/views.py | Inline role checks duplicate logic, use string literals, inconsistent bypass |
| V-5 | HIGH | societies/services.py | Super-admin bypass is tenant-agnostic with no audit binding |
| V-6 | HIGH | all apps | Authentication (LoginRequiredMixin) used in place of authorization |
| V-7 | HIGH | accounting/views.py | VoucherTemplateScopeMixin resolves society from GET/POST without membership check |
| V-8 | MEDIUM | housing_accounting/selection.py | Session-based tenant selection causes concurrent-window confusion |
| V-9 | MEDIUM | members/views.py | Post-hoc society validation instead of scoped lookup |
| V-10 | MEDIUM | members/models/model_Member.py | Member.user not validated against Membership in same society |
| V-11 | LOW | housing/views.py | SocietyListView shows only selected society |
| V-12 | LOW | societies/roles.py | Linear role hierarchy with no feature-level permission matrix |
| V-13 | LOW | housing_accounting/users/models.py | is_super_admin has no guardrail or break-glass procedure |

---

## 11. Glossary

- **Tenant:** A `Society` aggregate; the unit of data isolation.
- **Membership:** The `User ↔ Society` binding carrying a `role` and `is_active` flag.
- **Scope:** The `(society, financial_year)` tuple resolved per request.
- **IDOR:** Insecure Direct Object Reference — accessing an object by predictable identifier without authorization checks.
- **Pattern A/B/C/D:** View scoping conventions defined in §4.2.
- **Super-Admin Bypass:** The unconditional short-circuit on `is_super_admin` or `is_superuser` in access checks.

---

*End of Report.*
