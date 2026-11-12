"""Resource administration implementation, called only through the public D service."""

from types import SimpleNamespace

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import conflict, forbidden, not_found
from app.core.time import to_utc, utcnow
from app.modules.audit.service import record_operation
from app.modules.resources.models import (
    Building,
    OpeningException,
    OpeningRule,
    ResourceBlock,
    Room,
    Seat,
)
from app.modules.resources.opening import calculate, covers


def changed(session, actor, room, action, business_id=None):
    room.resource_version += 1
    session.flush()
    record_operation(
        session,
        module="D",
        business_id=business_id or room.id,
        action=action,
        actor_id=actor.user_id,
        note=f"room={room.id};version={room.resource_version}",
    )


def persist(session):
    try:
        session.flush()
    except IntegrityError as exc:
        raise conflict("资源标识或组合关系冲突") from exc


def active_sources(session, room_id, seat_id=None):
    query = select(ResourceBlock).where(
        ResourceBlock.room_id == room_id,
        ResourceBlock.status == "ACTIVE",
        or_(ResourceBlock.end_at.is_(None), ResourceBlock.end_at > utcnow()),
    )
    if seat_id is not None:
        query = query.where(or_(ResourceBlock.seat_id.is_(None), ResourceBlock.seat_id == seat_id))
    return list(session.scalars(query))


def reject_sources(session, room_id, seat_id=None):
    if active_sources(session, room_id, seat_id):
        raise conflict("变更与有效资源来源冲突；须按后续免责处置契约处理")


def building_allowed(actor, building_id):
    return actor.can_for_room(
        "resources:manage", SimpleNamespace(id=None, building_id=building_id, department_code=None)
    )


def save_building(session, actor, payload, building_id=None):
    # Creating buildings requires a global resource grant. Existing buildings may
    # be renamed by their building-scoped administrator under all room locks.
    if not building_allowed(actor, building_id):
        raise forbidden()
    if building_id is None:
        row = Building(**payload.model_dump())
        session.add(row)
        persist(session)
    else:
        row = session.scalar(select(Building).where(Building.id == building_id).with_for_update())
        if row is None:
            raise not_found()
        rooms = list(
            session.scalars(
                select(Room)
                .where(Room.building_id == building_id)
                .order_by(Room.id)
                .with_for_update()
            )
        )
        if not payload.is_active:
            for room in rooms:
                reject_sources(session, room.id)
            if any(room.is_active for room in rooms):
                raise conflict("请先停用建筑内教室")
        for key, value in payload.model_dump().items():
            setattr(row, key, value)
        for room in rooms:
            room.building_name = row.name
            changed(session, actor, room, "UPDATE_BUILDING")
        persist(session)
    record_operation(
        session, module="D", business_id=row.id, action="SAVE_BUILDING", actor_id=actor.user_id
    )
    return row


def save_room(session, actor, payload, room_id=None):
    from app.modules.resources.service import lock_room

    values = payload.model_dump()
    building = session.scalar(
        select(Building).where(Building.id == payload.building_id).with_for_update()
    )
    if building is None or not building.is_active:
        raise not_found()
    if room_id is None:
        # Do not let a room-only grant predict a new ID. Creation needs a global
        # or building scope, evaluated against a candidate without room identity.
        if not building_allowed(actor, building.id):
            raise forbidden()
        room = Room(**values, building_name=building.name)
        session.add(room)
        persist(session)
    else:
        room = lock_room(session, room_id)
        actor.require_for_room("resources:manage", room)
        candidate = SimpleNamespace(
            id=room.id, building_id=building.id, department_code=payload.department_code
        )
        actor.require_for_room("resources:manage", candidate)
        if (
            not payload.is_active
            or payload.department_code != room.department_code
            or payload.building_id != room.building_id
            or payload.capacity < room.capacity
            or payload.open_time > room.open_time
            or payload.close_time < room.close_time
        ):
            reject_sources(session, room.id)
        for key, value in values.items():
            setattr(room, key, value)
        room.building_name = building.name
    changed(session, actor, room, "SAVE_ROOM")
    persist(session)
    return room


def save_seat(session, actor, room_id, payload, seat_id=None):
    from app.modules.resources.service import lock_room

    room = lock_room(session, room_id)
    actor.require_for_room("resources:manage", room)
    if seat_id is None:
        seat = Seat(room_id=room_id, **payload.model_dump())
        session.add(seat)
    else:
        seat = session.get(Seat, seat_id, populate_existing=True)
        if seat is None or seat.room_id != room_id:
            raise not_found()
        if not payload.is_active:
            reject_sources(session, room_id, seat_id)
        for key, value in payload.model_dump().items():
            setattr(seat, key, value)
    persist(session)
    changed(session, actor, room, "SAVE_SEAT", seat.id)
    return seat


def replace_schedule(session, actor, room_id, payload):
    from app.modules.resources.service import lock_room

    room = lock_room(session, room_id)
    actor.require_for_room("resources:manage", room)
    weekly = [OpeningRule(room_id=room_id, **r.model_dump()) for r in payload.weekly]
    special = [
        OpeningException(
            room_id=room_id, kind=r.kind, start_at=to_utc(r.start_at), end_at=to_utc(r.end_at)
        )
        for r in payload.exceptions
    ]
    candidate = SimpleNamespace(opening_rules_enabled=True)
    for source in active_sources(session, room.id):
        # Open-ended maintenance is deliberately not implicitly interrupted.
        if source.end_at is None:
            raise conflict("开放规则变更与有效维修来源冲突")
        if not covers(
            calculate(candidate, weekly, special, source.start_at, source.end_at),
            source.start_at,
            source.end_at,
        ):
            raise conflict("开放规则缩短与有效资源来源冲突")
    session.execute(delete(OpeningRule).where(OpeningRule.room_id == room_id))
    session.execute(delete(OpeningException).where(OpeningException.room_id == room_id))
    session.add_all([*weekly, *special])
    room.opening_rules_enabled = True
    persist(session)
    changed(session, actor, room, "REPLACE_OPENING_SCHEDULE")
    return room
