from datetime import datetime, time

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, INTEGER, TIME, TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utcnow

OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


class Building(Base):
    __tablename__ = "d_buildings"
    __table_args__ = (OPTIONS,)
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    is_active: Mapped[bool] = mapped_column(TINYINT(1), default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow, onupdate=utcnow)


class Room(Base):
    __tablename__ = "d_rooms"
    __table_args__ = (
        CheckConstraint("capacity > 0", name="ck_room_capacity"),
        CheckConstraint("open_time < close_time", name="ck_room_hours"),
        OPTIONS,
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    building_id: Mapped[int | None] = mapped_column(ForeignKey("d_buildings.id"))
    opening_rules_enabled: Mapped[bool] = mapped_column(
        TINYINT(1), default=False, server_default="0"
    )
    building_name: Mapped[str] = mapped_column(String(100))
    capacity: Mapped[int] = mapped_column(INTEGER(unsigned=True))
    department_code: Mapped[str | None] = mapped_column(String(32))
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
        UniqueConstraint("id", "room_id", "source_module", "source_id", name="uq_block_owner"),
        UniqueConstraint("id", "seat_id", name="uq_block_seat"),
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


class OpeningRule(Base):
    __tablename__ = "d_room_opening_rules"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_opening_weekday"),
        CheckConstraint("start_time <> end_time", name="ck_opening_rule_period"),
        UniqueConstraint("room_id", "weekday", "start_time", "end_time", name="uq_opening_rule"),
        OPTIONS,
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("d_rooms.id"))
    weekday: Mapped[int] = mapped_column(TINYINT(unsigned=True))
    start_time: Mapped[time] = mapped_column(TIME)
    end_time: Mapped[time] = mapped_column(TIME)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3), default=utcnow, server_default=text("CURRENT_TIMESTAMP(3)")
    )


class OpeningException(Base):
    __tablename__ = "d_room_opening_exceptions"
    __table_args__ = (
        CheckConstraint("start_at < end_at", name="ck_opening_exception_period"),
        CheckConstraint("kind IN ('OPEN','CLOSE')", name="ck_opening_exception_kind"),
        Index("ix_opening_exception_room_start", "room_id", "start_at"),
        OPTIONS,
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("d_rooms.id"))
    kind: Mapped[str] = mapped_column(String(8))
    start_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    end_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3), default=utcnow, server_default=text("CURRENT_TIMESTAMP(3)")
    )
