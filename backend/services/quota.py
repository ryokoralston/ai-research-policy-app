"""Per-user daily cap on runs that spend money.

Motivation: the demo instance is reachable by anyone holding an account, and a
single research run costs real API money. Login is already rate limited
(`services.auth.check_login_rate_limit`), but nothing bounded what a signed-in
account could spend once past it.

Off by default: with `DEMO_RUN_QUOTA` unset (or 0) `consume()` is a no-op, so
the production instance behaves exactly as it did before this module existed.
Admins are always exempt — the owner has to be able to demo and debug.
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import get_settings
from database import get_db
from models import RunQuotaCounter, User
from services.auth import get_current_user

logger = logging.getLogger(__name__)

ROLE_ADMIN = "admin"


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def consume(db: Session, user_id: str, limit: int, feature: str) -> int:
    """Claim one run against `user_id`'s daily budget, or raise 429.

    The claim is a single conditional UPDATE (`count < limit`), so two
    concurrent requests cannot both take the last remaining run: the second
    one matches no row and is rejected. Returns the new count on success.
    """
    day = today_utc()

    # Ensure the day's row exists. A concurrent request may win this race; the
    # unique constraint turns that into an IntegrityError we can ignore.
    if db.query(RunQuotaCounter).filter_by(user_id=user_id, day=day).first() is None:
        try:
            db.add(RunQuotaCounter(id=str(uuid.uuid4()), user_id=user_id, day=day, count=0))
            db.commit()
        except IntegrityError:
            db.rollback()

    result = db.execute(
        update(RunQuotaCounter)
        .where(
            RunQuotaCounter.user_id == user_id,
            RunQuotaCounter.day == day,
            RunQuotaCounter.count < limit,
        )
        .values(count=RunQuotaCounter.count + 1, updated_at=datetime.utcnow())
    )
    db.commit()

    if result.rowcount == 0:
        logger.info("Quota exhausted: user=%s feature=%s limit=%d", user_id, feature, limit)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Daily limit of {limit} runs reached for this account. "
                "This is a demo instance — the cap resets at 00:00 UTC."
            ),
        )

    row = db.query(RunQuotaCounter).filter_by(user_id=user_id, day=day).first()
    return row.count if row else limit


def quota_guard(feature: str):
    """Build a FastAPI dependency that charges one run to the acting user."""

    async def _guard(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> None:
        limit = get_settings().demo_run_quota
        if limit <= 0 or current_user.role == ROLE_ADMIN:
            return
        consume(db, current_user.id, limit, feature)

    return _guard
