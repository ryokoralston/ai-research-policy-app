import uuid
from datetime import datetime
from sqlalchemy import String, Integer, Float, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class ResearchSession(Base):
    __tablename__ = "research_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    query: Mapped[str] = mapped_column(Text, nullable=False)
    topic: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")  # 'pending'|'running'|'complete'|'error'
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    results: Mapped[list["SearchResult"]] = relationship(
        "SearchResult", back_populates="session", cascade="all, delete-orphan",
        order_by="SearchResult.result_order"
    )
    reports: Mapped[list["Report"]] = relationship(  # type: ignore[name-defined]
        "Report", foreign_keys="Report.session_id", back_populates=None
    )
    risk_analyses: Mapped[list["RiskAnalysis"]] = relationship(
        "RiskAnalysis", back_populates="session", cascade="all, delete-orphan"
    )


class SearchResult(Base):
    __tablename__ = "search_results"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(String, ForeignKey("research_sessions.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(String)
    snippet: Mapped[str | None] = mapped_column(Text)
    full_content: Mapped[str | None] = mapped_column(Text)
    relevance_score: Mapped[float | None] = mapped_column(Float)
    ai_summary: Mapped[str | None] = mapped_column(Text)
    published_date: Mapped[str | None] = mapped_column(String)
    result_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped["ResearchSession"] = relationship("ResearchSession", back_populates="results")


class RiskAnalysis(Base):
    __tablename__ = "risk_analyses"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    subject: Mapped[str] = mapped_column(String, nullable=False)
    analysis_type: Mapped[str] = mapped_column(String, nullable=False)  # 'technology'|'policy'|'actor'
    content: Mapped[str | None] = mapped_column(Text)
    risk_scores_json: Mapped[str | None] = mapped_column("risk_scores", Text)  # JSON
    sources_json: Mapped[str | None] = mapped_column("sources", Text)  # JSON array of URLs
    session_id: Mapped[str | None] = mapped_column(String, ForeignKey("research_sessions.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped["ResearchSession | None"] = relationship("ResearchSession", back_populates="risk_analyses")
