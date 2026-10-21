from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, INTEGER
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utcnow


class Application(Base):
    __tablename__ = "m2_applications"
    __table_args__ = (
        UniqueConstraint("applicant_id", "request_key", name="uq_m2_applicant_request"),
        CheckConstraint("participant_count > 0", name="ck_m2_participants"),
        CheckConstraint("start_at < end_at", name="ck_m2_period"),
        CheckConstraint(
            "(status = 'PENDING' AND reviewer_id IS NULL AND reviewed_at IS NULL AND cancelled_at IS NULL)"
            " OR (status IN ('APPROVED', 'REJECTED') AND reviewer_id IS NOT NULL AND reviewed_at IS NOT NULL AND cancelled_at IS NULL)"
            " OR (status = 'CANCELLED' AND reviewer_id IS NULL AND reviewed_at IS NULL AND cancelled_at IS NOT NULL)",
            name="ck_m2_state",
        ),
        CheckConstraint(
            "reviewer_id IS NULL OR reviewer_id <> applicant_id", name="ck_m2_no_self_review"
        ),
        CheckConstraint(
            "status <> 'REJECTED' OR (review_comment IS NOT NULL AND CHAR_LENGTH(TRIM(review_comment)) > 0)",
            name="ck_m2_reject_reason",
        ),
        Index("ix_m2_applicant_created", "applicant_id", "created_at"),
        Index("ix_m2_status_created", "status", "created_at"),
        Index("ix_m2_room_start", "room_id", "start_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("d_users.id"))
    room_id: Mapped[int] = mapped_column(ForeignKey("d_rooms.id"))
    title: Mapped[str] = mapped_column(String(100))
    purpose: Mapped[str] = mapped_column(Text)
    participant_count: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    start_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    end_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    status: Mapped[str] = mapped_column(String(16), default="PENDING", server_default="PENDING")
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("d_users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    review_comment: Mapped[str | None] = mapped_column(String(500))
    cancelled_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    request_key: Mapped[str] = mapped_column(CHAR(36))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow, onupdate=utcnow)

    @property
    def display_status(self) -> str:
        return "ENDED" if self.status == "APPROVED" and self.end_at <= utcnow() else self.status
