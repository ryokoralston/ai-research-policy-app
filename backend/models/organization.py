"""Organization — the tenant a user and all of their content belong to.

Today the app creates exactly one organization per user (name = the user's
email), so org_id carries no extra information beyond user_id. It exists now
so the eventual multi-user-per-organization tenancy migration is a data
change, not a second schema migration: every ownership-bearing table already
stores org_id alongside user_id, and every row written from here on populates
both. Filtering is still strictly by user_id (see the routers) — org_id is
stored and correct, but not yet a filter.
"""
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
