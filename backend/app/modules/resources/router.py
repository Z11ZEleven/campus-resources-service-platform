from datetime import UTC, date, timedelta
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request
from sqlalchemy import func, or_, select, true

from app.core.errors import not_found
from app.core.schema import Id
from app.core.time import utcnow
from app.modules.auth.dependencies import CurrentUser, Db
from app.modules.auth.permissions import room_scope_predicate
from app.modules.resources import restrictions, service
from app.modules.resources.models import (
    Building,
    OpeningException,
    OpeningRule,
    ResourceBlock,
    Room,
    Seat,
)
from app.modules.resources.schemas import (
    AdminRestrictionInput,
    AdminRestrictionView,
    AvailabilityView,
    BuildingInput,
    BuildingView,
    RoomInput,
    RoomView,
    ScheduleInput,
    SeatInput,
    SeatView,
    SlotView,
)
from app.modules.resources.service import availability, check_room_access

router = APIRouter(prefix="/api/v1/resources", tags=["公共资源"])


@router.get("/rooms")
def rooms(
    session: Db,
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = select(Room).order_by(Room.id)
    if user.has_any("resources:manage"):
        query = query.where(room_scope_predicate(user, "resources:manage", Room))
    else:
        query = query.where(
            Room.is_active.is_(True),
            or_(Room.department_code.is_(None), Room.department_code == user.department_code),
        )
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(query.offset((page - 1) * page_size).limit(page_size))
    return {
        "items": [RoomView.model_validate(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/rooms/{room_id}", response_model=RoomView)
def room_detail(room_id: Id, session: Db, user: CurrentUser):
    room = session.get(Room, room_id)
    if room is None:
        raise not_found()
    check_room_access(user, room)
    return room


@router.get("/rooms/{room_id}/seats")
def seats(
    room_id: Id,
    session: Db,
    user: CurrentUser,
    has_socket: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    room = session.get(Room, room_id)
    if room is None:
        raise not_found()
    check_room_access(user, room)
    query = select(Seat).where(Seat.room_id == room_id).order_by(Seat.id)
    if not user.can_for_room("resources:manage", room):
        query = query.where(Seat.is_active.is_(True))
    if has_socket is not None:
        has = or_(Seat.has_fixed_socket.is_(True), Seat.has_mobile_socket.is_(True))
        query = query.where(has if has_socket else ~has)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(query.offset((page - 1) * page_size).limit(page_size))
    return {
        "items": [SeatView.model_validate(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/rooms/{room_id}/availability", response_model=AvailabilityView)
def room_availability(room_id: Id, date: date, session: Db, user: CurrentUser):
    room, seats, rows = availability(session, room_id=room_id, day=date, actor=user)
    block_ids = {row.primary_block_id for row in rows if row.primary_block_id}
    modules = (
        dict(
            session.execute(
                select(ResourceBlock.id, ResourceBlock.source_module).where(
                    ResourceBlock.id.in_(block_ids)
                )
            ).all()
        )
        if block_ids
        else {}
    )
    now = utcnow()
    can_book = (
        user.has("m1:create")
        and user.identity_type == "STUDENT"
        and (room.department_code is None or room.department_code == user.department_code)
    )
    return AvailabilityView(
        room_id=room.id,
        date=date,
        seats=[SeatView.model_validate(row) for row in seats],
        slots=[
            SlotView(
                seat_id=row.seat_id,
                slot_start=row.slot_start,
                slot_end=row.slot_start + timedelta(hours=1),
                status=row.status,
                primary_block_id=row.primary_block_id,
                source_module=modules.get(row.primary_block_id),
                source_count=row.source_count,
                resource_version=row.resource_version,
                bookable=can_book and row.status == "AVAILABLE" and row.slot_start > now,
            )
            for row in rows
        ],
    )


@router.get("/buildings")
def buildings(session: Db, user: CurrentUser):
    query = select(Building).order_by(Building.id)
    if user.has_any("resources:manage"):
        # Building identifiers are protected by the same grants as their rooms.
        allowed_rooms = select(Room.building_id).where(
            room_scope_predicate(user, "resources:manage", Room)
        )
        building_scopes = [
            g.scope_value
            for g in user.effective_grants()
            if "resources:manage" in g.permissions and g.scope_type == "BUILDING"
        ]
        global_scope = any(
            g.scope_type == "GLOBAL"
            and g.scope_value == "*"
            and "resources:manage" in g.permissions
            for g in user.effective_grants()
        )
        predicate = (
            true()
            if global_scope
            else or_(Building.id.in_(allowed_rooms), Building.id.in_(building_scopes))
        )
        rows = list(session.scalars(query.where(predicate)))
    else:
        visible = select(Room.building_id).where(
            Room.is_active.is_(True),
            or_(Room.department_code.is_(None), Room.department_code == user.department_code),
        )
        rows = list(
            session.scalars(query.where(Building.is_active.is_(True), Building.id.in_(visible)))
        )
    return {"items": [BuildingView.model_validate(row) for row in rows]}


@router.post("/buildings", response_model=BuildingView, status_code=201)
def create_building(payload: BuildingInput, session: Db, user: CurrentUser):
    return service.save_building(session, user, payload)


@router.put("/buildings/{building_id}", response_model=BuildingView)
def update_building(building_id: Id, payload: BuildingInput, session: Db, user: CurrentUser):
    return service.save_building(session, user, payload, building_id)


@router.post("/rooms", response_model=RoomView, status_code=201)
def create_room(payload: RoomInput, session: Db, user: CurrentUser):
    return service.save_room(session, user, payload)


@router.put("/rooms/{room_id}", response_model=RoomView)
def update_room(room_id: Id, payload: RoomInput, request: Request, session: Db, user: CurrentUser):
    room = service.lock_room(session, room_id)
    from app.modules.m1.reservations import refreshed_actor

    user = refreshed_actor(session, request.state.auth_session.id, user)
    user.require_for_room("resources:manage", room)
    if room.is_active and not payload.is_active:
        from app.modules.m1.resource_impact import apply_room_deactivation

        apply_room_deactivation(session, room=room, effective_at=utcnow(), actor=user)
    elif not room.opening_rules_enabled and (
        payload.open_time > room.open_time or payload.close_time < room.close_time
    ):
        from app.modules.m1.resource_impact import apply_schedule_change

        schedule = ScheduleInput(
            weekly=[
                {"weekday": day, "start_time": payload.open_time, "end_time": payload.close_time}
                for day in range(7)
            ]
        )
        apply_schedule_change(
            session, room=room, payload=schedule, effective_at=utcnow(), actor=user
        )
    return service.save_room(session, user, payload, room_id)


@router.post("/rooms/{room_id}/seats", response_model=SeatView, status_code=201)
def create_seat(room_id: Id, payload: SeatInput, session: Db, user: CurrentUser):
    return service.save_seat(session, user, room_id, payload)


@router.put("/rooms/{room_id}/seats/{seat_id}", response_model=SeatView)
def update_seat(
    room_id: Id, seat_id: Id, payload: SeatInput, request: Request, session: Db, user: CurrentUser
):
    from app.modules.m1.reservations import refreshed_actor
    from app.modules.m1.resource_impact import apply_seat_deactivation

    room = service.lock_room(session, room_id)
    user = refreshed_actor(session, request.state.auth_session.id, user)
    user.require_for_room("resources:manage", room)
    seat = session.get(Seat, seat_id, populate_existing=True)
    if seat is None or seat.room_id != room.id:
        raise not_found()
    affected = []
    if seat.is_active and not payload.is_active:
        affected = apply_seat_deactivation(
            session, room=room, seat_id=seat_id, effective_at=utcnow(), actor=user
        )
    seat = service.save_seat(session, user, room_id, payload, seat_id)
    result = SeatView.model_validate(seat).model_dump()
    result.update(affected_reservation_count=len(affected), affected_reservation_ids=affected)
    return result


@router.get("/rooms/{room_id}/opening", response_model=ScheduleInput)
def opening_schedule(room_id: Id, session: Db, user: CurrentUser):
    room = session.get(Room, room_id)
    if room is None:
        raise not_found()
    check_room_access(user, room)
    return {
        "weekly": [
            dict(weekday=r.weekday, start_time=r.start_time, end_time=r.end_time)
            for r in session.scalars(select(OpeningRule).where(OpeningRule.room_id == room_id))
        ],
        "exceptions": [
            dict(
                kind=r.kind,
                start_at=r.start_at.replace(tzinfo=UTC),
                end_at=r.end_at.replace(tzinfo=UTC),
            )
            for r in session.scalars(
                select(OpeningException).where(OpeningException.room_id == room_id)
            )
        ],
    }


@router.put("/rooms/{room_id}/opening", response_model=RoomView)
def update_schedule(
    room_id: Id, payload: ScheduleInput, request: Request, session: Db, user: CurrentUser
):
    from app.modules.m1.reservations import refreshed_actor
    from app.modules.m1.resource_impact import apply_schedule_change

    room = service.lock_room(session, room_id)
    user = refreshed_actor(session, request.state.auth_session.id, user)
    user.require_for_room("resources:manage", room)
    apply_schedule_change(session, room=room, payload=payload, effective_at=utcnow(), actor=user)
    return service.replace_schedule(session, user, room_id, payload)


@router.post("/rooms/{room_id}/restrictions", response_model=AdminRestrictionView, status_code=201)
def create_admin_restriction(
    room_id: Id,
    payload: AdminRestrictionInput,
    request: Request,
    session: Db,
    user: CurrentUser,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)],
):
    return restriction_view(
        restrictions.create_restriction(
            session, user, room_id, payload, idempotency_key, request.state.auth_session.id
        )
    )


@router.delete(
    "/rooms/{room_id}/restrictions/{restriction_id}", response_model=AdminRestrictionView
)
def release_admin_restriction(
    room_id: Id, restriction_id: Id, request: Request, session: Db, user: CurrentUser
):
    return restriction_view(
        restrictions.release_restriction(
            session, user, room_id, restriction_id, request.state.auth_session.id
        )
    )


def restriction_view(block):
    return {
        "id": block.source_id,
        "room_id": block.room_id,
        "seat_id": block.seat_id,
        "start_at": block.start_at,
        "end_at": block.end_at,
        "status": block.status,
    }
