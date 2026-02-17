from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal
from .model_AccountCategory import AccountCategory
from societies.models import Society

class Account(models.Model):
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
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="children",
    )
    is_active = models.BooleanField(default=True)
    system_protected = models.BooleanField(default=False)

    class Meta:
        ordering = ("name",)
        unique_together = ("society", "name", "parent")

    @property
    def account_type(self):
        return self.category.account_type

    @property
    def normal_side(self):
        return "DR" if self.account_type in {"ASSET", "EXPENSE"} else "CR"

    def __str__(self):
        return self.name

    def delete(self, *args, **kwargs):
        if self.system_protected:
            raise ValidationError("System-protected account cannot be deleted.")
        return super().delete(*args, **kwargs)
