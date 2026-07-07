"""Fernet encryption helpers for sensitive gate-ops data (e.g. Person ID numbers).

Mirrors the pattern in ``notifications/crypto.py`` so both apps share the same
key-derivation strategy and env-var override (``GATEOPS_ENCRYPTION_KEY`` /
``EMAIL_SETTINGS_ENCRYPTION_KEY``). When no explicit key is configured, the key
is derived from Django's ``SECRET_KEY`` via SHA-256 + urlsafe base64 encoding,
which is exactly what ``notifications.crypto`` does.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken
from django.conf import settings


class IdNumberDecryptionError(ValueError):
    """Raised when a stored ID number cannot be decrypted with the active key."""


def _get_gateops_crypto_key() -> bytes:
    """Resolve the Fernet key.

    Priority:
      1. ``settings.GATEOPS_ENCRYPTION_KEY`` (explicit per-app override)
      2. ``settings.EMAIL_SETTINGS_ENCRYPTION_KEY`` (shared with notifications)
      3. Derived from ``settings.SECRET_KEY`` (fallback, matches notifications)
    """
    configured_key = getattr(settings, "GATEOPS_ENCRYPTION_KEY", "") or getattr(
        settings, "EMAIL_SETTINGS_ENCRYPTION_KEY", ""
    )
    if configured_key:
        return configured_key.encode("utf-8")

    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def get_id_cipher() -> Fernet:
    return Fernet(_get_gateops_crypto_key())


def encrypt_id_number(raw_value: str) -> str:
    """Encrypt a plaintext ID number. Returns ``""`` for empty input."""
    if not raw_value:
        return ""
    return get_id_cipher().encrypt(raw_value.encode("utf-8")).decode("utf-8")


def _looks_like_fernet_token(value: str) -> bool:
    return value.startswith("gAAAA")


def decrypt_id_number(encrypted_value: str) -> str:
    """Decrypt a stored ID number.

    Non-Fernet values (e.g. legacy plaintext) are returned as-is to keep the
    system resilient during key rotation. Empty input returns ``""``.
    """
    if not encrypted_value:
        return ""
    if not _looks_like_fernet_token(encrypted_value):
        return encrypted_value

    try:
        return get_id_cipher().decrypt(encrypted_value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        msg = "Stored ID number could not be decrypted with the current key."
        raise IdNumberDecryptionError(msg) from exc
