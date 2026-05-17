# shares/models/model_ShareLedger.py

from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from societies.models.model_Society import Society
from members.models.model_Member import Member


class ShareLedger(models.Model):
    """
    Append-only share ledger tracking share ownership.
    NEVER update or delete records - always create new transactions.
    
    This is the SINGLE SOURCE OF TRUTH for share ownership.
    """
    
    class TransactionType(models.TextChoices):
        ALLOTMENT = "ALLOTMENT", "Share Allotment"
        TRANSFER = "TRANSFER", "Transfer"
        TRANSMISSION = "TRANSMISSION", "Transmission"
        CORRECTION = "CORRECTION", "Correction"
        # Additional types from design document
        TRANSFER_IN = "TRANSFER_IN", "Transfer In (from another member)"
        TRANSFER_OUT = "TRANSFER_OUT", "Transfer Out (to another member)"
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
    
    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Additional fields from design document (optional but recommended)
    reason = models.TextField(
        blank=True,
        help_text="Reason for adjustment/forfeiture/transmission"
    )
    created_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="share_transactions_created"
    )
    voucher = models.ForeignKey(
        "accounting.Voucher",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="share_transactions",
        help_text="Voucher generated for this share transaction (if any)"
    )
    
    class Meta:
        app_label = "shares"
        ordering = ["-transaction_date", "-created_at"]
        indexes = [
            models.Index(fields=["member", "transaction_date"]),
            models.Index(fields=["transaction_type", "transaction_date"]),
            models.Index(fields=["society", "member", "transaction_date"]),
            models.Index(fields=["society", "transaction_date"]),
            models.Index(fields=["voucher"]),
        ]
        verbose_name = "Share Ledger Entry"
        verbose_name_plural = "Share Ledger Entries"
    
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
        
        # Update member's denormalized share_balance (if field exists)
        # This will be handled by signals or services in the members app
        # For now, we'll keep it simple
        if hasattr(self.member, 'share_balance'):
            self.member.share_balance = self.balance_after
            self.member.save(update_fields=['share_balance'])
    
    def __str__(self):
        return f"{self.member} - {self.transaction_type}: +{self.shares_in}/-{self.shares_out} (Bal: {self.balance_after})"