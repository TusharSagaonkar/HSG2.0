from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
import logging

from reconciliation.models.model_ReconciliationLink import ReconciliationLink
from reconciliation.models.model_ReconciliationHistory import ReconciliationHistory
from reconciliation.models.model_BankStatementImport import BankStatementImport

logger = logging.getLogger(__name__)


# Cache the old state of a ReconciliationLink before it is saved.
@receiver(pre_save, sender=ReconciliationLink)
def cache_reconciliation_link_state(sender, instance, **kwargs):
    """Cache the current state of a ReconciliationLink before it is saved.

    The cached state is attached to the instance as ``_pre_save_state`` and is
    later used by ``log_reconciliation_history`` to determine what changed.
    """
    if instance.pk:
        try:
            old = sender.objects.get(pk=instance.pk)
            instance._pre_save_state = {
                "status": old.status,
                "match_type": old.match_type,
                "confidence_score": old.confidence_score,
            }
        except sender.DoesNotExist:
            instance._pre_save_state = {}
    else:
        # New instance – no previous state.
        instance._pre_save_state = {}


@receiver(post_save, sender=ReconciliationLink)
def log_reconciliation_history(sender, instance, created, **kwargs):
    """
    Automatically create a ReconciliationHistory entry whenever
    a ReconciliationLink is created or its status/match_type/confidence changes.
    """
    if created:
        ReconciliationHistory.objects.create(
            reconciliation_link=instance,
            action=ReconciliationHistory.Action.CREATED,
            previous_status="",
            new_status=instance.status,
            previous_match_type="",
            new_match_type=instance.match_type,
            previous_confidence=None,
            new_confidence=instance.confidence_score,
            performed_by=instance.matched_by,
            details={"created": True},
        )
        return

    # Use the cached state from pre_save to determine what changed.
    old_state = getattr(instance, "_pre_save_state", None)
    if old_state is None:
        # Fallback: try to fetch the previous state from the DB (may be same as new).
        try:
            old = ReconciliationLink.objects.get(pk=instance.pk)
            old_state = {
                "status": old.status,
                "match_type": old.match_type,
                "confidence_score": old.confidence_score,
            }
        except ReconciliationLink.DoesNotExist:
            old_state = {}

    # Determine what changed.
    changed = False
    action = ReconciliationHistory.Action.UPDATED
    previous_status = old_state.get("status", "")
    new_status = instance.status
    previous_match_type = old_state.get("match_type", "")
    new_match_type = instance.match_type
    previous_confidence = old_state.get("confidence_score")
    new_confidence = instance.confidence_score

    if previous_status != new_status:
        changed = True
        if new_status == ReconciliationLink.Status.REVERSED:
            action = ReconciliationHistory.Action.REVERSED
        elif new_status == ReconciliationLink.Status.MATCHED:
            action = ReconciliationHistory.Action.CONFIRMED
        elif new_status == ReconciliationLink.Status.FORCE_MATCHED:
            action = ReconciliationHistory.Action.FORCE_MATCHED
        elif new_status == ReconciliationLink.Status.DUPLICATE:
            action = ReconciliationHistory.Action.DUPLICATE
        elif new_status == ReconciliationLink.Status.EXCEPTION:
            action = ReconciliationHistory.Action.EXCEPTION
        elif new_status == ReconciliationLink.Status.IGNORED:
            action = ReconciliationHistory.Action.IGNORED
    elif previous_match_type != new_match_type:
        changed = True
    elif previous_confidence != new_confidence:
        changed = True

    if changed:
        ReconciliationHistory.objects.create(
            reconciliation_link=instance,
            action=action,
            previous_status=previous_status,
            new_status=new_status,
            previous_match_type=previous_match_type,
            new_match_type=new_match_type,
            previous_confidence=previous_confidence,
            new_confidence=new_confidence,
            performed_by=instance.matched_by,
            details={
                "auto_logged": True,
                "confidence_delta": (
                    (new_confidence or 0) - (previous_confidence or 0)
                    if previous_confidence is not None and new_confidence is not None
                    else None
                ),
            },
        )


@receiver(post_save, sender=BankStatementImport)
def log_bank_statement_import_event(sender, instance, created, **kwargs):
    """
    Log bank statement import lifecycle events for audit trail purposes.

    These events are logged via Python's logging framework (not ReconciliationHistory,
    since that model requires a ReconciliationLink FK). The log entries appear in
    the application's log stream and can be forwarded to audit systems.
    """
    if created:
        logger.info(
            "BankStatementImport #%s created: file='%s', society=%s, bank_account=%s, uploaded_by=%s",
            instance.pk,
            instance.file_name,
            instance.society_id,
            instance.bank_account_id,
            instance.uploaded_by_id,
        )
    else:
        # Detect status changes on existing imports
        logger.info(
            "BankStatementImport #%s updated: status=%s, row_count=%s",
            instance.pk,
            instance.import_status,
            instance.row_count,
        )


@receiver(post_delete, sender=BankStatementImport)
def log_bank_statement_import_deletion(sender, instance, **kwargs):
    """
    Log deletion of bank statement imports. This is a critical audit event
    since deleting an import cascades to all its transactions and links.
    """
    logger.warning(
        "BankStatementImport #%s DELETED: file='%s', society=%s, bank_account=%s, "
        "had status=%s with %s transactions. All associated transactions and "
        "reconciliation links are cascade-deleted.",
        instance.pk,
        instance.file_name,
        instance.society_id,
        instance.bank_account_id,
        instance.import_status,
        instance.row_count,
    )