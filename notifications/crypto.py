from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken
from django.conf import settings


class EmailSecretDecryptionError(ValueError):
    pass


def _get_email_crypto_key() -> bytes:
    configured_key = getattr(settings, "EMAIL_SETTINGS_ENCRYPTION_KEY", "")
    if configured_key:
        return configured_key.encode("utf-8")

    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def get_email_cipher() -> Fernet:
    return Fernet(_get_email_crypto_key())


def encrypt_email_secret(raw_value: str) -> str:
    if not raw_value:
        return ""
    return get_email_cipher().encrypt(raw_value.encode("utf-8")).decode("utf-8")


def _looks_like_fernet_token(value: str) -> bool:
    return value.startswith("gAAAA")


def decrypt_email_secret(encrypted_value: str) -> str:
    if not encrypted_value:
        return ""
    if not _looks_like_fernet_token(encrypted_value):
        return encrypted_value

    try:
        return get_email_cipher().decrypt(encrypted_value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        msg = "Stored SMTP password could not be decrypted with the current key."
        raise EmailSecretDecryptionError(msg) from exc
