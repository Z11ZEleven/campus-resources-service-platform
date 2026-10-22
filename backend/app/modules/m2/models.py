from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, INTEGER
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utcnow

OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}
WAITING = ("PENDING_FIRST_APPROVAL", "PENDING_ROOM_APPROVAL")
ACTIVE = (*WAITING, "RESOURCE_CONFIRMING")


class Application(Base):
    __tablename__ = "m2_applications"
    __table_args__ = (
        UniqueConstraint("applicant_id", "request_key", name="uq_m2_applicant_key"),
        CheckConstraint(
            "start_at < end_at AND TIMESTAMPDIFF(SECOND,start_at,end_at) BETWEEN 3600 AND 14400",
            name="ck_m2_period",
        ),
        CheckConstraint("version > 0", name="ck_m2_version"),
        CheckConstraint(
            "status IN ('PENDING_FIRST_APPROVAL','PENDING_ROOM_APPROVAL','RESOURCE_CONFIRMING','APPROVED','REJECTED','WITHDRAWN','AUTO_CANCELLED')",
            name="ck_m2_status",
        ),
        CheckConstraint(
            "status <> 'APPROVED' OR (resource_hold_id IS NOT NULL AND resource_sync_status='SUCCEEDED')",
            name="ck_m2_approved_hold",
        ),
        Index("ix_m2_owner_created", "applicant_id", "created_at"),
        Index("ix_m2_status_deadline", "status", "step_deadline_at"),
        Index("ix_m2_room_start", "room_id", "start_at"),
        OPTIONS,
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("d_users.id"))
    applicant_identity_id: Mapped[int] = mapped_column(ForeignKey("d_user_identities.id"))
    identity_type: Mapped[str] = mapped_column(String(20))
    department_code: Mapped[str] = mapped_column(String(32))
    room_id: Mapped[int] = mapped_column(ForeignKey("d_rooms.id"))
    purpose: Mapped[str] = mapped_column(Text)
    start_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    end_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    status: Mapped[str] = mapped_column(String(32))
    current_level: Mapped[int | None] = mapped_column(INTEGER(unsigned=True))
    step_deadline_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    resource_sync_status: Mapped[str] = mapped_column(
        String(24), default="NONE", server_default="NONE"
    )
    resource_hold_id: Mapped[int | None] = mapped_column(ForeignKey("d_resource_blocks.id"))
    final_reason: Mapped[str | None] = mapped_column(String(1000))
    finalized_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    withdrawn_request_key: Mapped[str | None] = mapped_column(CHAR(36))
    request_key: Mapped[str] = mapped_column(CHAR(36))
    version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1, server_default="1")
    submitted_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow, onupdate=utcnow)


class ApprovalStep(Base):
    __tablename__ = "m2_approval_steps"
    __table_args__ = (
        UniqueConstraint("application_id", "level", name="uq_m2_step_level"),
        UniqueConstraint("decided_by", "request_key", name="uq_m2_review_key"),
        CheckConstraint("level IN (1,2)", name="ck_m2_step_level"),
        CheckConstraint(
            "status IN ('PENDING','CONFIRMING','APPROVED','REJECTED','CANCELLED','EXPIRED')",
            name="ck_m2_step_status",
        ),
        OPTIONS,
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("m2_applications.id"))
    level: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    required_role: Mapped[str] = mapped_column(String(32))
    scope_type: Mapped[str] = mapped_column(String(16))
    scope_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="PENDING", server_default="PENDING")
    deadline_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("d_users.id"))
    decision_reason: Mapped[str | None] = mapped_column(String(500))
    decision: Mapped[str | None] = mapped_column(String(16))
    decided_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    request_key: Mapped[str | None] = mapped_column(CHAR(36))
    version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow, onupdate=utcnow)


class ApplicationEvent(Base):
    __tablename__ = "m2_application_events"
    __table_args__ = (
        UniqueConstraint("application_id", "version", "event_type", name="uq_m2_event"),
        OPTIONS,
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("m2_applications.id"))
    event_type: Mapped[str] = mapped_column(String(40))
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("d_users.id"))
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(1000))
    version: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)


class ResourceJob(Base):
    __tablename__ = "m2_resource_jobs"
    __table_args__ = (OPTIONS,)
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("m2_applications.id"), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)
    action: Mapped[str] = mapped_column(String(16), default="ACQUIRE", server_default="ACQUIRE")
    status: Mapped[str] = mapped_column(String(16), default="PENDING", server_default="PENDING")
    attempts: Mapped[int] = mapped_column(INTEGER(unsigned=True), default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    lease_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    lease_token: Mapped[str | None] = mapped_column(CHAR(36))
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow, onupdate=utcnow)
