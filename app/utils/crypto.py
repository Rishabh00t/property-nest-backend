"""Encrypt per-builder secrets (WhatsApp access tokens, app secrets) at rest.

Values encrypted here are stored with an "enc:" prefix, so rows written before
ENCRYPTION_KEY was configured (plain text) still read back correctly.
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_PREFIX = "enc:"
_fernet: Fernet | None = None


def _get_fernet() -> Fernet | None:
    global _fernet
    if not settings.encryption_key:
        return None
    if _fernet is None:
        _fernet = Fernet(settings.encryption_key.encode())
    return _fernet


def encrypt_secret(value: str) -> str:
    fernet = _get_fernet()
    if fernet is None:
        if settings.app_env != "development":
            print("WARNING: ENCRYPTION_KEY is not set; storing a WhatsApp secret in plain text.")
        return value
    return _PREFIX + fernet.encrypt(value.encode()).decode()


def decrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    if not value.startswith(_PREFIX):
        return value
    fernet = _get_fernet()
    if fernet is None:
        raise RuntimeError("ENCRYPTION_KEY is required to read encrypted WhatsApp credentials.")
    try:
        return fernet.decrypt(value[len(_PREFIX):].encode()).decode()
    except InvalidToken as error:
        raise RuntimeError("Stored WhatsApp credential could not be decrypted (wrong ENCRYPTION_KEY?).") from error


def mask_secret(value: str) -> str:
    if not value:
        return ""
    return f"{'•' * 8}{value[-4:]}" if len(value) > 4 else "•" * 8
