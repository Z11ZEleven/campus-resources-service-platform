import os
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier, Event
from uuid import uuid4

import httpx
import pytest
from conftest import token_headers
from sqlalchemy import delete, func, select

from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.time import utcnow
from app.modules.audit.models import OperationLog
from app.modules.auth.models import RoleScope, User, UserIdentity, UserRole
from app.modules.auth.permissions import load_principal
from app.modules.m2 import service
from app.modules.m2.models import Application, ApprovalStep, ResourceJob
from app.modules.m2.schemas import CreateApplication, Review, Withdrawal
from app.modules.m2.worker import run_scan
from app.modules.resources.commands import ResourceCommand
from app.modules.resources.models import Room, Seat
from app.modules.resources.service import lock_room, reserve_seat, start_maintenance

pytestmark = pytest.mark.mysql
BASE = "/api/v1/m2"


@pytest.fixture(scope="session")
def resource_server(mysql_engine, tmp_path_factory):
    """独立进程、真实 TCP；不能用 ASGITransport 替代模块间网络。"""
    previous = os.environ.get("M2_RESOURCE_BASE_URL")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    os.environ["M2_RESOURCE_BASE_URL"] = url
    get_settings.cache_clear()
    log_path = tmp_path_factory.mktemp("m2-network") / "resource-server.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [
                str(Path(__import__("sys").executable)),
                "-m",
                "uvicorn",
                "app.resource_api:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            env=os.environ.copy(),
            stdout=log,
            stderr=log,
        )
        try:
            limit = time.monotonic() + 15
            while True:
                try:
                    if httpx.get(url + "/health", timeout=0.5, trust_env=False).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() >= limit or process.poll() is not None:
                    pytest.fail("D 资源服务未启动：" + log_path.read_text()[-1500:])
                time.sleep(0.05)
            yield url
        finally:
            process.terminate()
            process.wait(timeout=10)
            if previous is None:
                os.environ.pop("M2_RESOURCE_BASE_URL", None)
            else:
                os.environ["M2_RESOURCE_BASE_URL"] = previous
            get_settings.cache_clear()


@pytest.fixture
def m2_data(factory, data, password_hash):
    result = {**data, "users": dict(data["users"]), "seat_room": data["room"]}
    result["start"] = data["start"] + timedelta(days=1)
    result["end"] = result["start"] + timedelta(hours=1)
    result["day"] = result["start"].date()
    from datetime import time as clock

    with factory() as session, session.begin():
        for key, department, campus in [
            ("whole", None, "MAIN"),
            ("dept", "CS", "MAIN"),
            ("ee", "EE", "MAIN"),
            ("annex", None, "ANNEX"),
        ]:
            room = Room(
                code=key,
                name=key,
                building_name="M2",
                capacity=10,
                department_code=department,
                campus_code=campus,
                booking_mode="WHOLE_ROOM",
                open_time=clock(8),
                close_time=clock(22),
                is_active=True,
                resource_version=0,
            )
            session.add(room)
            session.flush()
            result[key] = room.id
            for number in range(1, 4):
                session.add(
                    Seat(
                        room_id=room.id,
                        seat_no=str(number),
                        is_active=True,
                        has_fixed_socket=False,
                        has_mobile_socket=False,
                    )
                )
        specs = [
            ("department", "DEPT_ADMIN", "CS", "DEPARTMENT", "CS"),
            ("wrong_department", "DEPT_ADMIN", "EE", "DEPARTMENT", "EE"),
            ("campus", "CAMPUS_ADMIN", "OPS", "CAMPUS", "MAIN"),
            ("wrong_campus", "CAMPUS_ADMIN", "OPS", "CAMPUS", "ANNEX"),
            ("room_reviewer", "ROOM_ADMIN", "OPS", "ROOM", str(result["whole"])),
            ("dept_reviewer", "ROOM_ADMIN", "OPS", "ROOM", str(result["dept"])),
            ("wrong_room", "ROOM_ADMIN", "OPS", "ROOM", str(result["annex"])),
            ("staff", None, "OPS", None, None),
        ]
        for username, role, department, scope_type, key in specs:
            user = User(
                username=username,
                password_hash=password_hash,
                display_name=username,
                is_active=True,
            )
            session.add(user)
            session.flush()
            session.add(
                UserIdentity(
                    user_id=user.id,
                    identity_type="STAFF",
                    identity_no="M2-" + username,
                    department_code=department,
                    is_active=True,
                )
            )
            if role:
                session.add(UserRole(user_id=user.id, role_code=role))
                session.add(
                    RoleScope(user_id=user.id, role_code=role, scope_type=scope_type, scope_key=key)
                )
            session.flush()
            result["users"][username] = load_principal(session, user.id)
    return result


