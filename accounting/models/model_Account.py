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

    society = models.ForeignKey(
        Society,
        on_delete=models.CASCADE,
        related_name="accounts",
    )
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=20, blank=True, null=True)
    category = models.ForeignKey(
        AccountCategory,    
        on_delete=models.CASCADE,
        related_name="accounts",
        #null=True,        # ✅ TEMPORARY
        #blank=True, 
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
        ordering = ("name",)
        unique_together = ("society", "name", "parent")

    @property
    def normal_side(self):
        return "DR" if self.account_type in {"ASSET", "EXPENSE"} else "CR"

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
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

    def delete(self, *args, **kwargs):
        if self.system_protected:
            raise ValidationError("System-protected account cannot be deleted.")
        return super().delete(*args, **kwargs)
