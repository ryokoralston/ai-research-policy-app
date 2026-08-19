import uuid
from datetime import datetime
from sqlalchemy import String, Integer, Float, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class ResearchSession(Base):
    __tablename__ = "research_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # Ownership — every query on this table filters by user_id (org_id is
    # stored for the coming org-tenancy migration but is not yet a filter).
    # Nullable at the column level so the migration can add it to an existing
    # database; rows predating it were backfilled to the oldest admin.
    user_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), index=True)
    org_id: Mapped[str | None] = mapped_column(String, ForeignKey("organizations.id"), index=True)
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
    # Provenance of the publisher, one of services/source_tier.py's TIERS.
    # Provenance, not credibility — never used to weight a claim.
    source_tier: Mapped[str | None] = mapped_column(String)
    published_date: Mapped[str | None] = mapped_column(String)
    result_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped["ResearchSession"] = relationship("ResearchSession", back_populates="results")


class RiskAnalysis(Base):
    __tablename__ = "risk_analyses"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # Ownership — every query on this table filters by user_id (org_id is
    # stored for the coming org-tenancy migration but is not yet a filter).
    # Nullable at the column level so the migration can add it to an existing
    # database; rows predating it were backfilled to the oldest admin.
    user_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), index=True)
    org_id: Mapped[str | None] = mapped_column(String, ForeignKey("organizations.id"), index=True)
    subject: Mapped[str] = mapped_column(String, nullable=False)
    # Free-form, not an enum — see schemas/analysis.py. Frontend offers:
    # 'technology'|'policy'|'actor'|'use_case'|'supply_chain'
    analysis_type: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    risk_scores_json: Mapped[str | None] = mapped_column("risk_scores", Text)  # JSON
    citation_confidence_json: Mapped[str | None] = mapped_column("citation_confidence", Text)  # JSON
    # JSON {dimension_key: grounding confidence 0-10}. Distinct from
    # risk_scores (how bad the risk is) and from citation_confidence (one
    # grade for the whole document) — this is per-dimension evidence quality.
    dimension_confidence_json: Mapped[str | None] = mapped_column("dimension_confidence", Text)
    sources_json: Mapped[str | None] = mapped_column("sources", Text)  # JSON array of URLs
    session_id: Mapped[str | None] = mapped_column(String, ForeignKey("research_sessions.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped["ResearchSession | None"] = relationship("ResearchSession", back_populates="risk_analyses")
