"""
Signals for share management.
"""

import logging
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import Signal, receiver
from django.contrib.auth import get_user_model
from django.db import transaction

try:
    from shares.models import ShareLedger, ShareCertificate, EventLog
    from shares.services import EventLogService
    from members.models.model_Member import Member
    from members.models.model_Nominee import Nominee
except ImportError:  # pragma: no cover - model package is still being wired up
    ShareLedger = None
    ShareCertificate = None
    EventLog = None
    EventLogService = None
    Member = None
    Nominee = None


logger = logging.getLogger(__name__)
User = get_user_model()


# Legacy signals (keep for backward compatibility)
share_allotted = Signal()
share_transferred = Signal()
share_transmitted = Signal()
share_corrected = Signal()


def _emit_share_event(sender, instance, created, **kwargs):
    del sender, kwargs
    if not created:
        return

    event_map = {
        "ALLOTMENT": share_allotted,
        "TRANSFER_IN": share_transferred,
        "TRANSFER_OUT": share_transferred,
        "TRANSMISSION": share_transmitted,
        "ADJUSTMENT": share_corrected,
        "CORRECTION": share_corrected,
    }
    signal = event_map.get(getattr(instance, "transaction_type", None))
    if signal is not None:
        signal.send(sender=type(instance), instance=instance)


# ==================== ShareLedger Event Logging ====================

@receiver(post_save, sender=ShareLedger)
def log_share_ledger_event(sender, instance, created, **kwargs):
    """
    Log share ledger events to EventLog.
    Maps ShareLedger.TransactionType to EventLog.EventType.
    """
    if not created or EventLogService is None:
        return

    # Determine performed_by user
    performed_by = instance.created_by
    if not performed_by:
        # Fallback to system user (first superuser)
        from django.contrib.auth import get_user_model
        User = get_user_model()
        try:
            performed_by = User.objects.filter(is_superuser=True).first()
            if not performed_by:
                # Create a system user if none exists (should not happen in production)
                performed_by = User.objects.create(
                    username="system",
                    email="system@example.com",
                    is_superuser=True,
                    is_staff=True,
                    is_active=True
                )
        except Exception:
            # If all else fails, use the first user or None (will cause error)
            performed_by = User.objects.first()

    # Map transaction type to event type
    transaction_type = instance.transaction_type
    event_type_map = {
        ShareLedger.TransactionType.ALLOTMENT: EventLog.EventType.SHARE_ALLOTMENT,
        ShareLedger.TransactionType.TRANSFER: EventLog.EventType.SHARE_TRANSFER,
        ShareLedger.TransactionType.TRANSFER_IN: EventLog.EventType.SHARE_TRANSFER,
        ShareLedger.TransactionType.TRANSFER_OUT: EventLog.EventType.SHARE_TRANSFER,
        ShareLedger.TransactionType.TRANSMISSION: EventLog.EventType.SHARE_TRANSMISSION,
        ShareLedger.TransactionType.CORRECTION: EventLog.EventType.SHARE_CORRECTION,
        ShareLedger.TransactionType.ADJUSTMENT: EventLog.EventType.SHARE_ADJUSTMENT,
        ShareLedger.TransactionType.FORFEITURE: EventLog.EventType.SHARE_FORFEITURE,
        ShareLedger.TransactionType.BUYBACK: EventLog.EventType.SHARE_BUYBACK,
    }

    event_type = event_type_map.get(transaction_type)
    if event_type is None:
        logger.warning(
            "Unknown transaction type %s for ShareLedger %s, skipping event log",
            transaction_type, instance.id
        )
        return

    # Determine share count
    share_count = instance.shares_in if instance.shares_in > 0 else instance.shares_out

    # For transfers, we need from_member and to_member
    from_member = None
    to_member = None
    if transaction_type in [ShareLedger.TransactionType.TRANSFER,
                            ShareLedger.TransactionType.TRANSFER_IN,
                            ShareLedger.TransactionType.TRANSFER_OUT]:
        # This is tricky because a single ShareLedger entry only has one member.
        # The transfer is represented by two entries (TRANSFER_OUT and TRANSFER_IN).
        # We'll log each separately; the EventLogService.log_transfer_event expects both.
        # We'll handle this in a separate signal that listens for both entries.
        # For simplicity, we'll log as a generic share event.
        pass

    def _log_event():
        try:
            EventLogService.log_share_event(
                event_type=event_type,
                society=instance.society,
                performed_by=performed_by,
                member=instance.member,
                share_count=share_count,
                description=f"Share ledger entry: {transaction_type}",
                metadata={
                    "share_ledger_id": instance.id,
                    "transaction_date": instance.transaction_date.isoformat(),
                    "reference_id": instance.reference_id,
                    "reason": instance.reason,
                }
            )
            logger.debug("Logged share ledger event for %s", instance.id)
        except Exception as e:
            logger.exception("Failed to log share ledger event for %s: %s", instance.id, e)

    transaction.on_commit(_log_event)


