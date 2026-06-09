"""
Transparent encryption-at-rest for secret columns (API keys, SMTP password).

Secrets are stored in the DB as ``enc:v1:<fernet-token>``. Legacy plaintext
values (no prefix) are returned unchanged on read, so existing databases keep
working and are upgraded to ciphertext the next time the row is written.

Encryption key, resolved in order:
  1. ``SECRET_ENCRYPTION_KEY`` env var — a urlsafe-base64 Fernet key.
  2. A key file auto-generated next to the database (``<db_dir>/.secret_key``),
     created with 0600 permissions on first use. On Render this sits on the
     mounted persistent disk alongside the SQLite DB, so it survives redeploys.
"""
from __future__ import annotations

import functools
import os

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from config import get_settings

_PREFIX = "enc:v1:"


def _key_path() -> str:
    """Return the on-disk key file path, kept next to the SQLite database."""
    db_url = get_settings().database_url
    if db_url.startswith("sqlite"):
        db_path = db_url.replace("sqlite:///", "")
        base = os.path.dirname(os.path.abspath(db_path))
    else:
        base = os.path.abspath("./data")
    return os.path.join(base, ".secret_key")


@functools.lru_cache(maxsize=1)
def _fernet() -> Fernet:
    env_key = os.environ.get("SECRET_ENCRYPTION_KEY", "").strip()
    if env_key:
        return Fernet(env_key.encode())

    path = _key_path()
    if os.path.exists(path):
        with open(path, "rb") as f:
            return Fernet(f.read().strip())

    # First run with no key configured: generate and persist one (0600).
    key = Fernet.generate_key()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    """Encrypt a plaintext secret. No-op for empty or already-encrypted values."""
    if not value or value.startswith(_PREFIX):
        return value
    token = _fernet().encrypt(value.encode()).decode()
    return _PREFIX + token


def decrypt_secret(value: str) -> str:
    """Decrypt a stored secret. Returns legacy plaintext / empty values as-is."""
    if not value or not value.startswith(_PREFIX):
        return value
    token = value[len(_PREFIX):]
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        # Wrong/rotated key — return stored value rather than crash the request.
        return value


class EncryptedString(TypeDecorator):
    """A SQLAlchemy String that transparently encrypts at rest.

    The column type in SQLite is unchanged (TEXT), so no migration is needed:
    existing plaintext rows stay readable and are re-encrypted on next write.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        return encrypt_secret(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        return decrypt_secret(value)
