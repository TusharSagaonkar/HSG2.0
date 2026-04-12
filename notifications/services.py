from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import time
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.mail import get_connection
from django.db import transaction
from django.db import models
from django.template import Context
from django.template import Template
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
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


DEFAULT_EMAIL_TEMPLATE_DEFINITIONS = (
    {
        "template_name": "system.test_email",
        "subject_template": "Housing Accounting test email",
        "body_template": (
            "Hello,\n\n"
            "This is a test email from Housing Accounting.\n\n"
            "Recipient: {{ recipient_email }}\n"
            "Triggered at: {{ triggered_at }}\n"
            "Environment: {{ environment_name }}\n\n"
            "If you received this email, queueing, template registration, and delivery logs are working.\n"
        ),
        "variables": ["recipient_email", "triggered_at", "environment_name"],
    },
    {
        "template_name": "billing.bill_generated",
        "subject_template": "New bill {{ bill_number }} for {{ society_name }}",
        "body_template": (
            "Hello {{ member_name }},\n\n"
            "A new bill has been generated for {{ society_name }}.\n"
            "Bill number: {{ bill_number }}\n"
            "Due date: {{ due_date }}\n"
            "Amount: {{ amount }}\n\n"
            "Please review and pay before the due date.\n"
        ),
        "variables": ["member_name", "society_name", "bill_number", "due_date", "amount"],
    },
    {
        "template_name": "receipts.payment_receipt",
        "subject_template": "Receipt {{ receipt_id }} for {{ society_name }}",
        "body_template": (
            "Hello {{ member_name }},\n\n"
            "Your payment has been recorded successfully.\n"
            "Receipt ID: {{ receipt_id }}\n"
            "Amount received: {{ amount }}\n"
            "Receipt date: {{ receipt_date }}\n"
            "Society: {{ society_name }}\n\n"
            "Thank you.\n"
        ),
        "variables": ["member_name", "receipt_id", "amount", "receipt_date", "society_name"],
    },
    {
        "template_name": "reminders.payment_due",
        "subject_template": "Payment reminder for bill {{ bill_number }}",
        "body_template": (
            "Hello {{ member_name }},\n\n"
            "This is a reminder that bill {{ bill_number }} for {{ society_name }} is due on {{ due_date }}.\n"
            "Outstanding amount: {{ outstanding_amount }}\n\n"
            "Please arrange payment at the earliest.\n"
        ),
        "variables": [
            "member_name",
            "bill_number",
            "society_name",
            "due_date",
            "outstanding_amount",
        ],
    },
    {
        "template_name": "notices.general_notice",
        "subject_template": "{{ notice_title }}",
        "body_template": (
            "Hello {{ member_name }},\n\n"
            "{{ notice_body }}\n\n"
            "Regards,\n"
            "{{ society_name }}\n"
        ),
        "variables": ["member_name", "notice_title", "notice_body", "society_name"],
    },
    {
        "template_name": "authentication.user_created",
        "subject_template": "Welcome to Housing Accounting System",
        "body_template": (
            "Hello {{ user_name }},\n\n"
            "Your account has been created in Housing Accounting System.\n\n"
            "Society: {{ society_name }}\n"
            "Email: {{ user_email }}\n"
            "Role: {{ user_role }}\n\n"
            "Please verify your email by clicking the link below:\n"
            "{{ verification_link }}\n\n"
            "This link will expire in 24 hours.\n\n"
            "You can then login with your email and password.\n\n"
            "Regards,\n"
            "{{ society_name }}\n"
        ),
        "variables": [
            "user_name",
            "society_name",
            "user_email",
            "user_role",
            "verification_link",
        ],
    },
)


