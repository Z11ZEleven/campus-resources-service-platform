"""Persistent reservation timers, executed within the worker transaction."""

from datetime import timedelta

from sqlalchemy import select

from app.core.time import as_utc
from app.modules.m1 import period
from app.modules.m1.integrations import audit_reservation, notify_reservation
from app.modules.m1.models import Reservation, ReservationEvent, Violation
from app.modules.notifications.service import publish_notifications
from app.modules.resources.service import lock_room, release_m1_reservation


def handle_timer(session, payload):
    reservation_id = payload["reservation_id"]
    kind = payload["kind"]
    initial = session.get(Reservation, reservation_id)
    if initial is None:
        return
    room = lock_room(session, initial.room_id)
    row = session.scalar(
        select(Reservation)
        .where(Reservation.id == reservation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = as_utc(period.precise_utcnow()).replace(tzinfo=None)
    if kind in ("before_start", "after_start_10"):
        lower, upper = (
            (row.start_at - timedelta(minutes=15), row.start_at)
            if kind == "before_start"
            else (row.start_at + timedelta(minutes=10), row.start_at + timedelta(minutes=15))
        )
        if row.status == "RESERVED" and lower <= now < upper:
            publish_notifications(
                session,
                recipients=[row.user_id],
                source_module="M1",
                business_id=row.id,
                event_key=f"reservation:{row.id}:{kind}",
                title="预约签到提醒",
                content="请及时前往教室签到。",
                link=f"/m1/reservations/{row.id}",
            )
        return
    if (
        kind == "after_start_15"
        and row.status == "RESERVED"
        and now >= row.start_at + timedelta(minutes=15)
    ):
        release_m1_reservation(session, room=room, reservation_id=row.id, actor=None)
        row.status = "NO_SHOW"
        row.cancelled_at = now
        row.end_reason = "未按时签到"
        session.add(
            Violation(
                reservation_id=row.id,
                user_id=row.user_id,
                violation_type="NO_SHOW",
                occurred_at=now,
                reason="未按时签到",
            )
        )
        notify_reservation(
            session,
            row,
            event_suffix="no_show",
            recipients=[row.user_id],
            title="预约未签到",
            content="预约因未按时签到而结束。",
            link=f"/m1/reservations/{row.id}",
        )
        previous, action = "RESERVED", "NO_SHOW"
    elif kind == "end" and row.status == "CHECKED_IN" and now >= row.end_at:
        release_m1_reservation(session, room=room, reservation_id=row.id, actor=None)
        row.status = "COMPLETED"
        row.actual_end_at = row.end_at
        previous, action = "CHECKED_IN", "COMPLETED"
    else:
        return
    row.version += 1
    session.add(
        ReservationEvent(
            reservation_id=row.id,
            event_type=action,
            from_status=previous,
            to_status=row.status,
            actor_type="SYSTEM",
            actor_id=None,
            correlation_id=f"m1-timer-{row.id}-{kind}",
            details={},
        )
    )
    audit_reservation(
        session,
        row,
        event_suffix=action.lower(),
        action=action,
        from_status=previous,
        to_status=row.status,
    )
    session.flush()
