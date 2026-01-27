from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal
from .model_AccountCategory import AccountCategory
from housing.models import Society

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
        on_delete=models.PROTECT,
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

    class Meta:
        ordering = ("name",)
        unique_together = ("name", "parent")

    @property
    def account_type(self):
        return self.category.account_type

    def __str__(self):
        return self.name