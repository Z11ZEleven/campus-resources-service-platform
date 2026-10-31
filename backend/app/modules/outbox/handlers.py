from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.modules.audit.service import record_operation
from app.modules.notifications.service import publish_notifications


class PermanentOutboxError(Exception):
    """A receiver rejected the event permanently; retrying cannot help."""


Handler = Callable[[Session, dict[str, Any]], Any]


def deliver_notification(session: Session, payload: dict[str, Any]) -> Any:
    """Durable in-app notification channel.

    The inbox row is the real user-visible channel. The unique
    (recipient, source-module event key) constraint makes a repeated delivery
    after a lost acknowledgement idempotent.
    """

    try:
        return publish_notifications(session, **payload)
    except DomainError as exc:
        raise PermanentOutboxError(f"{exc.code}: {exc.message}") from exc
    except IntegrityError as exc:
        raise PermanentOutboxError(f"notification integrity error: {exc.orig}") from exc
    except (TypeError, KeyError, ValueError) as exc:
        raise PermanentOutboxError(f"invalid notification payload: {exc}") from exc


def receive_audit(session: Session, payload: dict[str, Any]) -> Any:
    """Controlled audit receiver with actor-type and event-key dedupe."""

    try:
        return record_operation(session, **payload)
    except DomainError as exc:
        raise PermanentOutboxError(f"{exc.code}: {exc.message}") from exc
    except IntegrityError as exc:
        raise PermanentOutboxError(f"audit integrity error: {exc.orig}") from exc
    except (TypeError, KeyError, ValueError) as exc:
        raise PermanentOutboxError(f"invalid audit payload: {exc}") from exc


HANDLERS: Mapping[str, Handler] = {
    "notification": deliver_notification,
    "audit": receive_audit,
}


def get_handler(
    name: str, handlers: Mapping[str, Handler] | None = None
) -> Handler:
    registry = HANDLERS if handlers is None else handlers
    try:
        return registry[name]
    except KeyError as exc:
        raise PermanentOutboxError(f"unknown outbox handler: {name}") from exc
