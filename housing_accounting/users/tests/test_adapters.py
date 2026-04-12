from housing_accounting.users.adapters import AccountAdapter
from notifications.models import EmailQueue


def test_account_adapter_sends_auth_email_via_global_email_service(monkeypatch):
    adapter = AccountAdapter()
    rendered_message = object()
    template = object()
    calls = {}

    def fake_render_mail(template_prefix, email, context):
        return rendered_message

    def fake_ensure_file_email_template(**kwargs):
        calls["template_kwargs"] = kwargs
        return template

    def fake_send_direct_email_message(message, **kwargs):
        calls["message"] = message
        calls["kwargs"] = kwargs

    monkeypatch.setattr(adapter, "render_mail", fake_render_mail)
    monkeypatch.setattr(
        "housing_accounting.users.adapters.ensure_file_email_template",
        fake_ensure_file_email_template,
    )
    monkeypatch.setattr(
        "housing_accounting.users.adapters.send_direct_email_message",
        fake_send_direct_email_message,
    )

    adapter.send_mail("account/email/email_confirmation", "member@example.com", {})

    assert calls["message"] is rendered_message
    assert calls["template_kwargs"]["template_name"] == "account/email/email_confirmation"
    assert calls["kwargs"]["email_type"] == EmailQueue.EmailType.AUTHENTICATION
    assert calls["kwargs"]["template"] is template
