import uuid
from datetime import datetime
from sqlalchemy import String, Integer, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Debate(Base):
    __tablename__ = "debates"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending")  # 'pending'|'running'|'complete'|'error'
    personas: Mapped[str | None] = mapped_column(Text)  # JSON array of persona keys
    synthesis: Mapped[str | None] = mapped_column(Text)
    # JSON result of services.consensus_meter.extract_consensus(): 3-5 claims
    # actually debated, each with every persona's agree/disagree/mixed stance.
    consensus_json: Mapped[str | None] = mapped_column("consensus", Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    arguments: Mapped[list["DebateArgument"]] = relationship(
        "DebateArgument", back_populates="debate", cascade="all, delete-orphan",
        order_by="DebateArgument.order_index"
    )


class DebateArgument(Base):
    __tablename__ = "debate_arguments"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    debate_id: Mapped[str] = mapped_column(String, ForeignKey("debates.id", ondelete="CASCADE"))
    persona_key: Mapped[str] = mapped_column(String, nullable=False)
    persona_name: Mapped[str] = mapped_column(String, nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-4, 0 for synthesis
    round_name: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    debate: Mapped["Debate"] = relationship("Debate", back_populates="arguments")