ALLAUTH_TEMPLATE_DEFINITIONS = (
    {
        "template_name": "account/email/email_confirmation",
        "subject_template_name": "account/email/email_confirmation_subject.txt",
        "body_template_name": "account/email/email_confirmation_message.txt",
        "variables": ["user", "current_site", "activate_url", "code"],
    },
    {
        "template_name": "account/email/email_confirmation_signup",
        "subject_template_name": "account/email/email_confirmation_signup_subject.txt",
        "body_template_name": "account/email/email_confirmation_signup_message.txt",
        "variables": ["user", "current_site", "activate_url", "code"],
    },
    {
        "template_name": "account/email/email_confirm",
        "subject_template_name": "account/email/email_confirm_subject.txt",
        "body_template_name": "account/email/email_confirm_message.txt",
        "variables": ["user", "current_site", "activate_url", "code"],
    },
    {
        "template_name": "account/email/password_reset_key",
        "subject_template_name": "account/email/password_reset_key_subject.txt",
        "body_template_name": "account/email/password_reset_key_message.txt",
        "variables": ["current_site", "password_reset_url", "username"],
    },
    {
        "template_name": "account/email/password_reset",
        "subject_template_name": "account/email/password_reset_subject.txt",
        "body_template_name": "account/email/password_reset_message.txt",
        "variables": ["current_site"],
    },
    {
        "template_name": "account/email/password_reset_code",
        "subject_template_name": "account/email/password_reset_code_subject.txt",
        "body_template_name": "account/email/password_reset_code_message.txt",
        "variables": ["current_site", "password_reset_code"],
    },
    {
        "template_name": "account/email/login_code",
        "subject_template_name": "account/email/login_code_subject.txt",
        "body_template_name": "account/email/login_code_message.txt",
        "variables": ["current_site", "code"],
    },
    {
        "template_name": "account/email/password_changed",
        "subject_template_name": "account/email/password_changed_subject.txt",
        "body_template_name": "account/email/password_changed_message.txt",
        "variables": ["current_site", "ip", "user_agent", "timestamp"],
    },
)


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
    if global_settings:
        return _build_resolved_email_config(
            source="global",
            settings_record=global_settings,
        )

    msg = "No active global email settings found."
    raise EmailConfigurationError(msg)


def render_email_template(
    template: EmailTemplate,
    context: dict[str, Any] | None = None,
) -> tuple[str, str]:
    template_context = Context(context or {})
    subject = Template(template.subject_template).render(template_context)
    subject = subject.strip().replace("\n", " ")
    body = Template(template.body_template).render(template_context)
    return subject, body


def ensure_email_template(
    *,
    template_name: str,
    subject_template: str,
    body_template: str,
    variables: list[str] | None = None,
) -> EmailTemplate:
    template, _ = EmailTemplate.objects.update_or_create(
        template_name=template_name,
        defaults={
            "subject_template": subject_template,
            "body_template": body_template,
            "variables": variables or [],
            "is_active": True,
        },
    )
    return template


def ensure_file_email_template(
    *,
    template_name: str,
    subject_template_name: str,
    body_template_name: str,
    variables: list[str] | None = None,
) -> EmailTemplate:
    return ensure_email_template(
        template_name=template_name,
        subject_template=_load_template_source(subject_template_name),
        body_template=_load_template_source(body_template_name),
        variables=variables,
    )


def ensure_default_email_templates() -> list[EmailTemplate]:
    templates = []
    for definition in DEFAULT_EMAIL_TEMPLATE_DEFINITIONS:
        templates.append(
            ensure_email_template(
                template_name=definition["template_name"],
                subject_template=definition["subject_template"],
                body_template=definition["body_template"],
                variables=definition["variables"],
            ),
        )
    for definition in ALLAUTH_TEMPLATE_DEFINITIONS:
        templates.append(
            ensure_file_email_template(
                template_name=definition["template_name"],
                subject_template_name=definition["subject_template_name"],
                body_template_name=definition["body_template_name"],
                variables=definition["variables"],
            ),
        )
    return templates


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
    template_name: str | None = None,
    template_subject_template: str | None = None,
    template_body_template: str | None = None,
    template_variables: list[str] | None = None,
    process_now: bool = False,
) -> EmailQueue:
    if template is None and template_id is not None:
        template = EmailTemplate.objects.get(pk=template_id, is_active=True)
    if template is None and template_name and template_subject_template and template_body_template:
        template = ensure_email_template(
            template_name=template_name,
            subject_template=template_subject_template,
            body_template=template_body_template,
            variables=template_variables,
        )
    elif template is None and template_name:
        template = EmailTemplate.objects.get(template_name=template_name, is_active=True)

    resolved_subject = subject or ""
    resolved_body = body or ""
    if template is None:
        template = ensure_email_template(
            template_name=template_name or _build_auto_template_name(
                email_type=email_type,
                subject=resolved_subject,
                body=resolved_body,
                body_html=body_html,
            ),
            subject_template=resolved_subject,
            body_template=resolved_body,
            variables=template_variables,
        )
    if not resolved_subject and not resolved_body:
        resolved_subject, resolved_body = render_email_template(
            template,
            context=context,
        )
    serialized_context = _serialize_email_context(context or {})

    queue_item = EmailQueue.objects.create(
        society=society,
        society_id=society_id or getattr(society, "id", None),
        recipient_email=recipient_email,
        subject=resolved_subject,
        body=resolved_body,
        body_html=body_html,
        template=template,
        context=serialized_context,
        email_type=email_type,
        scheduled_at=scheduled_at or timezone.now(),
        from_email=from_email,
        reply_to_email=reply_to_email,
    )
    _log_email_event(
        queue_item,
        attempt_no=0,
        status=queue_item.status,
        response=f"Queued using template '{template.template_name}'.",
    )
    if process_now:
        _process_single_queue_item(queue_item, now=timezone.now())
        queue_item.refresh_from_db()
    return queue_item


