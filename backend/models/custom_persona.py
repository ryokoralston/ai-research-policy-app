import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class CustomPersona(Base):
    """An organization-specific debate persona (e.g. "our VP of Engineering"),
    admin-created and shared across all users — see services/persona_service.py
    for how these are merged with the 10 hardcoded PERSONAS (templates/personas.py)
    into one uniform list for the debate feature.

    Unlike the hardcoded personas (explicitly fictional, per their UI
    disclaimer), a CustomPersona is meant to model a real internal
    stakeholder's known priorities and decision style for internal
    decision-support use — that disclaimer does not apply to these.
    """
    __tablename__ = "custom_personas"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # Slug derived server-side from `name` (see persona_service.derive_key) —
    # must not collide with a built-in PERSONAS key or another custom_personas.key.
    key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    initials: Mapped[str] = mapped_column(String, nullable=False)
    # Tailwind bg-* class, assigned from persona_service.CUSTOM_PALETTE at
    # creation time (cycled by creation order) — see persona_service.py.
    color: Mapped[str] = mapped_column(String, nullable=False)
    # What this person cares about / evaluates proposals by — free text.
    priorities: Mapped[str] = mapped_column(Text, nullable=False)
    # How they communicate, what they push back on, tone — free text.
    style: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
