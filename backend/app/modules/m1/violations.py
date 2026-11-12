"""Owner-scoped violation history."""

from datetime import datetime, time, timedelta

from sqlalchemy import func, select

from app.core.time import BUSINESS_TZ, as_utc, to_utc
from app.modules.auth.permissions import room_scope_predicate
from app.modules.m1.models import Reservation, Violation
from app.modules.resources.models import Room


def list_own(session, actor, day, page, page_size):
    actor.require("m1:violation:read_own")
    query = (
        select(Violation)
        .join(Reservation, Reservation.id == Violation.reservation_id)
        .join(Room, Room.id == Reservation.room_id)
        .where(
            Violation.user_id == actor.user_id, room_scope_predicate(actor, "m1:violation:read_own")
        )
    )
    if day:
        start = datetime.combine(day, time.min, tzinfo=BUSINESS_TZ)
        query = query.where(
            Violation.occurred_at >= to_utc(start),
            Violation.occurred_at < to_utc(start + timedelta(days=1)),
        )
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(
        query.order_by(Violation.occurred_at.desc(), Violation.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": str(row.id),
                "reservation_id": str(row.reservation_id),
                "violation_type": row.violation_type,
                "occurred_at": as_utc(row.occurred_at).isoformat(),
                "reason": row.reason,
            }
            for row in rows
        ],
    }
