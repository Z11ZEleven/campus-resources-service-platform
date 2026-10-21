from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, get_ident

import pytest
from conftest import token_headers
from sqlalchemy import event, func, select

from app.core.errors import DomainError
from app.modules.auth.models import UserIdentity
from app.modules.notifications.models import Notification
from app.modules.notifications.service import mark_all_read, publish_notifications

pytestmark = pytest.mark.mysql
BASE = "/api/v1/notifications"


def publish(session, data, **extra):
    return publish_notifications(session, recipients=[data["users"]["student"].user_id],
        source_module="M3", business_id=1, event_key="notification-test",
        title="工单更新", content="你上报的工单有新进展", link="/m3/work-orders/1", **extra)


def test_real_inbox_is_scoped_paginated_and_read_state_is_idempotent(client, data, factory):
    with factory() as session, session.begin():
        first = publish(session, data)[0].id
        publish_notifications(session, recipients=[data["users"]["student"].user_id, data["users"]["other"].user_id],
            source_module="M3", business_id=2, event_key="second", title="另一条进展", content="新的说明", link="/m3/work-orders/2")
    student = token_headers(data["users"]["student"])
    other = token_headers(data["users"]["other"])
    response = client.get(BASE, headers=student, params={"page_size": 1})
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == page["unread_count"] == 2 and len(page["items"]) == 1
    assert isinstance(page["items"][0]["id"], str) and page["items"][0]["created_at"].endswith("Z")
    assert client.get(BASE, headers=other).json()["total"] == 1
    assert client.post(f"{BASE}/{first}/read", headers=other).status_code == 404
    assert client.get(BASE, headers=other, params={"recipient_id": str(data["users"]["student"].user_id)}).json()["total"] == 1
    read = client.post(f"{BASE}/{first}/read", headers=student)
    assert read.status_code == 200 and read.json()["read_at"].endswith("Z")
    again = client.post(f"{BASE}/{first}/read", headers=student)
    assert again.json()["read_at"] == read.json()["read_at"]
    assert client.get(f"{BASE}/unread-count", headers=student).json() == {"unread_count": 1}
    assert client.get(BASE, headers=student, params={"unread_only": True}).json()["total"] == 1
    assert client.post(f"{BASE}/read-all", headers=student).json() == {"updated": 1, "unread_count": 0}
    assert client.post(f"{BASE}/read-all", headers=student).json() == {"updated": 0, "unread_count": 0}
    assert client.get(f"{BASE}/unread-count", headers=other).json()["unread_count"] == 1


def test_duplicate_publication_is_durable_and_does_not_unread_a_read_message(client, data, factory):
    with factory() as session, session.begin():
        row = publish(session, data)[0]
        first_id, created = row.id, row.created_at
    client.post(f"{BASE}/{first_id}/read", headers=token_headers(data["users"]["student"]))
    with factory() as session, session.begin():
        same = publish(session, data)[0]
        assert same.id == first_id and same.created_at == created and same.read_at is not None
    with pytest.raises(DomainError), factory() as session, session.begin():
        publish_notifications(session, recipients=[data["users"]["student"].user_id],
            source_module="M3", business_id=99, event_key="notification-test", title="不同事件",
            content="不可覆盖原消息", link="/m3/work-orders/99")
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Notification)) == 1
        assert session.get(Notification, first_id).title == "工单更新"


def test_concurrent_delivery_deduplicates_one_event_per_recipient(data, factory):
    barrier = Barrier(2)
    def send():
        with factory() as session, session.begin():
            barrier.wait(timeout=5)
            return publish(session, data)[0].id
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: send(), range(2)))
    assert values[0] == values[1]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Notification)) == 1


def test_mark_all_does_not_read_a_notification_committed_after_its_inbox_snapshot(data, factory, mysql_engine):
    actor = data["users"]["student"]
    with factory() as session, session.begin():
        old_id = publish(session, data)[0].id
    inserted, release, committed = Event(), Event(), Event()
    main_thread = get_ident()

    def late_delivery():
        try:
            with factory() as session, session.begin():
                row = publish_notifications(session, recipients=[actor.user_id], source_module="M3",
                    business_id=2, event_key="late-commit", title="稍后送达的通知", content="尚未提交，不应被全部已读吞掉", link="/m3/work-orders/2")[0]
                late_id = row.id
                inserted.set()
                assert release.wait(5)
            return late_id
        finally:
            committed.set()

    def commit_late():
        release.set()
        assert committed.wait(5)

    def before_sql(_connection, _cursor, statement, _params, _context, _many):
        # The old timestamp-only implementation has no snapshot. Let the new
        # delivery commit just before its UPDATE, which must expose that bug.
        if get_ident() == main_thread and statement.lstrip().upper().startswith("UPDATE D_NOTIFICATIONS"):
            commit_late()

    def after_sql(_connection, _cursor, statement, _params, _context, _many):
        if get_ident() == main_thread and statement.lstrip().startswith("SELECT d_notifications.id "):
            commit_late()

    event.listen(mysql_engine, "before_cursor_execute", before_sql)
    event.listen(mysql_engine, "after_cursor_execute", after_sql)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(late_delivery)
            assert inserted.wait(5)
            with factory() as session, session.begin():
                assert mark_all_read(session, actor=actor) == (1, 1)
            late_id = future.result(timeout=5)
    finally:
        release.set()
        event.remove(mysql_engine, "before_cursor_execute", before_sql)
        event.remove(mysql_engine, "after_cursor_execute", after_sql)
    with factory() as session:
        assert session.get(Notification, old_id).read_at is not None
        assert session.get(Notification, late_id).read_at is None


def test_inactive_recipients_are_skipped_and_transaction_failure_does_not_deliver(data, factory):
    with factory() as session, session.begin():
        identity = session.scalar(select(UserIdentity).where(UserIdentity.user_id == data["users"]["student"].user_id))
        identity.is_active = False
    with factory() as session, session.begin():
        assert publish(session, data) == []
        assert publish_notifications(session, recipients=[], source_module="M3", business_id=1,
            event_key="empty", title="无需收件人", content="空收件人", link="/m3/work-orders/1") == []
    with pytest.raises(RuntimeError), factory() as session, session.begin():
        publish_notifications(session, recipients=[data["users"]["property"].user_id], source_module="M3",
            business_id=1, event_key="rollback", title="应回滚", content="未提交不能送达", link="/m3/work-orders/1")
        raise RuntimeError("injected post-delivery failure")
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Notification)) == 0


@pytest.mark.parametrize("path,method", [(BASE,"get"), (BASE+"/unread-count","get"), (BASE+"/1/read","post"), (BASE+"/read-all","post")])
def test_notification_routes_require_authentication(client, path, method):
    assert getattr(client, method)(path).status_code == 401


@pytest.mark.parametrize("link", ["https://example.com", "//example.com", "javascript:alert(1)", "/\\example.com", "/m3/work-orders/1?redirect=//example.com"])
def test_publisher_rejects_unsafe_links(data, factory, link):
    with pytest.raises(DomainError), factory() as session, session.begin():
        publish_notifications(session, recipients=[data["users"]["student"].user_id], source_module="M3",
            business_id=1, event_key="unsafe", title="标题", content="内容", link=link)
