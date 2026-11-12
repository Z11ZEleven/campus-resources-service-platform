"""Idempotency and result ledger for service resource operations."""

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, INTEGER, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utcnow


class ResourceOperation(Base):
    __tablename__ = "d_resource_operations"
    __table_args__ = (
        Index("ix_resource_operation_service", "service_id"),
        UniqueConstraint("service_id", "operation_key", name="uq_resource_operation_key"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    source_module: Mapped[str] = mapped_column(String(8))
    source_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    room_id: Mapped[int] = mapped_column(ForeignKey("d_rooms.id"))
    service_id: Mapped[int] = mapped_column(ForeignKey("d_service_principals.id"))
    operation_key: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    request_payload: Mapped[dict] = mapped_column(JSON)
    status_code: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
