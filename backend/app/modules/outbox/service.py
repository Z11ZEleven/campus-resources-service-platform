import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.core.database import require_transaction
from app.core.errors import DomainError, conflict
from app.core.time import to_utc, utcnow
from app.modules.outbox.models import OutboxEvent

MODULES = {"D", "M1", "M2", "M3"}
STATUSES = {"PENDING", "PROCESSING", "SUCCEEDED", "MANUAL"}
MAX_LIMIT = 100


class LeaseLost(RuntimeError):
    """The caller no longer owns the lease/version it is trying to finish."""


@dataclass(frozen=True)
class ClaimedEvent:
    id: int
    source_module: str
    event_key: str
    handler: str
    business_id: int
    business_version: int
    payload: dict[str, Any]
    attempts: int
    max_attempts: int | None
    lease_token: str
    version: int


def _now(value: datetime | None) -> datetime:
    if value is None:
        return utcnow()
    if value.tzinfo is not None:
        value = to_utc(value)
    return value.replace(microsecond=(value.microsecond // 1000) * 1000)


def _canonical(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _text(value: Any, *, limit: int, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise DomainError(422, "INVALID_OUTBOX", f"无效的 {field}")
    return value


def _clip(value: Any, limit: int) -> str:
    text = str(value)
    return text[:limit]


def register_event(
    session: Session,
    *,
    source_module: str,
    event_key: str,
    handler: str,
    payload: dict[str, Any],
    business_id: int = 0,
    business_version: int = 1,
    available_at: datetime | None = None,
    max_attempts: int | None = None,
) -> OutboxEvent:
    """Write one public task row inside the caller's transaction.

    The unique key is scoped to the source module. Re-registering the same
    handler, business version and payload is a no-op, which makes retries from
    a business request safe. A different payload is a conflict rather than a
    silent overwrite.
    """

    require_transaction(session)
    if source_module not in MODULES:
        raise DomainError(422, "INVALID_OUTBOX", "无效的 Outbox 来源模块")
    event_key = _text(event_key, limit=160, field="事件键")
    handler = _text(handler, limit=40, field="处理器")
    if not isinstance(business_id, int) or not 0 <= business_id <= 2**64 - 1:
        raise DomainError(422, "INVALID_OUTBOX", "无效的业务标识")
    if not isinstance(business_version, int) or business_version <= 0:
        raise DomainError(422, "INVALID_OUTBOX", "无效的业务版本")
    if max_attempts is not None and (
        not isinstance(max_attempts, int) or max_attempts <= 0
    ):
        raise DomainError(422, "INVALID_OUTBOX", "无效的最大尝试次数")
    if not isinstance(payload, dict):
        raise DomainError(422, "INVALID_OUTBOX", "Outbox 载荷必须是对象")
    try:
        canonical_payload = _canonical(payload)
    except (TypeError, ValueError) as exc:
        raise DomainError(422, "INVALID_OUTBOX", "Outbox 载荷必须是合法 JSON") from exc

    now = utcnow()
    available = _now(available_at) if available_at is not None else now
    values = {
        "source_module": source_module,
        "event_key": event_key,
        "handler": handler,
        "business_id": business_id,
        "business_version": business_version,
        "payload": payload,
        "status": "PENDING",
        "available_at": available,
        "attempts": 0,
        "max_attempts": max_attempts,
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    statement = mysql_insert(OutboxEvent).values(values)
    session.execute(statement.on_duplicate_key_update(id=OutboxEvent.id))
    row = session.scalar(
        select(OutboxEvent)
        .where(
            OutboxEvent.source_module == source_module,
            OutboxEvent.event_key == event_key,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:  # pragma: no cover - defensive, insert must have produced a row
        raise RuntimeError("Outbox event was not persisted")
    if (
        row.handler != handler
        or row.business_id != business_id
        or row.business_version != business_version
        or _canonical(row.payload) != canonical_payload
    ):
        raise conflict("Outbox 事件键已用于其他内容")
    return row


def _claim_due(
    session: Session,
    *,
    column,
    status: str,
    current: datetime,
    limit: int,
) -> list[OutboxEvent]:
    return list(
        session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.status == status, column <= current)
            .order_by(column, OutboxEvent.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
    )


def claim_events(
    session: Session,
    *,
    worker_id: str,
    limit: int = 10,
    lease_seconds: int = 30,
    now: datetime | None = None,
) -> list[ClaimedEvent]:
    """Lease due or expired events. Concurrent workers use SKIP LOCKED."""

    require_transaction(session)
    if not worker_id or len(worker_id) > 64:
        raise DomainError(422, "INVALID_OUTBOX", "无效的 Worker 标识")
    if not 1 <= limit <= MAX_LIMIT:
        raise DomainError(422, "INVALID_OUTBOX", "无效的领取数量")
    if not 1 <= lease_seconds <= 3600:
        raise DomainError(422, "INVALID_OUTBOX", "无效的租约时长")
    current = _now(now)
    # Keep the two due reasons as separate index-friendly locking reads. A
    # single OR can make InnoDB scan and lock both PENDING and expired rows
    # before LIMIT, which makes another worker skip work that it should get.
    rows = _claim_due(
        session,
        column=OutboxEvent.available_at,
        status="PENDING",
        current=current,
        limit=limit,
    )
    remaining = limit - len(rows)
    if remaining:
        rows.extend(
            _claim_due(
                session,
                column=OutboxEvent.lease_expires_at,
                status="PROCESSING",
                current=current,
                limit=remaining,
            )
        )
    claimed: list[ClaimedEvent] = []
    for row in rows:
        token = str(uuid4())
        row.status = "PROCESSING"
        row.lease_owner = worker_id
        row.lease_token = token
        row.lease_expires_at = current + timedelta(seconds=lease_seconds)
        row.attempts += 1
        row.version += 1
        row.updated_at = current
        row.last_error = None
        row.error_code = None
        claimed.append(
            ClaimedEvent(
                id=row.id,
                source_module=row.source_module,
                event_key=row.event_key,
                handler=row.handler,
                business_id=row.business_id,
                business_version=row.business_version,
                payload=row.payload,
                attempts=row.attempts,
                max_attempts=row.max_attempts,
                lease_token=token,
                version=row.version,
            )
        )
    session.flush()
    return claimed


def _transition(
    session: Session,
    *,
    event_id: int,
    worker_id: str,
    lease_token: str,
    expected_version: int,
    values: dict[str, Any],
    now: datetime | None,
) -> None:
    current = _now(now)
    result = session.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.id == event_id,
            OutboxEvent.status == "PROCESSING",
            OutboxEvent.lease_owner == worker_id,
            OutboxEvent.lease_token == lease_token,
            OutboxEvent.version == expected_version,
        )
        .values(updated_at=current, version=expected_version + 1, **values)
    )
    if result.rowcount != 1:
        raise LeaseLost(f"Outbox event {event_id} lease or version is no longer current")


def complete_event(
    session: Session,
    *,
    event_id: int,
    worker_id: str,
    lease_token: str,
    expected_version: int,
    now: datetime | None = None,
) -> None:
    require_transaction(session)
    current = _now(now)
    _transition(
        session,
        event_id=event_id,
        worker_id=worker_id,
        lease_token=lease_token,
        expected_version=expected_version,
        values={
            "status": "SUCCEEDED",
            "lease_owner": None,
            "lease_token": None,
            "lease_expires_at": None,
            "last_error": None,
            "error_code": None,
            "completed_at": current,
        },
        now=current,
    )


def retry_event(
    session: Session,
    *,
    event_id: int,
    worker_id: str,
    lease_token: str,
    expected_version: int,
    error_code: str,
    error: str,
    available_at: datetime,
    now: datetime | None = None,
) -> None:
    require_transaction(session)
    _transition(
        session,
        event_id=event_id,
        worker_id=worker_id,
        lease_token=lease_token,
        expected_version=expected_version,
        values={
            "status": "PENDING",
            "available_at": _now(available_at),
            "lease_owner": None,
            "lease_token": None,
            "lease_expires_at": None,
            "last_error": _clip(error, 1000),
            "error_code": _clip(error_code, 40),
            "completed_at": None,
        },
        now=now,
    )


def mark_manual(
    session: Session,
    *,
    event_id: int,
    worker_id: str,
    lease_token: str,
    expected_version: int,
    error_code: str,
    error: str,
    now: datetime | None = None,
) -> None:
    require_transaction(session)
    current = _now(now)
    _transition(
        session,
        event_id=event_id,
        worker_id=worker_id,
        lease_token=lease_token,
        expected_version=expected_version,
        values={
            "status": "MANUAL",
            "lease_owner": None,
            "lease_token": None,
            "lease_expires_at": None,
            "last_error": _clip(error, 1000),
            "error_code": _clip(error_code, 40),
            "completed_at": current,
        },
        now=current,
    )


def get_event(session: Session, event_id: int) -> OutboxEvent | None:
    return session.get(OutboxEvent, event_id)


def list_events(
    session: Session,
    *,
    status: str | None = None,
    handler: str | None = None,
    limit: int = MAX_LIMIT,
) -> list[OutboxEvent]:
    if status is not None and status not in STATUSES:
        raise DomainError(422, "INVALID_OUTBOX", "无效的 Outbox 状态")
    if handler is not None and (not handler or len(handler) > 40):
        raise DomainError(422, "INVALID_OUTBOX", "无效的处理器")
    if not 1 <= limit <= 500:
        raise DomainError(422, "INVALID_OUTBOX", "无效的查询数量")
    statement = select(OutboxEvent)
    if status is not None:
        statement = statement.where(OutboxEvent.status == status)
    if handler is not None:
        statement = statement.where(OutboxEvent.handler == handler)
    return list(
        session.scalars(statement.order_by(OutboxEvent.available_at, OutboxEvent.id).limit(limit))
    )


def list_failures(session: Session, *, limit: int = MAX_LIMIT) -> list[OutboxEvent]:
    return list_events(session, status="MANUAL", limit=limit)
