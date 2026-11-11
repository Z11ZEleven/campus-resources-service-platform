"""D-owned, source-isolated administrator restrictions."""

import hashlib

from sqlalchemy import or_, select

from app.core.errors import DomainError, conflict, not_found
from app.core.time import to_utc, utcnow
from app.modules.audit.service import record_operation
from app.modules.auth.sessions import refresh_principal
from app.modules.resources.models import AdminRestrictionRequest, ResourceBlock, Seat
from app.modules.resources.service import _insert, lock_m1_user, lock_room


def create_restriction(session, actor, room_id, payload, key, auth_session_id):
    room = lock_room(session, room_id)
    actor = refresh_principal(session, auth_session_id, actor.user_id)
    actor.require_for_room("resources:manage", room)
    try:
        start, end = to_utc(payload.start_at), to_utc(payload.end_at)
    except ValueError as exc:
        raise DomainError(422, "TIMEZONE_REQUIRED", "时间必须包含时区") from exc
    if end <= start:
        raise DomainError(422, "INVALID_PERIOD", "限制时段必须为正时长")
    lock_m1_user(session, actor.user_id)
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    prior = session.scalar(
        select(AdminRestrictionRequest).where(
            AdminRestrictionRequest.actor_id == actor.user_id,
            AdminRestrictionRequest.key_hash == key_hash,
        )
    )
    if prior:
        block = session.scalar(
            select(ResourceBlock).where(
                ResourceBlock.source_module == "D", ResourceBlock.source_id == prior.id
            )
        )
        if (
            not block
            or (block.room_id, block.seat_id, block.start_at, block.end_at)
            != (room.id, payload.seat_id, start, end)
            or block.status != "ACTIVE"
        ):
            raise conflict("相同请求键的限制内容或状态不一致")
        return block
    if not room.is_active:
        raise conflict("教室已停用")
    if payload.seat_id is not None:
        seat = session.get(Seat, payload.seat_id, populate_existing=True)
        if seat is None or seat.room_id != room.id:
            raise not_found()
        if not seat.is_active:
            raise conflict("座位已停用")
    overlap = select(ResourceBlock.id).where(
        ResourceBlock.room_id == room.id,
        ResourceBlock.source_module.in_(["M1", "M2"]),
        ResourceBlock.status == "ACTIVE",
        ResourceBlock.start_at < end,
        or_(ResourceBlock.end_at.is_(None), ResourceBlock.end_at > start),
    )
    if payload.seat_id is not None:
        overlap = overlap.where(
            or_(ResourceBlock.seat_id.is_(None), ResourceBlock.seat_id == payload.seat_id)
        )
    if session.scalar(overlap.limit(1)) is not None:
        raise conflict("限制时段与已有预约或整室占用冲突")
    request = AdminRestrictionRequest(actor_id=actor.user_id, key_hash=key_hash)
    session.add(request)
    session.flush()
    return _insert(
        session,
        room,
        module="D",
        source_id=request.id,
        seat_id=payload.seat_id,
        start=start,
        end=end,
        actor=actor,
    )


def release_restriction(session, actor, room_id, restriction_id, auth_session_id):
    room = lock_room(session, room_id)
    actor = refresh_principal(session, auth_session_id, actor.user_id)
    actor.require_for_room("resources:manage", room)
    block = session.scalar(
        select(ResourceBlock)
        .where(
            ResourceBlock.source_module == "D",
            ResourceBlock.source_id == restriction_id,
            ResourceBlock.room_id == room.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if block is None:
        raise not_found()
    if block.status == "RELEASED":
        return block
    block.status, block.released_at = "RELEASED", utcnow()
    block.revision += 1
    room.resource_version += 1
    record_operation(
        session,
        module="D",
        business_id=block.source_id,
        action="RELEASE_RESOURCE",
        actor_id=actor.user_id,
        from_status="ACTIVE",
        to_status="RELEASED",
    )
    session.flush()
    return block
