from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import require_transaction
from app.core.errors import DomainError
from app.modules.audit.models import OperationLog


def record_operation(
    session: Session,
    *,
    module: str,
    business_id: int,
    action: str,
    actor_id: int,
    from_status: str | None = None,
    to_status: str | None = None,
    note: str | None = None,
) -> OperationLog:
    require_transaction(session)
    if module not in {"D", "M1", "M2", "M3"} or not 0 < len(action) <= 40:
        raise DomainError(422, "INVALID_LOG", "无效操作记录")
    row = OperationLog(
        module=module,
        business_id=business_id,
        action=action,
        actor_id=actor_id,
        from_status=from_status,
        to_status=to_status,
        note=note,
    )
    session.add(row)
    session.flush()
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
