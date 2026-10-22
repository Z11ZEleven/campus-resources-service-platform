"""Public source-owned allocation maintenance; source rows remain authoritative."""

from sqlalchemy import select

from app.modules.resources.allocation_models import SeatAllocation
from app.modules.resources.models import Seat


def materialize_source(session, source):
    if source.source_module == "M3":
        return
    seat_ids = (
        [source.seat_id]
        if source.source_module == "M1"
        else list(
            session.scalars(
                select(Seat.id).where(Seat.room_id == source.room_id, Seat.is_active.is_(True))
            )
        )
    )
    session.add_all(
        SeatAllocation(
            block_id=source.id,
            room_id=source.room_id,
            seat_id=seat_id,
            source_module=source.source_module,
            source_id=source.source_id,
            source_seat_id=source.seat_id,
            start_at=source.start_at,
            end_at=source.end_at,
            status=source.status,
            released_at=source.released_at,
        )
        for seat_id in seat_ids
    )
    session.flush()


def release_allocations(session, source):
    # Never replace all seat states: only this source's details are released.
    for row in session.scalars(select(SeatAllocation).where(SeatAllocation.block_id == source.id)):
        row.status = "RELEASED"
        row.released_at = source.released_at
    session.flush()
