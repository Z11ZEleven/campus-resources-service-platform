from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.dialects.mysql import BIGINT, DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utcnow


class SeatAllocation(Base):
    """Seat details for an authoritative source; legacy M1 links may remain logical.

    m1_reservation_id is typed and optional, never an unconditional FK on the
    polymorphic source_id. D services do not read the private reservation table.
    """

    __tablename__ = "d_seat_allocations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["block_id", "room_id", "source_module", "source_id"],
            [
                "d_resource_blocks.id",
                "d_resource_blocks.room_id",
                "d_resource_blocks.source_module",
                "d_resource_blocks.source_id",
            ],
            name="fk_allocation_block_owner",
        ),
        ForeignKeyConstraint(
            ["seat_id", "room_id"],
            ["d_seats.id", "d_seats.room_id"],
            name="fk_allocation_seat_room",
        ),
        ForeignKeyConstraint(
            ["m1_reservation_id", "room_id", "seat_id"],
            ["m1_reservations.id", "m1_reservations.room_id", "m1_reservations.seat_id"],
            name="fk_allocation_m1_reservation",
        ),
        ForeignKeyConstraint(
            ["block_id", "source_seat_id"],
            ["d_resource_blocks.id", "d_resource_blocks.seat_id"],
            name="fk_allocation_source_seat",
        ),
        UniqueConstraint("block_id", "seat_id", name="uq_allocation_block_seat"),
        CheckConstraint(
            "(source_module = 'M1' AND source_seat_id IS NOT NULL AND source_seat_id = seat_id) OR (source_module = 'M2' AND source_seat_id IS NULL)",
            name="ck_allocation_source",
        ),
        CheckConstraint(
            "m1_reservation_id IS NULL OR (source_module = 'M1' AND m1_reservation_id = source_id)",
            name="ck_allocation_m1_source",
        ),
        CheckConstraint("start_at < end_at", name="ck_allocation_period"),
        CheckConstraint(
            "(status = 'ACTIVE' AND released_at IS NULL) OR (status = 'RELEASED' AND released_at IS NOT NULL)",
            name="ck_allocation_status",
        ),
        Index("ix_allocation_seat_status_start_end", "seat_id", "status", "start_at", "end_at"),
        Index("ix_allocation_room_status_start", "room_id", "status", "start_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True)
    block_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    room_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    seat_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    source_module: Mapped[str] = mapped_column(String(8))
    source_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    source_seat_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True))
    m1_reservation_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True))
    start_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    end_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3))
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", server_default="ACTIVE")
    released_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), default=utcnow, onupdate=utcnow)
