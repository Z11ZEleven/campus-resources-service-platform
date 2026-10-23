from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utcnow


class User(Base):
    __tablename__ = "d_users"
    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(TINYINT(1), default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow, onupdate=utcnow)


class UserIdentity(Base):
    __tablename__ = "d_user_identities"
    __table_args__ = (
        UniqueConstraint("identity_type", "identity_no", name="uq_identity_number"),
        CheckConstraint("identity_type IN ('STUDENT','TEACHER','STAFF')", name="ck_identity_type"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("d_users.id"), unique=True)
    identity_type: Mapped[str] = mapped_column(String(20))
    identity_no: Mapped[str] = mapped_column(String(64))
    department_code: Mapped[str] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(TINYINT(1), default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow, onupdate=utcnow)


class UserRole(Base):
    __tablename__ = "d_user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_code", name="uq_user_role"),
        CheckConstraint(
            "role_code IN ('USER','ROOM_ADMIN','PROPERTY_ADMIN','REPAIRER','SYS_ADMIN','DEPT_ADMIN','CAMPUS_ADMIN')",
            name="ck_role_code",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("d_users.id"))
    role_code: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)


class RoleScope(Base):
    """审批范围不代替角色授权；使用时同时验证有效角色和身份。"""

    __tablename__ = "d_role_scopes"
    __table_args__ = (
        UniqueConstraint("user_id", "role_code", "scope_type", "scope_key", name="uq_role_scope"),
        CheckConstraint("role_code IN ('DEPT_ADMIN','CAMPUS_ADMIN','ROOM_ADMIN')", name="ck_scope_role"),
        CheckConstraint("scope_type IN ('DEPARTMENT','CAMPUS','ROOM')", name="ck_scope_type"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("d_users.id"))
    role_code: Mapped[str] = mapped_column(String(32))
    scope_type: Mapped[str] = mapped_column(String(16))
    scope_key: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(TINYINT(1), default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow, onupdate=utcnow)
