from typing import Annotated

from pydantic import BeforeValidator

from app.core.schema import Id, Output, UtcDateTime


def _validate_id(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("ID must be an integer or decimal integer string")
    if isinstance(value, str) and (not value.isascii() or not value.isdecimal()):
        raise ValueError("ID must be a positive decimal integer string")
    return value


NotificationId = Annotated[Id, BeforeValidator(_validate_id)]


class NotificationView(Output):
    id: Id
    recipient_id: Id
    source_module: str
    business_id: Id
    event_key: str
    title: str
    content: str
    link: str
    created_at: UtcDateTime
    read_at: UtcDateTime | None


class NotificationPage(Output):
    items: list[NotificationView]
    total: int
    page: int
    page_size: int
    unread_count: int


class UnreadCount(Output):
    unread_count: int


class MarkAllResult(UnreadCount):
    updated: int
