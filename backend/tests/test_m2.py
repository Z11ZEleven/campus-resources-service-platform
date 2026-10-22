from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from uuid import uuid4

import pytest
from alembic.config import Config
from conftest import token_headers
from sqlalchemy import delete, func, inspect, select
from sqlalchemy.exc import OperationalError

from alembic import command
from app.core.errors import DomainError
from app.core.time import to_utc, utcnow
from app.modules.audit.models import OperationLog
from app.modules.auth.models import User, UserIdentity, UserRole
from app.modules.m2 import service
from app.modules.m2.models import Application
from app.modules.m2.schemas import CreateApplication, ReviewApplication
from app.modules.resources.models import ResourceBlock, Room
from app.modules.resources.service import lock_room, reserve_seat, start_maintenance

pytestmark = pytest.mark.mysql
BASE = "/api/v1/m2/applications"


def payload(data, **changes):
    return dict(
        room_id=str(data["room"]),
        title="课程研讨",
        purpose="教师组织课程讨论",
        participant_count=3,
        start_at=data["start"].isoformat(),
        end_at=data["end"].isoformat(),
        request_key=str(uuid4()),
        **changes,
    )


def create(client, data, body=None):
    result = client.post(
        BASE, json=body or payload(data), headers=token_headers(data["users"]["teacher"])
    )
    assert result.status_code == 201, result.text
    return result.json()


def review(client, data, application_id, passed=True, comment="同意"):
    return client.post(
        f"{BASE}/{application_id}/review",
        json={"passed": passed, "comment": comment},
        headers=token_headers(data["users"]["admin"]),
    )


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_create_retry_same_key_and_no_pending_hold(client, data, factory):
    body = payload(data)
    row = create(client, data, body)
    assert row["applicant_id"] == str(data["users"]["teacher"].user_id)
    assert isinstance(row["id"], str) and row["start_at"].endswith("Z")
    assert row["status"] == row["display_status"] == "PENDING"
    again = client.post(BASE, json=body, headers=token_headers(data["users"]["teacher"]))
    assert again.status_code == 200 and again.json()["id"] == row["id"]
    body["title"] = "更换内容"
    assert (
        client.post(BASE, json=body, headers=token_headers(data["users"]["teacher"])).status_code
        == 409
    )
    with factory() as s:
        assert count(s, Application) == count(s, OperationLog) == 1
        assert count(s, ResourceBlock) == 0


@pytest.mark.parametrize("actor", ["student", "other", "admin", "property", "repairer"])
def test_only_teacher_with_user_role_can_create(client, data, actor):
    assert (
        client.post(
            BASE, json=payload(data), headers=token_headers(data["users"][actor])
        ).status_code
        == 403
    )


def test_teacher_role_required_and_no_impersonation(client, data, factory):
    headers = token_headers(data["users"]["teacher"])
    body = payload(data)
    body["applicant_id"] = str(data["users"]["student"].user_id)
    assert client.post(BASE, json=body, headers=headers).status_code == 422
    assert client.post(BASE, json=payload(data)).status_code == 401
    with factory() as s, s.begin():
        s.execute(delete(UserRole).where(UserRole.user_id == data["users"]["teacher"].user_id))
    assert client.post(BASE, json=payload(data), headers=headers).status_code == 403


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "  "),
        ("title", "长" * 101),
        ("purpose", " "),
        ("participant_count", 0),
        ("participant_count", 4),
        ("participant_count", 1.5),
        ("participant_count", True),
        ("request_key", "not-a-uuid"),
        ("start_at", "2026-01-01T09:00:00"),
    ],
)
def test_input_validation(client, data, field, value):
    body = payload(data)
    body[field] = value
    assert (
        client.post(BASE, json=body, headers=token_headers(data["users"]["teacher"])).status_code
        == 422
    )


@pytest.mark.parametrize(
    ("start_hours", "end_hours", "status"),
    [
        (0.5, 1, 422),
        (0, 0, 422),
        (0, -1, 422),
        (-2, 1, 409),
        (0, 14, 409),
        (0, 24, 422),
        (7 * 24, 7 * 24 + 1, 422),
        (-48, -47, 422),
    ],
)
def test_time_boundaries(client, data, start_hours, end_hours, status):
    body = payload(data)
    body["start_at"] = (data["start"] + timedelta(hours=start_hours)).isoformat()
    body["end_at"] = (data["start"] + timedelta(hours=end_hours)).isoformat()
    assert (
        client.post(BASE, json=body, headers=token_headers(data["users"]["teacher"])).status_code
        == status
    )


def test_m2_allows_more_than_four_hours_and_open_boundaries(client, data):
    body = payload(data)
    body["start_at"] = data["start"].replace(hour=8).isoformat()
    body["end_at"] = data["start"].replace(hour=22).isoformat()
    row = create(client, data, body)
    assert review(client, data, row["id"]).status_code == 200


