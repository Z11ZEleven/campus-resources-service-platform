from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utcnow


class Notification(Base):
    __tablename__ = "d_notifications"
    __table_args__ = (
        UniqueConstraint("recipient_id", "event_key", name="uq_notification_recipient_event"),
        CheckConstraint("source_module IN ('D','M1','M2','M3')", name="ck_notification_module"),
        Index("ix_notification_recipient_read_created", "recipient_id", "read_at", "created_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    recipient_id: Mapped[int] = mapped_column(ForeignKey("d_users.id"))
    source_module: Mapped[str] = mapped_column(String(8))
    business_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    event_key: Mapped[str] = mapped_column(String(160))
    title: Mapped[str] = mapped_column(String(160))
    content: Mapped[str] = mapped_column(String(1000))
    link: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
