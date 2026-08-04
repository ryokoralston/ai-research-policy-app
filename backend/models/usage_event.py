import uuid
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class UsageEvent(Base):
    """One billable call to an external provider.

    Recorded so the cost of a feature can be answered from data rather than
    estimated — the free-tier limits and any later billing both depend on
    knowing what a single research run actually consumes. Until this table
    existed, token counts were only ever written to the application log, and
    the research pipeline (generate_json / generate_text / stream_text) didn't
    log them at all.

    One row per provider call rather than per user action: a single research
    run fans out into query decomposition, one summary per source, and one or
    more syntheses, each potentially on a different model. Aggregating at write
    time would throw away exactly the breakdown needed to see which step is
    expensive.
    """

    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # Nullable: scheduled work (the daily digest) runs with no user in context.
    user_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), index=True)
    # Which part of the app spent this — "research", "report", "analysis", …
    feature: Mapped[str] = mapped_column(String, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)  # "anthropic" | "tavily"
    # Null for providers that don't bill per model (Tavily).
    model: Mapped[str | None] = mapped_column(String)

    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # Provider calls represented by this row. Always 1 for Anthropic; for
    # Tavily it's the number of searches, which is what that API bills on.
    request_count: Mapped[int] = mapped_column(Integer, default=1)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
