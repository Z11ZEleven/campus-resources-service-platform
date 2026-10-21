from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from conftest import token_headers
from sqlalchemy import func, select
from test_m3 import BASE, accept, action, advance, create

from app.core.errors import DomainError
from app.core.time import utcnow
from app.modules.auth.models import User, UserIdentity, UserRole
from app.modules.m3 import service, timeouts
from app.modules.m3.models import WorkOrder, WorkOrderEscalation, WorkOrderRating
from app.modules.m3.schemas import AssignWorkOrder, CreateWorkOrder, SubmitRating
from app.modules.notifications.models import Notification

pytestmark = pytest.mark.mysql


def rating(client, data, order, *, actor="student", score=5, comment=None):
    return action(
        client, data, order["id"], "rating", actor=actor,
        payload={"score": score, "comment": comment},
    )


def completed(client, data):
    order = advance(client, data, status="WAIT_ACCEPTANCE")
    response = accept(client, data, order)
    assert response.status_code == 200, response.text
    return response.json()


def set_stage_start(factory, order_id, started):
    with factory() as session, session.begin():
        session.get(WorkOrder, int(order_id)).stage_started_at = started


def count_rows(factory, model, order_id):
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model).where(
            model.work_order_id == int(order_id),
        ))


def test_rating_ownership_completed_once_replay_and_detail(client, data, factory):
    order = advance(client, data, status="REPORTED")
    assert rating(client, data, order).status_code == 409
    order = completed(client, data)
    assert rating(client, data, order, actor="other").status_code == 404
    assert rating(client, data, order, actor="repairer").status_code == 403
    assert rating(client, data, order, actor="property").status_code == 403
    response = rating(client, data, order, score=4, comment="  维修及时  ")
    assert response.status_code == 201, response.text
    original = response.json()
    retry = rating(client, data, order, score=4, comment="维修及时")
    assert retry.status_code == 201 and retry.json() == original
    assert rating(client, data, order, score=3, comment="维修及时").status_code == 409
    assert count_rows(factory, WorkOrderRating, order["id"]) == 1
    detail = client.get(f"{BASE}/{order['id']}", headers=token_headers(data["users"]["repairer"]))
    assert detail.status_code == 200
    assert detail.json()["rating"] == original
    assert detail.json()["timeout"] == {
        "due_at": None, "is_overdue": False, "current_stage_escalated": False,
    }


def test_rating_concurrent_same_input_has_one_row_and_one_notification_event(client, data, factory):
    order = completed(client, data)
    barrier = Barrier(2)

    def submit():
        barrier.wait(timeout=10)
        with factory() as session, session.begin():
            return service.submit_rating(
                session, order_id=int(order["id"]), actor=data["users"]["student"],
                body=SubmitRating(score=5, comment="满意"),
            ).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: submit(), range(2)))
    assert ids[0] == ids[1]
    assert count_rows(factory, WorkOrderRating, order["id"]) == 1
    with factory() as session:
        notifications = list(session.scalars(select(Notification).where(
            Notification.event_key == f"M3:{order['id']}:RATE_WORK_ORDER:1",
        )))
        assert {row.recipient_id for row in notifications} == {
            data["users"][name].user_id for name in ("student", "property", "repairer")
        }
        assert len(notifications) == 3


def test_rating_concurrent_different_input_cannot_overwrite(client, data, factory):
    order = completed(client, data)
    barrier = Barrier(2)

    def submit(score):
        barrier.wait(timeout=10)
        try:
            with factory() as session, session.begin():
                service.submit_rating(
                    session, order_id=int(order["id"]), actor=data["users"]["student"],
                    body=SubmitRating(score=score),
                )
            return "created"
        except DomainError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, [4, 5]))
    assert sorted(map(str, results)) == ["409", "created"]
    assert count_rows(factory, WorkOrderRating, order["id"]) == 1


@pytest.mark.parametrize("stage,hours", [
    ("REPORTED", 24), ("ASSIGNED", 24), ("REPAIRING", 48), ("WAIT_ACCEPTANCE", 24),
])
def test_timeout_strict_boundary_and_no_repeat(client, data, factory, stage, hours):
    order = advance(client, data, status=stage)
    deadline = utcnow()
    started = deadline - timedelta(hours=hours)
    set_stage_start(factory, order["id"], started)
    assert timeouts.run_timeout_scan(now=deadline) == {"checked": 0, "escalated": 0}
    assert timeouts.run_timeout_scan(now=deadline + timedelta(milliseconds=1))["escalated"] == 1
    assert timeouts.run_timeout_scan(now=deadline + timedelta(days=5)) == {
        "checked": 0, "escalated": 0,
    }
    with factory() as session:
        row = session.get(WorkOrder, int(order["id"]))
        assert row.status == stage and row.stage_started_at == started and row.escalation_count == 1
        escalation = session.scalar(select(WorkOrderEscalation).where(
            WorkOrderEscalation.work_order_id == row.id,
        ))
        assert escalation.due_at == deadline and escalation.triggered_by is None
        recipients = list(session.scalars(select(Notification.recipient_id).where(
            Notification.event_key == f"M3:{row.id}:TIMEOUT:{stage}:{row.completion_round}",
        )))
        assert recipients == [data["users"]["property"].user_id]


