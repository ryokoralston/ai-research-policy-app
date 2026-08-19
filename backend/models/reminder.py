import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # Ownership — every query on this table filters by user_id (org_id is
    # stored for the coming org-tenancy migration but is not yet a filter).
    # Nullable at the column level so the migration can add it to an existing
    # database; rows predating it were backfilled to the oldest admin.
    user_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), index=True)
    org_id: Mapped[str | None] = mapped_column(String, ForeignKey("organizations.id"), index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
