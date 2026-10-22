from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
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
        UniqueConstraint("id", "user_id", name="uq_identity_user"),
        Index("ix_identity_user", "user_id"),
        UniqueConstraint("identity_type", "identity_no", name="uq_identity_number"),
        CheckConstraint("identity_type IN ('STUDENT','TEACHER','STAFF')", name="ck_identity_type"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("d_users.id"))
    identity_type: Mapped[str] = mapped_column(String(20))
    identity_no: Mapped[str] = mapped_column(String(64))
    department_code: Mapped[str] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(TINYINT(1), default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow, onupdate=utcnow)


class UserRole(Base):
    __tablename__ = "d_user_roles"
    __table_args__ = (
        Index("ix_role_user", "user_id"),
        UniqueConstraint(
            "user_id", "role_code", "scope_type", "scope_value", name="uq_user_role_scope"
        ),
        CheckConstraint(
            "scope_type IN ('GLOBAL','ROOM','DEPARTMENT','BUILDING')", name="ck_role_scope"
        ),
        CheckConstraint(
            "role_code IN ('USER','ROOM_ADMIN','PROPERTY_ADMIN','REPAIRER','SYS_ADMIN')",
            name="ck_role_code",
        ),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("d_users.id"))
    role_code: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)

    scope_type: Mapped[str] = mapped_column(String(16), default="GLOBAL", server_default="GLOBAL")
    scope_value: Mapped[str] = mapped_column(String(64), default="*", server_default="*")


class RolePermission(Base):
    __tablename__ = "d_role_permissions"
    __table_args__ = (UniqueConstraint("role_code", "permission_code", name="uq_role_permission"),)
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    role_code: Mapped[str] = mapped_column(String(32))
    permission_code: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)


class AuthSession(Base):
    __tablename__ = "d_auth_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["identity_id", "user_id"],
            ["d_user_identities.id", "d_user_identities.user_id"],
            name="fk_session_identity_owner",
        ),
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(ForeignKey("d_users.id"))
    identity_id: Mapped[int] = mapped_column(ForeignKey("d_user_identities.id"))
    expires_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    revoked_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)


class ServicePrincipal(Base):
    __tablename__ = "d_service_principals"
    __table_args__ = (CheckConstraint("module IN ('M1','M2','M3')", name="ck_service_module"),)
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    module: Mapped[str] = mapped_column(String(8))
    is_active: Mapped[bool] = mapped_column(TINYINT(1), default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow, onupdate=utcnow)


class ServiceRoomScope(Base):
    __tablename__ = "d_service_room_scopes"
    __table_args__ = (UniqueConstraint("service_id", "room_id", name="uq_service_room_scope"),)
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("d_service_principals.id"))
    room_id: Mapped[int] = mapped_column(ForeignKey("d_rooms.id"))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
