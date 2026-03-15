import pytest

from allauth.account.adapter import DefaultAccountAdapter

from housing_accounting.users.adapters import AccountAdapter
from notifications.services import EmailConfigurationError


def test_account_adapter_falls_back_to_default_backend_on_email_config_error(monkeypatch):
    adapter = AccountAdapter()
    rendered_message = object()
    calls = {"fallback": 0}

    def fake_render_mail(template_prefix, email, context):
        return rendered_message

    def fake_send_direct_email_message(message, *, email_type):
        raise EmailConfigurationError("bad config")

    def fake_super_send_mail(self, template_prefix, email, context):
        calls["fallback"] += 1

    monkeypatch.setattr(adapter, "render_mail", fake_render_mail)
    monkeypatch.setattr(
        "housing_accounting.users.adapters.send_direct_email_message",
        fake_send_direct_email_message,
    )
    monkeypatch.setattr(DefaultAccountAdapter, "send_mail", fake_super_send_mail)

    adapter.send_mail("account/email/email_confirmation", "member@example.com", {})

    assert calls["fallback"] == 1
