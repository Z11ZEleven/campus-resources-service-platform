from datetime import date, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select

from app.core.errors import not_found
from app.core.schema import Id
from app.core.time import utcnow
from app.modules.auth.dependencies import CurrentUser, Db
from app.modules.resources.models import ResourceBlock, Room, Seat
from app.modules.resources.schemas import AvailabilityView, RoomView, SeatView, SlotView
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
    if not user.has("resources:manage"):
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
    if not user.has("resources:manage"):
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
