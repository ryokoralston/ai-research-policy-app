from datetime import datetime
from sqlalchemy import String, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from services.secret_crypto import EncryptedString


class ModelSettings(Base):
    __tablename__ = "model_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    main_model: Mapped[str] = mapped_column(String, default="claude-opus-4-6")
    fast_model: Mapped[str] = mapped_column(String, default="claude-haiku-4-5-20251001")
    # Encrypted at rest (see services/secret_crypto.py)
    anthropic_api_key: Mapped[str] = mapped_column(EncryptedString, default="")
    openai_api_key: Mapped[str] = mapped_column(EncryptedString, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