def body(data, room="whole", **overrides):
    payload = {
        "room_id": str(data[room]),
        "purpose": "课程活动申请理由",
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    payload.update(overrides)
    return payload


def headers(data, actor, key=None):
    return {**token_headers(data["users"][actor]), "Idempotency-Key": key or str(uuid4())}


def create(client, data, *, actor="student", room="whole", key=None, **overrides):
    response = client.post(
        BASE + "/applications",
        headers=headers(data, actor, key),
        json=body(data, room, **overrides),
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def detail(client, data, app, actor="student"):
    response = client.get(f"{BASE}/applications/{app['id']}", headers=headers(data, actor))
    assert response.status_code == 200, response.text
    return response.json()


def decide(client, data, app, actor, *, passed=True, comment="", key=None, expected=None):
    current = detail(client, data, app, actor)
    step = next(step for step in current["steps"] if step["level"] == current["current_level"])
    return client.post(
        f"{BASE}/approval-tasks/{step['id']}/{'approve' if passed else 'reject'}",
        headers=headers(data, actor, key),
        json={"comment": comment, "expected_version": expected or current["version"]},
    )


def first_approved(client, data, room="whole", actor="student"):
    app = create(client, data, room=room, actor=actor)
    response = decide(client, data, app, "campus" if room == "whole" else "department")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "PENDING_ROOM_APPROVAL"
    return response.json()


@pytest.mark.parametrize("actor", ["student", "teacher", "property", "repairer", "staff"])
def test_all_effective_identities_can_apply_without_user_role(client, m2_data, actor):
    app = create(client, m2_data, actor=actor)
    assert app["applicant_id"] == str(m2_data["users"][actor].user_id)
    assert app["status"] == "PENDING_FIRST_APPROVAL" and app["current_level"] == 1
    assert app["resource_sync_status"] == "NONE"


def test_create_idempotency_fields_and_invisible_data(client, m2_data, factory):
    key = str(uuid4())
    app = create(client, m2_data, key=key)
    assert create(client, m2_data, key=key)["id"] == app["id"]
    response = client.post(
        BASE + "/applications",
        headers=headers(m2_data, "student", key),
        json=body(m2_data, purpose="不同内容"),
    )
    assert response.status_code == 409
    assert (
        client.post(
            BASE + "/applications",
            headers=headers(m2_data, "student"),
            json=body(m2_data, applicant_id=str(m2_data["users"]["teacher"].user_id)),
        ).status_code
        == 422
    )
    assert client.post(BASE + "/applications", json=body(m2_data)).status_code == 401
    assert (
        client.post(
            BASE + "/applications",
            headers=token_headers(m2_data["users"]["student"]),
            json=body(m2_data),
        ).status_code
        == 422
    )
    hidden = client.get(f"{BASE}/applications/{app['id']}", headers=headers(m2_data, "other"))
    assert hidden.status_code == 404
    assert (
        client.get(BASE + "/applications/me", headers=headers(m2_data, "other")).json()["total"]
        == 0
    )
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Application)) == 1
        assert session.scalar(select(func.count()).select_from(ResourceJob)) == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("room_id", True),
        ("room_id", 1.5),
        ("room_id", "1e2"),
        ("purpose", " "),
        ("title", "未确认字段"),
        ("participant_count", 2),
        ("start_at", "2026-01-01T09:00:00"),
    ],
)
def test_input_types_and_data_minimization(client, m2_data, field, value):
    assert (
        client.post(
            BASE + "/applications",
            headers=headers(m2_data, "student"),
            json=body(m2_data, **{field: value}),
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    ("start_shift", "end_shift"), [(-24, -23), (0.5, 1), (0, 0), (0, 5), (-2, 1), (0, 14), (0, 24)]
)
def test_time_constraints(client, m2_data, start_shift, end_shift):
    response = client.post(
        BASE + "/applications",
        headers=headers(m2_data, "student"),
        json=body(
            m2_data,
            start_at=(m2_data["start"] + timedelta(hours=start_shift)).isoformat(),
            end_at=(m2_data["start"] + timedelta(hours=end_shift)).isoformat(),
        ),
    )
    assert response.status_code == 422, response.text


def test_mode_department_and_m1_mutual_exclusion(client, m2_data, factory):
    assert (
        client.post(
            BASE + "/applications",
            headers=headers(m2_data, "student"),
            json=body(m2_data, "seat_room"),
        ).status_code
        == 422
    )
    assert (
        client.post(
            BASE + "/applications", headers=headers(m2_data, "student"), json=body(m2_data, "ee")
        ).status_code
        == 403
    )
    rooms = client.get(
        BASE + "/rooms", headers=headers(m2_data, "student"), params={"page_size": 1}
    ).json()
    assert rooms["total"] == 3 and len(rooms["items"]) == 1
    assert rooms["items"][0]["booking_mode"] == "WHOLE_ROOM"
    m1_rooms = client.get(
        "/api/v1/resources/rooms",
        headers=headers(m2_data, "student"),
        params={"booking_mode": "SEAT"},
    ).json()
    assert all(room["booking_mode"] == "SEAT" for room in m1_rooms["items"])
    with pytest.raises(DomainError) as error, factory() as session, session.begin():
        reserve_seat(
            session,
            room_id=m2_data["whole"],
            seat_id=m2_data["seats"][0],
            reservation_id=50,
            start_at=m2_data["start"],
            end_at=m2_data["end"],
            actor=m2_data["users"]["student"],
        )
    assert error.value.status == 409


@pytest.mark.parametrize(
    ("room", "correct", "wrong"),
    [("whole", "campus", "wrong_campus"), ("dept", "department", "wrong_department")],
)
def test_first_level_role_and_scope_and_no_direct_approval(
    client, m2_data, factory, room, correct, wrong
):
    app = create(client, m2_data, room=room)
    step = detail(client, m2_data, app)["steps"][0]
    for actor in [wrong, "room_reviewer", "admin", "property", "student"]:
        result = client.post(
            f"{BASE}/approval-tasks/{step['id']}/approve",
            headers=headers(m2_data, actor),
            json={"expected_version": app["version"]},
        )
        assert result.status_code in (403, 404), result.text
    good = decide(client, m2_data, app, correct)
    assert good.status_code == 200 and good.json()["status"] == "PENDING_ROOM_APPROVAL"
    assert len(good.json()["steps"]) == 2
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ResourceJob)) == 0
        assert session.scalar(select(func.count()).select_from(ResourceCommand)) == 0


