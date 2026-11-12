"""Public D integration contract. No function commits, no module-private tables.

M1/M2/M3 must lock_room FIRST, then lock/validate their business record and call
these functions in the SAME transaction. Source existence, ownership and legal
business-state transitions remain the caller's responsibility. No raw HTTP
endpoint exposes resource mutation to end users.
"""

from datetime import date, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
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


def validate_whole_room_period(room, start_at, end_at, *, minimum_days=0):
    """整室时段不沿用 M1 的7天窗口；提交时另传提前2个自然日。"""
    try:
        start, end = to_utc(start_at), to_utc(end_at)
    except ValueError as exc:
        raise DomainError(422, "TIMEZONE_REQUIRED", "时间必须包含时区") from exc
    local_start, local_end = start_at.astimezone(BUSINESS_TZ), end_at.astimezone(BUSINESS_TZ)
    if any(value.minute or value.second or value.microsecond for value in (local_start, local_end)):
        raise DomainError(422, "WHOLE_HOUR_REQUIRED", "使用时间必须为北京时间整点")
    if start <= utcnow() or not timedelta(hours=1) <= end-start <= timedelta(hours=4):
        raise DomainError(422, "INVALID_PERIOD", "请申请未来1至4小时的时段")
    if local_start.date() != local_end.date():
        raise DomainError(422, "SAME_DAY_REQUIRED", "申请不得跨自然日")
    if local_start.date() < business_date() + timedelta(days=minimum_days):
        raise DomainError(422, "ADVANCE_REQUIRED", "至少提前两个北京时间自然日申请")
    if not (8 <= local_start.hour < local_end.hour <= 22) or (
        local_start.time().replace(tzinfo=None) < room.open_time
        or local_end.time().replace(tzinfo=None) > room.close_time
    ):
        raise DomainError(422, "OUTSIDE_HOURS", "时段须在08:00至22:00及教室开放时间内")
    return start, end