def test_department_and_disabled_room(client, data, factory):
    body = payload(data)
    body["room_id"] = str(data["private_room"])
    assert (
        client.post(BASE, json=body, headers=token_headers(data["users"]["teacher"])).status_code
        == 403
    )
    with factory() as s, s.begin():
        s.get(Room, data["room"]).is_active = False
    assert (
        client.post(
            BASE, json=payload(data), headers=token_headers(data["users"]["teacher"])
        ).status_code
        == 409
    )


def test_lists_detail_and_history_are_scoped(client, data, factory):
    row = create(client, data)
    other_headers = token_headers(data["users"]["other"])
    assert client.get(BASE, headers=other_headers).json()["total"] == 0
    assert client.get(f"{BASE}/{row['id']}", headers=other_headers).status_code == 404
    assert client.post(f"{BASE}/{row['id']}/cancel", headers=other_headers).status_code == 404
    admin_headers = token_headers(data["users"]["admin"])
    assert client.get(BASE, headers=admin_headers).json()["total"] == 1
    assert client.get(BASE, params={"mine": True}, headers=admin_headers).json()["total"] == 0
    result = client.get(f"{BASE}/{row['id']}", headers=admin_headers)
    assert result.status_code == 200
    assert result.json()["events"][0]["action"] == "CREATE_APPLICATION"
    assert client.get(BASE, params={"page_size": 101}, headers=admin_headers).status_code == 422
    assert client.get(BASE, params={"status": "UNKNOWN"}, headers=admin_headers).status_code == 422
    assert client.post(f"{BASE}/{row['id']}/cancel", headers=admin_headers).status_code == 403
    with factory() as s, s.begin():
        s.execute(delete(UserRole).where(UserRole.user_id == data["users"]["admin"].user_id))
    assert client.get(f"{BASE}/{row['id']}", headers=admin_headers).status_code == 404
    assert review(client, data, row["id"]).status_code == 403


def test_pagination_and_status_filter(client, data):
    first = create(client, data)
    second = create(client, data)
    assert review(client, data, first["id"], False, "用途需补充").status_code == 200
    headers = token_headers(data["users"]["teacher"])
    page1 = client.get(BASE, params={"page_size": 1}, headers=headers).json()
    page2 = client.get(BASE, params={"page_size": 1, "page": 2}, headers=headers).json()
    assert page1["total"] == page2["total"] == 2
    assert {page1["items"][0]["id"], page2["items"][0]["id"]} == {first["id"], second["id"]}
    pending = client.get(BASE, params={"status": "PENDING"}, headers=headers).json()
    assert pending["total"] == 1 and pending["items"][0]["id"] == second["id"]


def test_approval_idempotency_blocks_m1_and_updates_public_query(client, data, factory):
    row = create(client, data)
    first = review(client, data, row["id"])
    assert first.status_code == 200 and first.json()["status"] == "APPROVED"
    again = review(client, data, row["id"], comment="重复请求不得覆盖首条意见")
    assert again.json()["review_comment"] == "同意"
    assert review(client, data, row["id"], False, "改为驳回").status_code == 409
    assert (
        client.post(
            f"{BASE}/{row['id']}/cancel", headers=token_headers(data["users"]["teacher"])
        ).status_code
        == 409
    )
    with factory() as s:
        assert count(s, ResourceBlock) == 1
        assert count(s, OperationLog) == 3
        assert s.get(Room, data["room"]).resource_version == 1
    with pytest.raises(DomainError) as error, factory() as s, s.begin():
        reserve_seat(
            s,
            room_id=data["room"],
            seat_id=data["seats"][0],
            reservation_id=123,
            start_at=data["start"],
            end_at=data["end"],
            actor=data["users"]["student"],
        )
    assert error.value.status == 409
    result = client.get(
        f"/api/v1/resources/rooms/{data['room']}/availability",
        params={"date": data["day"]},
        headers=token_headers(data["users"]["student"]),
    ).json()
    blocked = [slot for slot in result["slots"] if slot["status"] == "ROOM_BOOKED"]
    assert len(blocked) == 3
    assert all(slot["source_module"] == "M2" and not slot["bookable"] for slot in blocked)


