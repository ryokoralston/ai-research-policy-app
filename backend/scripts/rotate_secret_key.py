"""Rotate SECRET_ENCRYPTION_KEY: re-encrypt every stored secret column under
a new Fernet key.

Why this exists: services/secret_crypto.py's SECRET_ENCRYPTION_KEY can never
be silently rotated by just changing the env var — decrypt_secret() silently
returns the stored ciphertext unchanged on a key mismatch (so callers don't
crash on a bad key), which means flipping the key without re-encrypting first
leaves every existing secret (API keys, SMTP password) permanently
undecryptable garbage. This script decrypts every secret column with the OLD
key and re-encrypts it with the NEW key, in one all-or-nothing pass: if any
row fails to decrypt with --old-key, it aborts before writing anything.

Intended use: the app-ownership handoff (see README.md's "Secret key
rotation" section) — generate a new key for the incoming owner, run this
script against the production database with both keys, update
SECRET_ENCRYPTION_KEY in Render's dashboard to the new value, and only then
consider the old key retired.

Not touched by this script: auth tokens (services/auth.py) reuse the same
Fernet key but are short-lived and never stored, so rotating the key simply
means every currently-logged-in user's token stops verifying and they need
to log in again afterward — expected, not a bug, and nothing to migrate.

Safe by default: running with no flags is a dry run (prints what it would
change, writes nothing). Pass --apply to actually commit.

Run from the backend/ directory:
    ./venv/bin/python -m scripts.rotate_secret_key --old-key <fernet-key> --new-key <fernet-key>
    ./venv/bin/python -m scripts.rotate_secret_key --old-key <fernet-key> --new-key <fernet-key> --apply
"""
import argparse
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text

from database import SessionLocal
from services.secret_crypto import _PREFIX

# (table, column) pairs — kept in sync with database.py's encrypt_legacy_secrets,
# the only other place that enumerates every encrypted-at-rest column.
SECRET_COLUMNS = [
    ("model_settings", "anthropic_api_key"),
    ("model_settings", "openai_api_key"),
    ("digest_settings", "smtp_password"),
]


def _decrypt_with(key: bytes, stored_value: str) -> str:
    """Raises cryptography.fernet.InvalidToken if `key` is wrong — deliberately
    not swallowed, unlike secret_crypto.decrypt_secret(), because this script
    must know for certain whether --old-key was correct rather than silently
    treating a wrong key as "nothing to decrypt"."""
    token = stored_value[len(_PREFIX):]
    return Fernet(key).decrypt(token.encode()).decode()


def _encrypt_with(key: bytes, plaintext: str) -> str:
    return _PREFIX + Fernet(key).encrypt(plaintext.encode()).decode()


def rotate(old_key: str, new_key: str, apply: bool) -> None:
    old_key_bytes = old_key.encode()
    new_key_bytes = new_key.encode()
    # Validate both are well-formed Fernet keys before touching the DB.
    Fernet(old_key_bytes)
    Fernet(new_key_bytes)

    db = SessionLocal()
    planned: list[tuple[str, str, object, str]] = []  # (table, col, row_id, new_value)
    skipped = 0

    for table, col in SECRET_COLUMNS:
        try:
            rows = db.execute(text(f"SELECT id, {col} FROM {table}")).fetchall()
        except Exception:
            continue  # table may not exist yet
        for row_id, value in rows:
            if not value:
                continue
            if not value.startswith(_PREFIX):
                print(f"  SKIP  {table}.{col} id={row_id}: legacy plaintext (not encrypted) — left as-is")
                skipped += 1
                continue
            try:
                plaintext = _decrypt_with(old_key_bytes, value)
            except InvalidToken:
                print(
                    f"\nABORT: {table}.{col} id={row_id} does not decrypt with --old-key.\n"
                    "Nothing has been written. Check the key and try again."
                )
                sys.exit(1)
            new_value = _encrypt_with(new_key_bytes, plaintext)
            planned.append((table, col, row_id, new_value))

    verb = "UPDATE" if apply else "WOULD UPDATE"
    for table, col, row_id, _ in planned:
        print(f"  {verb}  {table}.{col} id={row_id}")

    if not apply:
        print(
            f"\nDry run: {len(planned)} value(s) would be re-encrypted, "
            f"{skipped} legacy-plaintext value(s) would be left untouched.\n"
            "Re-run with --apply to write changes."
        )
        return

    for table, col, row_id, new_value in planned:
        db.execute(text(f"UPDATE {table} SET {col} = :v WHERE id = :id"), {"v": new_value, "id": row_id})
    db.commit()

    # Verify every rotated value round-trips under the new key before declaring success.
    for table, col, row_id, expected_new_value in planned:
        actual = db.execute(
            text(f"SELECT {col} FROM {table} WHERE id = :id"), {"id": row_id}
        ).scalar()
        if actual != expected_new_value:
            sys.exit(
                f"VERIFICATION FAILED for {table}.{col} id={row_id} — stored value doesn't match "
                "what was written. Investigate before trusting the new key."
            )

    print(
        f"\nDone: {len(planned)} value(s) re-encrypted under the new key, "
        f"{skipped} legacy-plaintext value(s) left untouched.\n"
        "Next: set SECRET_ENCRYPTION_KEY to --new-key wherever the app runs, and restart it.\n"
        "Everyone currently logged in will need to log in again (expected)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--old-key", required=True, help="Current SECRET_ENCRYPTION_KEY value")
    parser.add_argument("--new-key", required=True, help="New SECRET_ENCRYPTION_KEY value to rotate to")
    parser.add_argument("--apply", action="store_true", help="Actually write changes (default: dry run)")
    args = parser.parse_args()

    if args.old_key == args.new_key:
        sys.exit("--old-key and --new-key are identical — nothing to rotate.")

    rotate(args.old_key, args.new_key, args.apply)


if __name__ == "__main__":
    main()
