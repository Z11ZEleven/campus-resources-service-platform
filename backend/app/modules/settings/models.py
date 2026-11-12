from datetime import datetime

from sqlalchemy import String
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, INTEGER
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utcnow


class SystemSetting(Base):
    __tablename__ = "d_system_settings"
    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow, onupdate=utcnow)
