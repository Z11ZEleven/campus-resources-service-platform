"""Public resource availability facts for M1 search and locked revalidation."""

from sqlalchemy import or_, select

from app.modules.resources.models import ResourceBlock, Room, Seat
from app.modules.resources.opening import is_open


def conflicting_source(start, end):
    """Correlated predicate shared by search and locked resource checks."""
    return (
        select(ResourceBlock.id)
        .where(
            ResourceBlock.room_id == Room.id,
            ResourceBlock.status == "ACTIVE",
            ResourceBlock.start_at < end,
            or_(ResourceBlock.end_at.is_(None), ResourceBlock.end_at > start),
            or_(ResourceBlock.seat_id.is_(None), ResourceBlock.seat_id == Seat.id),
        )
        .exists()
    )


def seat_available(session, room, seat, start, end):
    """Caller must hold the room lock when using this to commit a reservation."""
    if not room.is_active or not seat.is_active or seat.room_id != room.id:
        return False
    if not is_open(session, room, start, end):
        return False
    return (
        session.scalar(
            select(Seat.id)
            .join(Room, Room.id == Seat.room_id)
            .where(Seat.id == seat.id, Room.id == room.id, conflicting_source(start, end))
            .limit(1)
        )
        is None
    )
