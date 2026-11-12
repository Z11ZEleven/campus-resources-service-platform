"""M1 reservation operations. The HTTP transaction owns commit/rollback."""

import hashlib
import json
from datetime import UTC, timedelta

from sqlalchemy import func, select

from app.core.errors import conflict, forbidden, not_found
from app.core.time import as_utc, to_utc, utcnow
from app.modules.auth.models import AuthSession
from app.modules.auth.permissions import load_principal
from app.modules.m1.integrations import audit_reservation
from app.modules.m1.models import Reservation, ReservationEvent
from app.modules.m1.period import precise_utcnow, validate_booking_period
from app.modules.m1.permissions import require_student_for_room
from app.modules.outbox.service import register_event
from app.modules.resources.models import Seat
from app.modules.resources.service import (
    acquire_m1_reservation,
    allocate_m1_source_id,
    lock_m1_user,
    lock_room,
    release_m1_reservation,
)


def view(row, *, original=False):
    return {
        "id": str(row.id),
        "identity_id": str(row.identity_id),
        "seat_id": str(row.seat_id),
        "room_id": str(row.room_id),
        "start_at": as_utc(row.start_at).isoformat(),
        "end_at": as_utc(row.end_at).isoformat(),
        "status": "RESERVED" if original else row.status,
        "version": "1" if original else str(row.version),
        "resource_snapshot": row.resource_snapshot,
        "settings_snapshot": row.settings_snapshot,
    }


def refreshed_actor(session, auth_session_id, actor):
    auth = session.scalar(
        select(AuthSession)
        .where(AuthSession.id == auth_session_id)
        .execution_options(populate_existing=True)
    )
    now = utcnow()
    if (
        not auth
        or auth.user_id != actor.user_id
        or auth.revoked_at is not None
        or auth.expires_at <= now
    ):
        from app.core.errors import DomainError

        raise DomainError(401, "INVALID_SESSION", "会话无效或已过期")
    session.expire_all()
    return load_principal(session, auth.user_id, auth.identity_id)


