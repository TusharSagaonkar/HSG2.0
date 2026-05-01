import re
from django.db import models
from django.core.exceptions import ValidationError
from .model_AccountCategory import AccountCategory
from societies.models import Society


class Account(models.Model):
    class AccountType(models.TextChoices):
        ASSET = "ASSET", "Asset"
        LIABILITY = "LIABILITY", "Liability"
        INCOME = "INCOME", "Income"
        EXPENSE = "EXPENSE", "Expense"
        EQUITY = "EQUITY", "Equity"

    class SubType(models.TextChoices):
        GST = "GST", "GST"
        BANK = "BANK", "Bank"
        MEMBER = "MEMBER", "Member"
        FUND = "FUND", "Fund"
        EXPENSE = "EXPENSE", "Expense"
        INCOME = "INCOME", "Income"
        GENERAL = "GENERAL", "General"

    class GstType(models.TextChoices):
        INPUT = "INPUT", "Input"
        OUTPUT = "OUTPUT", "Output"
        NONE = "NONE", "None"

    # Regex pattern for account codes: digits separated by dots (e.g., "1", "1.1", "1.1.1")
    CODE_PATTERN = re.compile(r"^\d+(\.\d+)*$")

    society = models.ForeignKey(
        Society,
        on_delete=models.CASCADE,
        related_name="accounts",
    )
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=20, blank=True, null=True, db_index=True)
    category = models.ForeignKey(
        AccountCategory,
        on_delete=models.CASCADE,
        related_name="accounts",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="children",
    )
    is_active = models.BooleanField(default=True)
    system_protected = models.BooleanField(default=False)
    account_type = models.CharField(
        max_length=20,
        choices=AccountType.choices,
        default=AccountType.ASSET,
    )
    sub_type = models.CharField(
        max_length=20,
        choices=SubType.choices,
        default=SubType.GENERAL,
    )
    is_gst = models.BooleanField(default=False)
    gst_type = models.CharField(
        max_length=10,
        choices=GstType.choices,
        default=GstType.NONE,
    )
    is_bank = models.BooleanField(default=False)
    is_member_related = models.BooleanField(default=False)
    is_vendor_related = models.BooleanField(default=False)
    is_contra = models.BooleanField(default=False)
    is_clearing = models.BooleanField(default=False)

    class Meta:
        ordering = ("code", "name")
        unique_together = ("society", "code")
        indexes = [
            models.Index(fields=["society", "code"]),
            models.Index(fields=["society", "account_type"]),
            models.Index(fields=["parent"]),
        ]

    @property
    def normal_side(self):
        return "DR" if self.account_type in {"ASSET", "EXPENSE"} else "CR"

    @property
    def level(self):
        """Return the depth level of this account in the hierarchy."""
        if not self.code:
            return 0
        return len(self.code.split("."))

    @property
    def is_leaf(self):
        """Return True if this account has no children."""
        return not self.children.filter(is_active=True).exists()

    @property
    def full_path(self):
        """Return the full path name including all ancestors."""
        names = []
        account = self
        while account:
            names.insert(0, account.name)
            account = account.parent
        return " → ".join(names)

    def __str__(self):
        if self.code:
            return f"{self.code} - {self.name}"
        return self.name

    def clean(self):
        super().clean()

        # Validate code format if provided
        if self.code and not self.CODE_PATTERN.match(self.code):
            raise ValidationError(
                {"code": f"Account code must match pattern '1', '1.1', '1.1.1', etc. Got: {self.code}"}
            )

        # Validate parent-child code relationship
        if self.parent and self.code and self.parent.code:
            if not self.code.startswith(self.parent.code + "."):
                raise ValidationError(
                    {"code": f"Account code '{self.code}' must start with parent code '{self.parent.code}.'"}
                )

        # Validate sibling codes are unique
        if self.code and self.society_id:
            siblings = Account.objects.filter(
                society=self.society,
                parent=self.parent,
                code=self.code,
            )
            if self.pk:
                siblings = siblings.exclude(pk=self.pk)
            if siblings.exists():
                raise ValidationError(
                    {"code": f"Account code '{self.code}' already exists under the same parent."}
                )

        if self.category and self.account_type and self.account_type != self.category.account_type:
            raise ValidationError("Account type must match category account type.")
        if not self.is_gst and self.gst_type != self.GstType.NONE:
            raise ValidationError("Non-GST account must have GST type NONE.")
        if self.is_gst and self.gst_type == self.GstType.NONE:
            raise ValidationError("GST account must be classified as INPUT or OUTPUT.")
        if self.is_gst and self.account_type not in {self.AccountType.ASSET, self.AccountType.LIABILITY}:
            raise ValidationError("GST accounts must be Asset (Input) or Liability (Output), never Income/Expense.")
        if self.is_clearing and self.account_type not in {self.AccountType.ASSET, self.AccountType.LIABILITY}:
            raise ValidationError("Clearing accounts must be Asset or Liability.")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.system_protected:
            raise ValidationError("System-protected account cannot be deleted.")
        return super().delete(*args, **kwargs)