@pytest.mark.parametrize("applicant", ["student", "teacher", "staff"])
def test_two_levels_real_network_and_notification_delivery(
    client, m2_data, factory, resource_server, applicant
):
    app = first_approved(client, m2_data, actor=applicant)
    result = decide(client, m2_data, app, "room_reviewer", comment="同意使用")
    assert result.status_code == 200, result.text
    approved = result.json()
    assert approved["status"] == "APPROVED" and approved["resource_hold_id"]
    assert approved["resource_sync_status"] == "SUCCEEDED"
    assert [step["status"] for step in approved["steps"]] == ["APPROVED", "APPROVED"]
    assert "APPROVED" in [event["event_type"] for event in approved["events"]]
    with factory() as session:
        command = session.scalar(select(ResourceCommand))
        assert command.status == "SUCCEEDED" and str(command.source_id) == approved["id"]
        assert session.scalar(select(ResourceJob.status)) == "DONE"
    inbox = client.get("/api/v1/notifications", headers=headers(m2_data, applicant)).json()
    assert any(
        n["source_module"] == "M2" and n["link"] == f"/m2/applications/{app['id']}"
        for n in inbox["items"]
    )
    source = client.get(
        f"/api/v1/resources/rooms/{m2_data['whole']}/availability",
        headers=headers(m2_data, "student"),
        params={"date": m2_data["day"]},
    ).json()
    assert (
        len(
            [
                s
                for s in source["slots"]
                if s["status"] == "ROOM_BOOKED" and s["source_module"] == "M2"
            ]
        )
        == 3
    )