def create(
    session, actor, auth_session_id, identity_id, seat_id, start_at, end_at, key, correlation_id
):
    # Normalize independent of the current time, so a later retry can replay.
    try:
        start, end = to_utc(start_at), to_utc(end_at)
    except ValueError:
        from app.core.errors import DomainError

        raise DomainError(422, "TIMEZONE_REQUIRED", "时间必须包含时区") from None
    digest = hashlib.sha256(
        json.dumps(
            [
                identity_id,
                seat_id,
                start_at.astimezone(UTC).isoformat(),
                end_at.astimezone(UTC).isoformat(),
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    prior = session.scalar(
        select(Reservation).where(
            Reservation.user_id == actor.user_id, Reservation.create_request_key == key
        )
    )
    if prior is not None and prior.request_hash != digest:
        raise conflict("幂等键已用于不同请求")
    seat = session.get(Seat, seat_id)
    if seat is None:
        raise not_found()
    room = lock_room(session, seat.room_id)
    # Same user/key requests in different rooms are serialized here.
    lock_m1_user(session, actor.user_id)
    actor = refreshed_actor(session, auth_session_id, actor)
    existing = session.scalar(
        select(Reservation).where(
            Reservation.user_id == actor.user_id, Reservation.create_request_key == key
        )
    )
    if existing and existing.request_hash != digest:
        raise conflict("幂等键已用于不同请求")
    if actor.identity_id != identity_id or actor.identity_type != "STUDENT":
        raise forbidden()
    actor.require_for_room("m1:reservation:create", room)
    if room.department_code is not None and room.department_code != actor.department_code:
        raise forbidden()
    if existing:
        return view(existing, original=True)
    require_student_for_room(actor, room)
    start, end = validate_booking_period(start_at, end_at, now=precise_utcnow())
    seat = session.get(Seat, seat_id, populate_existing=True)
    snapshot = {
        "seat_no": seat.seat_no,
        "room_name": room.name,
        "building_name": room.building_name,
        "has_fixed_socket": bool(seat.has_fixed_socket),
        "has_mobile_socket": bool(seat.has_mobile_socket),
    }
    settings = {"window_start": "08:00", "window_end": "22:00", "min_hours": 1, "max_hours": 4}
    floor = session.scalar(select(func.max(Reservation.id))) or 0
    reservation_id = allocate_m1_source_id(session, reservation_floor=floor)
    row = Reservation(
        id=reservation_id,
        user_id=actor.user_id,
        identity_id=identity_id,
        room_id=room.id,
        seat_id=seat.id,
        start_at=start,
        end_at=end,
        status="RESERVED",
        settings_snapshot=settings,
        resource_snapshot=snapshot,
        create_request_key=key,
        request_hash=digest,
    )
    session.add(row)
    session.flush()
    acquire_m1_reservation(
        session, room=room, seat=seat, reservation_id=row.id, start=start, end=end, actor=actor
    )
    session.add(
        ReservationEvent(
            reservation_id=row.id,
            event_type="CREATED",
            from_status=None,
            to_status="RESERVED",
            actor_type="USER",
            actor_id=actor.user_id,
            correlation_id=correlation_id,
            details={},
        )
    )
    audit_reservation(
        session,
        row,
        event_suffix="created",
        action="CREATE_RESERVATION",
        actor_type="USER",
        actor_id=actor.user_id,
        to_status="RESERVED",
    )
    for suffix, at in (
        ("before_start", start - timedelta(minutes=15)),
        ("after_start_10", start + timedelta(minutes=10)),
        ("after_start_15", start + timedelta(minutes=15)),
        ("end", end),
    ):
        register_event(
            session,
            source_module="M1",
            event_key=f"reservation:{row.id}:timer:{suffix}",
            handler="m1_timer",
            business_id=row.id,
            payload={"reservation_id": row.id, "kind": suffix},
            available_at=at,
            max_attempts=None,
        )
    session.flush()
    return view(row, original=True)


def cancel(session, actor, auth_session_id, reservation_id, correlation_id):
    row = session.scalar(
        select(Reservation).where(
            Reservation.id == reservation_id, Reservation.user_id == actor.user_id
        )
    )
    if not row:
        raise not_found()
    room = lock_room(session, row.room_id)
    row = session.scalar(
        select(Reservation)
        .where(Reservation.id == reservation_id, Reservation.user_id == actor.user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    actor = refreshed_actor(session, auth_session_id, actor)
    actor.require_for_room("m1:reservation:cancel_own", room)
    if row.status == "CANCELLED":
        return view(row)
    if row.status != "RESERVED" or as_utc(precise_utcnow()).replace(tzinfo=None) >= row.start_at:
        raise conflict("预约当前不可取消")
    release_m1_reservation(session, room=room, reservation_id=row.id, actor=actor)
    row.status, row.cancelled_at, row.version = (
        "CANCELLED",
        as_utc(precise_utcnow()).replace(tzinfo=None),
        row.version + 1,
    )
    session.add(
        ReservationEvent(
            reservation_id=row.id,
            event_type="CANCELLED",
            from_status="RESERVED",
            to_status="CANCELLED",
            actor_type="USER",
            actor_id=actor.user_id,
            correlation_id=correlation_id,
            details={},
        )
    )
    audit_reservation(
        session,
        row,
        event_suffix="cancelled",
        action="CANCEL_RESERVATION",
        actor_type="USER",
        actor_id=actor.user_id,
        from_status="RESERVED",
        to_status="CANCELLED",
    )
    session.flush()
    return view(row)


def get_own(session, actor, reservation_id):
    row = session.scalar(
        select(Reservation).where(
            Reservation.id == reservation_id, Reservation.user_id == actor.user_id
        )
    )
    if not row:
        raise not_found()
    actor.require_for_room("m1:reservation:read_own", row_room(session, row))
    return view(row)


def row_room(session, row):
    from app.modules.resources.models import Room

    return session.get(Room, row.room_id)


def list_own(session, actor, status, day, page, page_size):
    from datetime import datetime, time, timedelta

    from app.core.time import BUSINESS_TZ
    from app.modules.auth.permissions import room_scope_predicate
    from app.modules.resources.models import Room

    q = (
        select(Reservation)
        .join(Room, Room.id == Reservation.room_id)
        .where(
            Reservation.user_id == actor.user_id,
            room_scope_predicate(actor, "m1:reservation:read_own"),
        )
    )
    if status:
        q = q.where(Reservation.status == status)
    if day:
        start = datetime.combine(day, time.min, tzinfo=BUSINESS_TZ)
        q = q.where(
            Reservation.start_at >= to_utc(start),
            Reservation.start_at < to_utc(start + timedelta(days=1)),
        )
    total = session.scalar(select(func.count()).select_from(q.subquery()))
    rows = session.scalars(
        q.order_by(Reservation.created_at.desc(), Reservation.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {"total": total, "page": page, "page_size": page_size, "items": [view(r) for r in rows]}
