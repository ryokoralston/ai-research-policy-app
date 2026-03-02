import uuid
from datetime import datetime
from sqlalchemy import String, Integer, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String, nullable=False)
    report_type: Mapped[str] = mapped_column(String, nullable=False)  # 'congressional_brief'|'policy_memo'|'risk_assessment'
    status: Mapped[str] = mapped_column(String, default="draft")  # 'draft'|'complete'|'archived'
    content: Mapped[str | None] = mapped_column(Text)  # Full markdown
    session_id: Mapped[str | None] = mapped_column(String, ForeignKey("research_sessions.id", ondelete="SET NULL"))
    word_count: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[str | None] = mapped_column("metadata", Text)  # JSON blob
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    sections: Mapped[list["ReportSection"]] = relationship(
        "ReportSection", back_populates="report", cascade="all, delete-orphan",
        order_by="ReportSection.order_index"
    )


class ReportSection(Base):
    __tablename__ = "report_sections"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    report_id: Mapped[str] = mapped_column(String, ForeignKey("reports.id", ondelete="CASCADE"))
    section_key: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    citations_json: Mapped[str | None] = mapped_column("citations", Text)  # JSON array
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    report: Mapped["Report"] = relationship("Report", back_populates="sections")
