"""Public M1 callbacks; caller holds the room lock and transaction."""

from sqlalchemy import select

from app.core.time import as_utc
from app.modules.m1.integrations import audit_reservation, notify_reservation
from app.modules.m1.models import Reservation, ReservationEvent
from app.modules.resources.service import release_m1_reservation


def _active(session, room, instant):
    return list(
        session.scalars(
            select(Reservation)
            .where(
                Reservation.room_id == room.id,
                Reservation.status.in_(["RESERVED", "CHECKED_IN"]),
                Reservation.end_at > instant,
            )
            .order_by(Reservation.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def _finish(session, *, room, rows, instant, actor, reason, action, suffix, details):
    for row in rows:
        previous = row.status
        release_m1_reservation(session, room=room, reservation_id=row.id, actor=actor)
        if previous == "RESERVED":
            row.status, row.cancelled_at = "CANCELLED", instant
        else:
            row.status = "INTERRUPTED"
            row.actual_end_at = min(row.end_at, max(instant, row.checked_in_at))
        row.end_reason = reason
        row.version += 1
        actor_type = "USER" if actor is not None else "SYSTEM"
        actor_id = actor.user_id if actor is not None else None
        session.add(
            ReservationEvent(
                reservation_id=row.id,
                event_type=action,
                from_status=previous,
                to_status=row.status,
                actor_type=actor_type,
                actor_id=actor_id,
                correlation_id=suffix,
                reason=reason,
                details=details,
            )
        )
        notify_reservation(
            session,
            row,
            event_suffix=suffix,
            recipients=[row.user_id],
            title="预约受资源变更影响",
            content=f"{reason}，预约已免责结束。",
            link=f"/m1/reservations/{row.id}",
        )
        audit_reservation(
            session,
            row,
            event_suffix=suffix,
            action=action,
            actor_type=actor_type,
            actor_id=actor_id,
            from_status=previous,
            to_status=row.status,
            note=reason,
        )
    session.flush()
    return [row.id for row in rows]


def apply_maintenance(session, *, room, effective_at, source_id):
    instant = as_utc(effective_at).replace(tzinfo=None)
    return len(
        _finish(
            session,
            room=room,
            rows=_active(session, room, instant),
            instant=instant,
            actor=None,
            reason="教室维修",
            action="MAINTENANCE",
            suffix=f"maintenance:{source_id}",
            details={"source_module": "M3", "source_id": str(source_id)},
        )
    )


def apply_seat_deactivation(session, *, room, seat_id, effective_at, actor):
    instant = as_utc(effective_at).replace(tzinfo=None)
    rows = [row for row in _active(session, room, instant) if row.seat_id == seat_id]
    return _finish(
        session,
        room=room,
        rows=rows,
        instant=instant,
        actor=actor,
        reason="座位停用",
        action="RESOURCE_DISABLED",
        suffix=f"deactivation:{seat_id}",
        details={"seat_id": str(seat_id)},
    )


def apply_room_deactivation(session, *, room, effective_at, actor):
    instant = as_utc(effective_at).replace(tzinfo=None)
    return _finish(
        session,
        room=room,
        rows=_active(session, room, instant),
        instant=instant,
        actor=actor,
        reason="教室停用",
        action="RESOURCE_DISABLED",
        suffix=f"room-deactivated:{room.id}",
        details={"room_id": str(room.id)},
    )


def apply_schedule_change(session, *, room, payload, effective_at, actor):
    """End only reservations whose remaining planned interval is no longer open."""
    from types import SimpleNamespace

    from app.core.time import to_utc
    from app.modules.resources.opening import calculate, covers

    instant = as_utc(effective_at).replace(tzinfo=None)
    weekly = [SimpleNamespace(**item.model_dump()) for item in payload.weekly]
    exceptions = [
        SimpleNamespace(
            kind=item.kind,
            start_at=to_utc(item.start_at),
            end_at=to_utc(item.end_at),
        )
        for item in payload.exceptions
    ]
    candidate = SimpleNamespace(opening_rules_enabled=True)
    rows = [
        row
        for row in _active(session, room, instant)
        if not covers(
            calculate(candidate, weekly, exceptions, max(row.start_at, instant), row.end_at),
            max(row.start_at, instant),
            row.end_at,
        )
    ]
    return _finish(
        session,
        room=room,
        rows=rows,
        instant=instant,
        actor=actor,
        reason="开放规则变更",
        action="SCHEDULE_CHANGED",
        suffix=f"schedule:{room.resource_version}",
        details={"room_id": str(room.id)},
    )
