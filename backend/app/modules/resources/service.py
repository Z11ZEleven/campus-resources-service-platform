"""Public D integration contract. No function commits, no module-private tables.

M1/M2/M3 must lock_room FIRST, then lock/validate their business record and call
these functions in the SAME transaction. Source existence, ownership and legal
business-state transitions remain the caller's responsibility. No raw HTTP
endpoint exposes resource mutation to end users.
"""

from datetime import date, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import require_transaction
from app.core.errors import DomainError, conflict, forbidden, not_found
from app.core.time import BUSINESS_TZ, as_utc, business_date, to_utc, utcnow
from app.modules.audit.service import record_operation
from app.modules.auth.permissions import Principal, load_principal
from app.modules.resources.models import ResourceBlock, Room, Seat, SeatStatusSummary


def lock_room(session: Session, room_id: int) -> Room:
    require_transaction(session)
    room = session.scalar(
        select(Room)
        .where(Room.id == room_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if room is None:
        raise not_found()
    return room


def can_read_room(actor: Principal, room: Room) -> bool:
    return actor.can_for_room("resources:manage", room) or (
        not actor.has_any("resources:manage")
        and bool(room.is_active)
        and (room.department_code is None or room.department_code == actor.department_code)
    )


def check_room_access(actor: Principal, room: Room):
    if not can_read_room(actor, room):
        raise not_found()


def check_booking_eligibility(actor: Principal, room: Room):
    if not room.is_active:
        raise conflict("教室已停用")
    if room.department_code is not None and room.department_code != actor.department_code:
        raise forbidden()


def validate_day(day: date):
    today = business_date()
    if not today <= day < today + timedelta(days=get_settings().booking_window_days):
        raise DomainError(422, "OUTSIDE_WINDOW", "日期超出可预约窗口")


def validate_period(room: Room, start_at: datetime, end_at: datetime, *, m1: bool, session=None):
    try:
        start, end = to_utc(start_at), to_utc(end_at)
    except ValueError as exc:
        raise DomainError(422, "TIMEZONE_REQUIRED", "时间必须包含时区") from exc
    local_start = as_utc(start).astimezone(BUSINESS_TZ)
    local_end = as_utc(end).astimezone(BUSINESS_TZ)
    if any(
        (value.minute, value.second, value.microsecond) != (0, 0, 0)
        for value in (local_start, local_end)
    ):
        raise DomainError(422, "WHOLE_HOUR_REQUIRED", "本轮仅支持整点预约")
    if start <= utcnow() or end <= start:
        raise DomainError(422, "INVALID_PERIOD", "请提交未来的正时长预约")
    validate_day(local_start.date())
    if local_start.date() != local_end.date():
        raise DomainError(422, "SAME_DAY_REQUIRED", "本轮不支持跨日预约")
    from app.modules.resources.opening import is_open

    if session is not None:
        opened = is_open(session, room, start, end)
    else:
        opened = (
            local_start.time().replace(tzinfo=None) >= room.open_time
            and local_end.time().replace(tzinfo=None) <= room.close_time
        )
    if not opened:
        raise conflict("时段不在教室完整开放区间内")
    if m1 and end - start > timedelta(hours=get_settings().booking_max_hours):
        raise DomainError(422, "TOO_LONG", "超出个人预约最长时长")
    return start, end


def _existing(session, module, source_id):
    return session.scalar(
        select(ResourceBlock)
        .where(ResourceBlock.source_module == module, ResourceBlock.source_id == source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _reuse(existing, room_id, seat_id, start=None, end=None):
    if existing is None:
        return None
    if existing.status != "ACTIVE":
        raise conflict("该业务来源已释放，不允许重新激活")
    if (existing.room_id, existing.seat_id) != (room_id, seat_id):
        raise conflict("相同来源不能更换资源")
    if start is not None and (existing.start_at, existing.end_at) != (start, end):
        raise conflict("相同来源的时段不一致")
    return existing


def _overlap(session, room_id, start, end, seat_id=None):
    query = select(ResourceBlock.id).where(
        ResourceBlock.room_id == room_id,
        ResourceBlock.status == "ACTIVE",
        ResourceBlock.start_at < end,
        or_(ResourceBlock.end_at.is_(None), ResourceBlock.end_at > start),
    )
    if seat_id is not None:
        query = query.where(or_(ResourceBlock.seat_id.is_(None), ResourceBlock.seat_id == seat_id))
    return session.scalar(query.limit(1)) is not None


def _insert(session, room, *, module, source_id, seat_id, start, end, actor):
    if source_id <= 0:
        raise DomainError(422, "INVALID_SOURCE", "来源 ID 必须大于零")
    row = ResourceBlock(
        room_id=room.id,
        seat_id=seat_id,
        source_module=module,
        source_id=source_id,
        start_at=start,
        end_at=end,
        status="ACTIVE",
    )
    session.add(row)
    room.resource_version += 1
    session.flush()
    from app.modules.resources.allocations import materialize_source

    materialize_source(session, row)
    record_operation(
        session,
        module=module,
        business_id=source_id,
        action="ACQUIRE_RESOURCE",
        actor_id=actor.user_id,
        to_status="ACTIVE",
    )
    return row


def reserve_seat(
    session: Session,
    *,
    room_id: int,
    seat_id: int,
    reservation_id: int,
    start_at: datetime,
    end_at: datetime,
    actor: Principal,
) -> ResourceBlock:
    actor.require("m1:create")
    if actor.identity_type != "STUDENT":
        raise forbidden()
    room = lock_room(session, room_id)
    # Caller validates business status first. Retry of the same active source
    # can be returned even after time has advanced, but cannot change payload.
    start, end = to_utc(start_at), to_utc(end_at)
    reused = _reuse(_existing(session, "M1", reservation_id), room_id, seat_id, start, end)
    if reused:
        return reused
    check_booking_eligibility(actor, room)
    validate_period(room, start_at, end_at, m1=True, session=session)
    seat = session.get(Seat, seat_id, populate_existing=True)
    if seat is None or seat.room_id != room.id:
        raise not_found()
    if not seat.is_active:
        raise conflict("座位已停用")
    if _overlap(session, room.id, start, end, seat_id):
        raise conflict("座位或教室时段已被占用/限制")
    return _insert(
        session,
        room,
        module="M1",
        source_id=reservation_id,
        seat_id=seat_id,
        start=start,
        end=end,
        actor=actor,
    )


def occupy_room(
    session: Session,
    *,
    room_id: int,
    application_id: int,
    applicant_id: int,
    start_at: datetime,
    end_at: datetime,
    actor: Principal,
) -> ResourceBlock:
    actor.require("m2:approve")
    if actor.user_id == applicant_id:
        raise forbidden()
    room = lock_room(session, room_id)
    start, end = to_utc(start_at), to_utc(end_at)
    reused = _reuse(_existing(session, "M2", application_id), room_id, None, start, end)
    if reused:
        return reused
    applicant = load_principal(session, applicant_id)
    applicant.require("m2:create")
    if applicant.identity_type != "TEACHER":
        raise forbidden()
    check_booking_eligibility(applicant, room)
    validate_period(room, start_at, end_at, m1=False, session=session)
    if _overlap(session, room.id, start, end):
        raise conflict("整教室时段与已有预约或维修冲突")
    return _insert(
        session,
        room,
        module="M2",
        source_id=application_id,
        seat_id=None,
        start=start,
        end=end,
        actor=actor,
    )


def start_maintenance(
    session: Session,
    *,
    room_id: int,
    work_order_id: int,
    actor: Principal,
) -> ResourceBlock:
    actor.require("m3:start")
    room = lock_room(session, room_id)
    reused = _reuse(_existing(session, "M3", work_order_id), room_id, None)
    if reused:
        return reused
    # Existing reservations are NOT cancelled in iteration 1.
    return _insert(
        session,
        room,
        module="M3",
        source_id=work_order_id,
        seat_id=None,
        start=utcnow(),
        end=None,
        actor=actor,
    )


def release_source(
    session: Session,
    *,
    room_id: int,
    source_module: str,
    source_id: int,
    actor: Principal,
) -> ResourceBlock:
    permissions = {"M1": "m1:cancel", "M2": "m2:release", "M3": "m3:accept"}
    if source_module not in permissions:
        raise DomainError(422, "INVALID_SOURCE", "无效来源")
    actor.require(permissions[source_module])
    room = lock_room(session, room_id)
    row = _existing(session, source_module, source_id)
    if row is None or row.room_id != room.id:
        raise not_found()
    if row.status == "RELEASED":
        return row
    row.status, row.released_at = "RELEASED", utcnow()
    from app.modules.resources.allocations import release_allocations

    release_allocations(session, row)
    room.resource_version += 1
    session.flush()
    record_operation(
        session,
        module=source_module,
        business_id=source_id,
        action="RELEASE_RESOURCE",
        actor_id=actor.user_id,
        from_status="ACTIVE",
        to_status="RELEASED",
    )
    return row


def _day_bounds(day):
    start = to_utc(datetime.combine(day, datetime.min.time(), tzinfo=BUSINESS_TZ))
    return start, start + timedelta(days=1)


def _slot_state(room, seat, slot_start, matches, opened=None):
    local_start = as_utc(slot_start).astimezone(BUSINESS_TZ)
    local_end = local_start + timedelta(hours=1)
    if not room.is_active or not seat.is_active:
        return "DISABLED", None
    maintenance = [row for row in matches if row.source_module == "M3"]
    if maintenance:
        return "MAINTENANCE", min(maintenance, key=lambda row: row.id)
    if opened is False or (
        opened is None
        and (
            local_start.time() < room.open_time
            or (local_end.date() != local_start.date() or local_end.time() > room.close_time)
        )
    ):
        return "CLOSED", None
    for module, status in (("M2", "ROOM_BOOKED"), ("M1", "RESERVED")):
        rows = [row for row in matches if row.source_module == module]
        if rows:
            return status, min(rows, key=lambda row: row.id)
    return "AVAILABLE", None


def availability(session: Session, *, room_id: int, day: date, actor: Principal):
    validate_day(day)
    room = lock_room(session, room_id)
    check_room_access(actor, room)
    start, end = _day_bounds(day)
    seats = list(session.scalars(select(Seat).where(Seat.room_id == room.id).order_by(Seat.id)))
    rows = list(
        session.scalars(
            select(SeatStatusSummary)
            .join(Seat, Seat.id == SeatStatusSummary.seat_id)
            .where(
                Seat.room_id == room.id,
                SeatStatusSummary.slot_start >= start,
                SeatStatusSummary.slot_start < end,
            )
            .execution_options(populate_existing=True)
        )
    )
    if len(rows) != len(seats) * 24 or any(
        row.resource_version != room.resource_version for row in rows
    ):
        blocks = list(
            session.scalars(
                select(ResourceBlock).where(
                    ResourceBlock.room_id == room.id,
                    ResourceBlock.status == "ACTIVE",
                    ResourceBlock.start_at < end,
                    or_(ResourceBlock.end_at.is_(None), ResourceBlock.end_at > start),
                )
            )
        )
        from app.modules.resources.opening import covers, intervals

        opened_intervals = intervals(session, room, start, end)
        existing = {(row.seat_id, row.slot_start): row for row in rows}
        rows = []
        computed_at = utcnow()
        for seat in seats:
            for hour in range(24):
                slot = start + timedelta(hours=hour)
                matches = [
                    row
                    for row in blocks
                    if (
                        (row.seat_id is None or row.seat_id == seat.id)
                        and row.start_at < slot + timedelta(hours=1)
                        and (row.end_at is None or row.end_at > slot)
                    )
                ]
                state, primary = _slot_state(
                    room,
                    seat,
                    slot,
                    matches,
                    covers(opened_intervals, slot, slot + timedelta(hours=1)),
                )
                row = existing.get((seat.id, slot))
                if row is None:
                    row = SeatStatusSummary(seat_id=seat.id, slot_start=slot)
                    session.add(row)
                row.status = state
                row.primary_block_id = primary.id if primary else None
                row.source_count = len(matches)
                row.resource_version = room.resource_version
                row.computed_at = computed_at
                rows.append(row)
        session.flush()
    return room, seats, sorted(rows, key=lambda row: (row.seat_id, row.slot_start))


def set_room_active(session: Session, *, room_id: int, is_active: bool, actor: Principal):
    """Internal management hook: bump cache version under the same lock."""
    room = lock_room(session, room_id)
    actor.require_for_room("resources:manage", room)
    if bool(room.is_active) != is_active:
        if not is_active:
            from app.modules.resources.management import reject_sources

            reject_sources(session, room_id)
        room.is_active = is_active
        room.resource_version += 1
        record_operation(
            session,
            module="D",
            business_id=room.id,
            action="SET_ROOM_ACTIVE",
            actor_id=actor.user_id,
            note=str(is_active),
        )
    return room


def set_seat_active(
    session: Session,
    *,
    room_id: int,
    seat_id: int,
    is_active: bool,
    actor: Principal,
):
    room = lock_room(session, room_id)
    actor.require_for_room("resources:manage", room)
    seat = session.get(Seat, seat_id, populate_existing=True)
    if seat is None or seat.room_id != room.id:
        raise not_found()
    if bool(seat.is_active) != is_active:
        if not is_active:
            from app.modules.resources.management import reject_sources

            reject_sources(session, room_id, seat_id)
        seat.is_active = is_active
        room.resource_version += 1
        record_operation(
            session,
            module="D",
            business_id=seat.id,
            action="SET_SEAT_ACTIVE",
            actor_id=actor.user_id,
            note=str(is_active),
        )
    return seat


# Public management contract: implementations remain in the same D resources
# domain; callers never write another module's tables.
from app.modules.resources.management import (  # noqa: E402,F401
    replace_schedule,
    save_building,
    save_room,
    save_seat,
)
