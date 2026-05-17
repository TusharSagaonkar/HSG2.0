from django.db import models
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .model_Society import Society
from accounting.models import AccountMapping


class SocietyConfig(models.Model):
    """
    Per-society configuration for share management.
    Only parameters are configurable - core logic is fixed.
    """
    
    society = models.OneToOneField(
        Society,
        on_delete=models.CASCADE,
        related_name="share_config",
        help_text="Society this configuration belongs to"
    )
    
    # Share value configuration
    share_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=100.00,
        help_text="Face value of each share"
    )
    default_share_count = models.PositiveIntegerField(
        default=1,
        help_text="Default number of shares allotted to new members"
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
    premium_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Premium amount (if shares are issued above face value)"
    )
    
    # Nominee and approval configuration
    allow_multiple_nominees = models.BooleanField(
        default=False,
        help_text="Allow members to nominate multiple persons"
    )
    require_approval = models.BooleanField(
        default=True,
        help_text="Require admin approval for share-related actions"
    )
    
    # Voucher generation configuration
    auto_generate_vouchers = models.BooleanField(
        default=True,
        help_text="Automatically generate accounting vouchers for share transactions"
    )
    
    # Audit timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        app_label = "societies"
        verbose_name = "Society Configuration"
        verbose_name_plural = "Society Configurations"
        indexes = [
            models.Index(fields=["society"]),
        ]
    
    def clean(self):
        """
        Validate field constraints.
        """
        # Monetary fields must be non-negative
        monetary_fields = [
            ("share_value", self.share_value),
            ("entrance_fee", self.entrance_fee),
            ("transfer_fee", self.transfer_fee),
            ("premium_amount", self.premium_amount),
        ]
        for field_name, value in monetary_fields:
            if value < 0:
                raise ValidationError(
                    _(f"{field_name.replace('_', ' ').title()} must be non-negative.")
                )
        
        # default_share_count must be positive
        if self.default_share_count <= 0:
            raise ValidationError(
                _("Default share count must be positive.")
            )
    
    def get_account_mapping(self):
        """
        Returns the AccountMapping for this society, creating a default one if needed.
        """
        return AccountMapping.ensure_for_society(self.society)

    def get_share_capital_account(self):
        """
        Returns the share capital account for this society.
        """
        mapping = self.get_account_mapping()
        return mapping.share_capital_account

    def get_bank_account(self):
        """
        Returns the primary bank account for this society.
        """
        mapping = self.get_account_mapping()
        return mapping.bank_account
    
    def __str__(self):
        return f"Configuration for {self.society.name}"