from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.dialects.mysql import BIGINT, DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utcnow


class OperationLog(Base):
    __tablename__ = "d_operation_logs"
    __table_args__ = (
        CheckConstraint("module IN ('D','M1','M2','M3')", name="ck_log_module"),
        Index("ix_operation_business", "module", "business_id", "created_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    module: Mapped[str] = mapped_column(String(8))
    business_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    action: Mapped[str] = mapped_column(String(40))
    actor_id: Mapped[int] = mapped_column(ForeignKey("d_users.id"))
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
