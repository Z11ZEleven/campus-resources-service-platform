import re
from collections.abc import Iterable

from sqlalchemy import func, select, update
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.core.database import require_transaction
from app.core.errors import DomainError, conflict, not_found
from app.core.time import utcnow
from app.modules.auth.models import User, UserIdentity
from app.modules.auth.permissions import Principal
from app.modules.notifications.models import Notification


def publish_notifications(
    session: Session, *, recipients: Iterable[int], source_module: str, business_id: int,
    event_key: str, title: str, content: str, link: str,
) -> list[Notification]:
    """Internal public D contract; the caller authorizes business recipients.

    Commit of an inbox row is actual in-app delivery. Failure rolls back the
    caller's transaction. D does not inspect any module's private business table.
    """
    require_transaction(session)
    if (
        source_module not in {"D", "M1", "M2", "M3"}
        or isinstance(business_id, bool) or not 0 < business_id <= 18446744073709551615
        or not 0 < len(event_key) <= 150 or not 0 < len(title) <= 160
        or not 0 < len(content) <= 1000 or len(link) > 200
        or link.startswith("//") or not re.fullmatch(r"/[A-Za-z0-9/_-]*", link)
    ):
        raise DomainError(422, "INVALID_NOTIFICATION", "无效的站内通知")
    ids = set(recipients)
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in ids):
        raise DomainError(422, "INVALID_NOTIFICATION", "无效的通知收件人")
    if not ids:
        return []
    valid = list(session.scalars(
        select(User.id).join(UserIdentity, UserIdentity.user_id == User.id).where(
            User.id.in_(ids), User.is_active.is_(True), UserIdentity.is_active.is_(True),
        ).order_by(User.id)
    ))
    if not valid:
        return []
    key = f"{source_module}:{event_key}"
    now = utcnow()
    values = [dict(
        recipient_id=recipient, source_module=source_module, business_id=business_id,
        event_key=key, title=title, content=content, link=link, created_at=now, read_at=None,
    ) for recipient in valid]
    statement = insert(Notification).values(values)
    # A duplicate keeps the original notification, creation time and read state.
    session.execute(statement.on_duplicate_key_update(id=Notification.id))
    rows = list(session.scalars(select(Notification).where(
        Notification.recipient_id.in_(valid), Notification.event_key == key,
    ).execution_options(populate_existing=True)))
    if any((row.source_module, row.business_id, row.title, row.content, row.link)
           != (source_module, business_id, title, content, link) for row in rows):
        raise conflict("通知事件键已用于其他内容")
    session.flush()
    return rows


def unread_count(session: Session, *, actor: Principal) -> int:
    return session.scalar(select(func.count()).select_from(Notification).where(
        Notification.recipient_id == actor.user_id, Notification.read_at.is_(None),
    )) or 0


def list_notifications(session: Session, *, actor: Principal, page: int, page_size: int,
                       unread_only: bool = False):
    query = select(Notification).where(Notification.recipient_id == actor.user_id)
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(session.scalars(query.order_by(Notification.created_at.desc(), Notification.id.desc())
                                .offset((page - 1) * page_size).limit(page_size)))
    return rows, total, unread_count(session, actor=actor)


def mark_read(session: Session, *, notification_id: int, actor: Principal) -> Notification:
    require_transaction(session)
    row = session.scalar(select(Notification).where(
        Notification.id == notification_id, Notification.recipient_id == actor.user_id,
    ).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise not_found()
    if row.read_at is None:
        row.read_at = utcnow()
        session.flush()
    return row


def mark_all_read(session: Session, *, actor: Principal) -> tuple[int, int]:
    require_transaction(session)
    # Snapshot delivered inbox IDs with a non-locking read. Creation timestamps
    # or a max ID cannot exclude an earlier insert whose transaction commits
    # only after this operation started.
    ids = list(session.scalars(select(Notification.id).where(
        Notification.recipient_id == actor.user_id, Notification.read_at.is_(None),
    ).order_by(Notification.id)))
    read_at = utcnow()
    updated = 0
    for offset in range(0, len(ids), 1000):
        result = session.execute(update(Notification).where(
            Notification.id.in_(ids[offset:offset + 1000]),
            Notification.recipient_id == actor.user_id, Notification.read_at.is_(None),
        ).values(read_at=read_at))
        updated += result.rowcount
    session.flush()
    return updated, unread_count(session, actor=actor)
