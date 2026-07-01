"""Tests for services/secret_crypto.py — encryption at rest for secrets.

Sets SECRET_ENCRYPTION_KEY before import so no key file is written to disk.

Run from the backend directory:
    ./venv/bin/python -m tests.test_secret_crypto
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

from cryptography.fernet import Fernet

os.environ["SECRET_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

from services.secret_crypto import _PREFIX, decrypt_secret, encrypt_secret


def test_round_trip():
    secret = "sk-ant-super-secret-key"
    stored = encrypt_secret(secret)
    assert stored.startswith(_PREFIX), stored
    assert secret not in stored, "ciphertext must not contain the plaintext"
    assert decrypt_secret(stored) == secret


def test_encrypt_is_idempotent():
    once = encrypt_secret("value")
    twice = encrypt_secret(once)
    assert twice == once, "already-encrypted values must pass through unchanged"


def test_empty_values_pass_through():
    assert encrypt_secret("") == ""
    assert decrypt_secret("") == ""


def test_legacy_plaintext_passes_through_decrypt():
    assert decrypt_secret("legacy-plaintext-password") == "legacy-plaintext-password"


def test_invalid_token_returns_stored_value():
    """Wrong/rotated key must not crash the request — stored value comes back."""
    bogus = _PREFIX + "not-a-real-fernet-token"
    assert decrypt_secret(bogus) == bogus


# ── Test runner ───────────────────────────────────────────────────────────────

_PASSED: list[str] = []
_FAILED: list[str] = []


def _run(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print(f"  PASS  {name}")
    except Exception as exc:
        _FAILED.append(name)
        print(f"  FAIL  {name}: {exc}")


if __name__ == "__main__":
    print("\nRunning secret_crypto tests...\n")

    _run("encrypt/decrypt round trip", test_round_trip)
    _run("encrypt is idempotent", test_encrypt_is_idempotent)
    _run("empty values pass through", test_empty_values_pass_through)
    _run("legacy plaintext passes through decrypt", test_legacy_plaintext_passes_through_decrypt)
    _run("invalid token returns stored value", test_invalid_token_returns_stored_value)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
