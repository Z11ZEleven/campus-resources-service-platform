from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.core.database import require_transaction
from app.core.errors import DomainError, conflict
from app.core.time import utcnow
from app.modules.audit.models import OperationLog

MODULES = {"D", "M1", "M2", "M3"}
ACTOR_TYPES = {"USER", "SERVICE", "SYSTEM"}
RESULTS = {"SUCCESS", "FAILURE"}


def _validate_actor(
    *, actor_type: str, actor_id: int | None, service_principal_id: int | None
) -> None:
    if actor_type not in ACTOR_TYPES:
        raise DomainError(422, "INVALID_LOG", "无效的操作者类型")
    if actor_type == "USER" and (actor_id is None or service_principal_id is not None):
        raise DomainError(422, "INVALID_LOG", "用户操作者必须提供用户标识")
    if actor_type == "SERVICE" and (
        service_principal_id is None or actor_id is not None
    ):
        raise DomainError(422, "INVALID_LOG", "服务操作者必须提供服务主体标识")
    if actor_type == "SYSTEM" and (
        actor_id is not None or service_principal_id is not None
    ):
        raise DomainError(422, "INVALID_LOG", "系统操作者不能关联用户或服务主体")


def record_operation(
    session: Session,
    *,
    module: str,
    business_id: int,
    action: str,
    actor_id: int | None = None,
    actor_type: str = "USER",
    service_principal_id: int | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    note: str | None = None,
    result: str = "SUCCESS",
    event_key: str | None = None,
) -> OperationLog:
    """Append an audit row in the caller's transaction.

    Existing user-facing callers keep actor_type=USER. Worker and service
    callers must state SYSTEM or SERVICE explicitly; a service principal id is
    never stored as a user id. Supplying event_key makes cross-process
    receiving idempotent: the same event is returned once, while a different
    body for the same key is rejected.
    """

    require_transaction(session)
    if module not in MODULES or not 0 < len(action) <= 40:
        raise DomainError(422, "INVALID_LOG", "无效操作记录")
    if not isinstance(business_id, int) or not 0 <= business_id <= 2**64 - 1:
        raise DomainError(422, "INVALID_LOG", "无效的业务标识")
    _validate_actor(
        actor_type=actor_type,
        actor_id=actor_id,
        service_principal_id=service_principal_id,
    )
    if result not in RESULTS:
        raise DomainError(422, "INVALID_LOG", "无效的操作结果")
    if event_key is not None and (not event_key or len(event_key) > 160):
        raise DomainError(422, "INVALID_LOG", "无效的审计事件键")

    values: dict[str, Any] = {
        "module": module,
        "business_id": business_id,
        "action": action,
        "actor_id": actor_id,
        "actor_type": actor_type,
        "service_principal_id": service_principal_id,
        "from_status": from_status,
        "to_status": to_status,
        "note": note,
        "result": result,
        "event_key": event_key,
        "created_at": utcnow(),
    }
    if event_key is None:
        row = OperationLog(**values)
        session.add(row)
        session.flush()
        return row

    statement = mysql_insert(OperationLog).values(values)
    session.execute(statement.on_duplicate_key_update(id=OperationLog.id))
    row = session.scalar(
        select(OperationLog)
        .where(OperationLog.module == module, OperationLog.event_key == event_key)
        .execution_options(populate_existing=True)
    )
    if row is None:  # pragma: no cover - defensive, insert must have produced a row
        raise RuntimeError("audit row was not persisted")
    if (
        row.business_id != business_id
        or row.action != action
        or row.actor_id != actor_id
        or row.actor_type != actor_type
        or row.service_principal_id != service_principal_id
        or row.from_status != from_status
        or row.to_status != to_status
        or row.note != note
        or row.result != result
    ):
        raise conflict("审计事件键已用于其他记录")
    return row


def list_operations(session: Session, *, module: str, business_id: int):
    """Internal only. Caller MUST authorize access to the business record first."""
    return list(
        session.scalars(
            select(OperationLog)
            .where(OperationLog.module == module, OperationLog.business_id == business_id)
            .order_by(OperationLog.created_at, OperationLog.id)
        )
    )