@pytest.mark.parametrize("source", ["M1", "M2", "M3"])
def test_conflicts_leave_pending_without_side_effects(client, data, factory, source):
    if source == "M2":
        existing = create(client, data)
        assert review(client, data, existing["id"]).status_code == 200
    else:
        with factory() as s, s.begin():
            if source == "M1":
                reserve_seat(
                    s,
                    room_id=data["room"],
                    seat_id=data["seats"][0],
                    reservation_id=991,
                    start_at=data["start"],
                    end_at=data["end"],
                    actor=data["users"]["student"],
                )
            else:
                start_maintenance(
                    s, room_id=data["room"], work_order_id=992, actor=data["users"]["repairer"]
                )
    row = create(client, data)
    with factory() as s:
        before = count(s, OperationLog)
    result = review(client, data, row["id"])
    assert result.status_code == 409
    with factory() as s:
        app = s.get(Application, int(row["id"]))
        assert app.status == "PENDING" and app.reviewer_id is None and app.reviewed_at is None
        assert count(s, ResourceBlock) == 1 and count(s, OperationLog) == before
        assert s.get(Room, data["room"]).resource_version == 1


def test_adjacent_periods_do_not_conflict(client, data):
    first = create(client, data)
    body = payload(data)
    body["start_at"] = data["end"].isoformat()
    body["end_at"] = (data["end"] + timedelta(hours=1)).isoformat()
    second = create(client, data, body)
    assert review(client, data, first["id"]).status_code == 200
    assert review(client, data, second["id"]).status_code == 200


def test_rejection_reason_and_cancellation_idempotency(client, data, factory):
    rejected = create(client, data)
    assert review(client, data, rejected["id"], False, "  ").status_code == 422
    assert review(client, data, rejected["id"], False, "活动信息不足").status_code == 200
    assert (
        review(client, data, rejected["id"], False, "retry").json()["review_comment"]
        == "活动信息不足"
    )
    assert review(client, data, rejected["id"]).status_code == 409
    cancelled = create(client, data)
    url = f"{BASE}/{cancelled['id']}/cancel"
    headers = token_headers(data["users"]["teacher"])
    for _ in range(2):
        result = client.post(url, headers=headers)
        assert result.status_code == 200 and result.json()["cancelled_at"] is not None
    assert review(client, data, cancelled["id"]).status_code == 409
    with factory() as s:
        assert count(s, ResourceBlock) == 0 and count(s, OperationLog) == 4


def test_self_review_and_non_admin_review_forbidden(client, data, factory):
    row = create(client, data)
    headers = token_headers(data["users"]["teacher"])
    request = {"passed": True, "comment": ""}
    assert (
        client.post(f"{BASE}/{row['id']}/review", json=request, headers=headers).status_code == 403
    )
    with factory() as s, s.begin():
        s.add(UserRole(user_id=data["users"]["teacher"].user_id, role_code="ROOM_ADMIN"))
    assert (
        client.post(f"{BASE}/{row['id']}/review", json=request, headers=headers).status_code == 403
    )
    with factory() as s:
        assert s.get(Application, int(row["id"])).status == "PENDING"


@pytest.mark.parametrize(
    "change", ["capacity", "disabled", "department", "applicant_role", "applicant_identity", "past"]
)
def test_approval_revalidates_current_rules(client, data, factory, change):
    row = create(client, data)
    with factory() as s, s.begin():
        room = s.get(Room, data["room"])
        if change == "capacity":
            room.capacity = 1
        if change == "disabled":
            room.is_active = False
        if change == "department":
            room.department_code = "EE"
        if change == "applicant_role":
            s.execute(delete(UserRole).where(UserRole.user_id == data["users"]["teacher"].user_id))
        if change == "applicant_identity":
            s.scalar(
                select(UserIdentity).where(UserIdentity.user_id == data["users"]["teacher"].user_id)
            ).identity_type = "STUDENT"
        if change == "past":
            app = s.get(Application, int(row["id"]))
            app.start_at = to_utc(data["start"] - timedelta(days=2))
            app.end_at = to_utc(data["end"] - timedelta(days=2))
    assert review(client, data, row["id"]).status_code in (403, 409, 422)
    with factory() as s:
        assert s.get(Application, int(row["id"])).status == "PENDING"
        assert count(s, ResourceBlock) == 0


