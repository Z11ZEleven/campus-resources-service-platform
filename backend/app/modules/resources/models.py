from datetime import datetime, time

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, INTEGER, TIME, TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utcnow

OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


class Room(Base):
    __tablename__ = "d_rooms"
    __table_args__ = (
        CheckConstraint("capacity > 0", name="ck_room_capacity"),
        CheckConstraint("open_time < close_time", name="ck_room_hours"),
        CheckConstraint("booking_mode IN ('SEAT','WHOLE_ROOM')", name="ck_room_booking_mode"),
        OPTIONS,
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    building_name: Mapped[str] = mapped_column(String(100))
    capacity: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    department_code: Mapped[str | None] = mapped_column(String(32))
    booking_mode: Mapped[str] = mapped_column(String(16), default="SEAT", server_default="SEAT")
    campus_code: Mapped[str] = mapped_column(String(32), default="MAIN", server_default="MAIN")
    open_time: Mapped[time] = mapped_column(TIME)
    close_time: Mapped[time] = mapped_column(TIME)
    is_active: Mapped[bool] = mapped_column(TINYINT(1), default=True, server_default="1")
    resource_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow, onupdate=utcnow)


class Seat(Base):
    __tablename__ = "d_seats"
    __table_args__ = (
        UniqueConstraint("room_id", "seat_no", name="uq_room_seat"),
        UniqueConstraint("id", "room_id", name="uq_seat_room"),
        OPTIONS,
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("d_rooms.id"))
    seat_no: Mapped[str] = mapped_column(String(32))
    has_fixed_socket: Mapped[bool] = mapped_column(TINYINT(1), default=False, server_default="0")
    has_mobile_socket: Mapped[bool] = mapped_column(TINYINT(1), default=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(TINYINT(1), default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow, onupdate=utcnow)


class ResourceBlock(Base):
    __tablename__ = "d_resource_blocks"
    __table_args__ = (
        UniqueConstraint("source_module", "source_id", name="uq_block_source"),
        ForeignKeyConstraint(
            ["seat_id", "room_id"], ["d_seats.id", "d_seats.room_id"], name="fk_block_seat_room"
        ),
        CheckConstraint("source_id > 0", name="ck_block_source_id"),
        CheckConstraint(
            "(source_module = 'M1' AND seat_id IS NOT NULL AND end_at IS NOT NULL)"
            " OR (source_module = 'M2' AND seat_id IS NULL AND end_at IS NOT NULL)"
            " OR (source_module = 'M3' AND seat_id IS NULL)",
            name="ck_block_scope",
        ),
        CheckConstraint("end_at IS NULL OR start_at < end_at", name="ck_block_period"),
        CheckConstraint(
            "(status = 'ACTIVE' AND released_at IS NULL)"
            " OR (status = 'RELEASED' AND released_at IS NOT NULL)",
            name="ck_block_status",
        ),
        Index("ix_block_room_status_start", "room_id", "status", "start_at"),
        OPTIONS,
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("d_rooms.id"))
    seat_id: Mapped[int | None] = mapped_column(ForeignKey("d_seats.id"))
    source_module: Mapped[str] = mapped_column(String(8))
    source_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    start_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    end_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", server_default="ACTIVE")
    released_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow, onupdate=utcnow)


class SeatStatusSummary(Base):
    __tablename__ = "d_seat_status_summary"
    __table_args__ = (
        UniqueConstraint("seat_id", "slot_start", name="uq_summary_seat_slot"),
        CheckConstraint(
            "status IN ('AVAILABLE','RESERVED','ROOM_BOOKED','MAINTENANCE','CLOSED','DISABLED')",
            name="ck_summary_status",
        ),
        OPTIONS,
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    seat_id: Mapped[int] = mapped_column(ForeignKey("d_seats.id"))
    slot_start: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    status: Mapped[str] = mapped_column(String(24))
    primary_block_id: Mapped[int | None] = mapped_column(ForeignKey("d_resource_blocks.id"))
    source_count: Mapped[int] = mapped_column(INTEGER(unsigned=True), default=0, server_default="0")
    resource_version: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    computed_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow, onupdate=utcnow)
