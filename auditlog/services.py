"""Audit log service for ergonomic audit logging from views.

Provides a helper that extracts request context (society, user, IP, user agent,
request ID, session ID) and creates an AuditLog entry.
"""

from auditlog.models import AuditLog


def log_from_request(
    request,
    *,
    action,
    entity_type,
    entity_id,
    before_value=None,
    after_value=None,
    module=None,
    reason=None,
    duration_ms=None,
):
    """Create an AuditLog entry from request context.

    Extracts society, actor, IP address, user agent, request ID, and session ID
    from the request object automatically.

    Usage:
        from auditlog.services import log_from_request

        log_from_request(
            request,
            action=AuditLog.Action.POST,
            entity_type="voucher",
            entity_id=voucher.pk,
            module="accounting",
        )
    """
    society = getattr(request, "current_society", None)
    actor = getattr(request, "user", None)

    # Extract IP address
    ip_address = request.META.get("HTTP_X_FORWARDED_FOR")
    if ip_address:
        ip_address = ip_address.split(",")[0].strip()
    else:
        ip_address = request.META.get("REMOTE_ADDR")

    # Extract user agent
    user_agent = request.META.get("HTTP_USER_AGENT", "")

    # Extract request ID and session ID
    request_id = getattr(request, "request_id", None)
    session_id = request.session.session_key if hasattr(request, "session") else None

    return AuditLog.log(
        society=society,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        actor=actor,
        before_value=before_value,
        after_value=after_value,
        ip_address=ip_address,
        device_info={"user_agent": user_agent} if user_agent else None,
        module=module,
        reason=reason,
        request_id=request_id,
        session_id=session_id,
        user_agent=user_agent[:500] if user_agent else None,
        duration_ms=duration_ms,
    )
