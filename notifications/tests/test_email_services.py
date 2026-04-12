import pytest
from django.core import mail
from django.contrib.auth import get_user_model

from notifications.models import EmailLog
from notifications.models import EmailQueue
from notifications.models import GlobalEmailSettings
from notifications.models import SocietyEmailSettings
from notifications.crypto import decrypt_email_secret
from notifications.services import process_email_queue
from notifications.services import queue_email
from notifications.services import resolve_email_config
from notifications.services import send_direct_email_message
from societies.models import Society


@pytest.mark.django_db
def test_resolve_email_config_prefers_society_override():
    society = Society.objects.create(name="Green Valley")
    global_settings = GlobalEmailSettings.objects.create(
        smtp_host="smtp.global.test",
        smtp_port=587,
        smtp_username="global-user",
        use_tls=True,
        use_ssl=False,
        default_from_email="Global <global@example.com>",
        default_reply_to="global@example.com",
        active=True,
    )
    global_settings.set_smtp_password("global-secret")
    global_settings.save(update_fields=["smtp_password_encrypted"])

    society_settings = SocietyEmailSettings.objects.create(
        society=society,
        smtp_host="smtp.society.test",
        smtp_port=465,
        smtp_username="society-user",
        use_tls=False,
        use_ssl=True,
        default_from_email="Society <society@example.com>",
        default_reply_to="society@example.com",
        is_active=True,
    )
    society_settings.set_smtp_password("society-secret")
    society_settings.save(update_fields=["smtp_password_encrypted"])

    config = resolve_email_config(
        society_id=society.id,
        email_type=EmailQueue.EmailType.RECEIPT,
    )
    expected_password = "society-secret"  # noqa: S105

    assert config.source == "society"
    assert config.smtp_host == "smtp.society.test"
    assert config.smtp_password == expected_password


@pytest.mark.django_db
def test_resolve_email_config_for_authentication_forces_global():
    society = Society.objects.create(name="Green Valley")
    global_settings = GlobalEmailSettings.objects.create(
        smtp_host="smtp.global.test",
        smtp_port=587,
        smtp_username="global-user",
        use_tls=True,
        use_ssl=False,
        default_from_email="Global <global@example.com>",
        default_reply_to="global@example.com",
        active=True,
    )
    global_settings.set_smtp_password("global-secret")
    global_settings.save(update_fields=["smtp_password_encrypted"])

    SocietyEmailSettings.objects.create(
        society=society,
        smtp_host="smtp.society.test",
        smtp_port=465,
        smtp_username="society-user",
        use_tls=False,
        use_ssl=True,
        default_from_email="Society <society@example.com>",
        default_reply_to="society@example.com",
        is_active=True,
    )

    config = resolve_email_config(
        society_id=society.id,
        email_type=EmailQueue.EmailType.AUTHENTICATION,
    )

    assert config.source == "global"
    assert config.smtp_host == "smtp.global.test"


@pytest.mark.django_db
def test_smtp_password_is_encrypted_at_rest():
    settings_record = GlobalEmailSettings.objects.create(
        smtp_host="smtp.global.test",
        smtp_port=587,
        smtp_username="global-user",
        use_tls=True,
        use_ssl=False,
        default_from_email="Global <global@example.com>",
        default_reply_to="global@example.com",
        active=True,
    )

    settings_record.set_smtp_password("super-secret")
    settings_record.save(update_fields=["smtp_password_encrypted"])
    settings_record.refresh_from_db()
    expected_password = "super-secret"  # noqa: S105

    assert settings_record.smtp_password_encrypted != expected_password
    assert settings_record.smtp_password == expected_password


def test_decrypt_email_secret_accepts_legacy_plaintext_value():
    assert decrypt_email_secret("legacy-plain-password") == "legacy-plain-password"


