# shares/models/model_EventLog.py

from django.db import models
from django.utils import timezone
from django.contrib.auth import get_user_model
from members.models.model_Member import Member
from societies.models.model_Society import Society
from members.models.model_Nominee import Nominee


User = get_user_model()


class EventLog(models.Model):
    """
    Comprehensive audit log for share transactions and member events.
    Append-only immutable record for audit compliance.
    """
    
    class EventType(models.TextChoices):
        SHARE_ALLOTMENT = "SHARE_ALLOTMENT", "Share Allotment"
        SHARE_TRANSFER = "SHARE_TRANSFER", "Share Transfer"
        SHARE_TRANSMISSION = "SHARE_TRANSMISSION", "Share Transmission"
        SHARE_CORRECTION = "SHARE_CORRECTION", "Share Correction"
        SHARE_FORFEITURE = "SHARE_FORFEITURE", "Share Forfeiture"
        SHARE_BUYBACK = "SHARE_BUYBACK", "Share Buyback"
        SHARE_ADJUSTMENT = "SHARE_ADJUSTMENT", "Share Adjustment"
        NOMINEE_ADDED = "NOMINEE_ADDED", "Nominee Added"
        NOMINEE_UPDATED = "NOMINEE_UPDATED", "Nominee Updated"
        NOMINEE_REMOVED = "NOMINEE_REMOVED", "Nominee Removed"
        SHARE_CERTIFICATE_ISSUED = "SHARE_CERTIFICATE_ISSUED", "Share Certificate Issued"
        SHARE_CERTIFICATE_CANCELLED = "SHARE_CERTIFICATE_CANCELLED", "Share Certificate Cancelled"
        SHARE_CERTIFICATE_REPLACED = "SHARE_CERTIFICATE_REPLACED", "Share Certificate Replaced"
        SHARE_CERTIFICATE_TRANSFERRED = "SHARE_CERTIFICATE_TRANSFERRED", "Share Certificate Transferred"
        MEMBER_SHARE_BALANCE_CHANGED = "MEMBER_SHARE_BALANCE_CHANGED", "Member Share Balance Changed"
    
    # Timestamp
    timestamp = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        help_text="When the event occurred"
    )
    
    # Event identification
    event_type = models.CharField(
        max_length=50,
        choices=EventType.choices,
        db_index=True
    )
    
    # Primary member involved
    member = models.ForeignKey(
        Member,
        on_delete=models.PROTECT,
        related_name="event_logs",
        null=True,
        blank=True,
        help_text="Primary member involved in the event"
    )
    
    # For transfers: from and to members
    from_member = models.ForeignKey(
        Member,
        on_delete=models.PROTECT,
        related_name="event_logs_as_sender",
        null=True,
        blank=True,
        help_text="Member transferring shares (for transfers)"
    )
    
    to_member = models.ForeignKey(
        Member,
        on_delete=models.PROTECT,
        related_name="event_logs_as_receiver",
        null=True,
        blank=True,
        help_text="Member receiving shares (for transfers)"
    )
    
    # Share details
    share_count = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Number of shares involved"
    )
    
    share_value = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Value per share (if applicable)"
    )
    
    # Certificate reference
    certificate_number = models.CharField(
        max_length=50,
        blank=True,
        help_text="Certificate number (if applicable)"
    )
    
    # Nominee reference
    nominee = models.ForeignKey(
        Nominee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="event_logs",
        help_text="Nominee involved (if applicable)"
    )
    
    # Performed by user
    performed_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="performed_events",
        help_text="User who performed the action"
    )
    
    # Society context
    society = models.ForeignKey(
        Society,
        on_delete=models.PROTECT,
        related_name="event_logs",
        help_text="Society where the event occurred"
    )
    
    # Description
    description = models.TextField(
        help_text="Human-readable description of the event"
    )
    
    # Additional context
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional structured context (JSON)"
    )
    
    # Request context (captured via middleware)
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="IP address of the request"
    )
    
    user_agent = models.TextField(
        blank=True,
        help_text="User agent of the request"
    )
    
    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        app_label = "shares"
        ordering = ["-timestamp", "-created_at"]
        indexes = [
            models.Index(fields=["event_type", "timestamp"]),
            models.Index(fields=["member", "timestamp"]),
            models.Index(fields=["society", "timestamp"]),
            models.Index(fields=["performed_by", "timestamp"]),
            models.Index(fields=["certificate_number"]),
        ]
        verbose_name = "Event Log"
        verbose_name_plural = "Event Logs"
    
    def __str__(self):
        return f"{self.event_type} - {self.member or 'System'} - {self.timestamp.date()}"
    
    def clean(self):
        from django.core.exceptions import ValidationError
        
        # Ensure at least one member reference exists
        if not any([self.member, self.from_member, self.to_member]):
            raise ValidationError("At least one member reference is required")
        
        # Validate transfer consistency
        if self.event_type == self.EventType.SHARE_TRANSFER:
            if not self.from_member or not self.to_member:
                raise ValidationError("Transfer events require both from_member and to_member")
        
        # Validate share_count for share-related events
        share_events = [
            self.EventType.SHARE_ALLOTMENT,
            self.EventType.SHARE_TRANSFER,
            self.EventType.SHARE_TRANSMISSION,
            self.EventType.SHARE_CORRECTION,
            self.EventType.SHARE_FORFEITURE,
            self.EventType.SHARE_BUYBACK,
            self.EventType.SHARE_ADJUSTMENT,
        ]
        if self.event_type in share_events and self.share_count is None:
            raise ValidationError(f"Share count is required for {self.event_type}")
    
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)