def test_second_scope_self_review_rejection_and_replay(client, m2_data, factory):
    app = first_approved(client, m2_data)
    step = next(s for s in app["steps"] if s["level"] == 2)
    for actor in ["wrong_room", "admin", "campus", "student"]:
        result = client.post(
            f"{BASE}/approval-tasks/{step['id']}/approve",
            headers=headers(m2_data, actor),
            json={"expected_version": app["version"]},
        )
        assert result.status_code in (403, 404)
    assert (
        decide(client, m2_data, app, "room_reviewer", passed=False, comment=" ").status_code == 422
    )
    key = str(uuid4())
    rejected = decide(
        client, m2_data, app, "room_reviewer", passed=False, comment="理由不充分", key=key
    )
    assert rejected.status_code == 200 and rejected.json()["status"] == "REJECTED"
    replay = client.post(
        f"{BASE}/approval-tasks/{step['id']}/reject",
        headers=headers(m2_data, "room_reviewer", key),
        json={"expected_version": app["version"], "comment": "理由不充分"},
    )
    assert replay.status_code == 200
    wrong = client.post(
        f"{BASE}/approval-tasks/{step['id']}/approve",
        headers=headers(m2_data, "room_reviewer"),
        json={"expected_version": app["version"]},
    )
    assert wrong.status_code == 409
    own = create(client, m2_data, actor="campus")
    own_step = detail(client, m2_data, own, "campus")["steps"][0]
    self_review = client.post(
        f"{BASE}/approval-tasks/{own_step['id']}/approve",
        headers=headers(m2_data, "campus"),
        json={"expected_version": own["version"]},
    )
    assert self_review.status_code == 403
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(OperationLog)
                .where(OperationLog.action == "REVIEW_DENIED")
            )
            > 0
        )


@pytest.mark.parametrize("second_level", [False, True])
def test_withdraw_waiting_only_and_idempotency(client, m2_data, second_level):
    app = first_approved(client, m2_data) if second_level else create(client, m2_data)
    url = f"{BASE}/applications/{app['id']}/withdraw"
    assert (
        client.post(
            url, headers=headers(m2_data, "other"), json={"expected_version": app["version"]}
        ).status_code
        == 404
    )
    key = str(uuid4())
    for _ in range(2):
        response = client.post(
            url, headers=headers(m2_data, "student", key), json={"expected_version": app["version"]}
        )
        assert response.status_code == 200 and response.json()["status"] == "WITHDRAWN"
    assert (
        client.post(
            url,
            headers=headers(m2_data, "student"),
            json={"expected_version": response.json()["version"]},
        ).status_code
        == 409
    )


def test_scope_revocation_and_room_ownership_change_take_effect(client, m2_data, factory):
    app = create(client, m2_data, room="dept")
    assert (
        client.get(BASE + "/approval-tasks", headers=headers(m2_data, "department")).json()["total"]
        == 1
    )
    with factory() as session, session.begin():
        session.get(Room, m2_data["dept"]).department_code = "EE"
    assert (
        client.get(
            f"{BASE}/applications/{app['id']}", headers=headers(m2_data, "department")
        ).status_code
        == 404
    )
    assert (
        client.get(BASE + "/approval-tasks", headers=headers(m2_data, "department")).json()["total"]
        == 0
    )
    campus = create(client, m2_data)
    with factory() as session, session.begin():
        session.execute(
            delete(UserRole).where(UserRole.user_id == m2_data["users"]["campus"].user_id)
        )
    assert (
        client.get(
            f"{BASE}/applications/{campus['id']}", headers=headers(m2_data, "campus")
        ).status_code
        == 404
    )


def test_auto_timeout_reminder_and_restart_idempotency(client, m2_data, factory, monkeypatch):
    app = create(client, m2_data)
    with factory() as session, session.begin():
        row = session.get(Application, int(app["id"]))
        row.start_at = utcnow() + timedelta(hours=20)
        row.end_at = row.start_at + timedelta(hours=1)
        row.step_deadline_at = utcnow() + timedelta(hours=1)
    assert run_scan()["reminded"] == 1
    assert run_scan()["reminded"] == 0
    with factory() as session, session.begin():
        session.get(Application, int(app["id"])).step_deadline_at = utcnow() - timedelta(
            milliseconds=1
        )
    assert run_scan()["expired"] == 1
    assert run_scan()["expired"] == 0
    result = detail(client, m2_data, app)
    assert result["status"] == "AUTO_CANCELLED"
    assert [s["status"] for s in result["steps"]] == ["EXPIRED"]
    assert sum(e["event_type"] == "AUTO_CANCELLED" for e in result["events"]) == 1


def test_overlapping_pending_automatically_rejected_after_network_success(
    client, m2_data, resource_server
):
    first = first_approved(client, m2_data)
    pending = create(client, m2_data, actor="teacher")
    second = first_approved(client, m2_data, actor="staff")
    response = decide(client, m2_data, first, "room_reviewer")
    assert response.json()["status"] == "APPROVED"
    for app, actor in [(pending, "teacher"), (second, "staff")]:
        current = detail(client, m2_data, app, actor)
        assert current["status"] == "REJECTED"
        assert current["final_reason"] == "教室对应时段已被其他申请占用"
    adjacent = create(
        client,
        m2_data,
        start_at=m2_data["end"].isoformat(),
        end_at=(m2_data["end"] + timedelta(hours=1)).isoformat(),
    )
    adjacent = decide(client, m2_data, adjacent, "campus").json()
    assert decide(client, m2_data, adjacent, "room_reviewer").json()["status"] == "APPROVED"