@pytest.mark.django_db
def test_process_email_queue_sends_email_and_logs_delivery(settings):
    locmem_backend = "django.core.mail.backends.locmem.EmailBackend"
    settings.EMAIL_QUEUE_DELIVERY_BACKEND = locmem_backend
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    society = Society.objects.create(name="Green Valley")
    global_settings = GlobalEmailSettings.objects.create(
        smtp_host="smtp.global.test",
        smtp_port=587,
        smtp_username="global-user",
        use_tls=True,
        use_ssl=False,
        default_from_email="Global <global@example.com>",
        default_reply_to="reply@example.com",
        active=True,
    )
    global_settings.set_smtp_password("global-secret")
    global_settings.save(update_fields=["smtp_password_encrypted"])

    queue_item = queue_email(
        society=society,
        recipient_email="member@example.com",
        subject="Receipt ready",
        body="Your receipt is attached.",
        email_type=EmailQueue.EmailType.RECEIPT,
    )

    processed = process_email_queue()
    queue_item.refresh_from_db()

    assert processed == 1
    assert queue_item.status == EmailQueue.Status.SENT
    assert queue_item.smtp_used["source"] == "global"
    assert queue_item.template is not None
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["member@example.com"]
    assert list(
        EmailLog.objects.filter(email_queue=queue_item).order_by("created_at", "id").values_list("status", flat=True),
    ) == [
        EmailQueue.Status.PENDING,
        EmailQueue.Status.PROCESSING,
        EmailQueue.Status.SENT,
    ]


@pytest.mark.django_db
def test_process_email_queue_retries_when_no_global_config():
    society = Society.objects.create(name="Green Valley")
    queue_item = queue_email(
        society=society,
        recipient_email="member@example.com",
        subject="Receipt ready",
        body="Your receipt is attached.",
        email_type=EmailQueue.EmailType.RECEIPT,
    )

    process_email_queue()
    queue_item.refresh_from_db()

    assert queue_item.status == EmailQueue.Status.RETRY
    assert queue_item.retry_count == 1
    assert "No active global email settings found." in queue_item.error_message


@pytest.mark.django_db
def test_queue_email_registers_template_when_subject_and_body_are_provided():
    queue_item = queue_email(
        recipient_email="member@example.com",
        subject="Welcome",
        body="Hello from template registration.",
        email_type=EmailQueue.EmailType.OTHER,
    )

    assert queue_item.template is not None
    assert queue_item.template.template_name.startswith("auto.other.")
    assert queue_item.template.subject_template == "Welcome"
    assert queue_item.template.body_template == "Hello from template registration."
    assert list(
        EmailLog.objects.filter(email_queue=queue_item).order_by("created_at", "id").values_list("status", flat=True),
    ) == [EmailQueue.Status.PENDING]


@pytest.mark.django_db
def test_send_direct_email_message_queues_and_sends_immediately(settings):
    locmem_backend = "django.core.mail.backends.locmem.EmailBackend"
    settings.EMAIL_QUEUE_DELIVERY_BACKEND = locmem_backend
    settings.EMAIL_BACKEND = locmem_backend
    global_settings = GlobalEmailSettings.objects.create(
        smtp_host="smtp.global.test",
        smtp_port=587,
        smtp_username="global-user",
        use_tls=True,
        use_ssl=False,
        default_from_email="Global <global@example.com>",
        default_reply_to="reply@example.com",
        active=True,
    )
    global_settings.set_smtp_password("global-secret")
    global_settings.save(update_fields=["smtp_password_encrypted"])

    from django.core.mail import EmailMultiAlternatives

    send_direct_email_message(
        EmailMultiAlternatives(
            subject="Verify your account",
            body="Hello from auth mail.",
            to=["member@example.com"],
        ),
        email_type=EmailQueue.EmailType.AUTHENTICATION,
        template_name="account/email/email_confirmation",
        template_subject_template="Verify your account",
        template_body_template="Hello from auth mail.",
    )

    queue_item = EmailQueue.objects.get(recipient_email="member@example.com")
    assert queue_item.status == EmailQueue.Status.SENT
    assert queue_item.template.template_name == "account/email/email_confirmation"
    assert len(mail.outbox) == 1
    assert list(
        EmailLog.objects.filter(email_queue=queue_item).order_by("created_at", "id").values_list("status", flat=True),
    ) == [
        EmailQueue.Status.PENDING,
        EmailQueue.Status.PROCESSING,
        EmailQueue.Status.SENT,
    ]


@pytest.mark.django_db
def test_queue_email_serializes_non_json_context_values():
    user = get_user_model().objects.create_user(
        email="member@example.com",
        password="test-pass-123",
    )

    queue_item = queue_email(
        recipient_email="member@example.com",
        subject="Welcome",
        body="Hello from auth mail.",
        context={"user": user},
        email_type=EmailQueue.EmailType.AUTHENTICATION,
    )

    assert queue_item.context == {
        "user": {
            "model": user._meta.label_lower,
            "pk": user.pk,
            "str": str(user),
        },
    }
