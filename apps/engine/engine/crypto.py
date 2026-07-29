"""Encryption for OAuth refresh tokens.

A refresh token is a permanent key to someone's YouTube channel. It is encrypted at
rest, and the plaintext exists only inside the function that uses it.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import stat
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger

from engine.settings import PLACEHOLDER_SECRETS, get_settings


class DecryptionFailed(Exception):
    """Wrong key, or tampered ciphertext. Never surface the underlying detail."""


#: Where an auto-generated key lives. Under `storage/`, which is already gitignored,
#: so it cannot be committed by accident the way a value in `.env.example` could.
KEY_FILE = ".secret_key"


def _resolve_secret() -> str:
    """The key that protects refresh tokens.

    `STUDIO_SECRET_KEY` wins when it is set to something real. Otherwise a random
    key is generated once and kept in `storage/.secret_key` at 0600.

    The generation exists because the alternative was worse. `.env.example` shipped
    `STUDIO_SECRET_KEY=change-me-32-bytes-minimum-for-token-encryption`, and
    `scripts/setup.sh` copies that file to `.env` — so every install encrypted its
    YouTube refresh tokens, which are permanent access to the channel, under a key
    that is published in this repository. It is 47 characters, so it sailed past the
    length check that was the only guard. Nothing ever prompted anyone to change it.

    Making it fatal instead would have been safe, but it would have put a mandatory
    setup step in front of a tool whose whole point is not having one.
    """
    configured = get_settings().secret_key
    if configured and configured not in PLACEHOLDER_SECRETS:
        if len(configured) < 32:
            raise RuntimeError(
                "STUDIO_SECRET_KEY must be at least 32 characters — "
                "it protects channel refresh tokens"
            )
        return configured

    path = Path(get_settings().storage_root) / KEY_FILE
    if path.is_file():
        existing = path.read_text(encoding="utf-8").strip()
        if len(existing) >= 32:
            return existing
        logger.warning("{} is too short to be a key; regenerating", path)
        # Removed rather than overwritten, so the O_EXCL create below still gets to
        # be the thing that decides who wins.
        path.unlink(missing_ok=True)

    generated = secrets.token_urlsafe(48)
    path.parent.mkdir(parents=True, exist_ok=True)

    # O_CREAT|O_EXCL, so exactly one process can win.
    #
    # The API and the worker are separate processes started together by
    # `docker compose up`, and both call this on their first channel operation. With
    # a plain write, both generated a key and the second overwrote the first —
    # after which every refresh token the first process had already encrypted was
    # permanently undecryptable, and the only symptom is a channel that says it is
    # connected and fails every publish. The loser of the race reads the winner's
    # key instead of writing its own.
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        # Someone got there between the `is_file()` check above and here.
        existing = path.read_text(encoding="utf-8").strip()
        if len(existing) >= 32:
            return existing
        raise RuntimeError(f"{path} exists but is not a usable key; delete it and retry") from None

    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(generated)
    # Belt and braces: O_CREAT honours the umask, so 0o600 can still come out
    # narrower than asked but never wider than the umask allows. Setting it
    # explicitly afterwards is the only way to be certain of the mode.
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    logger.info(
        "generated a channel-encryption key at {} — back it up: without it, "
        "connected channels have to be re-authorised",
        path,
    )
    return generated


@lru_cache
def _cipher() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(_resolve_secret().encode()).digest())
    return Fernet(key)


def reset_cache() -> None:
    """Drop the cached cipher. For tests and for a settings reload."""
    _cipher.cache_clear()


def encrypt(plaintext: str) -> str:
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _cipher().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        # Deliberately opaque: the message reaches logs, and the detail helps nobody
        # except someone probing for it.
        raise DecryptionFailed("stored credential could not be decrypted") from exc
