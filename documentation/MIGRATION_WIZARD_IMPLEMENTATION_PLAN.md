# Housynk — Migration Wizard Implementation Plan

> **Document type:** Technical Implementation Plan
> **Status:** Draft for review
> **Source documents:** [`WIZARD_ARCHITECTURE_ANALYSIS.md`](WIZARD_ARCHITECTURE_ANALYSIS.md:1), [`MIGRATION_WIZARD_SPEC.md`](MIGRATION_WIZARD_SPEC.md:1)
> **Project root:** [`housing_accounting/`](../housing_accounting/:1)
> **New app:** [`onboarding/`](../onboarding/:1)

This plan maps every requirement in the specification to concrete Django code components — models, services, views, forms, templates, and migrations — organized into ordered implementation phases. It is intended to be handed off to Code mode for execution.

---

## Table of Contents

1. [New Django App: `onboarding/`](#1-new-django-app-onboarding)
2. [Models Design](#2-models-design)
3. [Services Design](#3-services-design)
4. [Views & URLs Design](#4-views--urls-design)
5. [Forms Design](#5-forms-design)
6. [Template Structure](#6-template-structure)
7. [Implementation Phases](#7-implementation-phases)
8. [Integration Points](#8-integration-points)
9. [File Manifest](#9-file-manifest)

---

## 1. New Django App: `onboarding/`

### 1.1 Rationale

All wizard-related code lives in a single new Django app called `onboarding`. This keeps the wizard self-contained, avoids polluting existing domain apps ([`housing/`](../housing/:1), [`accounting/`](../accounting/:1), [`members/`](../members/:1)), and follows the project's domain-driven app layout. The app orchestrates calls into existing services but owns its own models (wizard state, staging tables, upload tracking, migration audit log).

### 1.2 App Label & Multi-Tenant Integration

The app uses `app_label = "onboarding"` (the default from `AppConfig.name = "onboarding"`). This means:

- The [`DatabaseRouter`](../core/db_router.py:3) routes all `onboarding` models to the `default` database (same as all other domain apps).
- Tenant-scoped models (staging tables, wizard state) include a `society` FK and use [`TenantManager`](../societies/managers.py:47) for automatic contextvar-based filtering, exactly like [`gateops/models/model_Contractor.py`](../gateops/models/model_Contractor.py:6) does.
- The [`SocietyMiddleware`](../societies/middleware.py:5) sets `_current_tenant` contextvar; all `onboarding` queries are automatically scoped to the wizard's society.
- The wizard must explicitly set the contextvar when operating on a newly created society (see [§8.6](#86-tenant-middleware-context)).

### 1.3 App Structure

```
onboarding/
├── __init__.py
├── apps.py                          # AppConfig with ready() hook
├── admin.py                         # Admin registrations for debugging
├── urls.py                          # URLconf (app_name = "onboarding")
├── views.py                         # All wizard views (function-based + class-based)
├── forms.py                         # All wizard step forms
├── constants.py                     # Step definitions, template type enums, module registry
├── models/
│   ├── __init__.py                  # Re-exports all models
│   ├── model_OnboardingWizard.py    # Wizard session/state
│   ├── model_WizardStepLog.py       # Step completion audit trail
│   ├── model_UploadBatch.py         # File upload tracking
│   ├── model_StagingChartOfAccounts.py
│   ├── model_StagingTrialBalance.py
│   ├── model_StagingMemberOutstanding.py
│   ├── model_StagingVendorOutstanding.py
│   ├── model_StagingBankOpening.py
│   ├── model_StagingCashOpening.py
│   ├── model_StagingFixedAsset.py
│   ├── model_StagingSecurityDeposit.py
│   ├── model_StagingLoan.py
│   ├── model_StagingFund.py
│   └── model_MigrationAuditLog.py   # Migration-specific audit log
├── services/
│   ├── __init__.py                  # Re-exports all services
│   ├── wizard_service.py            # Wizard lifecycle management
│   ├── society_setup_service.py     # Society, structure, units, members
│   ├── module_config_service.py     # Module enablement
│   ├── financial_year_service.py    # FY + period creation
│   ├── staging_service.py           # Staging area CRUD
│   ├── validation_service.py        # Validation engine
│   ├── reconciliation_service.py    # Reconciliation dashboard + checklist
│   └── migration_finalization_service.py  # Opening journal + lock
├── migrations/
│   └── __init__.py                  # (migrations generated via makemigrations)
├── templates/
│   └── onboarding/
│       ├── base_wizard.html         # Base template with step progress bar
│       ├── wizard_list.html         # List of in-progress wizards
│       ├── step_society_details.html
│       ├── step_society_type.html
│       ├── step_module_selection.html
│       ├── step_accounting_start_year.html
│       ├── step_financial_year_creation.html
│       ├── step_structure.html
│       ├── step_unit_configuration.html
│       ├── step_member_assignment.html
│       ├── step_accounting_setup.html
│       ├── step_chart_of_accounts.html
│       ├── step_import_templates.html
│       ├── staging_area.html        # Generic staging table view
│       ├── reconciliation_dashboard.html
│       ├── validation_checklist.html
│       ├── step_final_approval.html
│       ├── step_complete.html       # Success page
│       └── partials/
│           ├── step_progress_bar.html
│           ├── staging_table.html
│           ├── validation_errors.html
│           └── reconciliation_summary.html
└── tests/
    ├── __init__.py
    ├── test_models.py
    ├── test_wizard_service.py
    ├── test_society_setup_service.py
    ├── test_staging_service.py
    ├── test_validation_service.py
    ├── test_reconciliation_service.py
    ├── test_migration_finalization_service.py
    └── test_views.py
```

### 1.4 AppConfig

```python
# onboarding/apps.py
from django.apps import AppConfig

class OnboardingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "onboarding"
    verbose_name = "Society Onboarding & Migration Wizard"

    def ready(self):
        # Import signals if needed (e.g., auto-create wizard step logs)
        pass
```

### 1.5 Registration in Settings

Add `"onboarding"` to `LOCAL_APPS` in [`config/settings/base.py`](../config/settings/base.py:147), placed **after** `"housing"` and `"accounting"` (since it depends on their models):

```python
LOCAL_APPS = [
    # ... existing apps ...
    "housing",
    "accounting",
    "onboarding",   # ← NEW: must come after housing & accounting
    # ... rest ...
]
```

### 1.6 URL Registration

Add to [`config/urls.py`](../config/urls.py:1):

```python
path("onboarding/", include("onboarding.urls")),
```

---

## 2. Models Design

All models follow the project's established patterns:
- **Tenant-scoped models** include `society` FK and use `TenantManager` for auto-filtering (pattern from [`gateops/models/model_Contractor.py`](../gateops/models/model_Contractor.py:6)).
- **Model-per-file** convention (pattern from [`accounting/models/`](../accounting/models/__init__.py:1) and [`societies/models/`](../societies/models/__init__.py:1)).
- **`BigAutoField`** primary keys (project default).
- **`@staticmethod` clean()** validation and `save()` that calls `self.clean()` (pattern from [`gateops/models/model_Contractor.py`](../gateops/models/model_Contractor.py:68)).

### 2.1 Wizard State Models

#### 2.1.1 `OnboardingWizard` — [`onboarding/models/model_OnboardingWizard.py`](../onboarding/models/model_OnboardingWizard.py:1)

Tracks the wizard session and current state. One record per wizard attempt.

| Field | Type | null/blank | Choices / Notes |
|-------|------|------------|-----------------|
| `society` | FK → `housing.Society` | null=True (set at Step 1) | `on_delete=CASCADE`, `related_name="onboarding_wizards"` |
| `current_step` | PositiveIntegerField | default=1 | 1–28 per spec |
| `society_type` | CharField(20) | blank=True | `NEW`, `EXISTING` (set at Step 2) |
| `status` | CharField(20) | default=`IN_PROGRESS` | `IN_PROGRESS`, `COMPLETED`, `ABANDONED`, `LOCKED` |
| `selected_modules` | JSONField | default=list | List of module keys from Step 3 |
| `accounting_start_year` | CharField(20) | blank=True | e.g. `"2026-27"` (set at Step 4) |
| `fy_pattern` | CharField(20) | default=`APRIL_MARCH` | `APRIL_MARCH`, `JAN_DEC`, `JUL_JUN` |
| `started_at` | DateTimeField | auto_now_add | |
| `completed_at` | DateTimeField | null=True, blank=True | Set when status → COMPLETED |
| `created_by` | FK → `User` | null=True | `on_delete=SET_NULL`, `related_name="+"` |
| `resumed_count` | PositiveIntegerField | default=0 | Incremented on each resume |
| `wizard_metadata` | JSONField | default=dict | Extra context (topology mode, etc.) |

**Meta:**
- `ordering = ["-started_at"]`
- `indexes`: `[Index(fields=["society", "status"]), Index(fields=["created_by", "status"])]`
- `constraints`: `UniqueConstraint(fields=["society"], condition=Q(status__in=["IN_PROGRESS", "LOCKED"]), name="uniq_active_wizard_per_society")` — only one active wizard per society.

**Manager:** `objects = TenantManager()` (auto-filters by society contextvar).

**Methods:**
- `__str__` → `f"Wizard #{self.pk} — {self.society.name if self.society else 'Unstarted'} ({self.status})"`
- `clean()` — validate `current_step` is in 1–28; validate `society_type` choices; if `status == COMPLETED`, `completed_at` must be set.

#### 2.1.2 `WizardStepLog` — [`onboarding/models/model_WizardStepLog.py`](../onboarding/models/model_WizardStepLog.py:1)

Audit trail of each step completion. Append-only (no update/delete — enforced in `save()` and `delete()`).

| Field | Type | null/blank | Notes |
|-------|------|------------|-------|
| `wizard` | FK → `OnboardingWizard` | — | `on_delete=CASCADE`, `related_name="step_logs"` |
| `society` | FK → `housing.Society` | null=True | Redundant for fast queries without joins |
| `step_name` | CharField(100) | — | e.g. `"society_details"`, `"trial_balance"` |
| `step_number` | PositiveIntegerField | — | 1–28 |
| `status` | CharField(20) | — | `STARTED`, `COMPLETED`, `SKIPPED`, `FAILED` |
| `data_snapshot` | JSONField | default=dict | Snapshot of step data at completion |
| `completed_at` | DateTimeField | auto_now_add | |
| `completed_by` | FK → `User` | null=True | `on_delete=SET_NULL` |

**Meta:**
- `ordering = ["step_number", "id"]`
- `indexes`: `[Index(fields=["wizard", "step_number"]), Index(fields=["society", "completed_at"])]`

**Methods:**
- `save()` — append-only: reject updates (pattern from [`auditlog/models.py`](../auditlog/models.py:159)).
- `delete()` — raise `PermissionError` (pattern from [`auditlog/models.py`](../auditlog/models.py:168)).

### 2.2 Staging Models

All staging models share a common abstract base and a common set of fields. They follow the pattern of [`gateops/models/model_Contractor.py`](../gateops/models/model_Contractor.py:6) (direct `society` FK, `is_active`/`deleted_at` soft-delete, `TenantManager`).

#### 2.2.1 `BaseStagingModel` (Abstract) — defined in [`onboarding/models/model_OnboardingWizard.py`](../onboarding/models/model_OnboardingWizard.py:1) or a separate `model_BaseStaging.py`

| Field | Type | null/blank | Notes |
|-------|------|------------|-------|
| `wizard` | FK → `OnboardingWizard` | — | `on_delete=CASCADE`, `related_name="+"` |
| `society` | FK → `housing.Society` | — | `on_delete=CASCADE` |
| `upload_batch` | FK → `UploadBatch` | null=True | `on_delete=SET_NULL` (rows survive batch deletion for audit) |
| `row_number` | PositiveIntegerField | — | 1-based row from source file |
| `raw_data` | JSONField | default=dict | Full raw row as parsed from file |
| `validation_status` | CharField(20) | default=`PENDING` | `PENDING`, `VALID`, `INVALID` |
| `validation_errors` | JSONField | default=list | List of `{column, message, suggested_fix}` dicts |
| `is_approved` | BooleanField | default=False | User-approved for commit |
| `is_committed` | BooleanField | default=False | Set True after Step 26 commit (makes row read-only) |
| `created_at` | DateTimeField | auto_now_add | |
| `updated_at` | DateTimeField | auto_now | |

**Meta (abstract):**
- `abstract = True`
- `indexes`: `[Index(fields=["wizard", "validation_status"]), Index(fields=["society", "upload_batch"])]`

**Manager:** `objects = TenantManager()` on each concrete subclass.

**Methods (on abstract):**
- `clean()` — if `is_committed` is True, reject any field changes (enforce immutability after commit).
- `save()` — calls `self.clean()`; if `is_committed`, raise on update.

#### 2.2.2 `StagingChartOfAccounts` — [`onboarding/models/model_StagingChartOfAccounts.py`](../onboarding/models/model_StagingChartOfAccounts.py:1)

Template T1. Inherits `BaseStagingModel`.

| Field | Type | null/blank | Notes |
|-------|------|------------|-------|
| `account_code` | CharField(50) | — | Regex `^\d+(\.\d+)*$` |
| `account_name` | CharField(200) | — | |
| `account_group` | CharField(100) | blank=True | Root category name |
| `account_type` | CharField(20) | — | `ASSET`, `LIABILITY`, `INCOME`, `EXPENSE`, `EQUITY` |
| `parent_code` | CharField(50) | blank=True | Must exist in COA if provided |
| `nature` | CharField(20) | blank=True | Sub-type: `GST`, `BANK`, `MEMBER`, `FUND`, etc. |
| `opening_debit` | DecimalField(12,2) | default=0 | |
| `opening_credit` | DecimalField(12,2) | default=0 | |
| `is_system_account` | BooleanField | default=False | |

**Meta:** `unique_together = ("wizard", "account_code")` (no duplicate codes per wizard).

#### 2.2.3 `StagingTrialBalance` — [`onboarding/models/model_StagingTrialBalance.py`](../onboarding/models/model_StagingTrialBalance.py:1)

Template T2. Inherits `BaseStagingModel`.

| Field | Type | null/blank | Notes |
|-------|------|------------|-------|
| `account_code` | CharField(50) | — | Must exist in COA |
| `account_name` | CharField(200) | — | Must match account's name |
| `debit` | DecimalField(12,2) | default=0 | ≥ 0 |
| `credit` | DecimalField(12,2) | default=0 | ≥ 0 |

**Meta:** `unique_together = ("wizard", "account_code")` (no account appears twice).

#### 2.2.4 `StagingMemberOutstanding` — [`onboarding/models/model_StagingMemberOutstanding.py`](../onboarding/models/model_StagingMemberOutstanding.py:1)

Template T3. Inherits `BaseStagingModel`.

| Field | Type | null/blank | Notes |
|-------|------|------------|-------|
| `unit_identifier` | CharField(50) | — | Must match existing `Unit.identifier` |
| `member_name` | CharField(200) | — | |
| `outstanding_amount` | DecimalField(12,2) | default=0 | ≥ 0 |
| `advance_maintenance` | DecimalField(12,2) | default=0 | ≥ 0 |
| `credit_balance` | DecimalField(12,2) | default=0 | ≥ 0 |
| `late_fees` | DecimalField(12,2) | default=0 | ≥ 0 |
| `interest_receivable` | DecimalField(12,2) | default=0 | ≥ 0 |

**Meta:** `unique_together = ("wizard", "unit_identifier", "member_name")`.

#### 2.2.5 `StagingVendorOutstanding` — [`onboarding/models/model_StagingVendorOutstanding.py`](../onboarding/models/model_StagingVendorOutstanding.py:1)

Template T4. Inherits `BaseStagingModel`.

| Field | Type | null/blank | Notes |
|-------|------|------------|-------|
| `vendor_name` | CharField(200) | — | |
| `outstanding_amount` | DecimalField(12,2) | default=0 | ≥ 0 |
| `advance_paid` | DecimalField(12,2) | default=0 | ≥ 0 |
| `retention` | DecimalField(12,2) | default=0 | ≥ 0 |
| `security_deposit` | DecimalField(12,2) | default=0 | ≥ 0 |

**Meta:** `unique_together = ("wizard", "vendor_name")`.

#### 2.2.6 `StagingBankOpening` — [`onboarding/models/model_StagingBankOpening.py`](../onboarding/models/model_StagingBankOpening.py:1)

Template T5. Inherits `BaseStagingModel`.

| Field | Type | null/blank | Notes |
|-------|------|------------|-------|
| `bank_name` | CharField(200) | — | |
| `account_number` | CharField(50) | — | |
| `ifsc` | CharField(20) | blank=True | Regex `^[A-Z]{4}0[A-Z0-9]{6}$` if provided |
| `branch` | CharField(200) | blank=True | |
| `opening_balance` | DecimalField(12,2) | default=0 | ≥ 0 |
| `account_code` | CharField(50) | — | Must exist in COA with `is_bank=True` |

**Meta:** `unique_together = ("wizard", "account_code")`.

#### 2.2.7 `StagingCashOpening` — [`onboarding/models/model_StagingCashOpening.py`](../onboarding/models/model_StagingCashOpening.py:1)

Template T6. Inherits `BaseStagingModel`.

| Field | Type | null/blank | Notes |
|-------|------|------------|-------|
| `opening_balance` | DecimalField(12,2) | default=0 | ≥ 0 |
| `account_code` | CharField(50) | — | Must exist in COA (Cash account) |

**Meta:** `unique_together = ("wizard", "account_code")` (only one cash row).

#### 2.2.8 `StagingFixedAsset` — [`onboarding/models/model_StagingFixedAsset.py`](../onboarding/models/model_StagingFixedAsset.py:1)

Template T7. Inherits `BaseStagingModel`.

| Field | Type | null/blank | Notes |
|-------|------|------------|-------|
| `asset_name` | CharField(200) | — | |
| `asset_category` | CharField(50) | — | `BUILDING`, `LIFT`, `GENERATOR`, `FURNITURE`, `OFFICE_EQUIPMENT`, `COMPUTERS`, `VEHICLES`, `DEPRECIATION` |
| `gross_value` | DecimalField(12,2) | default=0 | ≥ 0 |
| `depreciation` | DecimalField(12,2) | default=0 | ≥ 0 |
| `net_value` | DecimalField(12,2) | default=0 | `= gross_value - depreciation` |
| `account_code` | CharField(50) | — | Must exist in COA (asset account) |

**Meta:** `unique_together = ("wizard", "account_code", "asset_name")`.

#### 2.2.9 `StagingSecurityDeposit` — [`onboarding/models/model_StagingSecurityDeposit.py`](../onboarding/models/model_StagingSecurityDeposit.py:1)

Template T8. Inherits `BaseStagingModel`.

| Field | Type | null/blank | Notes |
|-------|------|------------|-------|
| `description` | CharField(200) | — | e.g. "Vendor Security", "Member Deposit" |
| `amount` | DecimalField(12,2) | default=0 | ≥ 0 |
| `against_account` | CharField(50) | — | Must exist in COA (liability account) |

**Meta:** `unique_together = ("wizard", "against_account", "description")`.

#### 2.2.10 `StagingLoan` — [`onboarding/models/model_StagingLoan.py`](../onboarding/models/model_StagingLoan.py:1)

Template T9. Inherits `BaseStagingModel`.

| Field | Type | null/blank | Notes |
|-------|------|------------|-------|
| `loan_name` | CharField(200) | — | |
| `loan_type` | CharField(20) | — | `BANK_LOAN`, `SOCIETY_LOAN`, `MEMBER_LOAN` |
| `outstanding_principal` | DecimalField(12,2) | default=0 | ≥ 0 |
| `interest` | DecimalField(12,2) | default=0 | ≥ 0 |
| `account_code` | CharField(50) | — | Must exist in COA (liability account) |

**Meta:** `unique_together = ("wizard", "account_code", "loan_name")`.

#### 2.2.11 `StagingFund` — [`onboarding/models/model_StagingFund.py`](../onboarding/models/model_StagingFund.py:1)

Template T10. Inherits `BaseStagingModel`.

| Field | Type | null/blank | Notes |
|-------|------|------------|-------|
| `fund_name` | CharField(100) | — | `REPAIR`, `SINKING`, `EDUCATION`, `FESTIVAL`, `RESERVE`, `CORPUS`, or custom |
| `fund_type` | CharField(20) | blank=True | Derived from name or user-specified |
| `balance` | DecimalField(12,2) | default=0 | ≥ 0 |
| `account_code` | CharField(50) | — | Must exist in COA with `sub_type=FUND` |

**Meta:** `unique_together = ("wizard", "account_code")`.

### 2.3 Upload Tracking

#### 2.3.1 `UploadBatch` — [`onboarding/models/model_UploadBatch.py`](../onboarding/models/model_UploadBatch.py:1)

Tracks each file upload for a template type within a wizard.

| Field | Type | null/blank | Notes |
|-------|------|------------|-------|
| `wizard` | FK → `OnboardingWizard` | — | `on_delete=CASCADE`, `related_name="upload_batches"` |
| `society` | FK → `housing.Society` | — | `on_delete=CASCADE` |
| `template_type` | CharField(10) | — | `T1`–`T10` (see choices below) |
| `file_name` | CharField(500) | — | Original uploaded filename |
| `file_path` | CharField(500) | blank=True | Stored file path (media) |
| `uploaded_at` | DateTimeField | auto_now_add | |
| `uploaded_by` | FK → `User` | null=True | `on_delete=SET_NULL` |
| `row_count` | PositiveIntegerField | default=0 | Parsed row count |
| `status` | CharField(20) | default=`UPLOADED` | `UPLOADED`, `VALIDATED`, `APPROVED`, `COMMITTED`, `DELETED` |
| `validation_summary` | JSONField | default=dict | `{total_rows, valid_rows, invalid_rows, errors_count}` |

**Template Type Choices:**
```python
class TemplateType(models.TextChoices):
    T1_CHART_OF_ACCOUNTS = "T1", "Chart of Accounts"
    T2_TRIAL_BALANCE = "T2", "Trial Balance"
    T3_MEMBER_OUTSTANDING = "T3", "Member Outstanding"
    T4_VENDOR_OUTSTANDING = "T4", "Vendor Outstanding"
    T5_BANK_OPENING = "T5", "Bank Opening Balances"
    T6_CASH_OPENING = "T6", "Cash Opening Balance"
    T7_FIXED_ASSETS = "T7", "Fixed Assets"
    T8_SECURITY_DEPOSITS = "T8", "Security Deposits"
    T9_LOANS = "T9", "Loans"
    T10_FUNDS = "T10", "Funds"
```

**Meta:**
- `ordering = ["-uploaded_at"]`
- `indexes`: `[Index(fields=["wizard", "template_type"]), Index(fields=["society", "status"])]`

**Manager:** `objects = TenantManager()`.

### 2.4 Migration Finalization

#### 2.4.1 `MigrationAuditLog` — [`onboarding/models/model_MigrationAuditLog.py`](../onboarding/models/model_MigrationAuditLog.py:1)

Append-only audit log specific to the migration process (complements the platform-wide [`AuditLog`](../auditlog/models.py:21)). This provides a migration-specific trail with before/after state snapshots.

| Field | Type | null/blank | Notes |
|-------|------|------------|-------|
| `wizard` | FK → `OnboardingWizard` | — | `on_delete=CASCADE`, `related_name="migration_logs"` |
| `society` | FK → `housing.Society` | — | `on_delete=CASCADE` |
| `action` | CharField(30) | — | `UPLOAD`, `VALIDATE`, `DELETE`, `APPROVE`, `COMMIT`, `LOCK`, `FINALIZE` |
| `actor` | FK → `User` | null=True | `on_delete=SET_NULL` |
| `timestamp` | DateTimeField | auto_now_add | |
| `details` | JSONField | default=dict | Action-specific metadata |
| `before_state` | JSONField | null=True, blank=True | State snapshot before action |
| `after_state` | JSONField | null=True, blank=True | State snapshot after action |

**Meta:**
- `ordering = ["-timestamp"]`
- `indexes`: `[Index(fields=["wizard", "action"]), Index(fields=["society", "timestamp"])]`

**Methods:**
- `save()` — append-only: reject updates (pattern from [`auditlog/models.py`](../auditlog/models.py:159)).
- `delete()` — raise `PermissionError` (pattern from [`auditlog/models.py`](../auditlog/models.py:168)).
- `@classmethod log(*, wizard, action, actor=None, details=None, before_state=None, after_state=None)` — canonical entry point (mirrors [`AuditLog.log()`](../auditlog/models.py:94)).

### 2.5 Models `__init__.py` — [`onboarding/models/__init__.py`](../onboarding/models/__init__.py:1)

```python
from .model_OnboardingWizard import OnboardingWizard
from .model_WizardStepLog import WizardStepLog
from .model_UploadBatch import UploadBatch
from .model_StagingChartOfAccounts import StagingChartOfAccounts
from .model_StagingTrialBalance import StagingTrialBalance
from .model_StagingMemberOutstanding import StagingMemberOutstanding
from .model_StagingVendorOutstanding import StagingVendorOutstanding
from .model_StagingBankOpening import StagingBankOpening
from .model_StagingCashOpening import StagingCashOpening
from .model_StagingFixedAsset import StagingFixedAsset
from .model_StagingSecurityDeposit import StagingSecurityDeposit
from .model_StagingLoan import StagingLoan
from .model_StagingFund import StagingFund
from .model_MigrationAuditLog import MigrationAuditLog

__all__ = [
    "OnboardingWizard", "WizardStepLog", "UploadBatch",
    "StagingChartOfAccounts", "StagingTrialBalance",
    "StagingMemberOutstanding", "StagingVendorOutstanding",
    "StagingBankOpening", "StagingCashOpening",
    "StagingFixedAsset", "StagingSecurityDeposit",
    "StagingLoan", "StagingFund", "MigrationAuditLog",
]
```

---

## 3. Services Design

The service layer follows the pattern established in [`gateops/services/contractor_service.py`](../gateops/services/contractor_service.py:83): `@staticmethod` methods, `@transaction.atomic` for mutations, explicit society scoping, and audit logging. All services are in [`onboarding/services/`](../onboarding/services/:1).

### 3.1 `WizardService` — [`onboarding/services/wizard_service.py`](../onboarding/services/wizard_service.py:1)

Manages the wizard lifecycle: creation, step navigation, resume, abandon.

```python
class WizardService:
    @staticmethod
    @transaction.atomic
    def create_wizard(*, user, fy_pattern="APRIL_MARCH") -> OnboardingWizard
        # Create a new OnboardingWizard with status=IN_PROGRESS, current_step=1
        # Log via MigrationAuditLog.log(action="CREATE")

    @staticmethod
    def get_wizard_state(*, wizard_id, user) -> dict
        # Return {wizard, current_step, step_logs, completed_steps, society_type, ...}
        # Used by views to render the current step

    @staticmethod
    @transaction.atomic
    def advance_step(*, wizard, step_number, step_data=None, user=None) -> OnboardingWizard
        # Validate step_number == wizard.current_step
        # Create WizardStepLog(status=COMPLETED, data_snapshot=step_data)
        # Increment wizard.current_step
        # Handle branch at Step 9: if society_type == NEW, jump to Step 28
        # Log via MigrationAuditLog

    @staticmethod
    @transaction.atomic
    def go_to_step(*, wizard, step_number, user=None) -> OnboardingWizard
        # Allow backward navigation (review/correction) up to Step 25
        # Cannot go beyond committed boundary
        # Create WizardStepLog(status=STARTED) for the target step

    @staticmethod
    @transaction.atomic
    def resume_wizard(*, wizard, user=None) -> OnboardingWizard
        # Increment resumed_count
        # Return user to wizard.current_step
        # Log via MigrationAuditLog

    @staticmethod
    @transaction.atomic
    def abandon_wizard(*, wizard, user=None) -> OnboardingWizard
        # Set status=ABANDONED
        # Log via MigrationAuditLog

    @staticmethod
    def list_wizards_for_user(*, user) -> QuerySet
        # Return OnboardingWizard.objects.filter(created_by=user, status__in=[IN_PROGRESS])
```

**Inputs/Outputs:**
- `create_wizard`: inputs = `user`, `fy_pattern`; output = `OnboardingWizard` instance.
- `advance_step`: inputs = `wizard`, `step_number`, `step_data` (dict), `user`; output = updated `OnboardingWizard`.
- `get_wizard_state`: inputs = `wizard_id`, `user`; output = dict with all state needed by views.

**Existing services called:** None directly (orchestrates other onboarding services).

### 3.2 `SocietySetupService` — [`onboarding/services/society_setup_service.py`](../onboarding/services/society_setup_service.py:1)

Creates the society, structures, units, and members. Wraps existing patterns from [`seed_deepsagar.py`](../housing/management/commands/seed_deepsagar.py:54) and [`create_society()`](../societies/services.py:36).

```python
class SocietySetupService:
    @staticmethod
    @transaction.atomic
    def create_society_record(*, wizard, user, society_data: dict) -> Society
        # Call societies.services.create_society(user=user, name=..., registration_number=..., address=...)
        # Persist extended fields (city, state, PAN, GST, TAN, email, phone, etc.) to SocietyConfig
        # Set wizard.society = society
        # Set session scope via selection.py
        # Log via AuditLog.log(action=CREATE, entity_type="Society")
        # Return society

    @staticmethod
    @transaction.atomic
    def create_structures(*, wizard, society, structures_data: list) -> list[Structure]
        # For each structure node: Structure.objects.get_or_create(society=..., parent=..., ...)
        # Validate nesting depth via Structure.clean()
        # Log each creation via AuditLog.log

    @staticmethod
    @transaction.atomic
    def create_units(*, wizard, society, units_data: list) -> list[Unit]
        # For each unit: Unit.objects.get_or_create(structure=..., identifier=..., ...)
        # Support bulk creation (pattern from BulkUnitCreateView)
        # Log via AuditLog.log

    @staticmethod
    @transaction.atomic
    def create_members(*, wizard, society, members_data: list) -> list[Member]
        # For each member: Member.objects.get_or_create(society=..., unit=..., full_name=..., role=...)
        # Call sync_member_unit_lifecycle(member) for each
        # Log via AuditLog.log

    @staticmethod
    @transaction.atomic
    def setup_accounting_defaults(*, wizard, society) -> None
        # Call ensure_standard_accounts(society) [categories + accounts]
        # Call AccountMapping.ensure_for_society(society)
        # Log via AuditLog.log
```

**Existing services called:**
- [`create_society()`](../societies/services.py:36) — creates Society + Membership.
- [`ensure_standard_accounts()`](../accounting/services/standard_accounts.py:729) — creates categories + ~150 accounts.
- [`AccountMapping.ensure_for_society()`](../accounting/models/model_AccountMapping.py:141) — maps semantic concepts.
- [`sync_member_unit_lifecycle()`](../housing/services/membership_lifecycle.py:36) — creates ownership + occupancy.
- [`AuditLog.log()`](../auditlog/models.py:94) — audit trail.

### 3.3 `ModuleConfigurationService` — [`onboarding/services/module_config_service.py`](../onboarding/services/module_config_service.py:1)

Enables/disables modules based on Step 3 selection. Core modules are always enabled.

```python
class ModuleConfigurationService:
    CORE_MODULES = ["accounting", "billing", "members", "society_admin"]
    OPTIONAL_MODULES = [
        "parking", "gateops", "reconciliation", "shares",
        "notifications", "facility_booking", "complaints",
        "vendor_management", "inventory", "asset_register",
        "email", "sms", "whatsapp", "analytics", "ai_assistant",
    ]

    @staticmethod
    @transaction.atomic
    def configure_modules(*, wizard, society, selected_modules: list[str]) -> None
        # Ensure CORE_MODULES are always included
        # Persist to wizard.selected_modules (JSON)
        # Update SocietyConfig or a new SocietyModuleConfig model if needed
        # Log via AuditLog.log

    @staticmethod
    def get_enabled_modules(*, wizard) -> list[str]
        # Return wizard.selected_modules (always includes CORE_MODULES)

    @staticmethod
    def is_module_enabled(*, wizard, module_key: str) -> bool
        # Check if a module is in the enabled set
```

**Existing services called:** [`SocietyConfig`](../societies/models/model_SocietyConfig.py:9) for persistence, [`AuditLog.log()`](../auditlog/models.py:94).

### 3.4 `FinancialYearSetupService` — [`onboarding/services/financial_year_service.py`](../onboarding/services/financial_year_service.py:1)

Creates the FinancialYear and triggers auto-period creation. Wraps the pattern from [`seed_deepsagar._ensure_open_financial_year()`](../housing/management/commands/seed_deepsagar.py:323).

```python
class FinancialYearSetupService:
    @staticmethod
    @transaction.atomic
    def create_financial_year(*, wizard, society, fy_label: str, fy_pattern: str) -> FinancialYear
        # Derive start_date/end_date from fy_label and fy_pattern
        # e.g. "2026-27" + APRIL_MARCH → 2026-04-01 to 2027-03-31
        # FinancialYear.objects.get_or_create(society=..., start_date=..., end_date=..., defaults={name, is_open=True})
        # FinancialYear.save() auto-creates monthly AccountingPeriods
        # Open periods up to today (handled by _create_accounting_periods)
        # Set session scope FY via selection.py
        # Log via AuditLog.log(action=CREATE, entity_type="FinancialYear")
        # Return fy

    @staticmethod
    def derive_fy_dates(*, fy_label: str, fy_pattern: str) -> tuple[date, date]
        # Parse "2026-27" → (2026-04-01, 2027-03-31) for APRIL_MARCH
        # Parse "2026-27" → (2026-01-01, 2026-12-31) for JAN_DEC
        # Parse "2026-27" → (2026-07-01, 2027-06-30) for JUL_JUN

    @staticmethod
    def get_fy_options(*, fy_pattern: str) -> list[str]
        # Generate list of FY labels for the pattern (current year ± 2)
```

**Existing services called:** [`FinancialYear.save()`](../accounting/models/model_FinancialYear.py:42) (auto-creates periods), [`AuditLog.log()`](../auditlog/models.py:94).

### 3.5 `StagingService` — [`onboarding/services/staging_service.py`](../onboarding/services/staging_service.py:1)

Manages the staging area: file upload, parsing, storage, deletion, approval.

```python
class StagingService:
    # Maps template_type → staging model class
    TEMPLATE_MODEL_MAP = {
        "T1": StagingChartOfAccounts,
        "T2": StagingTrialBalance,
        "T3": StagingMemberOutstanding,
        "T4": StagingVendorOutstanding,
        "T5": StagingBankOpening,
        "T6": StagingCashOpening,
        "T7": StagingFixedAsset,
        "T8": StagingSecurityDeposit,
        "T9": StagingLoan,
        "T10": StagingFund,
    }

    @staticmethod
    @transaction.atomic
    def upload_file(*, wizard, society, template_type, file, user) -> UploadBatch
        # Validate file extension (.xlsx or .csv)
        # Save file to media storage
        # Create UploadBatch(status=UPLOADED)
        # Parse file → rows
        # Call store_staging() to persist rows
        # Update batch.row_count
        # Log via MigrationAuditLog.log(action="UPLOAD")

    @staticmethod
    def parse_file(*, file_path: str, template_type: str) -> list[dict]
        # Parse CSV/XLSX into list of dicts (one per row)
        # Validate header row against template schema (R-10)
        # Raise ValidationError if headers don't match
        # Return list of raw row dicts

    @staticmethod
    @transaction.atomic
    def store_staging(*, wizard, society, batch, template_type, rows: list[dict]) -> int
        # Map rows to staging model fields
        # Create staging records with validation_status=PENDING
        # Return count of created rows

    @staticmethod
    @transaction.atomic
    def delete_batch(*, wizard, template_type, user) -> None
        # Delete all staging rows for this template_type
        # Mark UploadBatch.status=DELETED
        # Log via MigrationAuditLog.log(action="DELETE")
        # Cannot delete if any rows are is_committed=True

    @staticmethod
    def get_staging_data(*, wizard, template_type) -> QuerySet
        # Return staging queryset for the template_type
        # Ordered by row_number

    @staticmethod
    @transaction.atomic
    def approve_batch(*, wizard, template_type, user) -> UploadBatch
        # Validate all rows have validation_status=VALID
        # Set is_approved=True on all rows
        # Set UploadBatch.status=APPROVED
        # Log via MigrationAuditLog.log(action="APPROVE")
```

**Inputs/Outputs:**
- `upload_file`: inputs = `wizard`, `society`, `template_type`, `file` (UploadedFile), `user`; output = `UploadBatch`.
- `parse_file`: inputs = `file_path`, `template_type`; output = `list[dict]`.
- `store_staging`: inputs = `wizard`, `society`, `batch`, `template_type`, `rows`; output = `int` (row count).

**Existing services called:** [`MigrationAuditLog.log()`](../onboarding/models/model_MigrationAuditLog.py:1), [`AuditLog.log()`](../auditlog/models.py:94).

### 3.6 `ValidationService` — [`onboarding/services/validation_service.py`](../onboarding/services/validation_service.py:1)

Validates staging data against the spec's validation rules (§10 of the spec). This is the most complex service.

```python
class ValidationService:
    @staticmethod
    @transaction.atomic
    def validate_batch(*, wizard, society, template_type) -> dict
        # Run all applicable validation rules for the template_type
        # Update each staging row's validation_status and validation_errors
        # Return summary: {total, valid, invalid, errors: [...]}

    @staticmethod
    def validate_trial_balance(*, wizard, society) -> dict
        # V-T2-1: account_code exists in COA
        # V-T2-2: account_name matches
        # V-T2-3: not both debit and credit non-zero
        # V-T2-4: at least one of debit/credit non-zero
        # V-T2-5: no account appears twice
        # V-T2-6: Σ Debit == Σ Credit (hard gate)

    @staticmethod
    def validate_member_outstanding(*, wizard, society) -> dict
        # V-T3-1: unit_identifier matches existing Unit
        # V-T3-2: member_name non-empty
        # V-T3-3: outstanding_amount ≥ 0
        # V-T3-4: no duplicate (unit, member) rows
        # V-T3-5: Σ reconciles to Maintenance Receivable Ledger

    @staticmethod
    def validate_vendor_outstanding(*, wizard, society) -> dict
        # V-T4-1: vendor_name non-empty
        # V-T4-2: no duplicate vendor rows
        # V-T4-3: all amounts ≥ 0
        # V-T4-4: Σ reconciles to Vendor Control Account

    @staticmethod
    def validate_bank_balances(*, wizard, society) -> dict
        # V-T5-1: account_code exists with is_bank=True
        # V-T5-2: account_number non-empty
        # V-T5-3: IFSC regex if provided
        # V-T5-4: opening_balance ≥ 0
        # V-T5-5: no duplicate bank account codes
        # V-T5-6: each bank balance matches T2

    @staticmethod
    def validate_cross_references(*, wizard, society) -> dict
        # Cross-template reconciliation checks (C1–C9 from spec §10.3)
        # C1: Trial Balance balanced
        # C2: Balance Sheet matched (Assets = Liabilities + Equity)
        # C3: Bank balances matched
        # C4: Member outstanding matched
        # C5: Vendor outstanding matched
        # C6: Assets matched
        # C7: Funds matched
        # C8: Global debit = credit
        # C9: No validation errors

    @staticmethod
    def get_validation_report(*, wizard, template_type) -> dict
        # Return row-level error report: [{row_number, column, reason, suggested_fix}]
```

**Existing services called:** [`Account`](../accounting/models/model_Account.py:8) lookups (by code), [`Unit`](../members/models/model_Unit.py:4) lookups (by identifier), [`AccountCodes`](../accounting/services/gst_vouchers.py:13) constants.

### 3.7 `ReconciliationService` — [`onboarding/services/reconciliation_service.py`](../onboarding/services/reconciliation_service.py:1)

Generates the reconciliation dashboard data (Step 23) and runs the validation checklist (Step 24).

```python
class ReconciliationService:
    @staticmethod
    def generate_trial_balance(*, wizard, society) -> dict
        # Aggregate StagingTrialBalance rows
        # Return {rows: [{account_code, account_name, debit, credit}], total_debit, total_credit}

    @staticmethod
    def generate_balance_sheet(*, wizard, society) -> dict
        # Derive from staged T2 + T7 + T8 + T9 + T10
        # Return {assets: [...], liabilities: [...], equity: [...], totals}

    @staticmethod
    def generate_member_summary(*, wizard, society) -> dict
        # Aggregate StagingMemberOutstanding rows
        # Return {rows: [...], total_outstanding, total_advance, total_credit, ...}

    @staticmethod
    def generate_vendor_summary(*, wizard, society) -> dict
        # Aggregate StagingVendorOutstanding rows

    @staticmethod
    def generate_bank_summary(*, wizard, society) -> dict
        # Aggregate StagingBankOpening rows

    @staticmethod
    def generate_fund_summary(*, wizard, society) -> dict
        # Aggregate StagingFund rows

    @staticmethod
    def run_checklist(*, wizard, society) -> dict
        # Run all 9 checks (C1–C9)
        # Return {checks: [{id, name, passed, detail}], all_passed: bool}
        # all_passed gates Step 25 (Final Approval)
```

**Existing services called:** [`ValidationService.validate_cross_references()`](../onboarding/services/validation_service.py:1), [`AccountCodes`](../accounting/services/gst_vouchers.py:13) for account lookups.

### 3.8 `MigrationFinalizationService` — [`onboarding/services/migration_finalization_service.py`](../onboarding/services/migration_finalization_service.py:1)

The most critical service: creates the immutable Opening Journal and locks the migration. Follows the pattern from [`year_end.close_financial_year_with_carry_forward()`](../accounting/services/year_end.py:36).

```python
class MigrationFinalizationService:
    @staticmethod
    @transaction.atomic
    def finalize_migration(*, wizard, society, user) -> Voucher
        # Pre-flight: verify all checklist checks pass
        # Pre-flight: verify all staging batches are APPROVED
        # Call create_opening_journal()
        # Call lock_migration()
        # Set wizard.status = COMPLETED, wizard.completed_at = now
        # Log via AuditLog.log(action=POST)
        # Return the opening voucher

    @staticmethod
    @transaction.atomic
    def create_opening_journal(*, wizard, society, user) -> Voucher
        # Get the FinancialYear for the wizard
        # Create Voucher(voucher_type=OPENING, voucher_date=FY.start_date, narration="Opening balances - Migration")
        # Call create_opening_ledger_entries() for T2 accounts
        # Call create_opening_member_balances() for T3
        # Call create_opening_vendor_balances() for T4
        # Call create_opening_bank_balances() for T5 (already in T2, but ensure bank-specific entries)
        # Validate voucher is balanced (Voucher.clean())
        # Call voucher.post() — assigns voucher_number, sets posted_at (immutable)
        # Log via AuditLog.log(action=POST, entity_type="Voucher")
        # Return voucher

    @staticmethod
    def create_opening_ledger_entries(*, voucher, wizard, society) -> list[LedgerEntry]
        # For each StagingTrialBalance row with non-zero balance:
        #   Look up Account by code
        #   Create LedgerEntry(voucher=voucher, account=account, debit=..., credit=...)
        # Return list of created entries

    @staticmethod
    def create_opening_member_balances(*, voucher, wizard, society) -> list[LedgerEntry]
        # For each StagingMemberOutstanding row:
        #   Look up Unit by identifier
        #   Look up Member receivable account (AccountCodes.MAINTENANCE_DUE)
        #   Create LedgerEntry with unit FK (required for member-related accounts)
        #   Debit outstanding_amount (if > 0)
        # Return list

    @staticmethod
    def create_opening_vendor_balances(*, voucher, wizard, society) -> list[LedgerEntry]
        # For each StagingVendorOutstanding row:
        #   Look up vendor payable account
        #   Create LedgerEntry (credit outstanding_amount)
        # Return list

    @staticmethod
    @transaction.atomic
    def lock_migration(*, wizard, society, user) -> None
        # Set wizard.status = LOCKED
        # Mark all staging rows is_committed=True (read-only)
        # The opening voucher is already immutable via Voucher.post()
        # Log via MigrationAuditLog.log(action="LOCK")
        # Log via AuditLog.log(action=LOCK, entity_type="OnboardingWizard")

    @staticmethod
    def create_audit_log(*, wizard, action, actor=None, before_state=None, after_state=None) -> MigrationAuditLog
        # Wrapper for MigrationAuditLog.log()
```

**Existing services called:**
- [`Voucher`](../accounting/models/model_Voucher.py:14) creation + `.post()` (immutable after posting).
- [`LedgerEntry`](../accounting/models/model_LedgerEntry.py:10) creation (validates unit FK for member accounts).
- [`VoucherSequence`](../accounting/models/model_voucher_sequence.py:1) (auto-assigned in `.post()`).
- [`AuditLog.log()`](../auditlog/models.py:94) (platform-wide audit).
- [`FinancialYear.get_open_year_for_date()`](../accounting/models/model_FinancialYear.py:30) (validates FY is open).

---

## 4. Views & URLs Design

### 4.1 URL Structure — [`onboarding/urls.py`](../onboarding/urls.py:1)

```python
from django.urls import path
from onboarding import views

app_name = "onboarding"

urlpatterns = [
    path("", views.wizard_list, name="wizard-list"),
    path("start/", views.wizard_start, name="wizard-start"),
    path("<int:wizard_id>/", views.wizard_detail, name="wizard-detail"),
    path("<int:wizard_id>/step/<int:step_number>/", views.wizard_step, name="wizard-step"),
    path("<int:wizard_id>/step/<int:step_number>/save/", views.wizard_step_save, name="wizard-step-save"),
    path("<int:wizard_id>/upload/<str:template_type>/", views.staging_upload, name="staging-upload"),
    path("<int:wizard_id>/staging/<str:template_type>/", views.staging_view, name="staging-view"),
    path("<int:wizard_id>/staging/<str:template_type>/delete/", views.staging_delete, name="staging-delete"),
    path("<int:wizard_id>/staging/<str:template_type>/approve/", views.staging_approve, name="staging-approve"),
    path("<int:wizard_id>/reconciliation/", views.reconciliation_dashboard, name="reconciliation-dashboard"),
    path("<int:wizard_id>/checklist/", views.validation_checklist, name="validation-checklist"),
    path("<int:wizard_id>/finalize/", views.finalize_migration, name="finalize-migration"),
    path("<int:wizard_id>/complete/", views.wizard_complete, name="wizard-complete"),
]
```

### 4.2 View Specifications

| URL Name | Pattern | View Type | Template | Purpose |
|----------|---------|-----------|----------|---------|
| `wizard-list` | `/onboarding/` | Function-based | `wizard_list.html` | List in-progress wizards for the user |
| `wizard-start` | `/onboarding/start/` | Function-based (POST) | Redirect to step 1 | Create new wizard via `WizardService.create_wizard()` |
| `wizard-detail` | `/onboarding/<id>/` | Function-based | Redirect to current step | Redirect to `wizard.current_step` |
| `wizard-step` | `/onboarding/<id>/step/<n>/` | Function-based | `step_*.html` (varies by step) | Render specific step with form |
| `wizard-step-save` | `/onboarding/<id>/step/<n>/save/` | Function-based (POST) | Redirect to next step | Save step data, advance wizard |
| `staging-upload` | `/onboarding/<id>/upload/<T>/` | Function-based | `staging_area.html` | Upload file to staging |
| `staging-view` | `/onboarding/<id>/staging/<T>/` | Function-based | `staging_area.html` | View staging data + validation errors |
| `staging-delete` | `/onboarding/<id>/staging/<T>/delete/` | Function-based (POST) | Redirect to staging view | Delete staging batch |
| `staging-approve` | `/onboarding/<id>/staging/<T>/approve/` | Function-based (POST) | Redirect to staging view | Approve staging batch |
| `reconciliation-dashboard` | `/onboarding/<id>/reconciliation/` | Function-based | `reconciliation_dashboard.html` | Reconciliation dashboard (Step 23) |
| `validation-checklist` | `/onboarding/<id>/checklist/` | Function-based | `validation_checklist.html` | Validation checklist (Step 24) |
| `finalize-migration` | `/onboarding/<id>/finalize/` | Function-based (POST) | `step_final_approval.html` | Final approval + trigger migration (Steps 25-27) |
| `wizard-complete` | `/onboarding/<id>/complete/` | Function-based | `step_complete.html` | Success page (Step 28) |

### 4.3 View Implementation Pattern

All views follow the project's established patterns (from [`gateops/views.py`](../gateops/views.py:1) and [`housing/views.py`](../housing/views.py:1)):

```python
# Pattern for step views
@login_required
def wizard_step(request, wizard_id, step_number):
    wizard = get_object_or_404(OnboardingWizard, pk=wizard_id, created_by=request.user)
    society = wizard.society

    # Validate step access
    if step_number > wizard.current_step:
        messages.error(request, "Cannot access future steps.")
        return redirect("onboarding:wizard-step", wizard_id=wizard_id, step_number=wizard.current_step)

    # Get the form for this step
    form = get_step_form(step_number, wizard=wizard, society=society)

    # Get step state for template
    state = WizardService.get_wizard_state(wizard_id=wizard_id, user=request.user)

    return render(request, get_step_template(step_number), {
        "wizard": wizard,
        "step_number": step_number,
        "form": form,
        "state": state,
    })
```

**Key view conventions:**
- `@login_required` on all views (pattern from [`housing/views.py`](../housing/views.py:3)).
- [`get_selected_scope(request)`](../housing_accounting/selection.py:78) for tenant context.
- `messages.success/error/warning` for user feedback.
- `@transaction.atomic` on POST handlers (though `ATOMIC_REQUESTS=True` already wraps requests).
- Step-to-form and step-to-template mapping via a `STEP_CONFIG` dict in [`onboarding/constants.py`](../onboarding/constants.py:1).

### 4.4 Step-to-View Mapping

| Step | View Function | Form Class | Template |
|------|---------------|------------|----------|
| 1 | `wizard_step` | `SocietyDetailsForm` | `step_society_details.html` |
| 2 | `wizard_step` | `SocietyTypeForm` | `step_society_type.html` |
| 3 | `wizard_step` | `ModuleSelectionForm` | `step_module_selection.html` |
| 4 | `wizard_step` | `AccountingStartYearForm` | `step_accounting_start_year.html` |
| 5 | `wizard_step` | (auto, no form) | `step_financial_year_creation.html` |
| 6 | `wizard_step` | `StructureForm` | `step_structure.html` |
| 7 | `wizard_step` | `UnitConfigurationForm` | `step_unit_configuration.html` |
| 8 | `wizard_step` | `MemberAssignmentForm` | `step_member_assignment.html` |
| 9 | `wizard_step` | (auto, no form) | `step_accounting_setup.html` |
| 10 | `wizard_step` | (inline COA editor) | `step_chart_of_accounts.html` |
| 11 | `wizard_step` | (download links) | `step_import_templates.html` |
| 12–14 | `staging_upload` / `staging_view` / `staging_delete` | (file upload) | `staging_area.html` |
| 15–22 | `staging_view` (per template) | (validation display) | `staging_area.html` |
| 23 | `reconciliation_dashboard` | (none) | `reconciliation_dashboard.html` |
| 24 | `validation_checklist` | (none) | `validation_checklist.html` |
| 25–27 | `finalize_migration` | `FinalApprovalForm` | `step_final_approval.html` |
| 28 | `wizard_complete` | (none) | `step_complete.html` |

---

## 5. Forms Design

All forms follow the pattern from [`gateops/forms.py`](../gateops/forms.py:1): `SocietyScopedModelForm` base with `society` kwarg, crispy_forms `FormHelper`, and Bootstrap 5 widget classes.

### 5.1 `SocietyDetailsForm` — Step 1

[`onboarding/forms.py`](../onboarding/forms.py:1)

```python
class SocietyDetailsForm(forms.Form):
    """Step 1: Capture society legal identity and locale configuration."""
    name = forms.CharField(max_length=200, label="Society Name")
    registration_number = forms.CharField(max_length=100, label="Registration Number")
    registration_date = forms.DateField(label="Registration Date")
    society_type_choice = forms.ChoiceField(
        choices=[("RESIDENTIAL", "Residential"), ("COMMERCIAL", "Commercial"), ("MIXED", "Mixed")],
        label="Society Type",
    )
    address = forms.CharField(widget=forms.Textarea, label="Address")
    city = forms.CharField(max_length=100)
    state = forms.CharField(max_length=100)
    country = forms.CharField(max_length=100, initial="India")
    pin_code = forms.CharField(max_length=10, label="PIN Code")
    pan = forms.CharField(max_length=10, label="PAN")
    gst_number = forms.CharField(max_length=20, required=False, label="GST Number")
    tan = forms.CharField(max_length=10, required=False, label="TAN")
    email = forms.EmailField(label="Society Contact Email")
    phone = forms.CharField(max_length=15, label="Phone")
    time_zone = forms.ChoiceField(
        choices=[("Asia/Kolkata", "India (IST)"), ...],
        initial="Asia/Kolkata",
    )
    currency = forms.ChoiceField(choices=[("INR", "Indian Rupee")], initial="INR")
    fy_pattern = forms.ChoiceField(
        choices=[("APRIL_MARCH", "April–March"), ("JAN_DEC", "January–December"), ("JUL_JUN", "July–June")],
        initial="APRIL_MARCH",
        label="Financial Year Pattern",
    )

    def clean_pin_code(self):
        # Regex ^\d{6}$
    def clean_pan(self):
        # Regex ^[A-Z]{5}\d{4}[A-Z]$
    def clean_gst_number(self):
        # Regex if provided
    def clean_tan(self):
        # Regex if provided
    def clean_phone(self):
        # Regex ^\+?\d{10,15}$
    def clean_registration_date(self):
        # Not in future
```

### 5.2 `SocietyTypeForm` — Step 2

```python
class SocietyTypeForm(forms.Form):
    """Step 2: Choose NEW or EXISTING society."""
    society_type = forms.ChoiceField(
        choices=[
            ("NEW", "Brand New Society"),
            ("EXISTING", "Existing Society (Migrating from another software)"),
        ],
        widget=forms.RadioSelect,
        label="Select Society Type",
    )
```

### 5.3 `ModuleSelectionForm` — Step 3

```python
class ModuleSelectionForm(forms.Form):
    """Step 3: Select optional modules. Core modules are always enabled."""
    # Core modules rendered as disabled checkboxes (always checked)
    # Optional modules rendered as selectable checkboxes
    parking = forms.BooleanField(required=False, label="Parking Management")
    gateops = forms.BooleanField(required=False, label="Gate Management")
    reconciliation = forms.BooleanField(required=False, label="Bank Reconciliation")
    shares = forms.BooleanField(required=False, label="Share Certificate Management")
    notifications = forms.BooleanField(required=False, label="Email/SMS Notifications")
    # ... all optional modules ...

    def get_selected_modules(self) -> list[str]:
        # Return list of selected module keys
        # Always includes CORE_MODULES
```

### 5.4 `AccountingStartYearForm` — Step 4

```python
class AccountingStartYearForm(forms.Form):
    """Step 4: Choose the accounting start financial year."""
    accounting_start_year = forms.ChoiceField(
        label="Financial Year",
        # choices populated dynamically based on fy_pattern from Step 1
    )

    def __init__(self, *args, fy_pattern="APRIL_MARCH", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["accounting_start_year"].choices = (
            FinancialYearSetupService.get_fy_options(fy_pattern=fy_pattern)
        )
```

### 5.5 `StructureForm` — Step 6

```python
class StructureForm(forms.Form):
    """Step 6: Configure society structure (buildings, wings, floors)."""
    topology_mode = forms.ChoiceField(
        choices=[
            ("SINGLE_BUILDING", "Single Building"),
            ("MULTIPLE_BUILDINGS", "Multiple Buildings"),
            ("COMMERCIAL_UNITS", "Commercial Units"),
            ("MIXED_SOCIETY", "Mixed Society"),
        ],
        label="Topology Mode",
    )
    # Dynamic structure tree (rendered via JS/AJAX, submitted as JSON)
    structures_json = forms.CharField(
        widget=forms.HiddenInput,
        label="Structure Tree",
    )

    def clean_structures_json(self):
        # Parse JSON, validate each node has structure_type, name, parent, display_order
```

### 5.6 `UnitConfigurationForm` — Step 7

```python
class UnitConfigurationForm(forms.Form):
    """Step 7: Configure units within structures."""
    # Dynamic grid (pattern from BulkUnitCreateView)
    units_json = forms.CharField(
        widget=forms.HiddenInput,
        label="Units Data",
    )

    def clean_units_json(self):
        # Parse JSON, validate each unit has identifier, area, unit_type
        # Validate identifiers are unique within structure
```

### 5.7 `MemberAssignmentForm` — Step 8

```python
class MemberAssignmentForm(forms.Form):
    """Step 8: Assign members to units."""
    members_json = forms.CharField(
        widget=forms.HiddenInput,
        label="Members Data",
    )

    def clean_members_json(self):
        # Parse JSON, validate each member has full_name, email, role, unit_identifier
        # Validate unit exists
```

### 5.8 `FinalApprovalForm` — Step 25

```python
class FinalApprovalForm(forms.Form):
    """Step 25: Explicit final approval with warning confirmation."""
    confirm = forms.BooleanField(
        required=True,
        label="I understand that opening balances will become permanent and cannot be undone.",
    )
    reason = forms.CharField(
        widget=forms.Textarea,
        required=False,
        label="Reason / Notes",
    )
```

---

## 6. Template Structure

### 6.1 Base Wizard Template — [`onboarding/templates/onboarding/base_wizard.html`](../onboarding/templates/onboarding/base_wizard.html:1)

Extends the project's [`base.html`](../housing_accounting/templates/base.html:1) and provides:
- A step progress bar (visual indicator of current/completed steps).
- A wizard sidebar with navigation (backward only, up to Step 25).
- A content block for the specific step.
- HTMX integration for dynamic form updates (pattern from existing templates using `htmx.org`).

```django
{% extends "base.html" %}
{% block body %}
  <div class="wizard-container">
    {% include "onboarding/partials/step_progress_bar.html" %}
    <div class="wizard-content">
      {% block wizard_content %}{% endblock %}
    </div>
  </div>
{% endblock %}
```

### 6.2 Step Progress Bar — [`onboarding/templates/onboarding/partials/step_progress_bar.html`](../onboarding/templates/onboarding/partials/step_progress_bar.html:1)

Renders a horizontal progress indicator showing:
- Completed steps (green checkmark).
- Current step (highlighted).
- Future steps (greyed out).
- Branch indicator (NEW vs EXISTING path).

### 6.3 Individual Step Templates

Each step template extends `base_wizard.html` and renders the form via crispy_forms:

```django
{% extends "onboarding/base_wizard.html" %}
{% load crispy_forms_tags %}
{% block wizard_content %}
  <h2>Step {{ step_number }}: Society Details</h2>
  <form method="post" action="{% url 'onboarding:wizard-step-save' wizard.id step_number %}">
    {% csrf_token %}
    {{ form|crispy }}
    <button type="submit" class="btn btn-primary">Save & Continue</button>
    {% if step_number > 1 %}
      <a href="{% url 'onboarding:wizard-step' wizard.id step_number|add:'-1' %}" class="btn btn-secondary">Back</a>
    {% endif %}
  </form>
{% endblock %}
```

### 6.4 Staging Area Template — [`onboarding/templates/onboarding/staging_area.html`](../onboarding/templates/onboarding/staging_area.html:1)

Renders:
- File upload form (for the current template type).
- Staging data table (with row numbers, validation status badges, error annotations).
- Delete and Approve buttons.
- Validation error panel (row/column/reason/suggested fix).

Uses the partials:
- [`staging_table.html`](../onboarding/templates/onboarding/partials/staging_table.html:1) — renders the staging data table.
- [`validation_errors.html`](../onboarding/templates/onboarding/partials/validation_errors.html:1) — renders row-level errors.

### 6.5 Reconciliation Dashboard — [`onboarding/templates/onboarding/reconciliation_dashboard.html`](../onboarding/templates/onboarding/reconciliation_dashboard.html:1)

Renders six sections in a grid:
- Trial Balance summary.
- Balance Sheet summary.
- Member Outstanding summary.
- Vendor Outstanding summary.
- Bank Summary.
- Fund Summary.

Each section shows totals and a "match/mismatch" indicator against the trial balance.

### 6.6 Validation Checklist — [`onboarding/templates/onboarding/validation_checklist.html`](../onboarding/templates/onboarding/validation_checklist.html:1)

Renders the 9 checks (C1–C9) as a checklist:
- Each check shows: name, formula, pass/fail status, detail.
- The "Finalize" button is disabled until all checks pass (enforced via JS + server-side).

### 6.7 Success Page — [`onboarding/templates/onboarding/step_complete.html`](../onboarding/templates/onboarding/step_complete.html:1)

Renders the success summary table (society, modules, FY, accounting, members, billing, migration status) and a button to redirect to the society dashboard.

---

## 7. Implementation Phases

The implementation is broken into 9 ordered phases. Each phase builds on the previous one. Phases should be executed sequentially.

### Phase 1: Models & Migrations (Foundation)

**Goal:** Create all models, migrations, and the app skeleton.

**Files to create:**
- [`onboarding/__init__.py`](../onboarding/__init__.py:1)
- [`onboarding/apps.py`](../onboarding/apps.py:1)
- [`onboarding/admin.py`](../onboarding/admin.py:1)
- [`onboarding/constants.py`](../onboarding/constants.py:1) — step definitions, template type enums, module registry
- [`onboarding/models/__init__.py`](../onboarding/models/__init__.py:1)
- [`onboarding/models/model_OnboardingWizard.py`](../onboarding/models/model_OnboardingWizard.py:1)
- [`onboarding/models/model_WizardStepLog.py`](../onboarding/models/model_WizardStepLog.py:1)
- [`onboarding/models/model_UploadBatch.py`](../onboarding/models/model_UploadBatch.py:1)
- [`onboarding/models/model_StagingChartOfAccounts.py`](../onboarding/models/model_StagingChartOfAccounts.py:1)
- [`onboarding/models/model_StagingTrialBalance.py`](../onboarding/models/model_StagingTrialBalance.py:1)
- [`onboarding/models/model_StagingMemberOutstanding.py`](../onboarding/models/model_StagingMemberOutstanding.py:1)
- [`onboarding/models/model_StagingVendorOutstanding.py`](../onboarding/models/model_StagingVendorOutstanding.py:1)
- [`onboarding/models/model_StagingBankOpening.py`](../onboarding/models/model_StagingBankOpening.py:1)
- [`onboarding/models/model_StagingCashOpening.py`](../onboarding/models/model_StagingCashOpening.py:1)
- [`onboarding/models/model_StagingFixedAsset.py`](../onboarding/models/model_StagingFixedAsset.py:1)
- [`onboarding/models/model_StagingSecurityDeposit.py`](../onboarding/models/model_StagingSecurityDeposit.py:1)
- [`onboarding/models/model_StagingLoan.py`](../onboarding/models/model_StagingLoan.py:1)
- [`onboarding/models/model_StagingFund.py`](../onboarding/models/model_StagingFund.py:1)
- [`onboarding/models/model_MigrationAuditLog.py`](../onboarding/models/model_MigrationAuditLog.py:1)
- [`onboarding/migrations/__init__.py`](../onboarding/migrations/__init__.py:1)
- [`onboarding/migrations/0001_initial.py`](../onboarding/migrations/0001_initial.py:1) — generated via `makemigrations`

**Files to modify:**
- [`config/settings/base.py`](../config/settings/base.py:147) — add `"onboarding"` to `LOCAL_APPS`
- [`config/urls.py`](../config/urls.py:1) — add `path("onboarding/", include("onboarding.urls"))`

**Complexity:** Medium. 14 model files, 1 migration. Straightforward field definitions following existing patterns.

### Phase 2: Wizard State Management Service

**Goal:** Implement `WizardService` for wizard lifecycle (create, advance, resume, abandon).

**Files to create:**
- [`onboarding/services/__init__.py`](../onboarding/services/__init__.py:1)
- [`onboarding/services/wizard_service.py`](../onboarding/services/wizard_service.py:1)

**Complexity:** Low-Medium. State machine logic, step navigation, branch handling at Step 9.

### Phase 3: Society Setup (Steps 1–8)

**Goal:** Implement `SocietySetupService`, `ModuleConfigurationService`, `FinancialYearSetupService`, and the forms for Steps 1–8.

**Files to create:**
- [`onboarding/services/society_setup_service.py`](../onboarding/services/society_setup_service.py:1)
- [`onboarding/services/module_config_service.py`](../onboarding/services/module_config_service.py:1)
- [`onboarding/services/financial_year_service.py`](../onboarding/services/financial_year_service.py:1)
- [`onboarding/forms.py`](../onboarding/forms.py:1) — `SocietyDetailsForm`, `SocietyTypeForm`, `ModuleSelectionForm`, `AccountingStartYearForm`, `StructureForm`, `UnitConfigurationForm`, `MemberAssignmentForm`

**Complexity:** Medium-High. Wraps existing services, handles extended society fields, structure/unit/member creation with validation.

### Phase 4: Staging Area (Steps 10–14)

**Goal:** Implement `StagingService` for file upload, parsing, storage, deletion, approval.

**Files to create:**
- [`onboarding/services/staging_service.py`](../onboarding/services/staging_service.py:1)

**Complexity:** Medium. CSV/XLSX parsing, header validation, row-to-model mapping, batch management.

### Phase 5: Validation Engine (Step 13, 15–22)

**Goal:** Implement `ValidationService` with all template-specific and cross-template validation rules.

**Files to create:**
- [`onboarding/services/validation_service.py`](../onboarding/services/validation_service.py:1)

**Complexity:** High. 10 template-specific validators, 9 cross-template reconciliation checks, row-level error reporting. This is the most complex service.

### Phase 6: Reconciliation Dashboard (Steps 23–24)

**Goal:** Implement `ReconciliationService` for dashboard data and checklist.

**Files to create:**
- [`onboarding/services/reconciliation_service.py`](../onboarding/services/reconciliation_service.py:1)

**Complexity:** Medium. Aggregation queries over staging tables, balance sheet derivation, checklist execution.

### Phase 7: Migration Finalization (Steps 25–27)

**Goal:** Implement `MigrationFinalizationService` for the opening journal creation and lock.

**Files to create:**
- [`onboarding/services/migration_finalization_service.py`](../onboarding/services/migration_finalization_service.py:1)
- Add `FinalApprovalForm` to [`onboarding/forms.py`](../onboarding/forms.py:1)

**Complexity:** High. Atomic opening journal creation, ledger entry validation (unit FK for member accounts), voucher posting, immutability enforcement. Must follow the pattern from [`year_end.close_financial_year_with_carry_forward()`](../accounting/services/year_end.py:36).

### Phase 8: Views & Templates (All Steps)

**Goal:** Implement all views, URLs, and templates.

**Files to create:**
- [`onboarding/urls.py`](../onboarding/urls.py:1)
- [`onboarding/views.py`](../onboarding/views.py:1)
- [`onboarding/templates/onboarding/base_wizard.html`](../onboarding/templates/onboarding/base_wizard.html:1)
- [`onboarding/templates/onboarding/wizard_list.html`](../onboarding/templates/onboarding/wizard_list.html:1)
- [`onboarding/templates/onboarding/step_society_details.html`](../onboarding/templates/onboarding/step_society_details.html:1)
- [`onboarding/templates/onboarding/step_society_type.html`](../onboarding/templates/onboarding/step_society_type.html:1)
- [`onboarding/templates/onboarding/step_module_selection.html`](../onboarding/templates/onboarding/step_module_selection.html:1)
- [`onboarding/templates/onboarding/step_accounting_start_year.html`](../onboarding/templates/onboarding/step_accounting_start_year.html:1)
- [`onboarding/templates/onboarding/step_financial_year_creation.html`](../onboarding/templates/onboarding/step_financial_year_creation.html:1)
- [`onboarding/templates/onboarding/step_structure.html`](../onboarding/templates/onboarding/step_structure.html:1)
- [`onboarding/templates/onboarding/step_unit_configuration.html`](../onboarding/templates/onboarding/step_unit_configuration.html:1)
- [`onboarding/templates/onboarding/step_member_assignment.html`](../onboarding/templates/onboarding/step_member_assignment.html:1)
- [`onboarding/templates/onboarding/step_accounting_setup.html`](../onboarding/templates/onboarding/step_accounting_setup.html:1)
- [`onboarding/templates/onboarding/step_chart_of_accounts.html`](../onboarding/templates/onboarding/step_chart_of_accounts.html:1)
- [`onboarding/templates/onboarding/step_import_templates.html`](../onboarding/templates/onboarding/step_import_templates.html:1)
- [`onboarding/templates/onboarding/staging_area.html`](../onboarding/templates/onboarding/staging_area.html:1)
- [`onboarding/templates/onboarding/reconciliation_dashboard.html`](../onboarding/templates/onboarding/reconciliation_dashboard.html:1)
- [`onboarding/templates/onboarding/validation_checklist.html`](../onboarding/templates/onboarding/validation_checklist.html:1)
- [`onboarding/templates/onboarding/step_final_approval.html`](../onboarding/templates/onboarding/step_final_approval.html:1)
- [`onboarding/templates/onboarding/step_complete.html`](../onboarding/templates/onboarding/step_complete.html:1)
- [`onboarding/templates/onboarding/partials/step_progress_bar.html`](../onboarding/templates/onboarding/partials/step_progress_bar.html:1)
- [`onboarding/templates/onboarding/partials/staging_table.html`](../onboarding/templates/onboarding/partials/staging_table.html:1)
- [`onboarding/templates/onboarding/partials/validation_errors.html`](../onboarding/templates/onboarding/partials/validation_errors.html:1)
- [`onboarding/templates/onboarding/partials/reconciliation_summary.html`](../onboarding/templates/onboarding/partials/reconciliation_summary.html:1)

**Complexity:** Medium. Many files but each is straightforward. Follows existing template patterns.

### Phase 9: Tests

**Goal:** Comprehensive test coverage for all models, services, and views.

**Files to create:**
- [`onboarding/tests/__init__.py`](../onboarding/tests/__init__.py:1)
- [`onboarding/tests/test_models.py`](../onboarding/tests/test_models.py:1) — model validation, constraints, append-only enforcement
- [`onboarding/tests/test_wizard_service.py`](../onboarding/tests/test_wizard_service.py:1) — wizard lifecycle, step navigation, branching
- [`onboarding/tests/test_society_setup_service.py`](../onboarding/tests/test_society_setup_service.py:1) — society, structure, unit, member creation
- [`onboarding/tests/test_staging_service.py`](../onboarding/tests/test_staging_service.py:1) — upload, parse, store, delete, approve
- [`onboarding/tests/test_validation_service.py`](../onboarding/tests/test_validation_service.py:1) — all validation rules, cross-references
- [`onboarding/tests/test_reconciliation_service.py`](../onboarding/tests/test_reconciliation_service.py:1) — dashboard data, checklist
- [`onboarding/tests/test_migration_finalization_service.py`](../onboarding/tests/test_migration_finalization_service.py:1) — opening journal, lock, immutability
- [`onboarding/tests/test_views.py`](../onboarding/tests/test_views.py:1) — view access, step navigation, permissions

**Complexity:** Medium-High. Tests use [`SocietyTestCase`](../core/test_base.py:22) base class and [`SocietyFactory`](../core/test_factories.py:26)/[`UserFactory`](../core/test_factories.py:45) from [`core/test_factories.py`](../core/test_factories.py:1).

---

## 8. Integration Points

This section documents exactly how the wizard integrates with existing code. Each integration point includes the exact function signature, file path, and usage pattern.

### 8.1 `create_default_accounts_for_society()` — Standard Chart of Accounts

**Source:** [`accounting/services/standard_accounts.py:642`](../accounting/services/standard_accounts.py:642)

**Signature:**
```python
def create_default_accounts_for_society(society) -> dict
```

**Usage in wizard (Step 9):**
```python
from accounting.services.standard_accounts import ensure_standard_accounts

# ensure_standard_accounts() calls both ensure_standard_categories() and create_default_accounts_for_society()
# It is idempotent — safe to call multiple times.
ensure_standard_accounts(society)
```

**What it does:** Creates 5 root `AccountCategory` records + ~150 `Account` records from `NEW_ACCOUNT_TREE`. All accounts have `system_protected=True`.

**Idempotent:** Yes. Uses `get_or_create` / update-if-exists pattern.

### 8.2 `AccountMapping.ensure_for_society()` — Semantic Account Mapping

**Source:** [`accounting/models/model_AccountMapping.py:141`](../accounting/models/model_AccountMapping.py:141)

**Signature:**
```python
@classmethod
def ensure_for_society(cls, society) -> "AccountMapping"
```

**Usage in wizard (Step 9):**
```python
from accounting.models import AccountMapping

AccountMapping.ensure_for_society(society)
```

**What it does:** Creates a OneToOne `AccountMapping` record mapping semantic concepts (share_capital_account, bank_account, entrance_fee_account, etc.) to specific `Account` FKs using `AccountCodes` constants.

**Idempotent:** Yes. Uses `get_or_create`.

### 8.3 `FinancialYear` Creation & Auto-Period Creation

**Source:** [`accounting/models/model_FinancialYear.py:42`](../accounting/models/model_FinancialYear.py:42)

**Usage in wizard (Step 5):**
```python
from accounting.models import FinancialYear

fy, created = FinancialYear.objects.get_or_create(
    society=society,
    start_date=start_date,
    end_date=end_date,
    defaults={"name": f"FY {start_year}-{str(end_year)[-2:]}", "is_open": True},
)
# FinancialYear.save() auto-creates monthly AccountingPeriod records via _create_accounting_periods()
# Periods up to today are opened; future periods remain closed.
```

**Key behavior:** `FinancialYear.save()` calls `_create_accounting_periods()` which:
1. Creates 12 monthly `AccountingPeriod` records.
2. Opens all periods from FY start up to and including today's period.
3. Uses `bulk_create` for efficiency.

**Reference pattern:** [`seed_deepsagar._ensure_open_financial_year()`](../housing/management/commands/seed_deepsagar.py:323).

### 8.4 `Voucher` and `LedgerEntry` Creation for Opening Journal

**Source:** [`accounting/models/model_Voucher.py:14`](../accounting/models/model_Voucher.py:14), [`accounting/models/model_LedgerEntry.py:10`](../accounting/models/model_LedgerEntry.py:10)

**Usage in wizard (Step 26):**
```python
from accounting.models import Voucher, LedgerEntry
from decimal import Decimal

# 1. Create the opening voucher (draft — posted_at is null)
opening_voucher = Voucher.objects.create(
    society=society,
    voucher_type=Voucher.VoucherType.OPENING,
    voucher_date=fy.start_date,
    narration="Opening balances - Migration from previous system",
)

# 2. Create ledger entries for each account with a non-zero balance
for row in staging_trial_balance_rows:
    account = Account.objects.get(society=society, code=row.account_code)
    debit = row.debit if row.debit > 0 else Decimal("0.00")
    credit = row.credit if row.credit > 0 else Decimal("0.00")
    LedgerEntry.objects.create(
        voucher=opening_voucher,
        account=account,
        debit=debit,
        credit=credit,
        # unit=unit_fk  # REQUIRED for member-related accounts (code 1.5.x or 2.1.x)
    )

# 3. Post the voucher — assigns voucher_number, sets posted_at (immutable)
opening_voucher.post()
```

**Key constraints (enforced by model `clean()`):**
- Voucher must have balanced debit/credit totals.
- No same-account debit + credit.
- `LedgerEntry` for member-related accounts (code prefix `1.5.` or `2.1.`) **requires** a `unit` FK.
- `LedgerEntry` for income/expense/equity accounts **forbids** a `unit` FK.
- After `post()`, the voucher and its entries are immutable.

**Reference pattern:** [`year_end.close_financial_year_with_carry_forward()`](../accounting/services/year_end.py:88) (lines 88–105).

### 8.5 `AuditLog.log()` — Audit Trail

**Source:** [`auditlog/models.py:94`](../auditlog/models.py:94)

**Signature:**
```python
@classmethod
def log(
    cls, *,
    society,
    action,           # One of AuditLog.Action choices
    entity_type,      # Model name string
    entity_id,        # PK as string
    actor=None,       # User
    before_value=None,  # JSON dict
    after_value=None,   # JSON dict
    ip_address=None,
    device_info=None,
    request_id=None,
    session_id=None,
    user_agent=None,
    module=None,      # e.g. "onboarding"
    duration_ms=None,
    reason=None,
) -> AuditLog
```

**Usage in wizard (every step):**
```python
from auditlog.models import AuditLog

AuditLog.log(
    society=society,
    action=AuditLog.Action.CREATE,
    entity_type="Society",
    entity_id=str(society.pk),
    actor=request.user,
    after_value={"name": society.name, "registration_number": society.registration_number},
    module="onboarding",
)
```

**Key behavior:** Append-only. `save()` rejects updates. `delete()` raises `PermissionError`.

### 8.6 Tenant Middleware Context

**Source:** [`societies/middleware.py:5`](../societies/middleware.py:5), [`societies/managers.py:11`](../societies/managers.py:11)

**How it works:**
1. [`SocietyMiddleware`](../societies/middleware.py:5) calls [`get_selected_scope(request)`](../housing_accounting/selection.py:78) to get the current society from the session.
2. It sets `_current_tenant` contextvar to the society.
3. [`TenantManager.get_queryset()`](../societies/managers.py:52) reads the contextvar and auto-filters all queries by `society`.

**Usage in wizard:**
```python
from societies.managers import _current_tenant

# When the wizard creates a new society (Step 1), it must set the contextvar
# so that subsequent queries (e.g., Account.objects.filter(...)) are scoped:
_current_tenant.set(society)

# When the wizard needs to query across societies (e.g., list all wizards):
OnboardingWizard.objects.unscoped()  # bypasses tenant filter
```

**Session scope setting (after Step 1):**
```python
from housing_accounting.selection import _persist_selection

# Set the session to the new society so SocietyMiddleware picks it up on next request
_persist_selection(request, society=society, financial_year=None)
```

### 8.7 Society, Building, Wing, Floor, Unit, Member, UnitOwnership Creation

**Source models:**
- [`Society`](../societies/models/model_Society.py:4) — `app_label = "housing"`
- [`Structure`](../members/models/model_Structure.py:5) — `unique_together = (society, parent, name)`
- [`Unit`](../members/models/model_Unit.py:4) — `identifier`, `unit_type`, `area_sqft`
- [`Member`](../members/models/model_Member.py:7) — `unique_together = (society, unit, full_name, role)`
- [`UnitOwnership`](../members/models/model_UnitOwnership.py:6) — `unit`, `owner`, `ownership_role`
- [`UnitOccupancy`](../members/models/model_UnitOccupancy.py:6) — `unit`, `member`, `occupancy_type`

**Usage in wizard (Steps 1, 6, 7, 8):**

```python
# Step 1: Create Society + Membership
from societies.services import create_society
society = create_society(user=user, name=name, registration_number=reg, address=addr)

# Step 6: Create Structures (Building → Wing → Floor)
from members.models import Structure
building, _ = Structure.objects.get_or_create(
    society=society, parent=None,
    structure_type=Structure.StructureType.BUILDING,
    name="Tower A",
    defaults={"display_order": 1},
)
floor, _ = Structure.objects.get_or_create(
    society=society, parent=building,
    structure_type=Structure.StructureType.FLOOR,
    name="Ground Floor",
    defaults={"display_order": 1},
)

# Step 7: Create Units
from members.models import Unit
unit, _ = Unit.objects.get_or_create(
    structure=floor,
    identifier="A1-101",
    defaults={"unit_type": Unit.UnitType.FLAT, "area_sqft": Decimal("850.00")},
)

# Step 8: Create Members + sync lifecycle
from members.models import Member
from housing.services import sync_member_unit_lifecycle
member = Member.objects.create(
    society=society, unit=unit, full_name="John Doe",
    email="john@example.com", role=Member.MemberRole.OWNER,
)
sync_member_unit_lifecycle(member)  # Creates UnitOwnership + UnitOccupancy
```

**Reference pattern:** [`seed_deepsagar._ensure_structure_and_units()`](../housing/management/commands/seed_deepsagar.py:398), [`seed_deepsagar._ensure_owner_members()`](../housing/management/commands/seed_deepsagar.py:502).

---

## 9. File Manifest

Complete list of all files to be created, organized by directory.

### `onboarding/` (app root)

| File | Description |
|------|-------------|
| [`onboarding/__init__.py`](../onboarding/__init__.py:1) | Empty package marker |
| [`onboarding/apps.py`](../onboarding/apps.py:1) | `OnboardingConfig` AppConfig with `name="onboarding"` |
| [`onboarding/admin.py`](../onboarding/admin.py:1) | Admin registrations for wizard/staging models (debugging) |
| [`onboarding/constants.py`](../onboarding/constants.py:1) | Step definitions, template type enums, module registry, step-to-form/template mapping |
| [`onboarding/urls.py`](../onboarding/urls.py:1) | URLconf with `app_name = "onboarding"` and all wizard routes |
| [`onboarding/views.py`](../onboarding/views.py:1) | All wizard views (function-based + class-based) |
| [`onboarding/forms.py`](../onboarding/forms.py:1) | All wizard step forms (8 forms) |

### `onboarding/models/`

| File | Description |
|------|-------------|
| [`onboarding/models/__init__.py`](../onboarding/models/__init__.py:1) | Re-exports all 14 models |
| [`onboarding/models/model_OnboardingWizard.py`](../onboarding/models/model_OnboardingWizard.py:1) | Wizard session/state model with `TenantManager` |
| [`onboarding/models/model_WizardStepLog.py`](../onboarding/models/model_WizardStepLog.py:1) | Append-only step completion audit trail |
| [`onboarding/models/model_UploadBatch.py`](../onboarding/models/model_UploadBatch.py:1) | File upload tracking with template type and validation summary |
| [`onboarding/models/model_StagingChartOfAccounts.py`](../onboarding/models/model_StagingChartOfAccounts.py:1) | T1 staging: custom account additions |
| [`onboarding/models/model_StagingTrialBalance.py`](../onboarding/models/model_StagingTrialBalance.py:1) | T2 staging: opening trial balance |
| [`onboarding/models/model_StagingMemberOutstanding.py`](../onboarding/models/model_StagingMemberOutstanding.py:1) | T3 staging: per-flat member outstanding |
| [`onboarding/models/model_StagingVendorOutstanding.py`](../onboarding/models/model_StagingVendorOutstanding.py:1) | T4 staging: per-vendor outstanding |
| [`onboarding/models/model_StagingBankOpening.py`](../onboarding/models/model_StagingBankOpening.py:1) | T5 staging: per-bank opening balances |
| [`onboarding/models/model_StagingCashOpening.py`](../onboarding/models/model_StagingCashOpening.py:1) | T6 staging: cash opening balance |
| [`onboarding/models/model_StagingFixedAsset.py`](../onboarding/models/model_StagingFixedAsset.py:1) | T7 staging: fixed asset register |
| [`onboarding/models/model_StagingSecurityDeposit.py`](../onboarding/models/model_StagingSecurityDeposit.py:1) | T8 staging: security deposit liabilities |
| [`onboarding/models/model_StagingLoan.py`](../onboarding/models/model_StagingLoan.py:1) | T9 staging: loan outstanding balances |
| [`onboarding/models/model_StagingFund.py`](../onboarding/models/model_StagingFund.py:1) | T10 staging: restricted fund balances |
| [`onboarding/models/model_MigrationAuditLog.py`](../onboarding/models/model_MigrationAuditLog.py:1) | Append-only migration-specific audit log |

### `onboarding/services/`

| File | Description |
|------|-------------|
| [`onboarding/services/__init__.py`](../onboarding/services/__init__.py:1) | Re-exports all 7 services |
| [`onboarding/services/wizard_service.py`](../onboarding/services/wizard_service.py:1) | Wizard lifecycle: create, advance, resume, abandon, get_state |
| [`onboarding/services/society_setup_service.py`](../onboarding/services/society_setup_service.py:1) | Society, structure, unit, member creation (wraps existing services) |
| [`onboarding/services/module_config_service.py`](../onboarding/services/module_config_service.py:1) | Module enablement based on Step 3 selection |
| [`onboarding/services/financial_year_service.py`](../onboarding/services/financial_year_service.py:1) | FinancialYear + period creation with FY pattern derivation |
| [`onboarding/services/staging_service.py`](../onboarding/services/staging_service.py:1) | Staging area: upload, parse, store, delete, approve |
| [`onboarding/services/validation_service.py`](../onboarding/services/validation_service.py:1) | Validation engine: all template-specific + cross-template rules |
| [`onboarding/services/reconciliation_service.py`](../onboarding/services/reconciliation_service.py:1) | Reconciliation dashboard data + 9-check checklist |
| [`onboarding/services/migration_finalization_service.py`](../onboarding/services/migration_finalization_service.py:1) | Opening journal creation, ledger entries, lock migration |

### `onboarding/migrations/`

| File | Description |
|------|-------------|
| [`onboarding/migrations/__init__.py`](../onboarding/migrations/__init__.py:1) | Empty package marker |
| [`onboarding/migrations/0001_initial.py`](../onboarding/migrations/0001_initial.py:1) | Initial migration: all 14 models (generated via `makemigrations onboarding`) |

### `onboarding/templates/onboarding/`

| File | Description |
|------|-------------|
| [`onboarding/templates/onboarding/base_wizard.html`](../onboarding/templates/onboarding/base_wizard.html:1) | Base wizard template with step progress bar and content block |
| [`onboarding/templates/onboarding/wizard_list.html`](../onboarding/templates/onboarding/wizard_list.html:1) | List of in-progress wizards for the user |
| [`onboarding/templates/onboarding/step_society_details.html`](../onboarding/templates/onboarding/step_society_details.html:1) | Step 1: society details form |
| [`onboarding/templates/onboarding/step_society_type.html`](../onboarding/templates/onboarding/step_society_type.html:1) | Step 2: NEW vs EXISTING selection |
| [`onboarding/templates/onboarding/step_module_selection.html`](../onboarding/templates/onboarding/step_module_selection.html:1) | Step 3: module selection checkboxes |
| [`onboarding/templates/onboarding/step_accounting_start_year.html`](../onboarding/templates/onboarding/step_accounting_start_year.html:1) | Step 4: FY selection dropdown |
| [`onboarding/templates/onboarding/step_financial_year_creation.html`](../onboarding/templates/onboarding/step_financial_year_creation.html:1) | Step 5: FY creation confirmation |
| [`onboarding/templates/onboarding/step_structure.html`](../onboarding/templates/onboarding/step_structure.html:1) | Step 6: structure tree builder |
| [`onboarding/templates/onboarding/step_unit_configuration.html`](../onboarding/templates/onboarding/step_unit_configuration.html:1) | Step 7: unit configuration grid |
| [`onboarding/templates/onboarding/step_member_assignment.html`](../onboarding/templates/onboarding/step_member_assignment.html:1) | Step 8: member assignment form |
| [`onboarding/templates/onboarding/step_accounting_setup.html`](../onboarding/templates/onboarding/step_accounting_setup.html:1) | Step 9: accounting setup (branch point) |
| [`onboarding/templates/onboarding/step_chart_of_accounts.html`](../onboarding/templates/onboarding/step_chart_of_accounts.html:1) | Step 10: COA display + custom account add |
| [`onboarding/templates/onboarding/step_import_templates.html`](../onboarding/templates/onboarding/step_import_templates.html:1) | Step 11: template download links |
| [`onboarding/templates/onboarding/staging_area.html`](../onboarding/templates/onboarding/staging_area.html:1) | Steps 12–22: staging table view with upload/delete/approve |
| [`onboarding/templates/onboarding/reconciliation_dashboard.html`](../onboarding/templates/onboarding/reconciliation_dashboard.html:1) | Step 23: reconciliation dashboard with 6 sections |
| [`onboarding/templates/onboarding/validation_checklist.html`](../onboarding/templates/onboarding/validation_checklist.html:1) | Step 24: 9-check validation checklist |
| [`onboarding/templates/onboarding/step_final_approval.html`](../onboarding/templates/onboarding/step_final_approval.html:1) | Step 25: final approval with warning + confirm |
| [`onboarding/templates/onboarding/step_complete.html`](../onboarding/templates/onboarding/step_complete.html:1) | Step 28: success summary page |
| [`onboarding/templates/onboarding/partials/step_progress_bar.html`](../onboarding/templates/onboarding/partials/step_progress_bar.html:1) | Partial: visual step progress indicator |
| [`onboarding/templates/onboarding/partials/staging_table.html`](../onboarding/templates/onboarding/partials/staging_table.html:1) | Partial: staging data table with validation badges |
| [`onboarding/templates/onboarding/partials/validation_errors.html`](../onboarding/templates/onboarding/partials/validation_errors.html:1) | Partial: row-level error display (row/column/reason/fix) |
| [`onboarding/templates/onboarding/partials/reconciliation_summary.html`](../onboarding/templates/onboarding/partials/reconciliation_summary.html:1) | Partial: reconciliation section summary card |

### `onboarding/tests/`

| File | Description |
|------|-------------|
| [`onboarding/tests/__init__.py`](../onboarding/tests/__init__.py:1) | Empty package marker |
| [`onboarding/tests/test_models.py`](../onboarding/tests/test_models.py:1) | Model validation, constraints, append-only enforcement |
| [`onboarding/tests/test_wizard_service.py`](../onboarding/tests/test_wizard_service.py:1) | Wizard lifecycle, step navigation, branching at Step 9 |
| [`onboarding/tests/test_society_setup_service.py`](../onboarding/tests/test_society_setup_service.py:1) | Society, structure, unit, member creation |
| [`onboarding/tests/test_staging_service.py`](../onboarding/tests/test_staging_service.py:1) | Upload, parse, store, delete, approve |
| [`onboarding/tests/test_validation_service.py`](../onboarding/tests/test_validation_service.py:1) | All validation rules, cross-references |
| [`onboarding/tests/test_reconciliation_service.py`](../onboarding/tests/test_reconciliation_service.py:1) | Dashboard data, checklist execution |
| [`onboarding/tests/test_migration_finalization_service.py`](../onboarding/tests/test_migration_finalization_service.py:1) | Opening journal, lock, immutability |
| [`onboarding/tests/test_views.py`](../onboarding/tests/test_views.py:1) | View access, step navigation, permissions |

### Files to Modify (existing)

| File | Modification |
|------|-------------|
| [`config/settings/base.py`](../config/settings/base.py:147) | Add `"onboarding"` to `LOCAL_APPS` list (after `"accounting"`) |
| [`config/urls.py`](../config/urls.py:1) | Add `path("onboarding/", include("onboarding.urls"))` |

### Total File Count

| Category | Count |
|----------|-------|
| New Python files (app root) | 7 |
| New model files | 15 (14 models + `__init__`) |
| New service files | 8 (7 services + `__init__`) |
| New migration files | 2 (`__init__` + `0001_initial`) |
| New template files | 22 (18 step/base + 4 partials) |
| New test files | 9 (8 tests + `__init__`) |
| Modified existing files | 2 |
| **Total** | **65 files** |

---

*This implementation plan is the authoritative guide for building the Housynk Society Creation & Accounting Migration Wizard. All service interfaces, model designs, and integration points documented here must be respected during implementation. Refer to [`WIZARD_ARCHITECTURE_ANALYSIS.md`](WIZARD_ARCHITECTURE_ANALYSIS.md:1) for the full codebase analysis and [`MIGRATION_WIZARD_SPEC.md`](MIGRATION_WIZARD_SPEC.md:1) for the formal specification.*
