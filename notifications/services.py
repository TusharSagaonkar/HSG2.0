from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import time
from typing import Any

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.mail import get_connection
from django.db import transaction
from django.template import Context
from django.template import Template
from django.utils import timezone

from billing.models import Bill
from notifications.models import EmailLog
from notifications.models import EmailProviderType
from notifications.models import EmailQueue
from notifications.models import EmailTemplate
from notifications.models import GlobalEmailSettings
from notifications.models import ReminderLog
from notifications.models import SocietyEmailSettings
from notifications.crypto import EmailSecretDecryptionError


class EmailConfigurationError(Exception):
    pass


@dataclass(frozen=True)
class ResolvedEmailConfiguration:
    source: str
    source_id: int
    provider_type: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    use_tls: bool
    use_ssl: bool
    from_email: str
    reply_to_email: str
    daily_limit: int | None

    def as_audit_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_id": self.source_id,
            "provider_type": self.provider_type,
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port,
            "smtp_username": self.smtp_username,
            "use_tls": self.use_tls,
            "use_ssl": self.use_ssl,
            "from_email": self.from_email,
            "reply_to_email": self.reply_to_email,
            "daily_limit": self.daily_limit,
        }


def schedule_payment_reminders(*, society, as_of_date, channels=None, upcoming_days=3):
    channels = channels or [
        ReminderLog.Channel.EMAIL,
        ReminderLog.Channel.SMS,
        ReminderLog.Channel.WHATSAPP,
    ]
    schedule_at = timezone.make_aware(datetime.combine(as_of_date, time(hour=9)))
    scheduled = 0
    bills = (
        Bill.objects.filter(
            society=society,
            status__in=(
                Bill.BillStatus.OPEN,
                Bill.BillStatus.PARTIAL,
                Bill.BillStatus.OVERDUE,
            ),
        )
        .select_related("member")
        .order_by("due_date", "id")
    )
    for bill in bills:
        if bill.outstanding_amount <= 0:
            continue
        due_in_days = (bill.due_date - as_of_date).days
        if due_in_days > upcoming_days:
            continue

        for channel in channels:
            exists = ReminderLog.objects.filter(
                society=society,
                member=bill.member,
                bill=bill,
                channel=channel,
                scheduled_for__date=as_of_date,
            ).exists()
            if exists:
                continue
            message = (
                f"Reminder: Bill #{bill.bill_number} "
                f"of amount {bill.outstanding_amount} "
                f"is due on {bill.due_date}."
            )
            with transaction.atomic():
                ReminderLog.objects.create(
                    society=society,
                    member=bill.member,
                    bill=bill,
                    channel=channel,
                    message=message,
                    scheduled_for=schedule_at,
                )
            scheduled += 1
    return scheduled


def resolve_email_config(
    *,
    society=None,
    society_id=None,
    email_type=EmailQueue.EmailType.OTHER,
) -> ResolvedEmailConfiguration:
    force_global = email_type == EmailQueue.EmailType.AUTHENTICATION
    society_settings = None
    effective_society_id = society_id or getattr(society, "id", None)
    if not force_global and effective_society_id:
        society_settings = SocietyEmailSettings.objects.filter(
            society_id=effective_society_id,
            is_active=True,
        ).select_related("society").first()

    if society_settings:
        return _build_resolved_email_config(
            source="society",
            settings_record=society_settings,
        )

    global_settings = GlobalEmailSettings.objects.filter(active=True).first()
    if not global_settings:
        msg = "No active global email settings found."
        raise EmailConfigurationError(msg)

    return _build_resolved_email_config(
        source="global",
        settings_record=global_settings,
    )


def render_email_template(
    template: EmailTemplate,
    context: dict[str, Any] | None = None,
) -> tuple[str, str]:
    template_context = Context(context or {})
    subject = Template(template.subject_template).render(template_context)
    subject = subject.strip().replace("\n", " ")
    body = Template(template.body_template).render(template_context)
    return subject, body


def queue_email(  # noqa: PLR0913
    *,
    recipient_email: str,
    subject: str | None = None,
    body: str | None = None,
    society=None,
    society_id: int | None = None,
    template: EmailTemplate | None = None,
    template_id: int | None = None,
    context: dict[str, Any] | None = None,
    email_type: str = EmailQueue.EmailType.OTHER,
    scheduled_at=None,
    body_html: str = "",
    from_email: str = "",
    reply_to_email: str = "",
) -> EmailQueue:
    if template is None and template_id is not None:
        template = EmailTemplate.objects.get(pk=template_id, is_active=True)

    resolved_subject = subject or ""
    resolved_body = body or ""
    if template:
        resolved_subject, resolved_body = render_email_template(
            template,
            context=context,
        )

    return EmailQueue.objects.create(
        society=society,
        society_id=society_id or getattr(society, "id", None),
        recipient_email=recipient_email,
        subject=resolved_subject,
        body=resolved_body,
        body_html=body_html,
        template=template,
        context=context or {},
        email_type=email_type,
        scheduled_at=scheduled_at or timezone.now(),
        from_email=from_email,
        reply_to_email=reply_to_email,
    )


def send_direct_email_message(
    message: EmailMultiAlternatives,
    *,
    email_type: str,
    society_id: int | None = None,
) -> int:
    config = resolve_email_config(society_id=society_id, email_type=email_type)
    connection = _get_email_connection(config)
    if not message.from_email:
        message.from_email = config.from_email
    if not message.reply_to and config.reply_to_email:
        message.reply_to = [config.reply_to_email]
    message.connection = connection
    return message.send()