def test_retry_start_and_acceptance_do_not_reset_stage_start(client, data, factory):
    order = advance(client, data)
    started = utcnow() - timedelta(hours=1)
    set_stage_start(factory, order["id"], started)
    retry = action(client, data, order["id"], "start", actor="repairer")
    assert retry.status_code == 200
    with factory() as session:
        assert session.get(WorkOrder, int(order["id"])).stage_started_at == started
    response = action(client, data, order["id"], "submit-completion", actor="repairer",
                      payload={"completion_note": "处理完成"})
    assert response.status_code == 200
    order = response.json()
    assert accept(client, data, order).status_code == 200
    with factory() as session:
        accepted_start = session.get(WorkOrder, int(order["id"])).stage_started_at
    assert accept(client, data, order).status_code == 200
    with factory() as session:
        assert session.get(WorkOrder, int(order["id"])).stage_started_at == accepted_start


def test_rejection_restarts_timer_and_per_round_escalation(client, data, factory):
    order = advance(client, data)
    old = utcnow() - timedelta(hours=49)
    set_stage_start(factory, order["id"], old)
    assert timeouts.run_timeout_scan()["escalated"] == 1
    response = action(client, data, order["id"], "submit-completion", actor="repairer",
                      payload={"completion_note": "第一轮完成"})
    order = response.json()
    set_stage_start(factory, order["id"], utcnow() - timedelta(hours=25))
    assert timeouts.run_timeout_scan()["escalated"] == 1
    assert accept(client, data, order, passed=False, comment="仍有故障").status_code == 200
    assert timeouts.run_timeout_scan()["escalated"] == 0
    with factory() as session:
        row = session.get(WorkOrder, int(order["id"]))
        assert row.status == "REPAIRING" and row.stage_started_at > old
    set_stage_start(factory, order["id"], utcnow() - timedelta(hours=49))
    assert timeouts.run_timeout_scan()["escalated"] == 1
    with factory() as session:
        stages = set(session.execute(select(
            WorkOrderEscalation.stage, WorkOrderEscalation.completion_round,
        )).all())
        assert stages == {("REPAIRING", 0), ("WAIT_ACCEPTANCE", 1), ("REPAIRING", 1)}


def test_concurrent_scans_escalate_once(client, data, factory):
    order = advance(client, data, status="REPORTED")
    set_stage_start(factory, order["id"], utcnow() - timedelta(hours=25))
    barrier = Barrier(2)

    def scan():
        barrier.wait(timeout=10)
        return timeouts.run_timeout_scan()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: scan(), range(2)))
    assert sum(result["escalated"] for result in results) == 1
    assert count_rows(factory, WorkOrderEscalation, order["id"]) == 1


def test_already_escalated_orders_do_not_starve_later_candidates(client, data, factory):
    first = advance(client, data, status="REPORTED")
    second = advance(client, data, status="REPORTED")
    old = utcnow() - timedelta(hours=25)
    set_stage_start(factory, first["id"], old)
    set_stage_start(factory, second["id"], old)
    assert timeouts.run_timeout_scan(limit=1)["escalated"] == 1
    assert timeouts.run_timeout_scan(limit=1)["escalated"] == 1
    assert timeouts.run_timeout_scan(limit=1)["escalated"] == 0


def test_normal_transition_racing_scan_cannot_escalate_fresh_stage(client, data, factory):
    order = advance(client, data, status="REPORTED")
    set_stage_start(factory, order["id"], utcnow() - timedelta(hours=25))
    barrier = Barrier(2)

    def assign():
        barrier.wait(timeout=10)
        with factory() as session, session.begin():
            service.assign_work_order(
                session, order_id=int(order["id"]), actor=data["users"]["property"],
                body=AssignWorkOrder(assignee_id=data["users"]["repairer"].user_id),
            )

    def scan():
        barrier.wait(timeout=10)
        return timeouts.run_timeout_scan()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assigning = pool.submit(assign)
        scanning = pool.submit(scan)
        assigning.result(timeout=20)
        scanning.result(timeout=20)
    with factory() as session:
        row = session.get(WorkOrder, int(order["id"]))
        assert row.status == "ASSIGNED"
        escalations = list(session.scalars(select(WorkOrderEscalation)))
        assert all(item.stage == "REPORTED" for item in escalations)
        assert len(escalations) <= 1


