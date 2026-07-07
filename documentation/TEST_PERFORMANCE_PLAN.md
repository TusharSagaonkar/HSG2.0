# Test Performance Plan

> **Status: Infrastructure COMPLETE · Migration IN PROGRESS**
>
> Shared test infrastructure has been built and is ready to use. Existing tests
> are being migrated app-by-app to eliminate the per-test society-creation
> bottleneck. See [§4 Migration Plan](#4-migration-plan-refactoring-existing-tests)
> for the phased rollout and [§5 Migration Checklist](#5-migration-checklist-per-file)
> for file-by-file instructions.

## Overview

The project has **~873 tests across 10 apps**. The full test suite is
dominated by a single bottleneck: **~250+ tests each call
`Society.objects.create()`**, which triggers a `post_save` signal cascade that
bootstraps ~130+ database records and takes **5–8 seconds per call**. This
represents an estimated **~20–33 minutes of wasted bootstrap time** per full
test run.

The fix is shared test infrastructure — a single society created **once per
test session** (pytest) or **once per test class** (Django `TestCase`) and
reused across all tests. The infrastructure is complete; the remaining work is
migrating existing tests to use it.

---

## 1. Bottleneck Analysis

### 1.1 The Per-Test Society Creation Problem

Every `Society.objects.create()` call fires a `post_save` signal cascade:

**Accounting bootstrap** ([`accounting/signals.py`](accounting/signals.py:1)):
calls `create_default_accounts_for_society()`, which iterates the 733-line
`NEW_ACCOUNT_TREE` and creates:
- 1 `FinancialYear`
- 5 `AccountCategory` records
- ~100+ `Account` records

**GateOps bootstrap** ([`gateops/signals.py`](gateops/signals.py:1)):
creates:
- 1 `GateOpsSocietyConfig`
- 1 `Gate`
- 9 `VisitorCategory`
- 6 `VehicleCategory`
- 2 `MaterialCategory`
- 3 `PassType`
- 3 `ApprovalType`
- 6 `GateOpsRole`
- 1 `MasterSettings`
- **= ~32 records**

**Total per society creation: ~130+ records, 5–8 seconds.**

With ~250+ wasted society creations across the suite, this is
**~20–33 minutes of pure waste** per full run.

### 1.2 Affected Files Per App

| App | Files Affected | Est. Wasted Societies | Est. Time Waste |
|-----|---------------|----------------------|-----------------|
| accounting | 19 migratable files | ~86 | ~7–11 min |
| gateops | 2 frontend test files | ~34 | ~3.5 min |
| parking | 7 test files | ~61 | ~5–8 min |
| housing | 8 migratable files | ~46 | ~4–6 min |
| reports | 2 test files | ~17 | ~2 min |
| notifications | 1 test file | ~5 | ~0.5 min |
| societies | 1 test file | ~8 | ~1 min |
| **TOTAL** | **~40 files** | **~257** | **~20–33 min** |

> **Note:** Two additional files — [`accounting/tests/test_society_account_bootstrap.py`](accounting/tests/test_society_account_bootstrap.py:1) and [`housing/tests/test_society.py`](housing/tests/test_society.py:1) — call `Society.objects.create()` but **must not be migrated**: they test the bootstrap signal itself and genuinely need fresh societies.

### 1.3 Other Bottlenecks

| Bottleneck | Details |
|------------|---------|
| `setUp()` instead of `setUpTestData()` | Several files create the society in `setUp()` (runs per-test) instead of `setUpTestData()` (runs per-class). Found in [`accounting/tests/test_account.py`](accounting/tests/test_account.py:8), [`accounting/tests/test_financial_year.py`](accounting/tests/test_financial_year.py:10), [`housing/tests/test_structure.py`](housing/tests/test_structure.py:7), and others. |
| No shared `society` fixture existed | [`conftest.py`](conftest.py:1) previously had only `user` and `_media_storage` fixtures. The session-scoped `society` fixture is now added. |
| `factory_boy` used in only 1 of 10 apps | Only `reconciliation` used `factory_boy`. Other apps used raw `Society.objects.create()`. |
| Dual test styles with no shared infra | The project mixes Django `TestCase` classes and pytest functions. Shared infrastructure now supports both styles. |
| `pytest-xdist` NOT installed | No parallel test execution. See [§6 Future Optimizations](#6-future-optimizations-not-yet-implemented). |
| `FIXTURE_DIRS` configured but unused | Persistent fixtures could eliminate even the first society creation. See [§6](#6-future-optimizations-not-yet-implemented). |

### 1.4 Django/pytest Configuration

| Setting | Location | Value |
|---------|----------|-------|
| Password hasher | [`config/settings/test.py`](config/settings/test.py:9) | `MD5PasswordHasher` (fast) |
| Cache | [`config/settings/test.py`](config/settings/test.py:16) | `LocMemCache` |
| `ATOMIC_REQUESTS` | [`config/settings/test.py`](config/settings/test.py:26) | `False` |
| `CONN_MAX_AGE` | [`config/settings/test.py`](config/settings/test.py:27) | `0` |
| pytest addopts | [`pyproject.toml`](pyproject.toml:4) | `--reuse-db --import-mode=importlib` |
| Test runner | [`config/settings/test.py`](config/settings/test.py:7) | `DiscoverRunner` |
| `--keepdb` convention | [`conventions.md`](.agents/conventions.md:89) | Always pass `--keepdb` to `manage.py test` |

---

## 2. Solution: Shared Test Infrastructure (COMPLETED)

The following infrastructure has been created and is ready to use.

### 2.1 `core/test_factories.py` — Shared Factories

[`core/test_factories.py`](core/test_factories.py:1) provides:

- **`FIXED_SOCIETY_NAME = "Test Society Alpha"`** — the canonical name for the
  shared test society ([line 23](core/test_factories.py:23)).
- **`SocietyFactory`** — a `factory_boy` factory with
  `django_get_or_create = ("name",)` ([line 42](core/test_factories.py:42)).
  Repeated calls with the same name return the **existing** society without
  re-triggering the bootstrap signal cascade.
- **`UserFactory`** — `django_get_or_create` on `email`
  ([line 83](core/test_factories.py:83)).

```python
from core.test_factories import SocietyFactory, UserFactory, FIXED_SOCIETY_NAME

# First call: creates the society + bootstraps ~130 records (5-8s)
soc = SocietyFactory()

# Second call: returns the SAME society, no bootstrap (instant)
soc2 = SocietyFactory()
assert soc == soc2  # True
```

**How `django_get_or_create` prevents re-triggering:** Because the factory's
`Meta.django_get_or_create` is set to `("name",)`, `factory_boy` calls
`Society.objects.get_or_create(name=FIXED_SOCIETY_NAME, ...)` internally. If a
society with that name already exists, it returns the existing instance — no
`save()`, no `post_save` signal, no bootstrap.

### 2.2 `core/test_base.py` — Shared Base Class

[`core/test_base.py`](core/test_base.py:1) provides:

- **`SocietyTestCase(TestCase)`** — creates `cls.society` and `cls.user` once
  per class in `setUpTestData()` ([line 46](core/test_base.py:46)).

```python
from core.test_base import SocietyTestCase

class MyTest(SocietyTestCase):
    # cls.society is available — created ONCE per class
    # cls.user is available

    def test_something(self):
        # Accounts are already bootstrapped
        account = Account.objects.filter(society=self.society).first()
```

### 2.3 `conftest.py` — Session-Scoped `society` Fixture

Both [`conftest.py`](conftest.py:1) (root) and
[`housing_accounting/conftest.py`](housing_accounting/conftest.py:1) provide a
**session-scoped** `society` fixture ([line 18](conftest.py:18)):

```python
@pytest.fixture(scope="session")
def society(django_db_setup, django_db_blocker):
    """Session-scoped society with ALL bootstrapped accounts."""
    with django_db_blocker.unblock():
        soc = SocietyFactory()
    return soc
```

**Session scope means ONE society per test session**, reused across ALL
pytest-style tests. The first test to request the fixture pays the 5–8s
bootstrap cost; every subsequent test gets the same object instantly.

```python
def test_something(client, user, society):
    # society is pre-built — no need to create accounts
    account = Account.objects.filter(society=society).first()
```

### 2.4 `reconciliation/tests/factories.py` — Backwards-Compat Re-export

[`reconciliation/tests/factories.py`](reconciliation/tests/factories.py:23)
re-exports `SocietyFactory` from `core.test_factories` so existing imports of
the form `from reconciliation.tests.factories import SocietyFactory` keep
working. **Do not use this in new code** — import from `core.test_factories`
directly.

### 2.5 `.agents/conventions.md` — Testing Conventions

[`.agents/conventions.md`](.agents/conventions.md:1) documents 7 rules for
testing conventions that all agents must follow.

---

## 3. How the Two Test Styles Use the Infrastructure

### pytest-style (function-based tests)

Add `society` to the function parameters. The session-scoped fixture is
injected automatically.

```python
# BEFORE (5-8s per test)
def test_voucher_post(client, user):
    society = Society.objects.create(name="Post Society")
    client.force_login(user)
    ...

# AFTER (instant — reuses session society)
def test_voucher_post(client, user, society):
    client.force_login(user)
    ...
```

### TestCase-style (class-based tests)

Subclass `SocietyTestCase` instead of `TestCase`. The society is available as
`self.society`.

```python
# BEFORE (5-8s per test — setUp runs per-test)
class AccountTest(TestCase):
    def setUp(self):
        self.society = Society.objects.create(name="Test Society")

# AFTER (instant — setUpTestData runs once per class)
from core.test_base import SocietyTestCase

class AccountTest(SocietyTestCase):
    # self.society is already set by SocietyTestCase.setUpTestData()
    def test_something(self):
        account = Account.objects.filter(society=self.society).first()
```

---

## 4. Migration Plan: Refactoring Existing Tests

Each phase is independently completable. Work through phases in order —
Phase 1 has the highest impact-to-effort ratio.

### Phase 1: gateops frontend tests (HIGHEST IMPACT — ~34 societies)

**Estimated saving: ~3.5 minutes**

These two files call `_create_accessible_society()` **inside each test method**
instead of in `setUpTestData()`.

| File | Style | Societies | Action |
|------|-------|-----------|--------|
| [`gateops/tests/test_frontend_views.py`](gateops/tests/test_frontend_views.py:1) | TestCase | ~20 | Move `_create_accessible_society()` calls to `setUpTestData()` as `cls.alpha` / `cls.beta` |
| [`gateops/tests/test_lifecycle_frontend.py`](gateops/tests/test_lifecycle_frontend.py:1) | TestCase | ~14 | Move `_create_accessible_society()` calls to `setUpTestData()` as `cls.society` |

**Pattern:**

```python
# BEFORE
class GateOpsFrontendViewTest(TestCase):
    def setUp(self):
        self.user = UserFactory(password="password")

    def test_dashboard_renders(self):
        society = self._create_accessible_society("Alpha Heights")
        self._select_society(society)
        ...

# AFTER
class GateOpsFrontendViewTest(SocietyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.alpha = create_society(user=cls.user, name="Alpha Heights")
        cls.beta = create_society(user=cls.user, name="Beta Heights")

    def setUp(self):
        self._select_society(self.alpha)  # default to alpha

    def test_dashboard_renders(self):
        # self.alpha already exists — no creation
        ...
```

> **Note:** The other gateops test files ([`test_lifecycle.py`](gateops/tests/test_lifecycle.py:45), [`test_rule_engine.py`](gateops/tests/test_rule_engine.py:37), [`test_signals.py`](gateops/tests/test_signals.py:29), [`test_audit_log.py`](gateops/tests/test_audit_log.py:18), [`test_rule_models.py`](gateops/tests/test_rule_models.py:40), [`test_models.py`](gateops/tests/test_models.py:51)) already use `setUpTestData()` correctly and need no changes.

---

### Phase 2: accounting tests (~86 societies)

**Estimated saving: ~7–11 minutes**

Accounting has the most affected files. They split into two groups: pytest-style
(function-based) and TestCase-style (class-based).

#### pytest-style files (replace `Society.objects.create()` with `society` fixture)

| File | Societies | Key Lines |
|------|-----------|-----------|
| [`test_voucher_frontend.py`](accounting/tests/test_voucher_frontend.py:1) | ~24 | 31, 46, 64, 65, 167, 178, 214, 215, 233, 273, 311, 369, 434, 474, 515, 556, 594, 614, 635, 701, 752, 776, 795, 843 |
| [`test_voucher_templates.py`](accounting/tests/test_voucher_templates.py:1) | ~11 | 49, 68, 105, 106, 135, 178, 236, 271, 284, 352, 383 |
| [`test_year_end_workflow.py`](accounting/tests/test_year_end_workflow.py:1) | ~9 | 23, 50, 97, 131, 155, 217, 270, 302, 347 |
| [`test_voucher_type_policy.py`](accounting/tests/test_voucher_type_policy.py:1) | ~7 | 20, 79, 131, 183, 246, 333, 386 |
| [`test_gst_voucher_services.py`](accounting/tests/test_gst_voucher_services.py:1) | ~6 | 47, 67, 87, 106, 123, 141 |
| [`test_period_locks.py`](accounting/tests/test_period_locks.py:1) | ~6 | 16, 48, 87, 88, 142, 195 |
| [`test_reporting_engine.py`](accounting/tests/test_reporting_engine.py:1) | ~5 | 41, 92, 133, 174, 228 |
| [`test_voucher_posting.py`](accounting/tests/test_voucher_posting.py:1) | ~3 | 16, 30, 86 |
| [`test_reporting_exports.py`](accounting/tests/test_reporting_exports.py:1) | ~2 | 28, 78 |
| [`test_immutability.py`](accounting/tests/test_immutability.py:1) | ~2 | 16, 68 |
| [`test_double_entry_rules.py`](accounting/tests/test_double_entry_rules.py:1) | ~1 | 14 |

#### TestCase-style files (subclass `SocietyTestCase`, move to `setUpTestData`)

| File | Societies | Key Lines | Current Pattern |
|------|-----------|-----------|-----------------|
| [`test_voucher.py`](accounting/tests/test_voucher.py:1) | ~1 | 21 | `setUp()` → `self.society = Society.objects.create(...)` |
| [`test_voucher_same_account.py`](accounting/tests/test_voucher_same_account.py:1) | ~1 | 21 | `setUp()` → `self.society = Society.objects.create(...)` |
| [`test_financial_year.py`](accounting/tests/test_financial_year.py:1) | ~1 | 11 | `setUp()` → `self.society = Society.objects.create(...)` |
| [`test_account_tree.py`](accounting/tests/test_account_tree.py:1) | ~1 | 11 | `setUp()` → `self.society = Society.objects.create(...)` |
| [`test_account_category.py`](accounting/tests/test_account_category.py:1) | ~1 | 8 | `setUp()` → `self.society = Society.objects.create(...)` |
| [`test_account.py`](accounting/tests/test_account.py:1) | ~1 | 9 | `setUp()` → `self.society = Society.objects.create(...)` |
| [`test_ledger_entry.py`](accounting/tests/test_ledger_entry.py:1) | ~3 | 16, 76, 97 | `setUp()` + cross-society tests |
| [`test_trial_balance_dataset.py`](accounting/tests/test_trial_balance_dataset.py:1) | ~1 | 23 | `setUpTestData()` → `cls.society = Society.objects.create(...)` |

#### DO NOT MIGRATE

| File | Reason |
|------|--------|
| [`test_society_account_bootstrap.py`](accounting/tests/test_society_account_bootstrap.py:1) | Tests the bootstrap signal itself — genuinely needs fresh societies (lines 18, 19, 40, 55, 65, 66, 75) |

---

### Phase 3: parking tests (~61 societies)

**Estimated saving: ~5–8 minutes**

All parking test files are pytest-style. Several use helper functions
(`_setup_base()`, `_setup_owner_context()`, `_setup_flat_data()`,
`_setup_unit_with_member()`) that create a society internally — these helpers
should accept the `society` fixture as a parameter instead.

| File | Societies | Key Lines | Pattern |
|------|-----------|-----------|---------|
| [`test_sold_parking_management.py`](parking/tests/test_sold_parking_management.py:1) | ~15 | 55, 90, 118, 156, 177, 204, 225, 250, 273, 294, 320, 345, 369, 392, 420 | `society = Society.objects.create(...)` per test |
| [`test_rotation_policy_system.py`](parking/tests/test_rotation_policy_system.py:1) | ~5 | 43, 69, 117, 163, 192 | `society = Society.objects.create(...)` per test |
| [`test_bulk_parking_slots.py`](parking/tests/test_bulk_parking_slots.py:1) | ~4 | 13, 23, 49, 76 | `Society.objects.create(...)` per test |
| [`test_vehicle_rule_status_recalculation.py`](parking/tests/test_vehicle_rule_status_recalculation.py:1) | ~2 | 21, 221 | `_setup_owner_context()` helper + cross-society |
| [`test_vehicle_limit_enforcement.py`](parking/tests/test_vehicle_limit_enforcement.py:1) | ~1+ | 18 | `_setup_base()` helper creates society |
| [`test_vehicle_verification.py`](parking/tests/test_vehicle_verification.py:1) | ~1+ | 21 | `_setup_unit_with_member()` helper creates society |
| [`test_flat_dashboard.py`](parking/tests/test_flat_dashboard.py:1) | ~1+ | 24 | `_setup_flat_data()` helper creates society |

**Pattern for helper-based files:**

```python
# BEFORE
def _setup_base():
    society = Society.objects.create(name="Limit Society")
    structure = Structure.objects.create(society=society, ...)
    return society, structure

def test_limit_enforced():
    society, structure = _setup_base()
    ...

# AFTER
def _setup_base(society):
    structure = Structure.objects.create(society=society, ...)
    return society, structure

def test_limit_enforced(society):
    society, structure = _setup_base(society)
    ...
```

---

### Phase 4: housing tests (~46 societies)

**Estimated saving: ~4–6 minutes**

Housing has a mix of pytest-style and TestCase-style files.

| File | Style | Societies | Key Lines | Pattern |
|------|-------|-----------|-----------|---------|
| [`test_views.py`](housing/tests/test_views.py:1) | pytest | ~17 | 43, 53, 79, 126, 140, 166, 183, 236, 268, 311, 345, 404, 460, 504, 528, 552, 598 | `Society.objects.create(...)` per test |
| [`test_voucher_template_management.py`](housing/tests/test_voucher_template_management.py:1) | pytest | ~7 | 19, 51, 105, 117, 139, 186, 263 | `Society.objects.create(...)` per test |
| [`test_phase2_operations.py`](housing/tests/test_phase2_operations.py:1) | pytest | ~2+ | 29, 226 | `_create_society_context()` helper + cross-society |
| [`test_domain_frontend_pages.py`](housing/tests/test_domain_frontend_pages.py:1) | pytest | ~1+ | 27 | `_build_domain_data()` helper creates society |
| [`test_occupency.py`](housing/tests/test_occupency.py:1) | TestCase | ~1 | 15 | `setUp()` → `self.society = Society.objects.create(...)` |
| [`test_unit.py`](housing/tests/test_unit.py:1) | TestCase | ~1 | 7 | `setUp()` → `self.society = Society.objects.create(...)` |
| [`test_ownership.py`](housing/tests/test_ownership.py:1) | TestCase | ~1 | 16 | `setUp()` → `self.society = Society.objects.create(...)` |
| [`test_structure.py`](housing/tests/test_structure.py:1) | TestCase | ~2 | 8, 19 | `setUp()` + cross-society test |

#### DO NOT MIGRATE

| File | Reason |
|------|--------|
| [`test_society.py`](housing/tests/test_society.py:1) | Tests `Society` model creation itself — genuinely needs fresh societies (line 7) |

---

### Phase 5: reports, notifications, societies (~30 societies)

**Estimated saving: ~2–4 minutes**

| File | Style | Societies | Key Lines |
|------|-------|-----------|-----------|
| [`reports/tests/test_views.py`](reports/tests/test_views.py:1) | pytest | ~9 | 113, 132, 172, 193, 215, 262, 283, 304, 339 |
| [`reports/tests/test_services.py`](reports/tests/test_services.py:1) | pytest | ~8 | 55, 103, 126, 174, 213, 252, 291, 331 |
| [`notifications/tests/test_email_services.py`](notifications/tests/test_email_services.py:1) | pytest | ~5 | 19, 60, 126, 168 |
| [`societies/tests/test_rbac.py`](societies/tests/test_rbac.py:1) | pytest | ~8 | 86, 99, 131, 132, 142 |

> **Note on `societies/tests/test_rbac.py`:** This file uses both `create_society(user=user, name="...")` (which creates a society + membership) and `Society.objects.create(name="...")`. The `Society.objects.create()` calls (lines 86, 99, 131, 132, 142) should be replaced with the `society` fixture where the test doesn't specifically need a *second* society for isolation testing.

---

## 5. Migration Checklist (Per-File)

Agents should work through this checklist file-by-file. For each file, find the
current pattern at the listed lines and apply the target pattern.

### Phase 1: gateops

```
- [ ] gateops/tests/test_frontend_views.py (TestCase-style, ~20 societies)
  - Current: `society = self._create_accessible_society("Alpha Heights")` inside each test method
  - Target: Create `cls.alpha` / `cls.beta` in setUpTestData(), reference self.alpha in tests
  - Lines: 41, 63, 79, 91, 102, 103, 139, 140, 171, 172, 187, 188, 215, 216, 266, 267, 276, 277, 298, 299, 351
  - Note: Many tests create 2 societies (alpha + beta) for cross-society checks

- [ ] gateops/tests/test_lifecycle_frontend.py (TestCase-style, ~14 societies)
  - Current: `society = self._create_accessible_society("...")` inside each test method
  - Target: Create `cls.society` in setUpTestData(), reference self.society in tests
  - Lines: 85, 96, 106, 116, 130, 141, 142, 151, 173, 196, 217, 226, 237, 247
```

### Phase 2: accounting

```
- [ ] accounting/tests/test_voucher_frontend.py (pytest-style, ~24 societies)
  - Current: `society = Society.objects.create(name="...")` in each test function
  - Target: Add `society` to function params, remove the create call
  - Lines: 31, 46, 64, 65, 167, 178, 214, 215, 233, 273, 311, 369, 434, 474, 515, 556, 594, 614, 635, 701, 752, 776, 795, 843
  - Note: Lines 64-65 and 214-215 create 2 societies for cross-society tests — keep a second society only where isolation is tested

- [ ] accounting/tests/test_voucher_templates.py (pytest-style, ~11 societies)
  - Current: `society = Society.objects.create(name="...")` in each test function
  - Target: Add `society` to function params, remove the create call
  - Lines: 49, 68, 105, 106, 135, 178, 236, 271, 284, 352, 383
  - Note: Lines 105-106 create 2 societies for cross-society test

- [ ] accounting/tests/test_year_end_workflow.py (pytest-style, ~9 societies)
  - Current: `society = Society.objects.create(name="...")` in each test function
  - Target: Add `society` to function params, remove the create call
  - Lines: 23, 50, 97, 131, 155, 217, 270, 302, 347

- [ ] accounting/tests/test_voucher_type_policy.py (pytest-style, ~7 societies)
  - Current: `society = Society.objects.create(name="...")` in each test function
  - Target: Add `society` to function params, remove the create call
  - Lines: 20, 79, 131, 183, 246, 333, 386

- [ ] accounting/tests/test_gst_voucher_services.py (pytest-style, ~6 societies)
  - Current: `society = Society.objects.create(name="...")` in each test function
  - Target: Add `society` to function params, remove the create call
  - Lines: 47, 67, 87, 106, 123, 141

- [ ] accounting/tests/test_period_locks.py (pytest-style, ~6 societies)
  - Current: `society = Society.objects.create(name="...")` in each test function
  - Target: Add `society` to function params, remove the create call
  - Lines: 16, 48, 87, 88, 142, 195
  - Note: Lines 87-88 create 2 societies (closed + open) for cross-society test

- [ ] accounting/tests/test_reporting_engine.py (pytest-style, ~5 societies)
  - Current: `society = Society.objects.create(name="...")` in each test function
  - Target: Add `society` to function params, remove the create call
  - Lines: 41, 92, 133, 174, 228

- [ ] accounting/tests/test_voucher_posting.py (pytest-style, ~3 societies)
  - Current: `society = Society.objects.create(name="...")` in each test function
  - Target: Add `society` to function params, remove the create call
  - Lines: 16, 30, 86

- [ ] accounting/tests/test_reporting_exports.py (pytest-style, ~2 societies)
  - Current: `society = Society.objects.create(name="...")` in each test function
  - Target: Add `society` to function params, remove the create call
  - Lines: 28, 78

- [ ] accounting/tests/test_immutability.py (pytest-style, ~2 societies)
  - Current: `society = Society.objects.create(name="...")` in each test function
  - Target: Add `society` to function params, remove the create call
  - Lines: 16, 68

- [ ] accounting/tests/test_double_entry_rules.py (pytest-style, ~1 society)
  - Current: `society = Society.objects.create(name="Test Society")` in test function
  - Target: Add `society` to function params, remove the create call
  - Lines: 14

- [ ] accounting/tests/test_voucher.py (TestCase-style, ~1 society)
  - Current: `self.society = Society.objects.create(name="Test Society")` in setUp()
  - Target: Subclass SocietyTestCase, remove setUp() society creation
  - Lines: 21

- [ ] accounting/tests/test_voucher_same_account.py (TestCase-style, ~1 society)
  - Current: `self.society = Society.objects.create(name="Test Society")` in setUp()
  - Target: Subclass SocietyTestCase, remove setUp() society creation
  - Lines: 21

- [ ] accounting/tests/test_financial_year.py (TestCase-style, ~1 society)
  - Current: `self.society = Society.objects.create(name="Test Society")` in setUp()
  - Target: Subclass SocietyTestCase, remove setUp() society creation
  - Lines: 11

- [ ] accounting/tests/test_account_tree.py (TestCase-style, ~1 society)
  - Current: `self.society = Society.objects.create(name="Test Society")` in setUp()
  - Target: Subclass SocietyTestCase, remove setUp() society creation
  - Lines: 11

- [ ] accounting/tests/test_account_category.py (TestCase-style, ~1 society)
  - Current: `self.society = Society.objects.create(name="Test Society")` in setUp()
  - Target: Subclass SocietyTestCase, remove setUp() society creation
  - Lines: 8

- [ ] accounting/tests/test_account.py (TestCase-style, ~1 society)
  - Current: `self.society = Society.objects.create(name="Test Society")` in setUp()
  - Target: Subclass SocietyTestCase, remove setUp() society creation
  - Lines: 9

- [ ] accounting/tests/test_ledger_entry.py (TestCase-style, ~3 societies)
  - Current: `self.society = Society.objects.create(...)` in setUp() + cross-society in tests
  - Target: Subclass SocietyTestCase, keep second society only in cross-society tests
  - Lines: 16, 76, 97

- [ ] accounting/tests/test_trial_balance_dataset.py (TestCase-style, ~1 society)
  - Current: `cls.society = Society.objects.create(...)` in setUpTestData()
  - Target: Subclass SocietyTestCase (already uses setUpTestData — just swap base class)
  - Lines: 23
```

### Phase 3: parking

```
- [ ] parking/tests/test_sold_parking_management.py (pytest-style, ~15 societies)
  - Current: `society = Society.objects.create(name="...")` in each test function
  - Target: Add `society` to function params, remove the create call
  - Lines: 55, 90, 118, 156, 177, 204, 225, 250, 273, 294, 320, 345, 369, 392, 420

- [ ] parking/tests/test_rotation_policy_system.py (pytest-style, ~5 societies)
  - Current: `society = Society.objects.create(name="...")` in each test function
  - Target: Add `society` to function params, remove the create call
  - Lines: 43, 69, 117, 163, 192

- [ ] parking/tests/test_bulk_parking_slots.py (pytest-style, ~4 societies)
  - Current: `Society.objects.create(name="...")` in each test function
  - Target: Add `society` to function params, remove the create call
  - Lines: 13, 23, 49, 76

- [ ] parking/tests/test_vehicle_rule_status_recalculation.py (pytest-style, ~2 societies)
  - Current: `_setup_owner_context()` helper creates society; cross-society test at line 221
  - Target: Pass `society` fixture into helper; keep second society only for cross-society test
  - Lines: 21, 221

- [ ] parking/tests/test_vehicle_limit_enforcement.py (pytest-style, helper-based)
  - Current: `_setup_base()` helper creates society internally
  - Target: Pass `society` fixture into `_setup_base(society)`, remove internal create
  - Lines: 18

- [ ] parking/tests/test_vehicle_verification.py (pytest-style, helper-based)
  - Current: `_setup_unit_with_member()` helper creates society internally
  - Target: Pass `society` fixture into helper, remove internal create
  - Lines: 21

- [ ] parking/tests/test_flat_dashboard.py (pytest-style, helper-based)
  - Current: `_setup_flat_data()` helper creates society internally
  - Target: Pass `society` fixture into helper, remove internal create
  - Lines: 24
```

### Phase 4: housing

```
- [ ] housing/tests/test_views.py (pytest-style, ~17 societies)
  - Current: `Society.objects.create(name="...")` or `society = Society.objects.create(...)` in each test
  - Target: Add `society` to function params, remove the create call
  - Lines: 43, 53, 79, 126, 140, 166, 183, 236, 268, 311, 345, 404, 460, 504, 528, 552, 598

- [ ] housing/tests/test_voucher_template_management.py (pytest-style, ~7 societies)
  - Current: `society = Society.objects.create(name="...")` in each test function
  - Target: Add `society` to function params, remove the create call
  - Lines: 19, 51, 105, 117, 139, 186, 263

- [ ] housing/tests/test_phase2_operations.py (pytest-style, helper-based, ~2+ societies)
  - Current: `_create_society_context()` helper creates society; cross-society at line 226
  - Target: Pass `society` fixture into helper; keep second society only for cross-society test
  - Lines: 29, 226

- [ ] housing/tests/test_domain_frontend_pages.py (pytest-style, helper-based)
  - Current: `_build_domain_data()` helper creates society internally
  - Target: Pass `society` fixture into helper, remove internal create
  - Lines: 27

- [ ] housing/tests/test_occupency.py (TestCase-style, ~1 society)
  - Current: `self.society = Society.objects.create(name="Green Heights")` in setUp()
  - Target: Subclass SocietyTestCase, remove setUp() society creation
  - Lines: 15

- [ ] housing/tests/test_unit.py (TestCase-style, ~1 society)
  - Current: `self.society = Society.objects.create(name="Green Heights")` in setUp()
  - Target: Subclass SocietyTestCase, remove setUp() society creation
  - Lines: 7

- [ ] housing/tests/test_ownership.py (TestCase-style, ~1 society)
  - Current: `self.society = Society.objects.create(name="Green Heights")` in setUp()
  - Target: Subclass SocietyTestCase, remove setUp() society creation
  - Lines: 16

- [ ] housing/tests/test_structure.py (TestCase-style, ~2 societies)
  - Current: `self.society = Society.objects.create(...)` in setUp() + cross-society at line 19
  - Target: Subclass SocietyTestCase, keep second society only for cross-society test
  - Lines: 8, 19
```

### Phase 5: reports, notifications, societies

```
- [ ] reports/tests/test_views.py (pytest-style, ~9 societies)
  - Current: `society = Society.objects.create(name="...")` in each test function
  - Target: Add `society` to function params, remove the create call
  - Lines: 113, 132, 172, 193, 215, 262, 283, 304, 339

- [ ] reports/tests/test_services.py (pytest-style, ~8 societies)
  - Current: `society = Society.objects.create(name="...")` in each test function
  - Target: Add `society` to function params, remove the create call
  - Lines: 55, 103, 126, 174, 213, 252, 291, 331

- [ ] notifications/tests/test_email_services.py (pytest-style, ~5 societies)
  - Current: `society = Society.objects.create(name="Green Valley")` in each test function
  - Target: Add `society` to function params, remove the create call
  - Lines: 19, 60, 126, 168

- [ ] societies/tests/test_rbac.py (pytest-style, ~8 societies)
  - Current: Mix of `create_society(user=user, name="...")` and `Society.objects.create(name="...")`
  - Target: Use `society` fixture for the primary society; keep second society only for isolation tests
  - Lines: 86, 99, 131, 132, 142
  - Note: `create_society()` creates a society + membership — may need a fixture variant that attaches membership to the shared society
```

---

## 6. Future Optimizations (NOT YET IMPLEMENTED)

### 6.1 Parallel Execution

Install `pytest-xdist` and add `-n auto` to
[`pyproject.toml`](pyproject.toml:4):

```toml
addopts = "--ds=config.settings.test --reuse-db --import-mode=importlib -n auto"
```

**Tension with `setUpTestData`:** Django's `--parallel` flag wraps each
`TestCase` in its own process with its own database clone. This is safe but
means `setUpTestData` runs once per process. For pytest-style tests using the
session-scoped `society` fixture, parallel execution is safe because the
fixture is created once and shared.

**Recommendation:** Migrate all tests to the shared infrastructure first
(Phases 1–5), then enable parallel execution. The session-scoped `society`
fixture is parallel-safe; `SocietyTestCase` subclasses are safe with Django's
`--parallel` (each process gets its own DB).

### 6.2 Persistent Fixtures

Use Django's `dumpdata` to create a fixture file from the bootstrapped society,
then `loaddata` in a `django_db_setup` session fixture. This would eliminate
even the **first** society creation (the 5–8s bootstrap at session start).

```python
# conftest.py (future)
@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        call_command("loaddata", "test_society_bootstrap.json")
```

**Status:** `FIXTURE_DIRS` is already configured in settings but unused. This
is a low-effort, high-impact optimization once the migration is complete.

### 6.3 SimpleTestCase

Use `SimpleTestCase` instead of `TestCase` for tests that don't touch the
database (pure service logic, utility functions). This skips the database
transaction overhead entirely.

### 6.4 Query Optimization

Profile individual tests for N+1 queries using `django-silk` or
`pytest-django`'s `--debug` flag. The `assertNumQueries` context manager can
catch regressions:

```python
def test_dashboard_query_count(self):
    with self.assertNumQueries(5):
        response = self.client.get(self.url)
```

---

## 7. Agent Quick Reference

A concise reference for agents working on tests. **Read this before writing any
test.**

| Rule | Detail |
|------|--------|
| **NEVER** call `Society.objects.create()` in tests | It triggers a `post_save` signal cascade that creates ~130+ records and takes 5–8 seconds. |
| **pytest-style** | Add `society` to function params: `def test_x(client, user, society):` |
| **TestCase-style** | Subclass `SocietyTestCase` from `core.test_base`: `class MyTest(SocietyTestCase):` |
| **Import factories** | `from core.test_factories import SocietyFactory, UserFactory, FIXED_SOCIETY_NAME` |
| **Second society** | Only for isolation tests. Use `SocietyFactory(name="Test Society Beta")` — this *will* trigger bootstrap. |
| **Per-test state** | Reset mutable fields in `setUp()`, never the society or its accounts. |

**Run tests:**

```bash
# Django test runner (single app)
uv run python manage.py test accounting --keepdb

# pytest (single file, stop on first failure)
uv run pytest accounting/tests/test_voucher_frontend.py -x

# Full suite
uv run python manage.py test --keepdb

# Quick sanity check (no DB)
uv run python manage.py check
```

**Full conventions:** See [`.agents/conventions.md`](.agents/conventions.md:1)
