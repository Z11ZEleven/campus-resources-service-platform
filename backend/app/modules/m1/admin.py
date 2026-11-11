"""M1 administrative queries constrained by the caller's grants."""

from sqlalchemy import select

from app.core.errors import DomainError, forbidden
from app.core.time import to_utc, utcnow
from app.modules.auth.permissions import room_scope_predicate
from app.modules.m1.models import Reservation
from app.modules.resources.models import Room


def statistics(session, actor, start_at, end_at, room_id=None):
    if not actor.has_any("m1:statistics:read"):
        raise forbidden()
    try:
        start, end = to_utc(start_at), to_utc(end_at)
    except ValueError as exc:
        raise DomainError(422, "TIMEZONE_REQUIRED", "时间必须包含时区") from exc
    if start >= end:
        raise DomainError(422, "INVALID_PERIOD", "统计时段必须为正时长")
    query = (
        select(Reservation)
        .join(Room, Room.id == Reservation.room_id)
        .where(
            room_scope_predicate(actor, "m1:statistics:read"),
            Reservation.start_at < end,
            Reservation.end_at > start,
        )
    )
    if room_id is not None:
        room = session.get(Room, room_id)
        if room is None or not actor.can_for_room("m1:statistics:read", room):
            raise forbidden()
        query = query.where(Reservation.room_id == room_id)
    rows = session.scalars(query)
    totals = {
        "reservation_count": 0,
        "checked_in_count": 0,
        "cancelled_count": 0,
        "no_show_count": 0,
        "completed_count": 0,
        "interrupted_count": 0,
        "planned_minutes": 0,
        "actual_minutes": 0,
    }
    now = utcnow()
    for row in rows:
        totals["reservation_count"] += 1
        for status, key in [
            ("CANCELLED", "cancelled_count"),
            ("NO_SHOW", "no_show_count"),
            ("COMPLETED", "completed_count"),
            ("INTERRUPTED", "interrupted_count"),
        ]:
            if row.status == status:
                totals[key] += 1
        if row.checked_in_at is not None:
            totals["checked_in_count"] += 1
            actual_end = row.actual_end_at or (
                now if row.status == "CHECKED_IN" else row.checked_in_at
            )
            actual_start = max(row.checked_in_at, row.start_at, start)
            clipped_end = min(actual_end, row.end_at, end)
            totals["actual_minutes"] += max(0, (clipped_end - actual_start).total_seconds()) / 60
        totals["planned_minutes"] += (
            max(0, (min(row.end_at, end) - max(row.start_at, start)).total_seconds()) / 60
        )
    totals["fulfillment_rate"] = (
        totals["actual_minutes"] / totals["planned_minutes"] if totals["planned_minutes"] else None
    )
    return totals


def read_settings(session, actor):
    from app.modules.settings.service import read_int

    if not actor.has_any("m1:settings:write"):
        raise forbidden()
    value, version = read_int(session, "m1.max_hours", 4)
    return {"max_hours": value, "version": str(version)}


def update_settings(session, actor, auth_session_id, expected_version, max_hours):
    from app.core.errors import conflict
    from app.modules.audit.service import record_operation
    from app.modules.settings.models import SystemSetting
    from app.modules.settings.service import read_int

    actor.require("m1:settings:write")
    _, version = read_int(session, "m1.max_hours", 4, lock=True)
    from app.modules.m1.reservations import refreshed_actor

    actor = refreshed_actor(session, auth_session_id, actor)
    actor.require("m1:settings:write")
    if version != expected_version:
        raise conflict("参数版本已变化，请刷新后重试")
    row = session.get(SystemSetting, "m1.max_hours")
    if row.value != max_hours:
        row.value, row.version = max_hours, version + 1
        record_operation(
            session,
            module="M1",
            business_id=0,
            action="UPDATE_SETTINGS",
            actor_id=actor.user_id,
            note=f"max_hours={max_hours};version={row.version}",
        )
    session.flush()
    return {"max_hours": row.value, "version": str(row.version)}


def list_reservations(session, actor, room_id, page, page_size):
    from sqlalchemy import func

    from app.modules.m1.reservations import view

    if not actor.has_any("m1:reservation:read_scoped"):
        raise forbidden()
    query = (
        select(Reservation)
        .join(Room, Room.id == Reservation.room_id)
        .where(room_scope_predicate(actor, "m1:reservation:read_scoped"))
    )
    if room_id is not None:
        room = session.get(Room, room_id)
        if room is None or not actor.can_for_room("m1:reservation:read_scoped", room):
            raise forbidden()
        query = query.where(Reservation.room_id == room_id)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(
        query.order_by(Reservation.created_at.desc(), Reservation.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [view(row) for row in rows],
    }