def test_manual_scan_permission_and_trigger_identity(client, data, factory):
    order = advance(client, data, status="REPORTED")
    set_stage_start(factory, order["id"], utcnow() - timedelta(hours=25))
    assert client.post(BASE + "/scan-timeouts", headers=token_headers(
        data["users"]["student"],
    )).status_code == 403
    response = client.post(BASE + "/scan-timeouts", headers=token_headers(data["users"]["property"]))
    assert response.status_code == 200 and response.json()["escalated"] == 1
    with factory() as session:
        row = session.scalar(select(WorkOrderEscalation))
        assert row.triggered_by == data["users"]["property"].user_id
    policy = client.get(BASE + "/timeout-policy", headers=token_headers(data["users"]["student"]))
    assert policy.status_code == 200 and policy.json()["repairing_hours"] == 48


def test_disabled_admin_and_identity_do_not_receive_notifications(client, data, factory, password_hash):
    with factory() as session, session.begin():
        for active_user, active_identity in [(False, True), (True, False)]:
            user = User(username=f"inactive-{active_user}", password_hash=password_hash,
                        display_name="无效管理员", is_active=active_user)
            session.add(user)
            session.flush()
            session.add_all([
                UserIdentity(user_id=user.id, identity_type="STAFF", identity_no=f"bad-{user.id}",
                             department_code="OPS", is_active=active_identity),
                UserRole(user_id=user.id, role_code="PROPERTY_ADMIN"),
            ])
    order = create(client, data).json()
    with factory() as session:
        recipients = set(session.scalars(select(Notification.recipient_id).where(
            Notification.business_id == int(order["id"]), Notification.source_module == "M3",
        )))
        assert recipients == {data["users"][name].user_id for name in ("student", "property")}


def test_business_notification_failure_rolls_back_create_and_rating(client, data, factory, monkeypatch):
    order = completed(client, data)

    def fail(*args, **kwargs):
        raise RuntimeError("notification write failed")

    monkeypatch.setattr(service, "publish_notifications", fail)
    with pytest.raises(RuntimeError), factory() as session, session.begin():
        service.submit_rating(session, order_id=int(order["id"]), body=SubmitRating(score=4),
                              actor=data["users"]["student"])
    assert count_rows(factory, WorkOrderRating, order["id"]) == 0
    with pytest.raises(RuntimeError), factory() as session, session.begin():
        service.create_work_order(
            session, actor=data["users"]["student"],
            body=CreateWorkOrder(room_id=data["room"], title="应回滚", description="通知失败",
                                 category="OTHER", request_key=uuid4()),
        )
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(WorkOrder).where(
            WorkOrder.title == "应回滚",
        )) == 0


def test_timeout_notification_failure_rolls_back_then_next_scan_retries(client, data, factory, monkeypatch):
    order = advance(client, data, status="REPORTED")
    old = utcnow() - timedelta(hours=25)
    set_stage_start(factory, order["id"], old)
    original = timeouts.publish_notifications

    def fail(*args, **kwargs):
        raise RuntimeError("notification write failed")

    monkeypatch.setattr(timeouts, "publish_notifications", fail)
    assert timeouts.run_timeout_scan() == {"checked": 1, "escalated": 0}
    with factory() as session:
        row = session.get(WorkOrder, int(order["id"]))
        assert row.escalation_count == 0 and row.stage_started_at == old
    assert count_rows(factory, WorkOrderEscalation, order["id"]) == 0
    monkeypatch.setattr(timeouts, "publish_notifications", original)
    assert timeouts.run_timeout_scan()["escalated"] == 1


def test_transition_notification_failure_rolls_back_state_and_resource_hold(client, data, factory, monkeypatch):
    from app.modules.resources.models import ResourceBlock

    order = advance(client, data, status="ASSIGNED")
    with factory() as session:
        original_start = session.get(WorkOrder, int(order["id"])).stage_started_at

    def fail(*args, **kwargs):
        raise RuntimeError("notification write failed")

    monkeypatch.setattr(service, "publish_notifications", fail)
    with pytest.raises(RuntimeError), factory() as session, session.begin():
        service.start_work_order(
            session, order_id=int(order["id"]), actor=data["users"]["repairer"],
        )
    with factory() as session:
        row = session.get(WorkOrder, int(order["id"]))
        assert row.status == "ASSIGNED" and row.started_at is None
        assert row.stage_started_at == original_start
        assert session.scalar(select(func.count()).select_from(ResourceBlock).where(
            ResourceBlock.source_module == "M3", ResourceBlock.source_id == row.id,
        )) == 0
