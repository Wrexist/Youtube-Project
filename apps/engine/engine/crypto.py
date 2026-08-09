"""Encryption for OAuth refresh tokens.

A refresh token is a permanent key to someone's YouTube channel. It is encrypted at
rest, and the plaintext exists only inside the function that uses it.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger

from engine import secretfile
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
    # explicitly afterwards is the only way to be certain of the mode — and on
    # Windows the mode is not the mechanism at all, which is what `restrict` is for.
    secretfile.restrict(path)
    logger.info(
        "generated a channel-encryption key at {} — back it up: without it, "
        "connected channels have to be re-authorised",
        path,
    )
    return generated


#: Fixed salt. A per-install random salt would be strictly better and is not
#: possible here without a migration: the salt has to be identical on every process
#: that decrypts, and it would have to be stored next to the key file it is meant to
#: strengthen. Domain-separated instead, which is what actually matters — this
#: derivation cannot collide with another use of the same secret.
_KDF_SALT = b"studio.channel-credentials.v1"

#: scrypt parameters. n=2**14 with r=8, p=1 is roughly 16MB and a few tens of
#: milliseconds — negligible here (the result is cached for the process's life) and
#: the difference between a leaked database being brute-forceable and not.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


@lru_cache
def _cipher() -> Fernet:
    """The Fernet key, derived from the configured secret.

    Was a bare `sha256(secret)`. For the auto-generated 48-byte key that is fine —
    it already has far more entropy than any derivation adds. It is *not* fine for a
    human-chosen `STUDIO_SECRET_KEY`, which the 32-character minimum permits to be a
    passphrase: a single SHA-256 is a few hundred million guesses a second on a
    consumer GPU, and what it protects is permanent access to the operator's YouTube
    channel. scrypt makes the same guess list cost orders of magnitude more.
    """
    derived = hashlib.scrypt(
        _resolve_secret().encode(),
        salt=_KDF_SALT,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
        maxmem=64 * 1024 * 1024,
    )
    return Fernet(base64.urlsafe_b64encode(derived))


@lru_cache
def _legacy_cipher() -> Fernet:
    """The pre-scrypt derivation, kept only so existing channels keep working.

    Changing a KDF rewrites every key it derives. Without this, upgrading would make
    every stored refresh token permanently undecryptable, and the symptom is the one
    this module already warns about: a channel that reports itself connected and
    fails every publish. Read-only — nothing is ever encrypted under it again.
    """
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(_resolve_secret().encode()).digest()))


def reset_cache() -> None:
    """Drop the cached ciphers. For tests and for a settings reload."""
    _cipher.cache_clear()
    _legacy_cipher.cache_clear()


def encrypt(plaintext: str) -> str:
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt, accepting credentials written before the KDF changed.

    A legacy hit is logged once per call rather than silently upgraded: re-encrypting
    here would mean a read path performing a write, and the caller that owns the row
    is the one that should do it. `needs_reencryption` lets it ask.
    """
    try:
        return _cipher().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        pass

    try:
        plaintext = _legacy_cipher().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        # Deliberately opaque: the message reaches logs, and the detail helps nobody
        # except someone probing for it.
        raise DecryptionFailed("stored credential could not be decrypted") from exc

    logger.info("credential is still under the pre-scrypt key; re-save it to upgrade")
    return plaintext


def needs_reencryption(ciphertext: str) -> bool:
    """Whether this value is still under the legacy key.

    Raises `DecryptionFailed` for a value neither key opens. Returning `True` there
    would tell a caller to migrate a credential that cannot be read at all, and the
    migration would then write back nothing.
    """
    try:
        _cipher().decrypt(ciphertext.encode())
    except InvalidToken:
        decrypt(ciphertext)  # raises DecryptionFailed if the legacy key fails too
        return True
    return False
