import uuid
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class RunQuotaCounter(Base):
    """Billable runs a user has started on one UTC day.

    Deliberately separate from `usage_events`: that table is written from deep
    inside the provider clients via a ContextVar that only `research` sets, so
    rows for reports/debates/analyses carry no user id. A quota has to refuse
    the request before any model call happens, so it counts here instead.
    """

    __tablename__ = "run_quota_counters"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_run_quota_user_day"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    day: Mapped[str] = mapped_column(String, nullable=False)  # 'YYYY-MM-DD', UTC
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
