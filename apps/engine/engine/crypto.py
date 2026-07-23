"""Encryption for OAuth refresh tokens.

A refresh token is a permanent key to someone's YouTube channel. It is encrypted at
rest, and the plaintext exists only inside the function that uses it.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from engine.settings import get_settings


class DecryptionFailed(Exception):
    """Wrong key, or tampered ciphertext. Never surface the underlying detail."""


@lru_cache
def _cipher() -> Fernet:
    secret = get_settings().secret_key
    if len(secret) < 32:
        raise RuntimeError(
            "STUDIO_SECRET_KEY must be at least 32 characters — it protects channel refresh tokens"
        )
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _cipher().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        # Deliberately opaque: the message reaches logs, and the detail helps nobody
        # except someone probing for it.
        raise DecryptionFailed("stored credential could not be decrypted") from exc
