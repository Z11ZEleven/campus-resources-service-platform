from app.core.database import require_transaction
from app.core.errors import DomainError
from app.modules.m1.models import Reservation
from app.modules.outbox.models import OutboxEvent
from app.modules.outbox.service import register_event

MAX_DELIVERY_ATTEMPTS = 6


def _suffix(value: str) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= 80:
        raise DomainError(422, "INVALID_M1_EVENT", "无效的预约事件后缀")
    return value


def _reservation_id(reservation: Reservation) -> int:
    if reservation.id is None:
        raise DomainError(422, "INVALID_M1_EVENT", "预约必须先持久化再登记事件")
    return reservation.id


def notify_reservation(
    session,
    reservation: Reservation,
    *,
    event_suffix: str,
    recipients: list[int],
    title: str,
    content: str,
    link: str,
    max_attempts: int | None = MAX_DELIVERY_ATTEMPTS,
) -> OutboxEvent:
    """Register a notification task in the current business transaction.

    M1 owns when a notification is needed; D owns the channel and delivery.
    The caller must not commit here. A failed remote delivery leaves the
    business transaction and the event intact.
    """

    require_transaction(session)
    reservation_id = _reservation_id(reservation)
    suffix = _suffix(event_suffix)
    event_key = f"reservation:{reservation_id}:{suffix}"
    payload = {
        "recipients": sorted(set(recipients)),
        "source_module": "M1",
        "business_id": reservation_id,
        "event_key": event_key,
        "title": title,
        "content": content,
        "link": link,
    }
    return register_event(
        session,
        source_module="M1",
        event_key=event_key,
        handler="notification",
        business_id=reservation_id,
        payload=payload,
        max_attempts=max_attempts,
    )


def audit_reservation(
    session,
    reservation: Reservation,
    *,
    event_suffix: str,
    action: str,
    actor_type: str = "SYSTEM",
    actor_id: int | None = None,
    service_principal_id: int | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    note: str | None = None,
    result: str = "SUCCESS",
    max_attempts: int | None = MAX_DELIVERY_ATTEMPTS,
) -> OutboxEvent:
    """Register an M1 audit task without inventing a user actor.

    Worker/system actions use SYSTEM. Service principals use SERVICE and a
    service_principal_id, never a user id. Delivery follows the shared
    six-attempt policy and then exposes a MANUAL failure for follow-up.
    Repeated receivers dedupe on the event key.
    """

    require_transaction(session)
    reservation_id = _reservation_id(reservation)
    suffix = _suffix(event_suffix)
    event_key = f"reservation:{reservation_id}:audit:{suffix}"
    payload = {
        "module": "M1",
        "business_id": reservation_id,
        "action": action,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "service_principal_id": service_principal_id,
        "from_status": from_status,
        "to_status": to_status,
        "note": note,
        "result": result,
        "event_key": event_key,
    }
    return register_event(
        session,
        source_module="M1",
        event_key=event_key,
        handler="audit",
        business_id=reservation_id,
        payload=payload,
        max_attempts=max_attempts,
    )
