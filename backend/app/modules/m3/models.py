from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, INTEGER, TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utcnow


class WorkOrder(Base):
    __tablename__ = "m3_work_orders"
    __table_args__ = (
        CheckConstraint(
            "category IN ('AIR_CONDITIONING','LIGHTING','POWER_SOCKET','FURNITURE','OTHER')",
            name="ck_m3_work_order_category",
        ),
        CheckConstraint(
            "status IN ('REPORTED','ASSIGNED','REPAIRING','WAIT_ACCEPTANCE','COMPLETED')",
            name="ck_m3_work_order_status",
        ),
        CheckConstraint("completion_round >= 0", name="ck_m3_work_order_round"),
        CheckConstraint("escalation_count >= 0", name="ck_m3_work_order_escalation_count"),
        UniqueConstraint("reporter_id", "request_key", name="uq_m3_work_order_reporter_request"),
        Index("ix_m3_work_order_reporter_created", "reporter_id", "created_at"),
        Index("ix_m3_work_order_assignee_status", "assignee_id", "status"),
        Index("ix_m3_work_order_room_status", "room_id", "status"),
        Index("ix_m3_work_order_stage_started", "status", "stage_started_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    reporter_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), ForeignKey("d_users.id"))
    room_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), ForeignKey("d_rooms.id"))
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="REPORTED", server_default="REPORTED")
    assignee_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("d_users.id")
    )
    assigned_by: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("d_users.id")
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    started_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    completion_note: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    completion_round: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), default=0, server_default="0"
    )
    accepted_by: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("d_users.id")
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    acceptance_comment: Mapped[str | None] = mapped_column(String(500))
    request_key: Mapped[str] = mapped_column(CHAR(36))
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow, onupdate=utcnow)
    stage_started_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    escalation_count: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), default=0, server_default="0"
    )


class WorkOrderRating(Base):
    __tablename__ = "m3_work_order_ratings"
    __table_args__ = (
        UniqueConstraint("work_order_id", name="uq_m3_rating_work_order"),
        CheckConstraint("score BETWEEN 1 AND 5", name="ck_m3_rating_score"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    work_order_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("m3_work_orders.id")
    )
    reporter_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), ForeignKey("d_users.id"))
    score: Mapped[int] = mapped_column(TINYINT(unsigned=True))
    comment: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)


class WorkOrderEscalation(Base):
    __tablename__ = "m3_work_order_escalations"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('REPORTED','ASSIGNED','REPAIRING','WAIT_ACCEPTANCE')",
            name="ck_m3_escalation_stage",
        ),
        CheckConstraint("completion_round >= 0", name="ck_m3_escalation_round"),
        UniqueConstraint(
            "work_order_id", "stage", "completion_round", name="uq_m3_escalation_stage_round"
        ),
        Index("ix_m3_escalation_order_created", "work_order_id", "escalated_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    work_order_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("m3_work_orders.id")
    )
    stage: Mapped[str] = mapped_column(String(24))
    completion_round: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    stage_started_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    due_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    escalated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    reason: Mapped[str] = mapped_column(String(500))
    triggered_by: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("d_users.id")
    )