# ==================== Nominee Event Logging ====================

@receiver(post_save, sender=Nominee)
def log_nominee_save_event(sender, instance, created, **kwargs):
    """Log nominee creation or update."""
    if EventLogService is None:
        return

    performed_by = getattr(instance, 'created_by', None) or getattr(instance, 'updated_by', None)
    event_type = EventLog.EventType.NOMINEE_ADDED if created else EventLog.EventType.NOMINEE_UPDATED

    def _log_event():
        try:
            EventLogService.log_nominee_event(
                society=instance.member.society,
                member=instance.member,
                nominee=instance,
                event_type=event_type,
                performed_by=performed_by,
                description=f"Nominee {instance.name} {'added' if created else 'updated'}",
            )
            logger.debug("Logged nominee %s event for %s", 'creation' if created else 'update', instance.id)
        except Exception as e:
            logger.exception("Failed to log nominee save event for %s: %s", instance.id, e)

    transaction.on_commit(_log_event)


@receiver(post_delete, sender=Nominee)
def log_nominee_delete_event(sender, instance, **kwargs):
    """Log nominee deletion."""
    if EventLogService is None:
        return

    performed_by = getattr(instance, 'deleted_by', None)
    def _log_event():
        try:
            EventLogService.log_nominee_event(
                society=instance.member.society,
                member=instance.member,
                nominee=instance,
                event_type=EventLog.EventType.NOMINEE_REMOVED,
                performed_by=performed_by,
                description=f"Nominee {instance.name} removed",
            )
            logger.debug("Logged nominee deletion event for %s", instance.id)
        except Exception as e:
            logger.exception("Failed to log nominee delete event for %s: %s", instance.id, e)

    transaction.on_commit(_log_event)


# ==================== ShareCertificate Event Logging ====================

@receiver(post_save, sender=ShareCertificate)
def log_share_certificate_event(sender, instance, created, **kwargs):
    """Log share certificate creation or update."""
    if EventLogService is None:
        return

    performed_by = getattr(instance, 'issued_by', None) or getattr(instance, 'created_by', None)

    # Determine event type based on status
    if created:
        event_type = EventLog.EventType.SHARE_CERTIFICATE_ISSUED
    elif instance.status == ShareCertificate.Status.CANCELLED:
        event_type = EventLog.EventType.SHARE_CERTIFICATE_CANCELLED
    elif instance.status == ShareCertificate.Status.REPLACED:
        event_type = EventLog.EventType.SHARE_CERTIFICATE_REPLACED
    elif instance.status == ShareCertificate.Status.TRANSFERRED:
        event_type = EventLog.EventType.SHARE_CERTIFICATE_TRANSFERRED
    else:
        # Other status changes not logged
        return

    def _log_event():
        try:
            EventLogService.log_certificate_event(
                society=instance.member.society,
                member=instance.member,
                certificate_number=instance.certificate_no,
                event_type=event_type,
                performed_by=performed_by,
                share_count=instance.share_count,
                description=f"Share certificate {instance.certificate_no} {event_type.label.lower()}",
            )
            logger.debug("Logged share certificate event for %s", instance.certificate_no)
        except Exception as e:
            logger.exception("Failed to log share certificate event for %s: %s", instance.certificate_no, e)

    transaction.on_commit(_log_event)


# ==================== Member Share Balance Change Logging ====================

@receiver(pre_save, sender=Member)
def capture_member_share_balance_before(sender, instance, **kwargs):
    """Store old share_balance before save to detect changes."""
    if not instance.pk or EventLogService is None:
        return

    try:
        old_instance = Member.objects.get(pk=instance.pk)
        instance._old_share_balance = old_instance.share_balance
    except Member.DoesNotExist:
        instance._old_share_balance = None


@receiver(post_save, sender=Member)
def log_member_share_balance_change(sender, instance, created, **kwargs):
    """Log member share balance changes."""
    if EventLogService is None:
        return

    if created:
        # New member, no old balance
        return

    old_balance = getattr(instance, '_old_share_balance', None)
    if old_balance is None:
        return

    new_balance = instance.share_balance
    if old_balance == new_balance:
        return

    performed_by = getattr(instance, 'updated_by', None)
    def _log_event():
        try:
            EventLogService.log_member_share_balance_change(
                society=instance.society,
                member=instance,
                old_balance=old_balance,
                new_balance=new_balance,
                performed_by=performed_by,
                description=f"Member share balance changed from {old_balance} to {new_balance}",
            )
            logger.debug("Logged member share balance change for %s", instance.id)
        except Exception as e:
            logger.exception("Failed to log member share balance change for %s: %s", instance.id, e)

    transaction.on_commit(_log_event)


# ==================== Signal Connections ====================

# Connect legacy ShareLedger signal (keep existing)
if ShareLedger is not None:
    receiver(post_save, sender=ShareLedger)(_emit_share_event)
