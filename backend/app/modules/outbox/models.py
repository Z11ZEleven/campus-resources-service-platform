from datetime import datetime

from sqlalchemy import CheckConstraint, Index, String, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, INTEGER, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utcnow

STATUSES = "'PENDING','PROCESSING','SUCCEEDED','MANUAL'"


class OutboxEvent(Base):
    """Durable public task/event row written in the caller's transaction."""

    __tablename__ = "d_outbox_events"
    __table_args__ = (
        UniqueConstraint("source_module", "event_key", name="uq_outbox_source_event"),
        CheckConstraint("source_module IN ('D','M1','M2','M3')", name="ck_outbox_source"),
        CheckConstraint("CHAR_LENGTH(event_key) > 0", name="ck_outbox_event_key"),
        CheckConstraint("CHAR_LENGTH(handler) > 0", name="ck_outbox_handler"),
        CheckConstraint(f"status IN ({STATUSES})", name="ck_outbox_status"),
        CheckConstraint("business_version > 0", name="ck_outbox_business_version"),
        CheckConstraint("attempts >= 0", name="ck_outbox_attempts"),
        CheckConstraint(
            "max_attempts IS NULL OR max_attempts > 0", name="ck_outbox_max_attempts"
        ),
        CheckConstraint("version > 0", name="ck_outbox_version"),
        CheckConstraint(
            "(status = 'PROCESSING' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'PROCESSING' AND lease_owner IS NULL AND lease_token IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_outbox_lease",
        ),
        Index("ix_outbox_due", "status", "available_at", "id"),
        Index("ix_outbox_lease", "status", "lease_expires_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    source_module: Mapped[str] = mapped_column(String(8))
    event_key: Mapped[str] = mapped_column(String(160))
    handler: Mapped[str] = mapped_column(String(40))
    business_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=0, server_default="0")
    business_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), default=1, server_default="1"
    )
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", server_default="PENDING")
    available_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    attempts: Mapped[int] = mapped_column(INTEGER(unsigned=True), default=0, server_default="0")
    max_attempts: Mapped[int | None] = mapped_column(INTEGER(unsigned=True))
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    last_error: Mapped[str | None] = mapped_column(String(1000))
    error_code: Mapped[str | None] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
