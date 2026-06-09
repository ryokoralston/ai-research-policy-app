from datetime import datetime
from sqlalchemy import String, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from services.secret_crypto import EncryptedString


class DigestSettings(Base):
    __tablename__ = "digest_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    email_to: Mapped[str] = mapped_column(String, default="")
    email_from: Mapped[str] = mapped_column(String, default="")
    # Gmail app password — encrypted at rest (see services/secret_crypto.py)
    smtp_password: Mapped[str] = mapped_column(EncryptedString, default="")
    topics: Mapped[str] = mapped_column(
        String, default="AI policy,AI regulation,AI governance,AI safety"
    )
    timezone: Mapped[str] = mapped_column(String, default="America/New_York")
    send_hour: Mapped[int] = mapped_column(Integer, default=5)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
