from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from conftest import token_headers
from sqlalchemy import delete, func, select, text

from app.core.errors import DomainError
from app.modules.audit.models import OperationLog
from app.modules.auth.models import User, UserIdentity, UserRole
from app.modules.resources.models import ResourceBlock, Room
from app.modules.resources.service import occupy_room, release_source, reserve_seat

pytestmark = pytest.mark.mysql

BASE = "/api/v1/m3/work-orders"


def create(client, data, *, reporter="student", **overrides):
    payload = {
        "room_id": str(data["room"]),
        "title": "空调无法启动",
        "description": "按下开关后空调没有反应，请安排检查。",
        "category": "AIR_CONDITIONING",
        "request_key": str(uuid4()),
        **overrides,
    }
    return client.post(BASE, headers=token_headers(data["users"][reporter]), json=payload)


def action(client, data, order_id, name, *, actor, payload=None):
    return client.post(
        f"{BASE}/{order_id}/{name}",
        headers=token_headers(data["users"][actor]),
        json=payload,
    )


def advance(client, data, *, status="REPAIRING", assignee="repairer", **kwargs):
    response = create(client, data, **kwargs)
    assert response.status_code == 201, response.text
    order = response.json()
    if status == "REPORTED":
        return order
    response = action(
        client, data, order["id"], "assign", actor="property",
        payload={"assignee_id": str(data["users"][assignee].user_id)},
    )
    assert response.status_code == 200, response.text
    if status == "ASSIGNED":
        return response.json()
    response = action(client, data, order["id"], "start", actor=assignee)
    assert response.status_code == 200, response.text
    if status == "REPAIRING":
        return response.json()
    response = action(
        client, data, order["id"], "submit-completion", actor=assignee,
        payload={"completion_note": "已更换损坏开关并检查运行正常。"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def accept(client, data, order, *, passed=True, comment=None, round_no=None, actor="property"):
    return action(
        client, data, order["id"], "acceptance", actor=actor,
        payload={
            "passed": passed,
            "comment": comment,
            "expected_completion_round": order["completion_round"] if round_no is None else round_no,
        },
    )


def work_order_row(session, order_id):
    return session.execute(
        text("SELECT * FROM m3_work_orders WHERE id = :id"), {"id": int(order_id)}
    ).mappings().one()


def logs(session, order_id):
    return list(session.scalars(
        select(OperationLog).where(
            OperationLog.module == "M3", OperationLog.business_id == int(order_id)
        ).order_by(OperationLog.id)
    ))


def block(session, order_id):
    return session.scalar(select(ResourceBlock).where(
        ResourceBlock.source_module == "M3", ResourceBlock.source_id == int(order_id)
    ))


def reserve(session, data, source_id):
    return reserve_seat(
        session, room_id=data["room"], seat_id=data["seats"][0],
        reservation_id=source_id, start_at=data["start"], end_at=data["end"],
        actor=data["users"]["student"],
    )


def test_create_work_order_returns_string_ids(client, data):
    response = client.post(
        "/api/v1/m3/work-orders",
        headers=token_headers(data["users"]["student"]),
        json={
            "room_id": str(data["room"]),
            "title": "空调无法启动",
            "description": "按下开关后空调没有反应，请安排检查。",
            "category": "AIR_CONDITIONING",
            "request_key": str(uuid4()),
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert isinstance(body["id"], str)
    assert body["reporter_id"] == str(data["users"]["student"].user_id)
    assert body["room_id"] == str(data["room"])
    assert body["status"] == "REPORTED"
    assert body["completion_round"] == 0
    assert body["created_at"].endswith("Z")
    assert body["updated_at"].endswith("Z")
    assert body["assignee_id"] is None


def test_complete_lifecycle_keeps_hold_until_acceptance_and_preserves_history(client, data, factory):
    order = advance(client, data, status="WAIT_ACCEPTANCE")
    assert order["status"] == "WAIT_ACCEPTANCE"
    assert order["completion_round"] == 1
    assert order["assignee_id"] == str(data["users"]["repairer"].user_id)
    assert order["assigned_by"] == str(data["users"]["property"].user_id)
    for field in ("assigned_at", "started_at", "submitted_at"):
        assert order[field].endswith("Z")
    with factory() as session:
        hold = block(session, order["id"])
        assert hold.status == "ACTIVE" and hold.end_at is None
        assert hold.seat_id is None
    with pytest.raises(DomainError) as conflict, factory() as session, session.begin():
        reserve(session, data, 101)
    assert conflict.value.status == 409
    response = accept(client, data, order, comment="功能复测通过")
    assert response.status_code == 200, response.text
    completed = response.json()
    assert completed["status"] == "COMPLETED"
    assert completed["accepted_by"] == str(data["users"]["property"].user_id)
    assert completed["accepted_at"].endswith("Z")
    with factory() as session:
        assert block(session, order["id"]).status == "RELEASED"
        assert session.get(Room, data["room"]).resource_version == 2
    with factory() as session, session.begin():
        reserve(session, data, 102)
    detail = client.get(f"{BASE}/{order['id']}", headers=token_headers(data["users"]["student"]))
    assert detail.status_code == 200
    history = detail.json()["history"]
    transitions = [row["to_status"] for row in history if row["to_status"] in {
        "REPORTED", "ASSIGNED", "REPAIRING", "WAIT_ACCEPTANCE", "COMPLETED"
    }]
    assert transitions == ["REPORTED", "ASSIGNED", "REPAIRING", "WAIT_ACCEPTANCE", "COMPLETED"]
    assert any("已更换损坏开关" in (row["note"] or "") for row in history)
    assert any("功能复测通过" in (row["note"] or "") for row in history)
    assert all(isinstance(row["id"], str) and isinstance(row["actor_id"], str) for row in history)
    assert all(row["created_at"].endswith("Z") for row in history)


def test_creation_idempotency_is_per_reporter_and_rejects_changed_payload(client, data, factory):
    key = str(uuid4())
    first = create(client, data, request_key=key)
    retry = create(client, data, request_key=key)
    assert first.status_code == retry.status_code == 201
    assert first.json()["id"] == retry.json()["id"]
    changed = create(client, data, request_key=key, title="不同故障")
    assert changed.status_code == 409, changed.text
    other = create(client, data, reporter="other", request_key=key)
    assert other.status_code == 201 and other.json()["id"] != first.json()["id"]
    with factory() as session:
        assert len(logs(session, first.json()["id"])) == 1
        assert session.scalar(select(func.count()).select_from(ResourceBlock)) == 0


def test_role_scoped_lists_details_pagination_and_multirole_union(client, data, factory):
    own = advance(client, data, status="ASSIGNED")
    other = advance(client, data, status="REPORTED", reporter="other")
    headers = {name: token_headers(actor) for name, actor in data["users"].items()}
    for actor, expected in (("student", {own["id"]}), ("other", {other["id"]}),
                            ("repairer", {own["id"]}), ("property", {own["id"], other["id"]})):
        response = client.get(BASE, headers=headers[actor])
        assert response.status_code == 200, response.text
        assert {row["id"] for row in response.json()["items"]} == expected
        assert response.json()["total"] == len(expected)
    assert client.get(f"{BASE}/{own['id']}", headers=headers["other"]).status_code == 404
    assert client.get(f"{BASE}/{other['id']}", headers=headers["repairer"]).status_code == 404
    page = client.get(f"{BASE}?page=2&page_size=1", headers=headers["property"])
    assert page.status_code == 200
    assert page.json()["total"] == 2 and len(page.json()["items"]) == 1
    assert page.json()["page"] == 2 and page.json()["page_size"] == 1
    assert client.get(f"{BASE}?page=0", headers=headers["property"]).status_code == 422
    with factory() as session, session.begin():
        session.add(UserRole(user_id=data["users"]["other"].user_id, role_code="REPAIRER"))
        session.add(UserRole(user_id=data["users"]["other"].user_id, role_code="SYS_ADMIN"))
    # Existing tokens reload roles. A multi-role user sees the union of own reports and assignments.
    assigned = action(client, data, other["id"], "assign", actor="property",
                      payload={"assignee_id": str(data["users"]["other"].user_id)})
    assert assigned.status_code == 200
    new = advance(client, data, status="ASSIGNED", assignee="other")
    response = client.get(BASE, headers=headers["other"])
    assert {row["id"] for row in response.json()["items"]} == {other["id"], new["id"]}
    assert own["id"] not in {row["id"] for row in response.json()["items"]}
    with factory() as session, session.begin():
        admin_id = data["users"]["admin"].user_id
        session.execute(delete(UserRole).where(UserRole.user_id == admin_id))
        session.add(UserRole(user_id=admin_id, role_code="SYS_ADMIN"))
    # SYS_ADMIN alone never grants the property administrator's all-orders view.
    system_list = client.get(BASE, headers=headers["admin"])
    assert system_list.status_code in (200, 403)
    if system_list.status_code == 200:
        assert system_list.json()["total"] == 0 and system_list.json()["items"] == []
    assert client.get(f"{BASE}/{own['id']}", headers=headers["admin"]).status_code == 404


def test_rejection_retains_hold_and_stale_acceptance_cannot_process_new_round(client, data, factory):
    first = advance(client, data, status="WAIT_ACCEPTANCE")
    with factory() as session:
        hold_id = block(session, first["id"]).id
    assert accept(client, data, first, passed=False, comment="").status_code == 422
    rejected = accept(client, data, first, passed=False, comment="仍有异响，请复查")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REPAIRING"
    second = action(client, data, first["id"], "submit-completion", actor="repairer",
                    payload={"completion_note": "紧固固定螺丝，第二次试运行正常"})
    assert second.status_code == 200
    order = second.json()
    assert order["completion_round"] == 2
    assert accept(client, data, order, round_no=1).status_code == 409
    assert accept(client, data, order, round_no=1, passed=False, comment="旧请求").status_code == 409
    with factory() as session:
        assert work_order_row(session, first["id"])["status"] == "WAIT_ACCEPTANCE"
        assert block(session, first["id"]).id == hold_id
        assert block(session, first["id"]).status == "ACTIVE"
        assert session.get(Room, data["room"]).resource_version == 1
    assert accept(client, data, order, comment="第二轮验收通过").status_code == 200
    # A completed response can be replayed only for its actual completion round.
    assert accept(client, data, order, comment="第二轮验收通过").status_code == 200
    assert accept(client, data, order, round_no=1).status_code == 409
    detail = client.get(f"{BASE}/{order['id']}", headers=token_headers(data["users"]["student"]))
    history = detail.json()["history"]
    for note in ("已更换损坏开关", "仍有异响", "第二次试运行正常", "第二轮验收通过"):
        assert any(note in (row["note"] or "") for row in history)
    with factory() as session:
        assert len([row for row in logs(session, order["id"]) if row.to_status == "COMPLETED"]) == 1
        assert session.get(Room, data["room"]).resource_version == 2


def test_repeat_start_does_not_add_hold_or_history_and_completed_cannot_restart(client, data, factory):
    order = advance(client, data)
    with factory() as session:
        hold_id, log_count = block(session, order["id"]).id, len(logs(session, order["id"]))
    retry = action(client, data, order["id"], "start", actor="repairer")
    assert retry.status_code == 200
    with factory() as session:
        assert block(session, order["id"]).id == hold_id
        assert len(logs(session, order["id"])) == log_count
        assert session.get(Room, data["room"]).resource_version == 1
    submitted = action(client, data, order["id"], "submit-completion", actor="repairer",
                       payload={"completion_note": "测试维修完毕"})
    assert submitted.status_code == 200
    assert action(client, data, order["id"], "start", actor="repairer").status_code == 409
    assert accept(client, data, submitted.json()).status_code == 200
    assert action(client, data, order["id"], "start", actor="repairer").status_code == 409
    assert action(client, data, order["id"], "submit-completion", actor="repairer",
                  payload={"completion_note": "迟到的完工请求"}).status_code == 409
    assert accept(client, data, submitted.json(), passed=False, comment="迟到的驳回").status_code == 409
    with factory() as session:
        assert block(session, order["id"]).status == "RELEASED"


def test_two_work_orders_release_only_own_hold(client, data, factory):
    first = advance(client, data, status="WAIT_ACCEPTANCE")
    second = advance(client, data, status="WAIT_ACCEPTANCE")
    assert accept(client, data, first).status_code == 200
    with pytest.raises(DomainError) as conflict, factory() as session, session.begin():
        reserve(session, data, 201)
    assert conflict.value.status == 409
    with factory() as session:
        assert block(session, first["id"]).status == "RELEASED"
        assert block(session, second["id"]).status == "ACTIVE"
    assert accept(client, data, second).status_code == 200
    with factory() as session, session.begin():
        reserve(session, data, 202)


@pytest.mark.parametrize("existing_module", ["M1", "M2"])
def test_maintenance_preserves_existing_use_and_blocks_new_m1_m2(client, data, factory, existing_module):
    with factory() as session, session.begin():
        if existing_module == "M1":
            existing = reserve(session, data, 301)
        else:
            existing = occupy_room(
                session, room_id=data["room"], application_id=301,
                applicant_id=data["users"]["teacher"].user_id,
                start_at=data["start"], end_at=data["end"], actor=data["users"]["admin"],
            )
        existing_id = existing.id
    order = advance(client, data, status="WAIT_ACCEPTANCE")
    with factory() as session:
        assert session.get(ResourceBlock, existing_id).status == "ACTIVE"
    # D's public integration functions are real; M1/M2 HTTP modules do not exist yet.
    with pytest.raises(DomainError) as m1, factory() as session, session.begin():
        reserve_seat(
            session, room_id=data["room"], seat_id=data["seats"][1], reservation_id=302,
            start_at=data["start"], end_at=data["end"], actor=data["users"]["student"],
        )
    assert m1.value.status == 409
    with pytest.raises(DomainError) as m2, factory() as session, session.begin():
        occupy_room(
            session, room_id=data["room"], application_id=302,
            applicant_id=data["users"]["teacher"].user_id,
            start_at=data["start"], end_at=data["end"], actor=data["users"]["admin"],
        )
    assert m2.value.status == 409
    assert accept(client, data, order).status_code == 200
    with factory() as session:
        assert session.get(ResourceBlock, existing_id).status == "ACTIVE"
        assert block(session, order["id"]).status == "RELEASED"
    with factory() as session, session.begin():
        release_source(session, room_id=data["room"], source_module=existing_module,
                       source_id=301, actor=data["users"]["student" if existing_module == "M1" else "admin"])
        reserve(session, data, 303)


def test_operation_permissions_states_and_assignee_ownership(client, data, factory):
    order = advance(client, data, status="REPORTED")
    assert action(client, data, order["id"], "assign", actor="student",
                  payload={"assignee_id": str(data["users"]["repairer"].user_id)}).status_code == 403
    assert action(client, data, order["id"], "start", actor="repairer").status_code == 409
    assert action(client, data, order["id"], "submit-completion", actor="repairer",
                  payload={"completion_note": "尚未派单"}).status_code == 409
    assigned = action(client, data, order["id"], "assign", actor="property",
                      payload={"assignee_id": str(data["users"]["repairer"].user_id)})
    assert assigned.status_code == 200
    assert action(client, data, order["id"], "assign", actor="property",
                  payload={"assignee_id": str(data["users"]["repairer"].user_id)}).status_code == 409
    assert action(client, data, order["id"], "start", actor="student").status_code == 403
    assert accept(client, data, assigned.json(), round_no=1).status_code == 409
    with factory() as session, session.begin():
        session.add(UserRole(user_id=data["users"]["other"].user_id, role_code="REPAIRER"))
    assert action(client, data, order["id"], "start", actor="other").status_code == 403
    assert action(client, data, order["id"], "start", actor="repairer").status_code == 200
    assert action(client, data, order["id"], "submit-completion", actor="other",
                  payload={"completion_note": "他人的维修结果"}).status_code == 403
    response = action(client, data, order["id"], "submit-completion", actor="repairer",
                      payload={"completion_note": "正确维修人提交"})
    assert response.status_code == 200
    assert accept(client, data, response.json(), actor="repairer").status_code == 403


@pytest.mark.parametrize("invalidity", ["role", "user", "identity"])
def test_assignment_rejects_invalid_repairer_without_changing_order(client, data, factory, invalidity):
    order = advance(client, data, status="REPORTED")
    with factory() as session, session.begin():
        repairer_id = data["users"]["repairer"].user_id
        if invalidity == "role":
            session.execute(delete(UserRole).where(UserRole.user_id == repairer_id))
        elif invalidity == "user":
            session.get(User, repairer_id).is_active = False
        else:
            session.scalar(select(UserIdentity).where(UserIdentity.user_id == repairer_id)).is_active = False
    response = action(client, data, order["id"], "assign", actor="property",
                      payload={"assignee_id": str(repairer_id)})
    assert 400 <= response.status_code < 500
    with factory() as session:
        row = work_order_row(session, order["id"])
        assert row["status"] == "REPORTED" and row["assignee_id"] is None
        assert len(logs(session, order["id"])) == 1


def test_repairer_with_property_role_cannot_accept_own_work(client, data, factory):
    with factory() as session, session.begin():
        session.add(UserRole(user_id=data["users"]["property"].user_id, role_code="REPAIRER"))
    order = advance(client, data, status="WAIT_ACCEPTANCE", assignee="property")
    assert accept(client, data, order).status_code == 403
    assert accept(client, data, order, passed=False, comment="自行驳回也不可").status_code == 403
    with factory() as session:
        assert work_order_row(session, order["id"])["status"] == "WAIT_ACCEPTANCE"
        assert block(session, order["id"]).status == "ACTIVE"


@pytest.mark.parametrize("body", [
    {"title": " "}, {"title": "长" * 101}, {"description": " "},
    {"description": "长" * 2001}, {"category": "UNKNOWN"},
    {"request_key": "not-a-uuid"}, {"reporter_id": "999"}, {"status": "COMPLETED"},
])
def test_invalid_creation_fields_are_rejected(client, data, body):
    response = create(client, data, **body)
    assert response.status_code == 422, response.text


def test_create_requires_active_identity_user_role_and_accessible_room(client, data, factory):
    assert client.post(BASE, json={}).status_code == 401
    assert create(client, data, reporter="property").status_code == 403
    assert create(client, data, room_id=str(data["private_room"])).status_code == 404
    assert create(client, data, room_id="18446744073709551615").status_code == 404
    headers = token_headers(data["users"]["student"])
    with factory() as session, session.begin():
        session.scalar(select(UserIdentity).where(
            UserIdentity.user_id == data["users"]["student"].user_id
        )).is_active = False
    # A session issued before identity revocation must fail on its next request.
    assert client.post(BASE, headers=headers, json={
        "room_id": str(data["room"]), "title": "身份撤销测试", "description": "虚构故障",
        "category": "OTHER", "request_key": str(uuid4()),
    }).status_code == 401


@pytest.mark.parametrize("phase", ["start", "acceptance"])
def test_fault_after_resource_change_rolls_back_entire_http_transaction(client, data, factory, monkeypatch, phase):
    from app.modules.m3 import service

    order = advance(client, data, status="ASSIGNED" if phase == "start" else "WAIT_ACCEPTANCE")
    with factory() as session:
        previous_logs = len(logs(session, order["id"]))
        previous_version = session.get(Room, data["room"]).resource_version

    def fail_after_resource_change(*args, **kwargs):
        raise RuntimeError("injected failure after the real D resource mutation")

    monkeypatch.setattr(service, "record_operation", fail_after_resource_change)
    if phase == "start":
        response = action(client, data, order["id"], "start", actor="repairer")
    else:
        response = accept(client, data, order)
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "INTERNAL_ERROR"
    assert response.json()["detail"]["request_id"] == response.headers["X-Request-ID"]
    assert "injected failure" not in response.text
    with factory() as session:
        row = work_order_row(session, order["id"])
        assert row["status"] == order["status"]
        assert len(logs(session, order["id"])) == previous_logs
        assert session.get(Room, data["room"]).resource_version == previous_version
        if phase == "start":
            assert block(session, order["id"]) is None
            assert row["started_at"] is None
        else:
            assert block(session, order["id"]).status == "ACTIVE"
            assert row["accepted_by"] is None and row["accepted_at"] is None


def test_concurrent_start_and_acceptance_do_not_duplicate_side_effects(client, data, factory):
    order = advance(client, data, status="ASSIGNED")

    def run_together(call):
        barrier = Barrier(2)

        def request(_):
            barrier.wait(timeout=5)
            return call()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(request, range(2)))

    starts = run_together(lambda: action(client, data, order["id"], "start", actor="repairer"))
    assert [response.status_code for response in starts] == [200, 200]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ResourceBlock)) == 1
        assert len([row for row in logs(session, order["id"]) if row.to_status == "REPAIRING"]) == 1
        assert session.get(Room, data["room"]).resource_version == 1
    submitted = action(client, data, order["id"], "submit-completion", actor="repairer",
                       payload={"completion_note": "并发验收前完成维修"})
    assert submitted.status_code == 200
    approvals = run_together(lambda: accept(client, data, submitted.json()))
    assert [response.status_code for response in approvals] == [200, 200]
    with factory() as session:
        assert block(session, order["id"]).status == "RELEASED"
        assert len([row for row in logs(session, order["id"]) if row.to_status == "COMPLETED"]) == 1
        assert session.get(Room, data["room"]).resource_version == 2


def test_concurrent_same_key_different_rooms_returns_conflict_not_duplicate(client, data, factory):
    barrier, key = Barrier(2), str(uuid4())

    def request(room_id):
        barrier.wait(timeout=5)
        return create(client, data, reporter="other", room_id=str(room_id), request_key=key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(request, (data["room"], data["private_room"])))
    assert sorted(response.status_code for response in responses) == [201, 409]
    with factory() as session:
        assert session.scalar(text("SELECT count(*) FROM m3_work_orders WHERE request_key = :key"),
                              {"key": key}) == 1
        assert session.scalar(select(func.count()).select_from(OperationLog)) == 1


def test_list_filters_preserve_visibility_and_match_filtered_totals(client, data):
    lighting = advance(client, data, status="REPORTED", category="LIGHTING")
    assigned = advance(client, data, status="ASSIGNED")
    hidden = advance(client, data, status="ASSIGNED", reporter="other")
    private = advance(client, data, status="REPORTED", reporter="other",
                      room_id=str(data["private_room"]), category="LIGHTING")
    student_headers = token_headers(data["users"]["student"])
    property_headers = token_headers(data["users"]["property"])
    cases = [
        (student_headers, {"status": "ASSIGNED"}, {assigned["id"]}),
        (student_headers, {"category": "LIGHTING"}, {lighting["id"]}),
        (student_headers, {"room_id": str(data["room"])}, {lighting["id"], assigned["id"]}),
        (student_headers, {"room_id": str(data["private_room"])}, set()),
        (student_headers, {"status": "REPORTED", "category": "LIGHTING",
                           "room_id": str(data["room"])}, {lighting["id"]}),
        (property_headers, {"status": "ASSIGNED"}, {assigned["id"], hidden["id"]}),
        (property_headers, {"category": "LIGHTING"}, {lighting["id"], private["id"]}),
        (property_headers, {"room_id": str(data["private_room"])}, {private["id"]}),
    ]
    for headers, filters, expected_ids in cases:
        response = client.get(BASE, headers=headers, params=filters)
        assert response.status_code == 200, response.text
        page = response.json()
        assert {row["id"] for row in page["items"]} == expected_ids
        assert page["total"] == len(page["items"]) == len(expected_ids)
    for filters in ({"status": "UNKNOWN"}, {"category": "UNKNOWN"},
                    {"room_id": "0"}, {"room_id": "not-a-room"}):
        assert client.get(BASE, headers=student_headers, params=filters).status_code == 422


def test_creation_replay_after_completion_preserves_final_state_without_side_effects(client, data, factory):
    key = str(uuid4())
    order = advance(client, data, status="WAIT_ACCEPTANCE", request_key=key)
    completed = accept(client, data, order, comment="验收通过，创建重试不得重新上报")
    assert completed.status_code == 200
    with factory() as session:
        previous_logs = len(logs(session, order["id"]))
        previous_blocks = session.scalar(select(func.count()).select_from(ResourceBlock))
        previous_hold_id = block(session, order["id"]).id
        previous_version = session.get(Room, data["room"]).resource_version
    replay = create(client, data, request_key=key)
    assert replay.status_code == 201, replay.text
    assert replay.json() == completed.json()
    assert replay.json()["status"] == "COMPLETED"
    with factory() as session:
        assert len(logs(session, order["id"])) == previous_logs
        assert session.scalar(select(func.count()).select_from(ResourceBlock)) == previous_blocks
        assert session.get(Room, data["room"]).resource_version == previous_version
        hold = block(session, order["id"])
        assert hold.id == previous_hold_id and hold.status == "RELEASED"
        assert session.scalar(text("SELECT count(*) FROM m3_work_orders WHERE request_key = :key"),
                              {"key": key}) == 1


def test_revoked_repairer_role_invalidates_original_token_permissions(client, data, factory):
    assigned = advance(client, data, status="ASSIGNED")
    repairing = advance(client, data, status="REPAIRING")
    original_headers = token_headers(data["users"]["repairer"])
    assert client.get(f"{BASE}/{assigned['id']}", headers=original_headers).status_code == 200
    assert client.get(f"{BASE}/{repairing['id']}", headers=original_headers).status_code == 200
    with factory() as session, session.begin():
        previous_logs = {order["id"]: len(logs(session, order["id"])) for order in (assigned, repairing)}
        previous_version = session.get(Room, data["room"]).resource_version
        session.execute(delete(UserRole).where(
            UserRole.user_id == data["users"]["repairer"].user_id,
            UserRole.role_code == "REPAIRER",
        ))
    start = client.post(f"{BASE}/{assigned['id']}/start", headers=original_headers)
    complete = client.post(f"{BASE}/{repairing['id']}/submit-completion", headers=original_headers,
                           json={"completion_note": "角色撤销后使用原令牌提交"})
    assert start.status_code == complete.status_code == 403
    for order in (assigned, repairing):
        assert client.get(f"{BASE}/{order['id']}", headers=original_headers).status_code == 404
    listed = client.get(BASE, headers=original_headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 0 and listed.json()["items"] == []
    with factory() as session:
        for order in (assigned, repairing):
            assert work_order_row(session, order["id"])["status"] == order["status"]
            assert len(logs(session, order["id"])) == previous_logs[order["id"]]
        assert block(session, assigned["id"]) is None
        assert block(session, repairing["id"]).status == "ACTIVE"
        assert session.get(Room, data["room"]).resource_version == previous_version
