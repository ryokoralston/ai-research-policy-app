"""Reset a user's password directly against the database.

Why this exists: there is no self-service password reset — no email delivery is
configured for account recovery, and `PATCH /api/auth/password` requires the
current password, so a forgotten password locks the account out permanently.
An admin can reset *another* user from /users, but that doesn't help when the
locked-out account is the only admin. This script is the out-of-band recovery
path for that case, and for local development where the throwaway password has
been lost.

It writes the same bcrypt hash the app does (services/user_service.hash_password)
and records a `user.password_reset` audit entry, so a reset performed here shows
up in the Activity Log alongside resets performed through the UI rather than
looking like the password silently changed itself.

Enforces the same 8-character minimum as routers/auth.py and routers/users.py —
without it this script could set a password the app itself would refuse, leaving
an account that can log in but can't change its own password.

Note on lockouts: repeated failed logins are rate limited (5 per 5 minutes per
IP) by an in-memory counter in services/auth.py. That counter lives in the
running server process, so this script cannot clear it — if you are locked out,
either wait for the window to pass or restart the backend.

Safe by default: running without --apply is a dry run (prints what it would
change, writes nothing).

Run from the backend/ directory:
    ./venv/bin/python -m scripts.reset_password --list
    ./venv/bin/python -m scripts.reset_password --email you@example.com
    ./venv/bin/python -m scripts.reset_password --email you@example.com --apply
    ./venv/bin/python -m scripts.reset_password --email you@example.com --password 'chosen-one' --apply
"""
import argparse
import os
import secrets
import string
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from database import SessionLocal
from models.user import User
from services import audit_log
from services.user_service import get_user_by_email, hash_password, verify_password

# Matches the API's minimum (routers/auth.py, routers/users.py).
MIN_PASSWORD_LENGTH = 8

# Generated passwords: no ambiguous glyphs, so one read back over a call or
# copied off a terminal doesn't turn into a second lockout.
_GENERATED_ALPHABET = (
    "".join(c for c in string.ascii_letters + string.digits if c not in "lIO01")
)
_GENERATED_LENGTH = 20


def generate_password() -> str:
    return "".join(secrets.choice(_GENERATED_ALPHABET) for _ in range(_GENERATED_LENGTH))


def list_users(db) -> None:
    users = db.query(User).order_by(User.created_at).all()
    if not users:
        print("No accounts exist. Visit /login — the app will offer the one-time bootstrap form.")
        return
    print(f"{len(users)} account(s):")
    for u in users:
        state = "active" if u.is_active else "INACTIVE"
        print(f"  {u.email}  role={u.role}  {state}")


def reset(email: str, password: str | None, apply: bool) -> None:
    db = SessionLocal()
    try:
        user = get_user_by_email(db, email)
        if user is None:
            print(f"No account with email {email!r}.\n")
            list_users(db)
            sys.exit(1)

        new_password = password or generate_password()
        if len(new_password) < MIN_PASSWORD_LENGTH:
            sys.exit(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters "
                "— the API enforces the same minimum, so a shorter one would "
                "log in but fail every later password change."
            )

        if not apply:
            print(
                f"Dry run: would reset the password for {user.email} "
                f"(role={user.role}, {'active' if user.is_active else 'INACTIVE'}).\n"
                "Nothing has been written. Re-run with --apply to commit."
            )
            if password is None:
                print("A password would be generated and printed at that point.")
            return

        user.password_hash = hash_password(new_password)
        audit_log.record(
            db,
            user=user,
            action="user.password_reset",
            resource_type="user",
            resource_id=user.id,
            detail="reset via scripts/reset_password.py",
        )
        db.commit()

        # Read back through the same check the login path uses, so a bad write
        # surfaces here rather than as a failed login later.
        db.refresh(user)
        if not verify_password(new_password, user.password_hash):
            sys.exit(
                "VERIFICATION FAILED: the stored hash does not match the new password. "
                "Investigate before relying on this reset."
            )

        print(f"Password reset for {user.email}.")
        if password is None:
            print(f"\n  New password: {new_password}\n")
            print("Store it now — it is not written anywhere else and cannot be recovered.")
        if not user.is_active:
            print(
                "\nNote: this account is deactivated, so it still cannot log in. "
                "Reactivate it from the Users page with an admin account."
            )
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--email", help="Account to reset")
    parser.add_argument(
        "--password",
        help="Password to set. Omit to generate a strong one and print it.",
    )
    parser.add_argument("--list", action="store_true", help="List accounts and exit")
    parser.add_argument(
        "--apply", action="store_true", help="Actually write the change (default: dry run)"
    )
    args = parser.parse_args()

    if args.list:
        db = SessionLocal()
        try:
            list_users(db)
        finally:
            db.close()
        return

    if not args.email:
        parser.error("--email is required (or use --list to see the accounts)")

    reset(args.email, args.password, args.apply)


if __name__ == "__main__":
    main()
