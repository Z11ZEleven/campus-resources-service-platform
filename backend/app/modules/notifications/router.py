from fastapi import APIRouter, Query

from app.modules.auth.dependencies import CurrentUser, Db
from app.modules.notifications import service
from app.modules.notifications.schemas import (
    MarkAllResult,
    NotificationId,
    NotificationPage,
    NotificationView,
    UnreadCount,
)

router = APIRouter(prefix="/api/v1/notifications", tags=["D 站内通知"])


@router.get("", response_model=NotificationPage)
def list_messages(session: Db, user: CurrentUser, page: int = Query(default=1, ge=1),
                  page_size: int = Query(default=20, ge=1, le=100), unread_only: bool = False):
    rows, total, count = service.list_notifications(
        session, actor=user, page=page, page_size=page_size, unread_only=unread_only,
    )
    return NotificationPage(items=[NotificationView.model_validate(row) for row in rows],
                            total=total, page=page, page_size=page_size, unread_count=count)


@router.get("/unread-count", response_model=UnreadCount)
def count_unread(session: Db, user: CurrentUser):
    return UnreadCount(unread_count=service.unread_count(session, actor=user))


@router.post("/read-all", response_model=MarkAllResult)
def read_all(session: Db, user: CurrentUser):
    updated, count = service.mark_all_read(session, actor=user)
    return MarkAllResult(updated=updated, unread_count=count)


@router.post("/{notification_id}/read", response_model=NotificationView)
def read_one(notification_id: NotificationId, session: Db, user: CurrentUser):
    return service.mark_read(session, notification_id=notification_id, actor=user)
