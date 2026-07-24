from datetime import datetime
from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class ModelCatalogEntry(Base):
    """Latest known Anthropic model per family (opus/sonnet/haiku/fable),
    refreshed periodically from the /v1/models endpoint — see
    services.model_catalog.refresh_model_catalog().
    """

    __tablename__ = "model_catalog_entries"

    family: Mapped[str] = mapped_column(String, primary_key=True)
    model_id: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