def test_failure_after_resource_acquisition_rolls_back_everything(factory, data, monkeypatch):
    with factory() as s, s.begin():
        app, _ = service.create_application(
            s, CreateApplication.model_validate(payload(data)), data["users"]["teacher"]
        )
        app_id = app.id
    original = service.record_operation

    def fail_after_log(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected failure after log and resource write")

    monkeypatch.setattr(service, "record_operation", fail_after_log)
    with pytest.raises(RuntimeError), factory() as s, s.begin():
        service.review_application(
            s, app_id, ReviewApplication(passed=True), data["users"]["admin"]
        )
    with factory() as s:
        assert s.get(Application, app_id).status == "PENDING"
        assert count(s, ResourceBlock) == 0 and count(s, OperationLog) == 1
        assert s.get(Room, data["room"]).resource_version == 0


def test_ended_is_display_only(client, data, factory):
    row = create(client, data)
    assert review(client, data, row["id"]).status_code == 200
    with factory() as s, s.begin():
        app = s.get(Application, int(row["id"]))
        app.start_at, app.end_at = utcnow() - timedelta(hours=2), utcnow() - timedelta(hours=1)
    result = client.get(
        f"{BASE}/{row['id']}", headers=token_headers(data["users"]["teacher"])
    ).json()
    assert result["status"] == "APPROVED" and result["display_status"] == "ENDED"


def test_concurrent_create_same_request_is_idempotent(factory, data):
    body = CreateApplication.model_validate(payload(data))
    barrier = Barrier(2)

    def attempt(_):
        with factory() as s, s.begin():
            barrier.wait(timeout=5)
            app, created = service.create_application(s, body, data["users"]["teacher"])
            return app.id, created

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert results[0][0] == results[1][0]
    assert sorted(result[1] for result in results) == [False, True]
    with factory() as s:
        assert count(s, Application) == count(s, OperationLog) == 1


@pytest.mark.parametrize("same_application", [False, True])
def test_concurrent_approval_has_one_source(factory, data, same_application):
    ids = []
    with factory() as s, s.begin():
        for _ in range(2):
            row, _ = service.create_application(
                s, CreateApplication.model_validate(payload(data)), data["users"]["teacher"]
            )
            ids.append(row.id)
    if same_application:
        ids[1] = ids[0]
    barrier = Barrier(2)

    def attempt(app_id):
        try:
            with factory() as s, s.begin():
                barrier.wait(timeout=5)
                service.review_application(
                    s, app_id, ReviewApplication(passed=True), data["users"]["admin"]
                )
            return 200
        except DomainError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ids))
    assert sorted(results) == ([200, 200] if same_application else [200, 409])
    with factory() as s:
        assert count(s, ResourceBlock) == 1
        assert (
            s.scalar(
                select(func.count())
                .select_from(Application)
                .where(Application.status == "APPROVED")
            )
            == 1
        )
        assert s.get(Room, data["room"]).resource_version == 1


def test_approval_racing_cancellation_only_one_terminal_state(factory, data):
    with factory() as s, s.begin():
        row, _ = service.create_application(
            s, CreateApplication.model_validate(payload(data)), data["users"]["teacher"]
        )
        app_id = row.id
    barrier = Barrier(2)

    def attempt(action):
        try:
            with factory() as s, s.begin():
                barrier.wait(timeout=5)
                if action == "approve":
                    service.review_application(
                        s, app_id, ReviewApplication(passed=True), data["users"]["admin"]
                    )
                else:
                    service.cancel_application(s, app_id, data["users"]["teacher"])
            return 200
        except DomainError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["approve", "cancel"]))
    assert sorted(results) == [200, 409]
    with factory() as s:
        state = s.get(Application, app_id).status
        assert state in ("APPROVED", "CANCELLED")
        assert count(s, ResourceBlock) == (1 if state == "APPROVED" else 0)


def test_cancel_waits_for_room_lock(factory, data):
    with factory() as s, s.begin():
        row, _ = service.create_application(
            s, CreateApplication.model_validate(payload(data)), data["users"]["teacher"]
        )
        app_id = row.id
    entered, finished = Event(), Event()

    def cancel():
        with factory() as s, s.begin():
            entered.set()
            service.cancel_application(s, app_id, data["users"]["teacher"])
            finished.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with factory() as s, s.begin():
            lock_room(s, data["room"])
            future = pool.submit(cancel)
            assert entered.wait(timeout=5)
            assert not finished.wait(timeout=0.25)
        future.result(timeout=5)
    assert finished.is_set()


def test_database_constraints_reject_invalid_terminal_state(factory, data):
    with factory() as s, s.begin():
        row, _ = service.create_application(
            s, CreateApplication.model_validate(payload(data)), data["users"]["teacher"]
        )
        app_id = row.id
    with pytest.raises(OperationalError) as error, factory() as s, s.begin():
        s.get(Application, app_id).status = "APPROVED"  # Missing reviewer and reviewed_at.
        s.flush()
    assert error.value.orig.args[0] == 3819
    assert "ck_m2_state" in str(error.value.orig)


def test_m2_migration_roundtrip_preserves_public_rows(factory, data, mysql_engine):
    cfg = Config("alembic.ini")
    command.check(cfg)
    command.downgrade(cfg, "0003_m3_extensions")
    assert "m2_applications" not in inspect(mysql_engine).get_table_names()
    with factory() as s:
        assert s.get(Room, data["room"]).code == "PUBLIC"
    command.upgrade(cfg, "head")
    command.check(cfg)
    with factory() as s:
        assert count(s, Application) == 0 and count(s, User) == 6
    temporal = [
        c for c in inspect(mysql_engine).get_columns("m2_applications") if c["name"].endswith("_at")
    ]
    assert all(c["type"].fsp == 3 for c in temporal)
