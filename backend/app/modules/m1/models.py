from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utcnow

OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}
STATES = "'RESERVED','CHECKED_IN','CANCELLED','NO_SHOW','COMPLETED','INTERRUPTED'"


class Reservation(Base):
    __tablename__ = "m1_reservations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["identity_id", "user_id"],
            ["d_user_identities.id", "d_user_identities.user_id"],
            name="fk_m1_reservation_identity_user",
        ),
        ForeignKeyConstraint(
            ["seat_id", "room_id"],
            ["d_seats.id", "d_seats.room_id"],
            name="fk_m1_reservation_seat_room",
        ),
        UniqueConstraint("user_id", "create_request_key", name="uq_m1_reservation_request"),
        UniqueConstraint("id", "room_id", "seat_id", name="uq_m1_reservation_resource"),
        UniqueConstraint("id", "user_id", name="uq_m1_reservation_user"),
        UniqueConstraint("id", "user_id", "room_id", name="uq_m1_reservation_user_room"),
        CheckConstraint("start_at < end_at", name="ck_m1_reservation_period"),
        CheckConstraint(f"status IN ({STATES})", name="ck_m1_reservation_status"),
        CheckConstraint("version > 0", name="ck_m1_reservation_version"),
        CheckConstraint(
            "CHAR_LENGTH(create_request_key) > 0 AND CHAR_LENGTH(request_hash) = 64",
            name="ck_m1_reservation_request",
        ),
        Index("ix_m1_reservation_user_created", "user_id", "created_at", "id"),
        Index("ix_m1_reservation_room_status_start", "room_id", "status", "start_at"),
        Index("ix_m1_reservation_status_start", "status", "start_at"),
        Index("ix_m1_reservation_status_end", "status", "end_at"),
        OPTIONS,
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("d_users.id"))
    identity_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    room_id: Mapped[int] = mapped_column(ForeignKey("d_rooms.id"))
    seat_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    start_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    end_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    status: Mapped[str] = mapped_column(String(24), default="RESERVED", server_default="RESERVED")
    checked_in_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    actual_end_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    cancelled_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    end_reason: Mapped[str | None] = mapped_column(String(500))
    settings_snapshot: Mapped[dict] = mapped_column(JSON)
    resource_snapshot: Mapped[dict] = mapped_column(JSON)
    create_request_key: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow, onupdate=utcnow)


class ReservationEvent(Base):
    __tablename__ = "m1_reservation_events"
    __table_args__ = (
        CheckConstraint(
            f"from_status IS NULL OR from_status IN ({STATES})", name="ck_m1_event_from_status"
        ),
        CheckConstraint(f"to_status IN ({STATES})", name="ck_m1_event_to_status"),
        CheckConstraint(
            "(actor_type IN ('USER','SERVICE') AND actor_id IS NOT NULL) OR (actor_type = 'SYSTEM' AND actor_id IS NULL)",
            name="ck_m1_event_actor",
        ),
        CheckConstraint("CHAR_LENGTH(event_type) > 0", name="ck_m1_event_type"),
        Index("ix_m1_event_reservation_created", "reservation_id", "created_at", "id"),
        OPTIONS,
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    reservation_id: Mapped[int] = mapped_column(ForeignKey("m1_reservations.id"))
    event_type: Mapped[str] = mapped_column(String(64))
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24))
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True))
    correlation_id: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(String(500))
    details: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)


class Violation(Base):
    __tablename__ = "m1_violations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["reservation_id", "user_id"],
            ["m1_reservations.id", "m1_reservations.user_id"],
            name="fk_m1_violation_reservation_user",
        ),
        UniqueConstraint("reservation_id", name="uq_m1_violation_reservation"),
        CheckConstraint("violation_type = 'NO_SHOW'", name="ck_m1_violation_type"),
        Index("ix_m1_violation_user_occurred", "user_id", "occurred_at", "id"),
        OPTIONS,
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    reservation_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    user_id: Mapped[int] = mapped_column(ForeignKey("d_users.id"))
    violation_type: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)


class RoomCheckinCode(Base):
    __tablename__ = "m1_room_checkin_codes"
    __table_args__ = (
        UniqueConstraint("room_id", "business_date", name="uq_m1_checkin_room_date"),
        CheckConstraint("version > 0", name="ck_m1_checkin_code_version"),
        CheckConstraint("CHAR_LENGTH(nonce) >= 32", name="ck_m1_checkin_code_nonce"),
        OPTIONS,
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("d_rooms.id"))
    business_date: Mapped[date] = mapped_column(Date)
    nonce: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(BIGINT(unsigned=True), default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow, onupdate=utcnow)


class CheckinAttempt(Base):
    __tablename__ = "m1_checkin_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["reservation_id", "user_id", "room_id"],
            ["m1_reservations.id", "m1_reservations.user_id", "m1_reservations.room_id"],
            name="fk_m1_attempt_reservation_user_room",
        ),
        CheckConstraint("CHAR_LENGTH(result) > 0", name="ck_m1_attempt_result"),
        Index("ix_m1_attempt_user_room_time", "user_id", "room_id", "attempted_at"),
        OPTIONS,
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("d_users.id"))
    reservation_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True))
    room_id: Mapped[int] = mapped_column(ForeignKey("d_rooms.id"))
    attempted_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    result: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