def _existing(session, module, source_id):
    return session.scalar(
        select(ResourceBlock)
        .where(ResourceBlock.source_module == module, ResourceBlock.source_id == source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _audit_identity(actor):
    from app.modules.auth.models import ServicePrincipal

    if isinstance(actor, ServicePrincipal):
        return {"actor_type": "SERVICE", "service_principal_id": actor.id}
    return {"actor_id": actor.user_id}


def _advance_revision(session, room, row, revision, actor):
    row.revision = revision
    row.is_versioned = True
    room.resource_version += 1
    record_operation(
        session,
        module=row.source_module,
        business_id=row.source_id,
        action="ADVANCE_RESOURCE_REVISION",
        **_audit_identity(actor),
        from_status=row.status,
        to_status=row.status,
        note=str(revision),
    )
    session.flush()


def _reuse(session, room, existing, seat_id, actor, start=None, end=None, revision=None):
    if existing is None:
        return None
    if revision is None and existing.is_versioned:
        raise conflict("版本化来源必须提供版本")
    if revision is not None and revision < existing.revision:
        raise conflict("来源版本已过期")
    if existing.status != "ACTIVE":
        raise conflict("该业务来源已释放，不允许重新激活")
    if (existing.room_id, existing.seat_id) != (room.id, seat_id):
        raise conflict("相同来源不能更换资源")
    if start is not None and (existing.start_at, existing.end_at) != (start, end):
        raise conflict("相同来源的时段不一致")
    if revision is not None and (revision > existing.revision or not existing.is_versioned):
        _advance_revision(session, room, existing, revision, actor)
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


def _insert(
    session, room, *, module, source_id, seat_id, start, end, actor, revision=None, released=False
):
    if source_id <= 0:
        raise DomainError(422, "INVALID_SOURCE", "来源 ID 必须大于零")
    row = ResourceBlock(
        room_id=room.id,
        seat_id=seat_id,
        source_module=module,
        source_id=source_id,
        start_at=start,
        end_at=end,
        status="RELEASED" if released else "ACTIVE",
        released_at=utcnow() if released else None,
        revision=revision if revision is not None else 0,
        is_versioned=revision is not None,
    )
    session.flush()  # Flush unrelated pending work outside the conflict handler.
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError as exc:
        if exc.orig.args and exc.orig.args[0] == 1062 and "uq_block_source" in str(exc.orig):
            raise conflict("业务来源已存在") from exc
        raise
    room.resource_version += 1
    session.flush()
    if released:
        record_operation(
            session,
            module=module,
            business_id=source_id,
            action="RELEASE_RESOURCE",
            **_audit_identity(actor),
            to_status="RELEASED",
        )
        return row
    from app.modules.resources.allocations import materialize_source

    materialize_source(session, row)
    record_operation(
        session,
        module=module,
        business_id=source_id,
        action="ACQUIRE_RESOURCE",
        **_audit_identity(actor),
        to_status="ACTIVE",
    )
    return row


def lock_m1_user(session, user_id):
    """Lock a user actor after the room and before the M1 source sequence."""
    from app.modules.auth.models import User

    if session.scalar(select(User.id).where(User.id == user_id).with_for_update()) is None:
        raise not_found()


def _lock_m1_source_sequence(session):
    from sqlalchemy.dialects.mysql import insert

    from app.modules.resources.models import M1SourceSequence

    session.execute(
        insert(M1SourceSequence).values(id=1, high_water=0).on_duplicate_key_update(id=1)
    )
    return session.scalar(
        select(M1SourceSequence)
        .where(M1SourceSequence.id == 1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def allocate_m1_source_id(session, *, reservation_floor=0):
    """Allocate under a shared lock also held by legacy M1 source writers."""
    sequence = _lock_m1_source_sequence(session)
    from sqlalchemy import func

    maximum = (
        session.scalar(
            select(func.max(ResourceBlock.source_id)).where(ResourceBlock.source_module == "M1")
        )
        or 0
    )
    value = max(sequence.high_water, maximum, reservation_floor) + 1
    if value > 2**64 - 1:
        raise conflict("M1 来源标识空间已耗尽")
    sequence.high_water = value
    session.flush()
    return value


def reserve_seat(
    session: Session,
    *,
    room_id: int,
    seat_id: int,
    reservation_id: int,
    start_at: datetime,
    end_at: datetime,
    actor: Principal,
    revision: int | None = None,
) -> ResourceBlock:
    actor.require("m1:create")
    if actor.identity_type != "STUDENT":
        raise forbidden()
    if revision is not None and (
        not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 0
        or revision > 2**64 - 1
    ):
        raise DomainError(422, "INVALID_REVISION", "来源版本必须为非负整数")
    room = lock_room(session, room_id)
    lock_m1_user(session, actor.user_id)
    # Caller validates business status first. Retry of the same active source
    # can be returned even after time has advanced, but cannot change payload.
    start, end = to_utc(start_at), to_utc(end_at)
    reused = _reuse(
        session,
        room,
        _existing(session, "M1", reservation_id),
        seat_id,
        actor,
        start,
        end,
        revision,
    )
    if reused:
        return reused
    check_booking_eligibility(actor, room)
    if room.booking_mode != "SEAT":
        raise conflict("该教室仅接受整室申请，不接受个人座位预约")
    validate_period(room, start_at, end_at, m1=True, session=session)
    seat = session.get(Seat, seat_id, populate_existing=True)
    if seat is None or seat.room_id != room.id:
        raise not_found()
    if not seat.is_active:
        raise conflict("座位已停用")
    if _overlap(session, room.id, start, end, seat_id):
        raise conflict("座位或教室时段已被占用/限制")
    sequence = _lock_m1_source_sequence(session)
    sequence.high_water = max(sequence.high_water, reservation_id)
    return _insert(
        session,
        room,
        module="M1",
        source_id=reservation_id,
        seat_id=seat_id,
        start=start,
        end=end,
        actor=actor,
        revision=revision,
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
    revision: int | None = None,
) -> ResourceBlock:
    actor.require("m2:approve")
    if actor.user_id == applicant_id:
        raise forbidden()
    if revision is not None and (
        not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 0
        or revision > 2**64 - 1
    ):
        raise DomainError(422, "INVALID_REVISION", "来源版本必须为非负整数")
    room = lock_room(session, room_id)
    start, end = to_utc(start_at), to_utc(end_at)
    reused = _reuse(
        session, room, _existing(session, "M2", application_id), None, actor, start, end, revision
    )
    if reused:
        return reused
    applicant = load_principal(session, applicant_id)
    if room.booking_mode == "WHOLE_ROOM":
        from app.modules.auth.scopes import approval_requirement, require_scope

        require_scope(session, actor, approval_requirement(room, 2))
        validate_whole_room_period(room, start_at, end_at)
    else:
        # 保留现有公开函数对历史 SEAT 资源的行为；新 M2 网络入口只接受 WHOLE_ROOM。
        applicant.require("m2:create")
        if applicant.identity_type != "TEACHER":
            raise forbidden()
        validate_period(room, start_at, end_at, m1=False)
    check_booking_eligibility(applicant, room)
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
        revision=revision,
    )


def start_maintenance(
    session: Session,
    *,
    room_id: int,
    work_order_id: int,
    actor: Principal,
    revision: int | None = None,
) -> ResourceBlock:
    actor.require("m3:start")
    if revision is not None and (
        not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 0
        or revision > 2**64 - 1
    ):
        raise DomainError(422, "INVALID_REVISION", "来源版本必须为非负整数")
    room = lock_room(session, room_id)
    reused = _reuse(
        session, room, _existing(session, "M3", work_order_id), None, actor, revision=revision
    )
    if reused:
        return reused
    # Caller applies M1 impact through its public callback in the same transaction.
    return _insert(
        session,
        room,
        module="M3",
        source_id=work_order_id,
        seat_id=None,
        start=utcnow(),
        end=None,
        actor=actor,
        revision=revision,
    )


def release_source(
    session: Session,
    *,
    room_id: int,
    source_module: str,
    source_id: int,
    actor: Principal,
    revision: int | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> ResourceBlock:
    permissions = {"M1": "m1:cancel", "M2": "m2:release", "M3": "m3:accept"}
    if source_module not in permissions:
        raise DomainError(422, "INVALID_SOURCE", "无效来源")
    actor.require(permissions[source_module])
    if revision is not None and (
        not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 0
        or revision > 2**64 - 1
    ):
        raise DomainError(422, "INVALID_REVISION", "来源版本必须为非负整数")
    legacy_m2_release = (
        source_module == "M2" and revision is None and start_at is None and end_at is None
    )
    if (source_module == "M2" and not legacy_m2_release and (start_at is None or end_at is None)) or (
        source_module != "M2" and (start_at is not None or end_at is not None)
    ):
        raise DomainError(422, "INVALID_PERIOD", "M2 释放须提供原时段，M1/M3 不接受时段")
    try:
        start = to_utc(start_at) if start_at is not None else None
        end = to_utc(end_at) if end_at is not None else None
    except ValueError as exc:
        raise DomainError(422, "TIMEZONE_REQUIRED", "时间必须包含时区") from exc
    if start is not None and end <= start:
        raise DomainError(422, "INVALID_PERIOD", "时段必须为正时长")
    room = lock_room(session, room_id)
    row = _existing(session, source_module, source_id)
    if legacy_m2_release and (row is None or row.is_versioned):
        raise DomainError(422, "INVALID_PERIOD", "版本化 M2 释放须提供原时段")
    if row is None:
        if revision is None:
            raise not_found()
        return _insert(
            session,
            room,
            module=source_module,
            source_id=source_id,
            seat_id=None,
            start=start,
            end=end,
            actor=actor,
            revision=revision,
            released=True,
        )
    if row.room_id != room.id:
        raise conflict("相同来源不能更换资源")
    if start is not None and (row.start_at, row.end_at) != (start, end):
        raise conflict("相同来源的时段不一致")
    if revision is None and row.is_versioned:
        raise conflict("版本化来源必须提供版本")
    if revision is not None and revision < row.revision:
        raise conflict("来源版本已过期")
    if row.status == "RELEASED":
        if revision is not None and (revision > row.revision or not row.is_versioned):
            _advance_revision(session, room, row, revision, actor)
        return row
    if revision is not None and revision == row.revision:
        raise conflict("相同版本状态不一致")
    if revision is None and row.revision == 2**64 - 1:
        raise conflict("来源版本已达上限")
    row.revision = revision if revision is not None else row.revision + 1
    row.is_versioned = revision is not None
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


def expire_m2_source(session: Session, *, room_id: int, source_id: int):
    """仅供已认证 D 命令服务在审批截止后补偿；不对终端用户暴露。"""
    room = lock_room(session, room_id)
    row = _existing(session, "M2", source_id)
    if row is None or row.room_id != room_id or row.status == "RELEASED":
        return row
    row.status, row.released_at = "RELEASED", utcnow()
    from app.modules.resources.allocations import release_allocations

    release_allocations(session, row)
    room.resource_version += 1
    record_operation(session, module="M2", business_id=source_id, action="EXPIRE_RESOURCE",
                     actor_id=None, actor_type="SYSTEM", from_status="ACTIVE", to_status="RELEASED")
    session.flush()
    return row


def _slot_state(room, seat, slot_start, matches, opened=None):
    local_start = as_utc(slot_start).astimezone(BUSINESS_TZ)
    local_end = local_start + timedelta(hours=1)
    if not room.is_active or not seat.is_active:
        return "DISABLED", None
    restrictions = [row for row in matches if row.source_module == "D"]
    if restrictions:
        return "DISABLED", min(restrictions, key=lambda row: row.id)
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
    room = lock_room(session, room_id)
    if room.booking_mode == "SEAT":
        validate_day(day)
    elif day < business_date():
        raise DomainError(422, "OUTSIDE_WINDOW", "查询日期不能早于今天")
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


def acquire_m1_reservation(session, *, room, seat, reservation_id, start, end, actor):
    """Acquire a typed M1 source after the caller holds the room lock."""
    from app.modules.resources.allocation_models import SeatAllocation
    from app.modules.resources.eligibility import seat_available

    if not seat_available(session, room, seat, start, end):
        raise conflict("座位或教室时段不可用")
    row = _insert(
        session,
        room,
        module="M1",
        source_id=reservation_id,
        seat_id=seat.id,
        start=start,
        end=end,
        actor=actor,
    )
    allocation = session.scalar(select(SeatAllocation).where(SeatAllocation.block_id == row.id))
    allocation.m1_reservation_id = reservation_id
    session.flush()
    return row


def release_m1_reservation(session, *, room, reservation_id, actor):
    """Release only the specified M1 source after business authorization."""
    from app.modules.resources.allocations import release_allocations

    row = _existing(session, "M1", reservation_id)
    if row is None or row.room_id != room.id:
        raise not_found()
    if row.status == "ACTIVE":
        row.status = "RELEASED"
        row.released_at = utcnow()
        room.resource_version += 1
        release_allocations(session, row)
        record_operation(
            session,
            module="M1",
            business_id=reservation_id,
            action="RELEASE_RESOURCE",
            actor_id=actor.user_id if actor is not None else None,
            actor_type="USER" if actor is not None else "SYSTEM",
            from_status="ACTIVE",
            to_status="RELEASED",
        )
    session.flush()
    return row


def apply_service_source(session, *, room, module, source_id, active, revision, start, end, actor):
    """Apply a pre-authorized M2/M3 source while the caller holds the room lock."""
    from app.modules.resources.allocations import release_allocations
    from app.modules.resources.opening import is_open

    existing = _existing(session, module, source_id)
    if existing:
        if existing.room_id != room.id:
            raise conflict("相同来源不能更换资源")
        if revision < existing.revision:
            raise conflict("来源版本已过期")
        if module == "M2" and (existing.start_at, existing.end_at) != (start, end):
            raise conflict("相同来源的时段不一致")
        if existing.status == "RELEASED" and active:
            raise conflict("来源已终止，不允许重新激活")
        if revision == existing.revision:
            if active != (existing.status == "ACTIVE"):
                raise conflict("相同版本状态不一致")
            return existing, False
        if not active and existing.status == "ACTIVE":
            existing.status, existing.released_at = "RELEASED", utcnow()
            release_allocations(session, existing)
        _advance_revision(session, room, existing, revision, actor)
        return existing, False
    if not active:
        return _insert(
            session,
            room,
            module=module,
            source_id=source_id,
            seat_id=None,
            start=start,
            end=end,
            actor=actor,
            revision=revision,
            released=True,
        ), False
    if not room.is_active:
        raise conflict("教室已停用")
    if module == "M2":
        if not is_open(session, room, start, end):
            raise conflict("时段不在教室开放区间内")
        if _overlap(session, room.id, start, end):
            raise conflict("整教室时段与已有预约或维修冲突")
    else:
        start, end = utcnow(), None
    return _insert(
        session,
        room,
        module=module,
        source_id=source_id,
        seat_id=None,
        start=start,
        end=end,
        actor=actor,
        revision=revision,
    ), True