def test_maintenance_conflict_is_final_rejection(client, m2_data, factory, resource_server):
    with factory() as session, session.begin():
        start_maintenance(
            session, room_id=m2_data["whole"], work_order_id=100, actor=m2_data["users"]["repairer"]
        )
    app = first_approved(client, m2_data)
    response = decide(client, m2_data, app, "room_reviewer")
    assert response.status_code == 200 and response.json()["status"] == "REJECTED"
    assert response.json()["final_reason"]


def test_m3_public_workflow_keeps_whole_room_locations_and_m2_hold(
    client, m2_data, resource_server
):
    rooms = client.get("/api/v1/resources/rooms", headers=headers(m2_data, "student")).json()
    assert any(room["id"] == str(m2_data["whole"]) for room in rooms["items"])
    app = first_approved(client, m2_data)
    assert decide(client, m2_data, app, "room_reviewer").json()["status"] == "APPROVED"
    order = client.post(
        "/api/v1/m3/work-orders",
        headers=headers(m2_data, "student"),
        json={
            "room_id": str(m2_data["whole"]),
            "title": "整室教室报修",
            "description": "验证原有 M3 流程不受影响",
            "category": "OTHER",
            "request_key": str(uuid4()),
        },
    )
    assert order.status_code == 201, order.text
    url = "/api/v1/m3/work-orders/" + order.json()["id"]
    for action, actor, payload in [
        ("assign", "property", {"assignee_id": str(m2_data["users"]["repairer"].user_id)}),
        ("start", "repairer", None),
        ("submit-completion", "repairer", {"completion_note": "维修完成"}),
        (
            "acceptance",
            "property",
            {"passed": True, "comment": "验收通过", "expected_completion_round": 1},
        ),
    ]:
        response = client.post(url + "/" + action, headers=headers(m2_data, actor), json=payload)
        assert response.status_code == 200, response.text
    result = client.get(
        f"/api/v1/resources/rooms/{m2_data['whole']}/availability",
        headers=headers(m2_data, "student"),
        params={"date": m2_data["day"]},
    ).json()
    occupied = [slot for slot in result["slots"] if slot["status"] == "ROOM_BOOKED"]
    assert len(occupied) == 3 and all(slot["source_module"] == "M2" for slot in occupied)


def test_same_step_concurrency_only_one_transition(factory, m2_data):
    with factory() as session, session.begin():
        app, _ = service.create_application(
            session,
            CreateApplication.model_validate(body(m2_data)),
            m2_data["users"]["student"],
            str(uuid4()),
        )
        app_id, step_id, version = app.id, service.steps_for(session, app)[0].id, app.version
    barrier = Barrier(2)

    def attempt(_):
        try:
            with factory() as session, session.begin():
                barrier.wait(timeout=5)
                service.review_step(
                    session,
                    step_id,
                    Review(expected_version=version),
                    m2_data["users"]["campus"],
                    str(uuid4()),
                    True,
                )
            return "OK"
        except DomainError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sorted(map(str, results)) == ["409", "OK"]
    with factory() as session:
        assert session.get(Application, app_id).status == "PENDING_ROOM_APPROVAL"
        assert (
            len(
                list(
                    session.scalars(
                        select(ApprovalStep).where(ApprovalStep.application_id == app_id)
                    )
                )
            )
            == 2
        )


def test_withdraw_waits_for_real_room_lock(factory, m2_data):
    with factory() as session, session.begin():
        app, _ = service.create_application(
            session,
            CreateApplication.model_validate(body(m2_data)),
            m2_data["users"]["student"],
            str(uuid4()),
        )
        app_id, version = app.id, app.version
    entered, finished = Event(), Event()

    def withdraw():
        with factory() as session, session.begin():
            entered.set()
            service.withdraw(
                session,
                app_id,
                Withdrawal(expected_version=version),
                m2_data["users"]["student"],
                str(uuid4()),
            )
            finished.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with factory() as session, session.begin():
            lock_room(session, m2_data["whole"])
            future = pool.submit(withdraw)
            assert entered.wait(5) and not finished.wait(0.25)
        future.result(timeout=5)
    assert finished.is_set()
