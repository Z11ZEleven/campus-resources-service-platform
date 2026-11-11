"""Permission-filtered search; results are observations, not reservations."""

from sqlalchemy import or_, select

from app.core.errors import conflict, forbidden
from app.modules.auth.permissions import room_scope_predicate
from app.modules.m1.period import precise_utcnow, validate_booking_period
from app.modules.resources.eligibility import conflicting_source
from app.modules.resources.models import OpeningException, OpeningRule, Room, Seat
from app.modules.resources.opening import calculate, covers


def search_seats(
    session, actor, identity_id, start_at, end_at, room_id, fixed, mobile, page, page_size
):
    if actor.identity_id != identity_id or actor.identity_type != "STUDENT":
        raise forbidden()
    if not actor.has_any("m1:seat:search") or not actor.has_any("m1:reservation:create"):
        raise forbidden()
    from app.modules.settings.service import read_int

    max_hours, _version = read_int(session, "m1.max_hours", 4)
    start, end = validate_booking_period(
        start_at, end_at, now=precise_utcnow(), max_hours=max_hours
    )
    query = (
        select(Room, Seat)
        .join(Seat, Seat.room_id == Room.id)
        .where(
            Room.is_active.is_(True),
            Seat.is_active.is_(True),
            ~conflicting_source(start, end),
            or_(Room.department_code.is_(None), Room.department_code == actor.department_code),
            room_scope_predicate(actor, "m1:seat:search"),
            room_scope_predicate(actor, "m1:reservation:create"),
        )
    )
    if room_id is not None:
        query = query.where(Room.id == room_id)
    if fixed is not None:
        query = query.where(Seat.has_fixed_socket == fixed)
    if mobile is not None:
        query = query.where(Seat.has_mobile_socket == mobile)
    rows = _consistent_open_seats(session, query, start, end)
    selected = rows[(page - 1) * page_size : page * page_size]
    return {
        "total": len(rows),
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "seat_id": str(seat.id),
                "room_id": str(room.id),
                "seat_no": seat.seat_no,
                "room_name": room.name,
                "building_name": room.building_name,
                "has_fixed_socket": bool(seat.has_fixed_socket),
                "has_mobile_socket": bool(seat.has_mobile_socket),
                "resource_version": str(room.resource_version),
            }
            for room, seat in selected
        ],
    }


def _consistent_open_seats(session, query, start, end):
    for _attempt in range(3):
        candidates = list(
            session.execute(
                query.order_by(Room.id, Seat.id).execution_options(populate_existing=True)
            )
        )
        rooms = {room.id: room for room, _seat in candidates}
        versions = {room.id: room.resource_version for room in rooms.values()}
        if not rooms:
            return []
        weekly = list(session.scalars(select(OpeningRule).where(OpeningRule.room_id.in_(rooms))))
        exceptions = list(
            session.scalars(
                select(OpeningException).where(
                    OpeningException.room_id.in_(rooms),
                    OpeningException.start_at < end,
                    OpeningException.end_at > start,
                )
            )
        )
        opened = {
            room_id: covers(
                calculate(
                    room,
                    [rule for rule in weekly if rule.room_id == room_id],
                    [rule for rule in exceptions if rule.room_id == room_id],
                    start,
                    end,
                ),
                start,
                end,
            )
            for room_id, room in rooms.items()
        }
        current = {
            room_id: version
            for room_id, version in session.execute(
                select(Room.id, Room.resource_version).where(Room.id.in_(rooms))
            )
        }
        if current == versions:
            return [(room, seat) for room, seat in candidates if opened[room.id]]
        session.expire_all()
    raise conflict("资源正在变化，请重试搜索")