def send_direct_email_message(
    message: EmailMultiAlternatives,
    *,
    email_type: str,
    society_id: int | None = None,
    template: EmailTemplate | None = None,
    template_name: str | None = None,
    template_subject_template: str | None = None,
    template_body_template: str | None = None,
    template_variables: list[str] | None = None,
    context: dict[str, Any] | None = None,
) -> int:
    sent_count = 0
    for recipient_email in message.to:
        queue_item = queue_email(
            recipient_email=recipient_email,
            subject=message.subject,
            body=message.body,
            society_id=society_id,
            template=template,
            context=context,
            email_type=email_type,
            scheduled_at=timezone.now(),
            body_html=_extract_body_html(message),
            from_email=message.from_email or "",
            reply_to_email=message.reply_to[0] if message.reply_to else "",
            template_name=template_name,
            template_subject_template=template_subject_template,
            template_body_template=template_body_template,
            template_variables=template_variables,
            process_now=True,
        )
        if queue_item.status != EmailQueue.Status.SENT:
            msg = queue_item.error_message or "Email delivery failed."
            raise EmailConfigurationError(msg)
        sent_count += 1
    return sent_count


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
    _log_email_event(
        queue_item,
        attempt_no=queue_item.retry_count + 1,
        status=queue_item.status,
        response="Email processing started.",
    )

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
        _log_email_event(
            queue_item,
            attempt_no=queue_item.retry_count,
            status=queue_item.status,
            response=str(exc),
        )
        return

    queue_item.status = EmailQueue.Status.SENT
    queue_item.sent_at = now
    queue_item.error_message = ""
    queue_item.save(update_fields=["status", "sent_at", "error_message", "updated_at"])
    _log_email_event(
        queue_item,
        attempt_no=queue_item.retry_count + 1,
        status=queue_item.status,
        response="sent",
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


def _build_auto_template_name(
    *,
    email_type: str,
    subject: str,
    body: str,
    body_html: str,
) -> str:
    digest = hashlib.sha1(
        f"{email_type}\n{subject}\n{body}\n{body_html}".encode("utf-8"),
    ).hexdigest()[:12]
    return f"auto.{email_type.lower()}.{digest}"


def _extract_body_html(message: EmailMultiAlternatives) -> str:
    for alternative in getattr(message, "alternatives", []):
        mimetype = getattr(alternative, "mimetype", None)
        content = getattr(alternative, "content", None)
        if mimetype is None and isinstance(alternative, tuple) and len(alternative) == 2:
            content, mimetype = alternative
        if mimetype == "text/html":
            return content or ""
    return ""


def _serialize_email_context(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, models.Model):
        return {
            "model": value._meta.label_lower,
            "pk": value.pk,
            "str": str(value),
        }
    if isinstance(value, dict):
        return {
            str(key): _serialize_email_context(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple | set):
        return [_serialize_email_context(item) for item in value]
    return str(value)


def _load_template_source(template_name: str) -> str:
    template = get_template(template_name)
    template_origin = getattr(template, "origin", None)
    if template_origin and getattr(template_origin, "template_name", None):
        return template_origin.loader.get_contents(template_origin)

    inner_template = getattr(template, "template", template)
    source = getattr(inner_template, "source", "")
    if source:
        return source

    origin = getattr(inner_template, "origin", None)
    if origin:
        return origin.loader.get_contents(origin)

    msg = f"Template source could not be loaded for '{template_name}'."
    raise TemplateDoesNotExist(msg)


def _log_email_event(
    queue_item: EmailQueue,
    *,
    attempt_no: int,
    status: str,
    response: str,
) -> EmailLog:
    return EmailLog.objects.create(
        email_queue=queue_item,
        attempt_no=attempt_no,
        smtp_host=queue_item.smtp_used.get("smtp_host", ""),
        response=response,
        status=status,
    )


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
