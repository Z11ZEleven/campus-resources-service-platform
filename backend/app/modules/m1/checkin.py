"""Room-scoped, transactionally serialized check-in codes and attempts."""

import hashlib
import hmac
import secrets
from datetime import datetime, time, timedelta

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.errors import DomainError, conflict, forbidden, not_found
from app.core.time import BUSINESS_TZ, as_utc
from app.modules.audit.service import record_operation
from app.modules.m1.integrations import audit_reservation
from app.modules.m1.models import CheckinAttempt, Reservation, ReservationEvent, RoomCheckinCode
from app.modules.m1.period import precise_utcnow
from app.modules.m1.reservations import refreshed_actor, view
from app.modules.resources.service import lock_room


def _secret():
    secret = get_settings().m1_checkin_secret
    if (
        secret is None
        or len(secret.get_secret_value()) < 32
        or secret.get_secret_value().startswith("REPLACE_")
    ):
        raise DomainError(503, "CHECKIN_UNAVAILABLE", "签到服务未配置")
    return secret.get_secret_value().encode()


def _code(room_id, day, nonce):
    digest = hmac.new(
        _secret(), f"{room_id}:{day.isoformat()}:{nonce}".encode(), hashlib.sha256
    ).digest()
    return f"{int.from_bytes(digest, 'big') % 100000000:08d}"


def _date(now):
    return as_utc(now).astimezone(BUSINESS_TZ).date()


def _daily(session, room_id, day):
    row = session.scalar(
        select(RoomCheckinCode).where(
            RoomCheckinCode.room_id == room_id, RoomCheckinCode.business_date == day
        )
    )
    if row is None:
        row = RoomCheckinCode(room_id=room_id, business_date=day, nonce=secrets.token_hex(32))
        session.add(row)
        session.flush()
    return row


def admin_code(session, actor, auth_id, room_id, rotate=False):
    room = lock_room(session, room_id)
    actor = refreshed_actor(session, auth_id, actor)
    actor.require_for_room("m1:checkin_code:rotate" if rotate else "m1:checkin_code:read", room)
    now = precise_utcnow()
    day = _date(now)
    _secret()
    row = _daily(session, room.id, day)
    if rotate:
        old = _code(room.id, day, row.nonce)
        while True:
            nonce = secrets.token_hex(32)
            if _code(room.id, day, nonce) != old:
                break
        row.nonce = nonce
        row.version += 1
    record_operation(
        session,
        module="M1",
        business_id=room.id,
        action="ROTATE_CHECKIN_CODE" if rotate else "READ_CHECKIN_CODE",
        actor_id=actor.user_id,
        note=f"校园日 {day.isoformat()}",
    )
    expiry = datetime.combine(day + timedelta(days=1), time.min, tzinfo=BUSINESS_TZ)
    session.flush()
    return {"code": _code(room.id, day, row.nonce), "expires_at": expiry.isoformat()}


def check_in(session, actor, auth_id, reservation_id, submitted, correlation_id):
    row = session.scalar(
        select(Reservation).where(
            Reservation.id == reservation_id, Reservation.user_id == actor.user_id
        )
    )
    if row is None:
        raise not_found()
    room = lock_room(session, row.room_id)
    row = session.scalar(
        select(Reservation)
        .where(Reservation.id == reservation_id, Reservation.user_id == actor.user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    actor = refreshed_actor(session, auth_id, actor)
    if actor.identity_type != "STUDENT" or actor.identity_id != row.identity_id:
        raise forbidden()
    actor.require_for_room("m1:reservation:checkin", room)
    if row.status == "CHECKED_IN":
        return view(row), None
    now = as_utc(precise_utcnow()).replace(tzinfo=None)
    if (
        row.status != "RESERVED"
        or now < row.start_at - timedelta(minutes=15)
        or now >= row.start_at + timedelta(minutes=15)
    ):
        raise conflict("预约当前不可签到")
    count = session.scalar(
        select(func.count())
        .select_from(CheckinAttempt)
        .where(
            CheckinAttempt.user_id == actor.user_id,
            CheckinAttempt.room_id == room.id,
            CheckinAttempt.result == "INVALID_CODE",
            CheckinAttempt.attempted_at > now - timedelta(minutes=10),
            CheckinAttempt.attempted_at <= now,
        )
    )
    if count >= 5:
        return None, (429, "CHECKIN_RATE_LIMITED", "签到尝试过多，请稍后重试")
    _secret()
    day = _date(now)
    code = _code(room.id, day, _daily(session, room.id, day).nonce)
    valid = (
        len(submitted) == 8
        and submitted.isascii()
        and submitted.isdigit()
        and hmac.compare_digest(code, submitted)
    )
    if not valid:
        session.add(
            CheckinAttempt(
                user_id=actor.user_id,
                reservation_id=row.id,
                room_id=room.id,
                attempted_at=now,
                result="INVALID_CODE",
            )
        )
        session.flush()
        return None, (400, "INVALID_CHECKIN_CODE", "签到码错误")
    row.status, row.checked_in_at, row.version = "CHECKED_IN", now, row.version + 1
    session.add(
        ReservationEvent(
            reservation_id=row.id,
            event_type="CHECKED_IN",
            from_status="RESERVED",
            to_status="CHECKED_IN",
            actor_type="USER",
            actor_id=actor.user_id,
            correlation_id=correlation_id,
            details={},
        )
    )
    audit_reservation(
        session,
        row,
        event_suffix="checked_in",
        action="CHECK_IN",
        actor_type="USER",
        actor_id=actor.user_id,
        from_status="RESERVED",
        to_status="CHECKED_IN",
    )
    session.flush()
    return view(row), None
