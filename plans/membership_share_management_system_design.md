# Membership & Share Management System - System Design Document

## Table of Contents
1. [Overview](#overview)
2. [Core Principles](#core-principles)
3. [System Architecture](#system-architecture)
4. [Data Models](#data-models)
5. [Module Specifications](#module-specifications)
6. [Integration Points](#integration-points)
7. [Event-Driven Workflow](#event-driven-workflow)
8. [Implementation Roadmap](#implementation-roadmap)
9. [Appendices](#appendices)

---

## Overview

### Objective
Build a fully automated, audit-compliant membership system for Co-operative Housing Societies that:
- Manages members, nominees, and shares
- Automates share allotment, transfer, and transmission
- Generates accounting vouchers automatically
- Enforces legal and operational constraints
- Supports per-society configuration (controlled, not free-form)

### Scope
This system extends the existing housing accounting platform (`/home/tushar/Documents/Projects/housing_accounting`) by adding dedicated modules for membership lifecycle management and share capital tracking.

### Key Stakeholders
- **Society Administrators**: Manage member records, approve transfers
- **Accountants**: Monitor share capital accounts, verify vouchers
- **Members**: View share certificates, nominee details
- **Regulatory Bodies**: Audit trail compliance

---

## Core Principles

These principles MUST NOT be broken during implementation:

### 1. Ownership ≠ Accounting
- **Share Ledger** tracks share ownership (who owns how many shares)
- **Accounting Ledger** tracks money movements (share capital accounts)
- These are separate concerns with distinct audit trails

### 2. Share Ledger = Ownership
- Single source of truth for share ownership
- Append-only records (never update/delete, only add new transactions)
- Balance calculated from running total

### 3. Accounting Ledger = Money
- All monetary transactions flow through proper double-entry bookkeeping
- Share-related money movements generate proper vouchers automatically

### 4. Append-Only System
- Critical data (share transactions, vouchers) never overwritten
- Corrections done via reversing entries or new transactions
- Full audit trail maintained

### 5. Event-Driven Architecture
- Actions trigger events
- Events trigger automated workflows
- Loose coupling between modules

### 6. Controlled Configurability
- Only parameters configurable (share value, fees, limits)
- Core logic is fixed and non-negotiable
- Society-specific settings via `SocietyConfig`

---

## System Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Django Web Framework                     │
├─────────────────────────────────────────────────────────────┤
│                     Presentation Layer                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ Members  │  │ Shares   │  │ Nominees │  │ Accounts │  │
│  │ UI       │  │ UI       │  │ UI       │  │ UI       │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
├─────────────────────────────────────────────────────────────┤
│                      Business Layer                         │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Event Bus / Signal Handler             │   │
│  └─────────────────────────────────────────────────────┘   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ Membership│  │  Share   │  │  Nominee │  │ Accounting│  │
│  │ Services  │  │ Services │  │ Services │  │ Services │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Rule Engine (Controlled)                │   │
│  └─────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────┤
│                       Data Layer                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │  Members │  │  Shares  │  │ Nominees │  │ Accounts │  │
│  │  Models  │  │  Models  │  │  Models  │  │  Models  │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Society Config (Settings)               │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### Module Dependencies

```
Membership Module ──────> Accounting Module
       │                         │
       ▼                         ▼
Nominee Module ────────> Share Management
       │                         │
       └─────────────────────────┘
                 │
                 ▼
         Rule Engine (Events)
                 │
                 ▼
         Society Config
```

---

## Data Models

### 1. Society Model (Extension)

**File:** [`societies/models/model_Society.py`](societies/models/model_Society.py)

The existing `Society` model will be extended with additional fields for share management:

```python
# societies/models/model_Society.py (extension)

class Society(models.Model):
    # ... existing fields ...
    
    # Share management fields (new)
    share_capital_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="society_share_capital",
        null=True,
        blank=True,
        help_text="Main share capital account for the society"
    )
    
    class Meta:
        app_label = "housing"
```

### 2. Member Model (Extension)

**File:** [`members/models/model_Member.py`](members/models/model_Member.py)

The existing `Member` model will be extended:

```python
# members/models/model_Member.py (extension)

class Member(models.Model):
    # ... existing fields ...
    
    # Additional fields for share management
    member_number = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Unique member number assigned by society"
    )
    kyc_verified = models.BooleanField(
        default=False,
        help_text="KYC documents verified status"
    )
    kyc_verified_at = models.DateTimeField(null=True, blank=True)
    kyc_verified_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="kyc_verified_members"
    )
    
    # Share-related fields
    share_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Current share balance (denormalized from ShareLedger)"
    )
    
    class Meta:
        # ... existing meta ...
        # Add unique constraint for member_number per society
        constraints = [
            models.UniqueConstraint(
                fields=("society", "member_number"),
                name="unique_member_number_per_society",
                condition=models.Q(member_number__isnull=False)
            )
        ]
```

### 3. Nominee Model (New)

**File:** `members/models/model_Nominee.py`

```python
# members/models/model_Nominee.py

from django.db import models
from django.core.exceptions import ValidationError
from members.models.model_Member import Member


class Nominee(models.Model):
    """
    Nominee records for members.
    Implements versioning via is_active and deactivated_at.
    """
    
    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name="nominees"
    )
    name = models.CharField(max_length=255)
    relationship = models.CharField(
        max_length=50,
        help_text="Relationship to member (e.g., Spouse, Child, Parent)"
    )
    percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="Percentage of shares nominated (0-100)"
    )
    priority_order = models.PositiveIntegerField(
        default=1,
        help_text="Priority order for multiple nominees (1 = highest)"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Set to False when nominee is replaced (don't delete)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    deactivated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when nominee was replaced/deactivated"
    )
    deactivated_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deactivated_nominees"
    )
    
    class Meta:
        app_label = "housing"
        ordering = ["priority_order", "created_at"]
        constraints = [
            # Total percentage of active nominees cannot exceed 100
            models.CheckConstraint(
                check=models.Q(percentage__gte=0) & models.Q(percentage__lte=100),
                name="nominee_percentage_range"
            )
        ]
    
    def clean(self):
        # Validate total percentage of active nominees for the member
        if self.is_active and self.percentage > 0:
            from django.db.models import Sum
            total = Nominee.objects.filter(
                member=self.member,
                is_active=True
            ).exclude(pk=self.pk).aggregate(
                total=models.Sum('percentage')
            )['total'] or 0
            
            if total + self.percentage > 100:
                raise ValidationError(
                    f"Total nominee percentage cannot exceed 100%. "
                    f"Current total: {total}%, adding: {self.percentage}%"
                )
    
    def deactivate(self, user=None):
        """Deactivate nominee (soft delete)."""
        self.is_active = False
        self.deactivated_at = models.functions.Now()
        self.deactivated_by = user
        self.save(update_fields=['is_active', 'deactivated_at', 'deactivated_by'])
    
    def __str__(self):
        return f"{self.name} ({self.relationship}) - {self.percentage}%"
```

### 4. ShareLedger Model (New - Critical)

**File:** `members/models/model_ShareLedger.py`

```python
# members/models/model_ShareLedger.py

from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from societies.models import Society
from members.models.model_Member import Member


class ShareLedger(models.Model):
    """
    Append-only share ledger tracking share ownership.
    NEVER update or delete records - always create new transactions.
    
    This is the SINGLE SOURCE OF TRUTH for share ownership.
    """
    
    class TransactionType(models.TextChoices):
        ALLOTMENT = "ALLOTMENT", "Share Allotment"
        TRANSFER_IN = "TRANSFER_IN", "Transfer In (from another member)"
        TRANSFER_OUT = "TRANSFER_OUT", "Transfer Out (to another member)"
        TRANSMISSION = "TRANSMISSION", "Transmission (nominee inheritance)"
        FORFEITURE = "FORFEITURE", "Share Forfeiture"
        BUYBACK = "BUYBACK", "Share Buyback by Society"
        ADJUSTMENT = "ADJUSTMENT", "Balance Adjustment (with reason)"
    
    society = models.ForeignKey(
        Society,
        on_delete=models.CASCADE,
        related_name="share_transactions"
    )
    member = models.ForeignKey(
        Member,
        on_delete=models.PROTECT,
        related_name="share_transactions"
    )
    
    # Share movement
    shares_in = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Shares coming in"
    )
    shares_out = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Shares going out"
    )
    balance_after = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Running balance after this transaction"
    )
    
    # Transaction metadata
    transaction_type = models.CharField(
        max_length=20,
        choices=TransactionType.choices
    )
    reference_id = models.CharField(
        max_length=100,
        blank=True,
        help_text="External reference (transfer ID, certificate number, etc.)"
    )
    transaction_date = models.DateField(default=timezone.localdate)
    reason = models.TextField(
        blank=True,
        help_text="Reason for adjustment/forfeiture/transmission"
    )
    
    # Audit fields
    created_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="share_transactions_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Link to generated voucher (for money movements)
    voucher = models.ForeignKey(
        "accounting.Voucher",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="share_transactions",
        help_text="Voucher generated for this share transaction (if any)"
    )
    
    class Meta:
        app_label = "housing"
        ordering = ["-transaction_date", "-created_at"]
        indexes = [
            models.Index(fields=["society", "member", "transaction_date"]),
            models.Index(fields=["society", "transaction_date"]),
            models.Index(fields=["voucher"]),
        ]
    
    def clean(self):
        # Validate shares_in and shares_out are not both set
        if self.shares_in > 0 and self.shares_out > 0:
            raise ValidationError("Cannot have both shares_in and shares_out in same transaction")
        
        # Validate at least one is set
        if self.shares_in == 0 and self.shares_out == 0:
            raise ValidationError("Must specify either shares_in or shares_out")
        
        # Validate balance_after is non-negative
        if self.balance_after < 0:
            raise ValidationError("Share balance cannot be negative")
    
    def save(self, *args, **kwargs):
        # Calculate balance_after if not set (for new records)
        if self.pk is None and not self.balance_after:
            previous_balance = ShareLedger.objects.filter(
                society=self.society,
                member=self.member,
                transaction_date__lte=self.transaction_date
            ).exclude(pk=self.pk).order_by('-transaction_date', '-created_at').first()
            
            if previous_balance:
                self.balance_after = previous_balance.balance_after + self.shares_in - self.shares_out
            else:
                self.balance_after = self.shares_in - self.shares_out
        
        super().save(*args, **kwargs)
        
        # Update member's denormalized share_balance
        self.member.share_balance = self.balance_after
        self.member.save(update_fields=['share_balance'])
    
    def __str__(self):
        return f"{self.member} - {self.transaction_type}: +{self.shares_in}/-{self.shares_out} (Bal: {self.balance_after})"
```

### 5. ShareCertificate Model (New)

**File:** `members/models/model_ShareCertificate.py`

```python
# members/models/model_ShareCertificate.py

from django.db import models
from django.core.exceptions import ValidationError
from members.models.model_Member import Member


class ShareCertificate(models.Model):
    """
    Share certificate tracking for members.
    Certificates are issued when shares are allotted.
    """
    
    class Status(models.TextChoices):
        ISSUED = "ISSUED", "Issued"
        TRANSFERRED = "TRANSFERRED", "Transferred"
        CANCELLED = "CANCELLED", "Cancelled"
        LOST = "LOST", "Lost (Replacement Issued)"
    
    member = models.ForeignKey(
        Member,
        on_delete=models.PROTECT,
        related_name="share_certificates"
    )
    certificate_no = models.CharField(
        max_length=50,
        unique=True,
        help_text="Unique certificate number"
    )
    share_count = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Number of shares covered by this certificate"
    )
    issued_date = models.DateField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ISSUED
    )
    
    # For transfers
    transferred_to = models.ForeignKey(
        Member,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_certificates"
    )
    transferred_date = models.DateField(null=True, blank=True)
    
    # Audit fields
    issued_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="certificates_issued"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        app_label = "housing"
        ordering = ["-issued_date", "certificate_no"]
        indexes = [
            models.Index(fields=["member", "status"]),
            models.Index(fields=["certificate_no"]),
        ]
    
    def clean(self):
        if self.status == self.Status.TRANSFERRED and not self.transferred_to:
            raise ValidationError("Transferred certificate must have transferred_to member")
    
    def __str__(self):
        return f"Cert #{self.certificate_no} - {self.member} ({self.share_count} shares)"
```

### 6. SocietyConfig Model (New)

**File:** `members/models/model_SocietyConfig.py`

```python
# members/models/model_SocietyConfig.py

from django.db import models
from django.core.exceptions import ValidationError
from societies.models import Society


class SocietyConfig(models.Model):
    """
    Per-society configuration for share management.
    Only parameters are configurable - core logic is fixed.
    """
    
    society = models.OneToOneField(
        Society,
        on_delete=models.CASCADE,
        related_name="share_config"
    )
    
    # Share value configuration
    share_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Face value of each share"
    )
    default_share_count = models.PositiveIntegerField(
        default=1,
        help_text="Default number of shares allotted to new members"
    )
    min_share_count = models.PositiveIntegerField(
        default=1,
        help_text="Minimum shares a member must hold"
    )
    max_share_count = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Maximum shares a member can hold (null = unlimited)"
    )
    
    # Fee configuration
    entrance_fee = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="One-time entrance fee for new members"
    )
    transfer_fee = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Fee for share transfer"
    )
    transmission_fee = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Fee for share transmission to nominee"
    )
    premium_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Premium amount (if shares are issued above face value)"
    )
    
    # Nominee configuration
    allow_multiple_nominees = models.BooleanField(
        default=False,
        help_text="Allow members to nominate multiple persons"
    )
    max_nominee_count = models.PositiveIntegerField(
        default=1,
        help_text="Maximum number of nominees allowed"
    )
    
    # Workflow configuration
    require_approval_for_transfer = models.BooleanField(
        default=True,
        help_text="Require admin approval for share transfers"
    )
    require_approval_for_allotment = models.BooleanField(
        default=True,
        help_text="Require admin approval for new share allotments"
    )
    
    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="society_configs_updated"
    )
    
    class Meta:
        app_label = "housing"
    
    def clean(self):
        if self.max_share_count and self.max_share_count < self.min_share_count:
            raise ValidationError("max_share_count cannot be less than min_share_count")
        
        if self.allow_multiple_nominees and self.max_nominee_count < 2:
            raise ValidationError("max_nominee_count must be >= 2 when multiple nominees allowed")
    
    def __str__(self):
        return f"Config for {self.society.name}"
```

### 7. AccountMapping Model (New)

**File:** `members/models/model_AccountMapping.py`

```python
# members/models/model_AccountMapping.py

from django.db import models
from societies.models import Society


class AccountMapping(models.Model):
    """
    Maps society-specific accounts for share-related transactions.
    Ensures all share-related money movements go to correct accounts.
    """
    
    society = models.OneToOneField(
        Society,
        on_delete=models.CASCADE,
        related_name="share_account_mapping"
    )
    
    # Share capital accounts
    share_capital_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="society_share_capital_mapping",
        help_text="Main share capital account (Liability)"
    )
    share_premium_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="society_share_premium_mapping",
        null=True,
        blank=True,
        help_text="Share premium account (if shares issued above face value)"
    )
    
    # Fee income accounts
    entrance_fee_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="society_entrance_fee_mapping",
        help_text="Income account for entrance fees"
    )
    transfer_fee_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="society_transfer_fee_mapping",
        help_text="Income account for transfer fees"
    )
    transmission_fee_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="society_transmission_fee_mapping",
        null=True,
        blank=True,
        help_text="Income account for transmission fees"
    )
    
    # Bank account for receiving payments
    bank_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="society_share_bank_mapping",
        help_text="Bank account for share-related receipts"
    )
    
    # Receivable account (for member dues)
    member_receivable_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="society_share_receivable_mapping",
        help_text="Member receivable account for share dues"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        app_label = "housing"
    
    def clean(self):
        # Ensure all accounts belong to the same society
        accounts = [
            self.share_capital_account,
            self.entrance_fee_account,
            self.transfer_fee_account,
            self.bank_account,
            self.member_receivable_account,
        ]
        if self.share_premium_account:
            accounts.append(self.share_premium_account)
        if self.transmission_fee_account:
            accounts.append(self.transmission_fee_account)
        
        for account in accounts:
            if account.society_id != self.society_id:
                raise ValidationError(f"Account {account} must belong to {self.society}")
    
    def __str__(self):
        return f"Account Mapping for {self.society.name}"
```

---

## Module Specifications

### 1. Membership Module

**Location:** `members/` app (extend existing)

#### Responsibilities
- Member lifecycle management (join, exit, status changes)
- KYC/identity document tracking
- Member status tracking (active, inactive, suspended)
- Member number generation

#### Key Services

**File:** `members/services/membership.py`

```python
# members/services/membership.py

from django.db import transaction
from django.utils import timezone
from members.models.model_Member import Member
from .share_ledger import ShareLedgerService
from .voucher_generator import VoucherGenerator


class MembershipService:
    """Handles member lifecycle operations."""
    
    @staticmethod
    @transaction.atomic
    def register_new_member(society, member_data, user):
        """
        Register a new member with default share allotment.
        
        Flow:
        1. Create Member record
        2. Generate member number
        3. Allot default shares (via ShareLedgerService)
        4. Generate accounting voucher (via VoucherGenerator)
        5. Trigger 'member.registered' event
        """
        # Implementation here
        pass
    
    @staticmethod
    @transaction.atomic
    def deactivate_member(member, reason, user):
        """
        Deactivate a member (soft delete).
        
        Flow:
        1. Check for pending share balance
        2. Transfer/shares out (via ShareLedgerService)
        3. Update member status
        4. Trigger 'member.deactivated' event
        """
        # Implementation here
        pass
    
    @staticmethod
    def verify_kyc(member, user):
        """Mark member KYC as verified."""
        member.kyc_verified = True
        member.kyc_verified_at = timezone.now()
        member.kyc_verified_by = user
        member.save(update_fields=['kyc_verified', 'kyc_verified_at', 'kyc_verified_by'])
```

### 2. Nominee Module

**Location:** `members/` app

#### Responsibilities
- Nominee record creation and updates
- Versioning (deactivate old, create new - never update)
- Priority ordering
- Percentage validation (total ≤ 100%)

#### Key Services

**File:** `members/services/nominee.py`

```python
# members/services/nominee.py

from django.db import transaction
from members.models.model_Nominee import Nominee


class NomineeService:
    """Handles nominee operations with versioning."""
    
    @staticmethod
    @transaction.atomic
    def add_nominee(member, nominee_data, user):
        """
        Add a new nominee.
        Validates total percentage doesn't exceed 100%.
        """
        # Implementation here
        pass
    
    @staticmethod
    @transaction.atomic
    def update_nominee(nominee, new_data, user):
        """
        Update nominee by deactivating old and creating new.
        NEVER updates existing nominee record.
        """
        # Deactivate old
        nominee.deactivate(user)
        
        # Create new with updated data
        new_nominee = Nominee.objects.create(
            member=member,
            name=new_data['name'],
            relationship=new_data['relationship'],
            percentage=new_data['percentage'],
            priority_order=new_data.get('priority_order', nominee.priority_order),
            is_active=True
        )
        
        return new_nominee
    
    @staticmethod
    def get_active_nominees(member):
        """Get all active nominees for a member."""
        return Nominee.objects.filter(
            member=member,
            is_active=True
        ).order_by('priority_order')
```

### 3. Share Management Module

**Location:** `members/` app

#### Responsibilities
- Share Ledger management (append-only)
- Share Certificate issuance
- Share allotment, transfer, transmission
- Balance calculation and validation

#### Key Services

**File:** `members/services/share_ledger.py`

```python
# members/services/share_ledger.py

from django.db import transaction
from django.utils import timezone
from members.models.model_ShareLedger import ShareLedger
from members.models.model_ShareCertificate import ShareCertificate
from members.models.model_SocietyConfig import SocietyConfig


class ShareLedgerService:
    """Manages all share ledger operations (append-only)."""
    
    @staticmethod
    @transaction.atomic
    def allot_shares(member, share_count, user, voucher=None):
        """
        Allot new shares to a member.
        
        Creates ShareLedger entry with:
        - shares_in = share_count
        - transaction_type = ALLOTMENT
        - balance_after = calculated
        
        Returns: ShareLedger entry
        """
        society = member.society
        config = SocietyConfig.objects.get(society=society)
        
        # Validate max shares
        if config.max_share_count and member.share_balance + share_count > config.max_share_count:
            raise ValueError(f"Cannot exceed max share count of {config.max_share_count}")
        
        # Get current balance
        last_entry = ShareLedger.objects.filter(
            society=society,
            member=member
        ).order_by('-transaction_date', '-created_at').first()
        
        current_balance = last_entry.balance_after if last_entry else 0
        new_balance = current_balance + share_count
        
        # Create ledger entry
        ledger_entry = ShareLedger.objects.create(
            society=society,
            member=member,
            shares_in=share_count,
            shares_out=0,
            balance_after=new_balance,
            transaction_type=ShareLedger.TransactionType.ALLOTMENT,
            transaction_date=timezone.localdate(),
            created_by=user,
            voucher=voucher
        )
        
        return ledger_entry
    
    @staticmethod
    @transaction.atomic
    def transfer_shares(from_member, to_member, share_count, user, reason=""):
        """
        Transfer shares between members.
        
        Creates two ShareLedger entries:
        1. TRANSFER_OUT for from_member
        2. TRANSFER_IN for to_member
        
        Also handles certificate transfer if applicable.
        """
        # Validate from_member has enough shares
        if from_member.share_balance < share_count:
            raise ValueError(f"Insufficient shares. Available: {from_member.share_balance}")
        
        # Create transfer out entry
        last_from = ShareLedger.objects.filter(
            society=from_member.society,
            member=from_member
        ).order_by('-transaction_date', '-created_at').first()
        balance_from = last_from.balance_after if last_from else 0
        
        transfer_out = ShareLedger.objects.create(
            society=from_member.society,
            member=from_member,
            shares_in=0,
            shares_out=share_count,
            balance_after=balance_from - share_count,
            transaction_type=ShareLedger.TransactionType.TRANSFER_OUT,
            transaction_date=timezone.localdate(),
            reason=reason,
            created_by=user
        )
        
        # Create transfer in entry
        last_to = ShareLedger.objects.filter(
            society=to_member.society,
            member=to_member
        ).order_by('-transaction_date', '-created_at').first()
        balance_to = last_to.balance_after if last_to else 0
        
        transfer_in = ShareLedger.objects.create(
            society=to_member.society,
            member=to_member,
            shares_in=share_count,
            shares_out=0,
            balance_after=balance_to + share_count,
            transaction_type=ShareLedger.TransactionType.TRANSFER_IN,
            transaction_date=timezone.localdate(),
            reason=reason,
            created_by=user
        )
        
        return transfer_out, transfer_in
    
    @staticmethod
    @transaction.atomic
    def transmit_shares(deceased_member, nominee, user, reason=""):
        """
        Transmit shares to nominee upon member death.
        
        Creates TRANSMISSION entry and updates nominee to member.
        """
        # Get total shares of deceased
        total_shares = deceased_member.share_balance
        
        if total_shares <= 0:
            raise ValueError("Deceased member has no shares to transmit")
        
        # Create transmission entry for deceased
        last_entry = ShareLedger.objects.filter(
            society=deceased_member.society,
            member=deceased_member
        ).order_by('-transaction_date', '-created_at').first()
        balance = last_entry.balance_after if last_entry else 0
        
        transmission = ShareLedger.objects.create(
            society=deceased_member.society,
            member=deceased_member,
            shares_in=0,
            shares_out=total_shares,
            balance_after=0,  # All shares transmitted
            transaction_type=ShareLedger.TransactionType.TRANSMISSION,
            transaction_date=timezone.localdate(),
            reason=reason,
            created_by=user
        )
        
        # Allot shares to nominee (if nominee is also a member)
        # This would need nominee to be registered as member first
        
        return transmission
```

### 4. Accounting Module (Integration)

**Location:** `accounting/` app (extend existing)

#### Responsibilities
- Generate vouchers for share transactions
- Maintain share capital accounts
- Ensure double-entry bookkeeping

#### Key Services

**File:** `members/services/voucher_generator.py`

```python
# members/services/voucher_generator.py

from django.db import transaction
from accounting.models.model_Voucher import Voucher
from accounting.models.model_LedgerEntry import LedgerEntry
from members.models.model_AccountMapping import AccountMapping


class VoucherGenerator:
    """Generates accounting vouchers for share transactions."""
    
    @staticmethod
    @transaction.atomic
    def generate_share_allotment_voucher(member, share_count, user):
        """
        Generate voucher for share allotment.
        
        Debit: Bank Account (share value * share_count)
        Credit: Share Capital Account (share value * share_count)
        If premium: Credit Share Premium Account
        """
        society = member.society
        config = society.share_config
        mapping = society.share_account_mapping
        
        total_amount = config.share_value * share_count
        
        # Create voucher
        voucher = Voucher.objects.create(
            society=society,
            voucher_type=Voucher.VoucherType.RECEIPT,
            voucher_date=timezone.localdate(),
            narration=f"Share allotment for {member.full_name} - {share_count} shares",
            created_by=user
        )
        
        # Debit: Bank Account
        LedgerEntry.objects.create(
            voucher=voucher,
            account=mapping.bank_account,
            side=LedgerEntry.Side.DEBIT,
            amount=total_amount
        )
        
        # Credit: Share Capital Account
        LedgerEntry.objects.create(
            voucher=voucher,
            account=mapping.share_capital_account,
            side=LedgerEntry.Side.CREDIT,
            amount=total_amount
        )
        
        return voucher
    
    @staticmethod
    @transaction.atomic
    def generate_transfer_fee_voucher(member, user):
        """
        Generate voucher for share transfer fee.
        
        Debit: Member Receivable or Bank
        Credit: Transfer Fee Income Account
        """
        society = member.society
        config = society.share_config
        mapping = society.share_account_mapping
        
        if config.transfer_fee <= 0:
            return None
        
        voucher = Voucher.objects.create(
            society=society,
            voucher_type=Voucher.VoucherType.RECEIPT,
            voucher_date=timezone.localdate(),
            narration=f"Share transfer fee from {member.full_name}",
            created_by=user
        )
        
        # Debit: Member Receivable
        LedgerEntry.objects.create(
            voucher=voucher,
            account=mapping.member_receivable_account,
            side=LedgerEntry.Side.DEBIT,
            amount=config.transfer_fee
        )
        
        # Credit: Transfer Fee Income
        LedgerEntry.objects.create(
            voucher=voucher,
            account=mapping.transfer_fee_account,
            side=LedgerEntry.Side.CREDIT,
            amount=config.transfer_fee
        )
        
        return voucher
```

### 5. Rule Engine (Controlled Automation)

**Location:** `members/services/rule_engine.py`

#### Responsibilities
- Map events to actions
- Enforce business rules
- Controlled configurability (no custom rules)

#### Implementation

```python
# members/services/rule_engine.py

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone


class RuleEngine:
    """
    Event-driven rule engine.
    Maps events to automated actions.
    Core logic is FIXED - only parameters configurable.
    """
    
    # Event definitions (fixed)
    EVENTS = {
        'member.registered': 'Member registered',
        'member.deactivated': 'Member deactivated',
        'share.allotted': 'Shares allotted',
        'share.transferred': 'Shares transferred',
        'share.transmitted': 'Shares transmitted',
        'nominee.added': 'Nominee added',
        'nominee.updated': 'Nominee updated',
    }
    
    @classmethod
    def handle_event(cls, event_name, context):
        """
        Handle an event by executing configured actions.
        
        Args:
            event_name: One of EVENTS keys
            context: Dict with event context (member, shares, user, etc.)
        """
        if event_name not in cls.EVENTS:
            raise ValueError(f"Unknown event: {event_name}")
        
        # Route to appropriate handler
        handler = getattr(cls, f'on_{event_name.replace(".", "_")}', None)
        if handler:
            return handler(context)
        
        return None
    
    @classmethod
    def on_member_registered(cls, context):
        """
        Actions when member registers:
        1. Allot default shares (if auto-allotment enabled)
        2. Generate entrance fee voucher (if applicable)
        """
        member = context['member']
        user = context['user']
        config = member.society.share_config
        
        # Auto-allot default shares if configured
        if config.default_share_count > 0:
            from .share_ledger import ShareLedgerService
            ShareLedgerService.allot_shares(
                member,
                config.default_share_count,
                user
            )
        
        # Generate entrance fee voucher if applicable
        if config.entrance_fee > 0:
            from .voucher_generator import VoucherGenerator
            VoucherGenerator.generate_entrance_fee_voucher(member, user)
    
    @classmethod
    def on_share_transferred(cls, context):
        """
        Actions when shares transferred:
        1. Generate transfer fee voucher
        2. Update certificates
        """
        from_member = context['from_member']
        to_member = context['to_member']
        user = context['user']
        config = from_member.society.share_config
        
        if config.transfer_fee > 0:
            from .voucher_generator import VoucherGenerator
            VoucherGenerator.generate_transfer_fee_voucher(from_member, user)
```

### 6. Configuration Module

**Location:** `members/models/model_SocietyConfig.py` (already defined in Data Models)

#### Responsibilities
- Store society-specific settings
- Validate configuration parameters
- Provide defaults

---

## Integration Points

### 1. Integration with Existing Accounting System

#### Voucher Model Integration
- New vouchers created via `VoucherGenerator` use existing [`accounting/models/model_Voucher.py`](accounting/models/model_Voucher.py)
- All share-related vouchers follow existing double-entry rules
- Voucher posting uses existing accounting workflow

#### Account Model Integration
- Share capital accounts are standard [`accounting/models/model_Account.py`](accounting/models/model_Account.py)
- Account mapping ensures proper account types (Liability for share capital)
- Uses existing account hierarchy

#### LedgerEntry Integration
- All voucher lines create standard [`accounting/models/model_LedgerEntry.py`](accounting/models/model_LedgerEntry.py)
- Maintains audit trail in existing accounting ledger

### 2. Integration with Existing Member Model

#### Extending Member Model
- Existing [`members/models/model_Member.py`](members/models/model_Member.py) extended with share fields
- Backward compatible (new fields are optional or have defaults)
- Existing member queries remain functional

#### Member-Society Relationship
- Uses existing `society` ForeignKey
- Respects existing society membership constraints

### 3. Integration with Django Signals

```python
# members/signals.py

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from members.models.model_Member import Member
from members.models.model_ShareLedger import ShareLedger
from members.services.rule_engine import RuleEngine


@receiver(post_save, sender=Member)
def member_saved_handler(sender, instance, created, **kwargs):
    """Trigger events when member is saved."""
    if created:
        RuleEngine.handle_event('member.registered', {
            'member': instance,
            'user': instance.created_by if hasattr(instance, 'created_by') else None
        })


@receiver(post_save, sender=ShareLedger)
def share_ledger_saved_handler(sender, instance, created, **kwargs):
    """Trigger events when share ledger entry is created."""
    if created:
        event_map = {
            'ALLOTMENT': 'share.allotted',
            'TRANSFER_IN': 'share.transferred',
            'TRANSFER_OUT': 'share.transferred',
            'TRANSMISSION': 'share.transmitted',
        }
        
        event = event_map.get(instance.transaction_type)
        if event:
            RuleEngine.handle_event(event, {
                'member': instance.member,
                'shares': instance.shares_in or instance.shares_out,
                'ledger_entry': instance,
                'user': instance.created_by
            })
```

### 4. Integration with Existing Admin Interface

- Extend [`housing/admin.py`](housing/admin.py) or create new `members/admin.py`
- Use existing admin patterns for consistency
- Add inline admins for related models (NomineeInline, ShareLedgerInline)

---

## Event-Driven Workflow

### Workflow Diagram (Text-Based)

```
┌─────────────────────────────────────────────────────────────────────┐
│                     MEMBER REGISTRATION FLOW                        │
└─────────────────────────────────────────────────────────────────────┘

[User Action: Register New Member]
        │
        ▼
┌───────────────────────────────────┐
│ 1. Create Member Record          │
│    - Validate data               │
│    - Generate member number       │
│    - Set status = ACTIVE         │
└───────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────┐
│ 2. Trigger Event:                 │
│    member.registered              │
└───────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────┐
│ 3. Rule Engine:                   │
│    on_member_registered()         │
│                                   │
│    Check: auto_allot_shares?      │
│    - YES → Allot default shares   │
│    - NO  → Wait for manual action │
└───────────────────────────────────┘
        │
        ▼ (if auto-allot)
┌───────────────────────────────────┐
│ 4. ShareLedgerService:            │
│    allot_shares()                 │
│                                   │
│    - Create ShareLedger entry     │
│    - Calculate balance_after      │
│    - Update member.share_balance  │
└───────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────┐
│ 5. Trigger Event:                 │
│    share.allotted                 │
└───────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────┐
│ 6. Rule Engine:                   │
│    on_share_allotted()            │
│                                   │
│    - Generate voucher?            │
│    - Issue certificate?           │
└───────────────────────────────────┘
        │
        ▼ (if generate voucher)
┌───────────────────────────────────┐
│ 7. VoucherGenerator:              │
│    generate_share_allotment_      │
│    voucher()                      │
│                                   │
│    Debit:  Bank Account           │
│    Credit: Share Capital Account  │
└───────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────┐
│ 8. Accounting System:             │
│    - Post voucher                 │
│    - Update ledger entries        │
│    - Update account balances      │
└───────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────┐
│ 9. Optional: Issue Certificate    │
│    - Create ShareCertificate      │
│    - Generate certificate PDF     │
│    - Send to member               │
└───────────────────────────────────┘
        │
        ▼
[Complete: Member Registered with Shares]


┌─────────────────────────────────────────────────────────────────────┐
│                       SHARE TRANSFER FLOW                          │
└─────────────────────────────────────────────────────────────────────┘

[User Action: Transfer Shares]
        │
        ▼
┌───────────────────────────────────┐
│ 1. Validate Transfer:             │
│    - Check from_member balance    │
│    - Check to_member exists       │
│    - Check max_share limit        │
│    - Check approval required?     │
└───────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────┐
│ 2. If Approval Required:          │
│    - Create TransferRequest       │
│    - Notify administrators        │
│    - Wait for approval            │
│    - (Flow pauses here)           │
└───────────────────────────────────┘
        │
        ▼ (after approval or if no approval needed)
┌───────────────────────────────────┐
│ 3. ShareLedgerService:            │
│    transfer_shares()               │
│                                   │
│    - Create TRANSFER_OUT entry    │
│      for from_member              │
│    - Create TRANSFER_IN entry     │
│      for to_member                │
│    - Update both balances         │
└───────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────┐
│ 4. Trigger Event:                 │
│    share.transferred              │
└───────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────┐
│ 5. Rule Engine:                   │
│    on_share_transferred()         │
│                                   │
│    - Generate transfer fee voucher│
│    - Update certificates          │
│    - Notify parties               │
└───────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────┐
│ 6. VoucherGenerator:              │
│    generate_transfer_fee_         │
│    voucher()                       │
│                                   │
│    Debit:  Member Receivable      │
│    Credit: Transfer Fee Income    │
└───────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────┐
│ 7. Update Certificates:           │
│    - Mark old certificate as      │
│      TRANSFERRED                  │
│    - Create new certificate       │
│      for to_member                │
└───────────────────────────────────┘
        │
        ▼
[Complete: Shares Transferred]


┌─────────────────────────────────────────────────────────────────────┐
│                     SHARE TRANSMISSION FLOW                        │
│              (Upon Member Death - to Nominee)                       │
└─────────────────────────────────────────────────────────────────────┘

[Trigger: Member Death Reported]
        │
        ▼
┌───────────────────────────────────┐
│ 1. Verify Member Death:           │
│    - Update member status         │
│    - Set exit_date                │
│    - Validate death certificate  │
└───────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────┐
│ 2. Get Active Nominees:           │
│    - Query Nominee where          │
│      member = deceased            │
│      is_active = True             │
│    - Order by priority_order      │
└───────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────┐
│ 3. Validate Transmission:         │
│    - Check total shares > 0       │
│    - Check nominee exists         │
│    - Verify nominee is also a     │
│      registered member            │
└───────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────┐
│ 4. ShareLedgerService:            │
│    transmit_shares()              │
│                                   │
│    - Create TRANSMISSION entry    │
│      for deceased member          │
│    - Balance → 0                  │
└───────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────┐
│ 5. Allot Shares to Nominee:       │
│    - Use allot_shares() for       │
│      nominee (now member)         │
│    - Same share count             │
└───────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────┐
│ 6. Trigger Event:                 │
│    share.transmitted              │
└───────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────┐
│ 7. Rule Engine:                   │
│    on_share_transmitted()         │
│                                   │
│    - Generate transmission fee?    │
│    - Update certificates          │
│    - Notify legal heirs           │
└───────────────────────────────────┘
        │
        ▼
[Complete: Shares Transmitted to Nominee]
```

### Event Catalog

| Event Name | Trigger | Actions |
|------------|---------|---------|
| `member.registered` | New member created | Allot default shares, generate entrance fee voucher |
| `member.deactivated` | Member deactivated | Transfer/shares out, notify society |
| `share.allotted` | Shares allotted | Generate voucher, issue certificate |
| `share.transferred` | Shares transferred | Generate transfer fee, update certificates |
| `share.transmitted` | Shares transmitted | Generate transmission fee, notify nominees |
| `nominee.added` | Nominee added | Validate percentages, notify member |
| `nominee.updated` | Nominee updated | Validate percentages, audit log |

---

## Implementation Roadmap

### Phase 1: Foundation (Week 1-2)

**Goal:** Set up data models and basic infrastructure

1. **Create Database Models**
   - [ ] Create `Nominee` model ([`members/models/model_Nominee.py`](members/models/model_Nominee.py))
   - [ ] Create `ShareLedger` model ([`members/models/model_ShareLedger.py`](members/models/model_ShareLedger.py))
   - [ ] Create `ShareCertificate` model ([`members/models/model_ShareCertificate.py`](members/models/model_ShareCertificate.py))
   - [ ] Create `SocietyConfig` model ([`members/models/model_SocietyConfig.py`](members/models/model_SocietyConfig.py))
   - [ ] Create `AccountMapping` model ([`members/models/model_AccountMapping.py`](members/models/model_AccountMapping.py))

2. **Extend Existing Models**
   - [ ] Add share-related fields to `Member` model ([`members/models/model_Member.py`](members/models/model_Member.py))
   - [ ] Add share_capital_account to `Society` model ([`societies/models/model_Society.py`](societies/models/model_Society.py))

3. **Create Migrations**
   - [ ] Generate and test all migrations
   - [ ] Ensure backward compatibility

4. **Update Model Imports**
   - [ ] Update [`housing/models.py`](housing/models.py) with new model exports
   - [ ] Update [`members/models/__init__.py`](members/models/__init__.py)

### Phase 2: Core Services (Week 3-4)

**Goal:** Implement business logic services

1. **Share Ledger Service**
   - [ ] Implement `ShareLedgerService` ([`members/services/share_ledger.py`](members/services/share_ledger.py))
   - [ ] Implement append-only logic
   - [ ] Implement balance calculation
   - [ ] Add transaction types (allotment, transfer, transmission)

2. **Membership Service**
   - [ ] Implement `MembershipService` ([`members/services/membership.py`](members/services/membership.py))
   - [ ] Implement member registration with auto-allotment
   - [ ] Implement member deactivation

3. **Nominee Service**
   - [ ] Implement `NomineeService` ([`members/services/nominee.py`](members/services/nominee.py))
   - [ ] Implement versioning (deactivate + create new)
   - [ ] Implement percentage validation

4. **Voucher Generator**
   - [ ] Implement `VoucherGenerator` ([`members/services/voucher_generator.py`](members/services/voucher_generator.py))
   - [ ] Generate share allotment vouchers
   - [ ] Generate fee vouchers (entrance, transfer, transmission)

### Phase 3: Rule Engine & Events (Week 5)

**Goal:** Implement event-driven automation

1. **Rule Engine**
   - [ ] Implement `RuleEngine` ([`members/services/rule_engine.py`](members/services/rule_engine.py))
   - [ ] Map events to actions
   - [ ] Implement all event handlers

2. **Django Signals**
   - [ ] Create [`members/signals.py`](members/signals.py)
   - [ ] Connect signals to models
   - [ ] Test event flow

3. **Configuration**
   - [ ] Implement society config validation
   - [ ] Create default config on society creation
   - [ ] Add account mapping setup

### Phase 4: UI & Admin (Week 6-7)

**Goal:** Create user interfaces

1. **Admin Interface**
   - [x] Create [`members/admin.py`](members/admin.py)
   - [x] Add ShareLedger admin with read-only view
   - [x] Add Nominee admin with inline editing
   - [x] Add ShareCertificate admin
   - [x] Add SocietyConfig admin

2. **Member UI** (extend existing [`housing/templates/housing/`](housing/templates/housing/))
   - [ ] Member registration form with share allotment
   - [ ] Share balance display
   - [ ] Nominee management interface
   - [ ] Share certificate view/download

3. **Share Management UI**
   - [ ] Share transfer form
   - [ ] Share transmission form
   - [ ] Share ledger view (read-only)
   - [ ] Certificate issuance interface

### Phase 5: Integration & Testing (Week 8)

**Goal:** Full system integration and testing

1. **Integration Testing**
   - [ ] Test accounting integration (vouchers, ledger entries)
   - [ ] Test member model extensions
   - [ ] Test event flow end-to-end

2. **Unit Tests**
   - [ ] Create [`members/tests/test_share_ledger.py`](members/tests/test_share_ledger.py)
   - [ ] Create [`members/tests/test_nominee.py`](members/tests/test_nominee.py)
   - [ ] Create [`members/tests/test_voucher_generator.py`](members/tests/test_voucher_generator.py)
   - [ ] Create [`members/tests/test_rule_engine.py`](members/tests/test_rule_engine.py)

3. **Audit & Compliance**
   - [ ] Verify append-only constraints
   - [ ] Verify audit trails
   - [ ] Test rollback scenarios (reversing entries)

### Phase 6: Deployment & Documentation (Week 9-10)

**Goal:** Production readiness

1. **Documentation**
   - [ ] User manual for share management
   - [ ] Admin guide for configuration
   - [ ] API documentation (if applicable)

2. **Deployment**
   - [ ] Deploy to staging
   - [ ] User acceptance testing
   - [ ] Deploy to production
   - [ ] Monitor and fix issues

---

## Appendices

### Appendix A: Field Specifications Summary

| Model | Field | Type | Constraints | Description |
|-------|-------|------|-------------|-------------|
| Society | share_capital_account | FK(Account) | Null=True | Main share capital account |
| Member | member_number | CharField(50) | Unique per society | Unique member identifier |
| Member | share_balance | DecimalField | Default=0 | Current share balance (denormalized) |
| Member | kyc_verified | BooleanField | Default=False | KYC status |
| Nominee | member | FK(Member) | CASCADE | Parent member |
| Nominee | percentage | DecimalField(5,2) | 0-100 | Share percentage |
| Nominee | is_active | BooleanField | Default=True | Active status (for versioning) |
| ShareLedger | shares_in | DecimalField(12,2) | >=0 | Shares incoming |
| ShareLedger | shares_out | DecimalField(12,2) | >=0 | Shares outgoing |
| ShareLedger | balance_after | DecimalField(12,2) | >=0 | Running balance |
| ShareLedger | transaction_type | CharField(20) | Choices | Type of transaction |
| ShareCertificate | certificate_no | CharField(50) | Unique | Certificate number |
| ShareCertificate | share_count | DecimalField(12,2) | >0 | Shares covered |
| SocietyConfig | share_value | DecimalField(12,2) | >0 | Face value per share |
| SocietyConfig | entrance_fee | DecimalField(12,2) | >=0 | One-time entrance fee |
| AccountMapping | share_capital_account | FK(Account) | Required | Share capital liability account |

### Appendix B: Error Codes

| Code | Description | Action |
|------|-------------|--------|
| ERR_SHARE_INSUFFICIENT | Member has insufficient shares for transfer | Check balance, reduce transfer amount |
| ERR_SHARE_MAX_LIMIT | Transfer would exceed member's max share limit | Check SocietyConfig.max_share_count |
| ERR_NOMINEE_PERCENTAGE | Total nominee percentage exceeds 100% | Reduce percentage or remove nominees |
| ERR_NOMINEE_MAX_COUNT | Too many nominees for society config | Check SocietyConfig.max_nominee_count |
| ERR_KYC_REQUIRED | KYC verification required for action | Complete KYC first |
| ERR_APPROVAL_REQUIRED | Action requires admin approval | Submit for approval |
| ERR_VOUCHER_GENERATION | Failed to generate accounting voucher | Check account mapping, voucher config |

### Appendix C: SQL Migration Example

```sql
-- Example migration for ShareLedger table
CREATE TABLE housing_shareledger (
    id SERIAL PRIMARY KEY,
    society_id INTEGER NOT NULL REFERENCES housing_society(id),
    member_id INTEGER NOT NULL REFERENCES housing_member(id),
    shares_in DECIMAL(12,2) NOT NULL DEFAULT 0,
    shares_out DECIMAL(12,2) NOT NULL DEFAULT 0,
    balance_after DECIMAL(12,2) NOT NULL,
    transaction_type VARCHAR(20) NOT NULL,
    reference_id VARCHAR(100) NOT NULL DEFAULT '',
    transaction_date DATE NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_by_id INTEGER REFERENCES users_user(id),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    voucher_id INTEGER REFERENCES accounting_voucher(id)
);

CREATE INDEX idx_shareledger_society_member ON housing_shareledger(society_id, member_id);
CREATE INDEX idx_shareledger_transaction_date ON housing_shareledger(transaction_date);
CREATE INDEX idx_shareledger_voucher ON housing_shareledger(voucher_id);
```

### Appendix D: Test Cases

```python
# members/tests/test_share_ledger.py

from django.test import TestCase
from members.models import Member, ShareLedger
from members.services.share_ledger import ShareLedgerService


class ShareLedgerTest(TestCase):
    def test_append_only_constraint(self):
        """Verify that ShareLedger is append-only."""
        member = Member.objects.create(...)
        
        # Create first entry
        entry1 = ShareLedgerService.allot_shares(member, 10, user)
        
        # Try to update (should not be done in practice)
        # But verify balance_after is correct
        self.assertEqual(entry1.balance_after, 10)
        
        # Create second entry
        entry2 = ShareLedgerService.allot_shares(member, 5, user)
        self.assertEqual(entry2.balance_after, 15)
        
        # Verify we have 2 entries (not 1 updated)
        self.assertEqual(ShareLedger.objects.filter(member=member).count(), 2)
    
    def test_transfer_shares(self):
        """Test share transfer between members."""
        from_member = Member.objects.create(...)
        to_member = Member.objects.create(...)
        
        # Allot shares to from_member
        ShareLedgerService.allot_shares(from_member, 100, user)
        
        # Transfer 30 shares
        ShareLedgerService.transfer_shares(from_member, to_member, 30, user)
        
        # Verify balances
        self.assertEqual(from_member.share_balance, 70)
        self.assertEqual(to_member.share_balance, 30)
```

---

## Summary

This system design document outlines a comprehensive membership and share management system that:

1. **Follows core principles**: Ownership ≠ Accounting, Append-only, Event-driven
2. **Extends existing system**: Builds on current Django models and accounting system
3. **Maintains audit compliance**: Full audit trail via append-only ledgers
4. **Provides controlled configurability**: Society-specific settings without changing core logic
5. **Integrates seamlessly**: Uses existing accounting, member, and society models

The implementation is phased over 10 weeks, starting with data models and progressing through services, events, UI, and testing.

---

**Document Version:** 1.0  
**Created:** 2026-05-01  
**Last Updated:** 2026-05-01  
**Status:** Draft for Review