def process_email_queue(*, limit: int | None = None, now=None) -> int:
    now = now or timezone.now()
    queryset = EmailQueue.objects.filter(
        scheduled_at__lte=now,
        status__in=(EmailQueue.Status.PENDING, EmailQueue.Status.RETRY),
    ).order_by("scheduled_at", "id")
    if limit is not None:
        queryset = queryset[:limit]

    processed = 0
    for queue_item in queryset:
        _process_single_queue_item(queue_item, now=now)
        processed += 1
    return processed


def _process_single_queue_item(queue_item: EmailQueue, *, now) -> None:
    queue_item.status = EmailQueue.Status.PROCESSING
    queue_item.error_message = ""
    queue_item.save(update_fields=["status", "error_message", "updated_at"])

    try:
        config = resolve_email_config(
            society_id=queue_item.society_id,
            email_type=queue_item.email_type,
        )
        _enforce_daily_limit(config, sent_on=timezone.localdate(now))
        _send_queue_item(queue_item, config)
    except Exception as exc:  # noqa: BLE001
        queue_item.retry_count += 1
        queue_item.error_message = str(exc)
        queue_item.status = _next_queue_status(queue_item.retry_count)
        queue_item.save(
            update_fields=["retry_count", "error_message", "status", "updated_at"],
        )
        EmailLog.objects.create(
            email_queue=queue_item,
            attempt_no=queue_item.retry_count,
            smtp_host=queue_item.smtp_used.get("smtp_host", ""),
            response=str(exc),
            status=queue_item.status,
        )
        return

    queue_item.status = EmailQueue.Status.SENT
    queue_item.sent_at = now
    queue_item.error_message = ""
    queue_item.save(update_fields=["status", "sent_at", "error_message", "updated_at"])
    EmailLog.objects.create(
        email_queue=queue_item,
        attempt_no=queue_item.retry_count + 1,
        smtp_host=queue_item.smtp_used.get("smtp_host", ""),
        response="sent",
        status=queue_item.status,
    )


def _send_queue_item(
    queue_item: EmailQueue,
    config: ResolvedEmailConfiguration,
) -> None:
    if config.provider_type != EmailProviderType.SMTP:
        msg = f"Provider {config.provider_type} is not supported yet."
        raise EmailConfigurationError(msg)

    connection = _get_email_connection(config)
    from_email = queue_item.from_email or config.from_email
    reply_to = queue_item.reply_to_email or config.reply_to_email
    message = EmailMultiAlternatives(
        subject=queue_item.subject,
        body=queue_item.body,
        from_email=from_email,
        to=[queue_item.recipient_email],
        reply_to=[reply_to] if reply_to else None,
        connection=connection,
    )
    if queue_item.body_html:
        message.attach_alternative(queue_item.body_html, "text/html")
    message.send()
    queue_item.smtp_used = config.as_audit_dict()
    queue_item.from_email = from_email
    queue_item.reply_to_email = reply_to
    queue_item.save(
        update_fields=["smtp_used", "from_email", "reply_to_email", "updated_at"],
    )


def _get_email_connection(config: ResolvedEmailConfiguration):
    backend_path = getattr(
        settings,
        "EMAIL_QUEUE_DELIVERY_BACKEND",
        settings.EMAIL_BACKEND,
    )
    if backend_path == "django.core.mail.backends.smtp.EmailBackend":
        return get_connection(
            backend=backend_path,
            fail_silently=False,
            host=config.smtp_host,
            port=config.smtp_port,
            username=config.smtp_username,
            password=config.smtp_password,
            use_tls=config.use_tls,
            use_ssl=config.use_ssl,
            timeout=settings.EMAIL_TIMEOUT,
        )
    return get_connection(backend=backend_path, fail_silently=False)


def _enforce_daily_limit(config: ResolvedEmailConfiguration, *, sent_on) -> None:
    if not config.daily_limit:
        return
    sent_count = EmailQueue.objects.filter(
        status=EmailQueue.Status.SENT,
        sent_at__date=sent_on,
        smtp_used__source=config.source,
        smtp_used__source_id=config.source_id,
    ).count()
    if sent_count >= config.daily_limit:
        msg = "Daily email limit reached for the selected configuration."
        raise EmailConfigurationError(msg)


def _next_queue_status(retry_count: int) -> str:
    max_retries = getattr(settings, "EMAIL_QUEUE_MAX_RETRIES", 3)
    if retry_count >= max_retries:
        return EmailQueue.Status.FAILED
    return EmailQueue.Status.RETRY


def _build_resolved_email_config(*, source: str, settings_record) -> ResolvedEmailConfiguration:
    try:
        smtp_password = settings_record.smtp_password
    except EmailSecretDecryptionError as exc:
        msg = (
            "Stored SMTP password could not be read. "
            "Open email settings and save the password again."
        )
        raise EmailConfigurationError(msg) from exc

    return ResolvedEmailConfiguration(
        source=source,
        source_id=settings_record.id,
        provider_type=settings_record.provider_type,
        smtp_host=settings_record.smtp_host,
        smtp_port=settings_record.smtp_port,
        smtp_username=settings_record.smtp_username,
        smtp_password=smtp_password,
        use_tls=settings_record.use_tls,
        use_ssl=settings_record.use_ssl,
        from_email=settings_record.default_from_email,
        reply_to_email=settings_record.default_reply_to,
        daily_limit=settings_record.daily_limit,
    )
