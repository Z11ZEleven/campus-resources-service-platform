import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture(autouse=True)
def freeze_search_clock(request, monkeypatch):
    if "data" in request.fixturenames:
        from datetime import timedelta

        from app.core.time import to_utc

        data = request.getfixturevalue("data")
        monkeypatch.setattr(
            "app.modules.m1.search.precise_utcnow",
            lambda: to_utc(data["start"]) - timedelta(hours=1),
        )


def test_seat_search_requires_authenticated_session():
    with TestClient(create_app()) as client:
        response = client.get(
            "/api/v1/m1/seats",
            params={
                "identity_id": "1",
                "start_at": "2026-11-12T09:00:00+08:00",
                "end_at": "2026-11-12T10:00:00+08:00",
            },
        )
    assert response.status_code == 401


def test_search_filters_identity_scope_conflicts_and_paginates(client, data, factory):
    from conftest import token_headers

    from app.core.time import to_utc
    from app.modules.resources.models import ResourceBlock

    student = data["users"]["student"]
    with factory() as session, session.begin():
        session.add(
            ResourceBlock(
                room_id=data["room"],
                seat_id=data["seats"][0],
                source_module="M1",
                source_id=700,
                start_at=to_utc(data["start"]),
                end_at=to_utc(data["end"]),
                status="ACTIVE",
            )
        )
    params = {
        "identity_id": str(student.identity_id),
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
        "page": 1,
        "page_size": 1,
    }
    response = client.get("/api/v1/m1/seats", params=params, headers=token_headers(student))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["items"][0]["seat_id"] == str(data["seats"][1])
    assert "user_id" not in str(body)
    params["identity_id"] = str(data["users"]["other"].identity_id)
    assert (
        client.get("/api/v1/m1/seats", params=params, headers=token_headers(student)).status_code
        == 403
    )


def test_search_respects_scopes_opening_and_half_open_boundaries(client, data, factory):
    from datetime import timedelta

    from conftest import token_headers
    from sqlalchemy import select

    from app.core.time import to_utc
    from app.modules.auth.models import UserRole
    from app.modules.resources.models import OpeningException, ResourceBlock

    student = data["users"]["student"]
    start, end = to_utc(data["start"]), to_utc(data["end"])
    with factory() as session, session.begin():
        grant = session.scalar(select(UserRole).where(UserRole.user_id == student.user_id))
        grant.scope_type, grant.scope_value = "ROOM", str(data["room"])
        session.add(
            ResourceBlock(
                room_id=data["room"],
                seat_id=None,
                source_module="M2",
                source_id=701,
                start_at=end,
                end_at=end + timedelta(hours=1),
                status="ACTIVE",
            )
        )
    params = {
        "identity_id": student.identity_id,
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
        "has_fixed_socket": "true",
    }
    headers = token_headers(student)
    response = client.get("/api/v1/m1/seats", params=params, headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["seat_id"] == str(data["seats"][0])
    with factory() as session, session.begin():
        session.add(
            OpeningException(
                room_id=data["room"],
                kind="CLOSE",
                start_at=start + timedelta(minutes=20),
                end_at=start + timedelta(minutes=30),
            )
        )
    assert client.get("/api/v1/m1/seats", params=params, headers=headers).json()["total"] == 0


def test_search_denies_non_student_and_unselected_identity(client, data):
    from conftest import token_headers

    teacher = data["users"]["teacher"]
    params = {
        "identity_id": teacher.identity_id,
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    assert (
        client.get("/api/v1/m1/seats", params=params, headers=token_headers(teacher)).status_code
        == 403
    )


def test_booking_period_requires_same_day_window_and_future():
    from datetime import datetime

    import pytest

    from app.core.errors import DomainError
    from app.modules.m1.period import validate_booking_period

    now = datetime.fromisoformat("2026-11-11T00:00:00+00:00")

    def validate(start, end):
        return validate_booking_period(
            datetime.fromisoformat(start), datetime.fromisoformat(end), now=now
        )

    assert validate("2026-11-11T09:00:00+08:00", "2026-11-11T10:00:00+08:00")[0].tzinfo is None
    for start, end in [
        ("2026-11-12T09:00:00+08:00", "2026-11-12T10:00:00+08:00"),
        ("2026-11-11T07:00:00+08:00", "2026-11-11T09:00:00+08:00"),
        ("2026-11-11T21:00:00+08:00", "2026-11-11T23:00:00+08:00"),
        ("2026-11-11T09:30:00+08:00", "2026-11-11T10:30:00+08:00"),
        ("2026-11-11T09:00:00+08:00", "2026-11-11T14:00:00+08:00"),
        ("2026-11-11T09:00:00", "2026-11-11T10:00:00"),
    ]:
        with pytest.raises(DomainError):
            validate(start, end)


def test_room_wide_maintenance_and_released_sources(client, data, factory):
    from datetime import timedelta

    from conftest import token_headers

    from app.core.time import to_utc
    from app.modules.resources.models import ResourceBlock

    student = data["users"]["student"]
    start = to_utc(data["start"])
    params = {
        "identity_id": student.identity_id,
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    headers = token_headers(student)
    with factory() as session, session.begin():
        session.add(
            ResourceBlock(
                room_id=data["room"],
                seat_id=None,
                source_module="M3",
                source_id=702,
                start_at=start - timedelta(hours=1),
                end_at=None,
                status="ACTIVE",
            )
        )
    assert client.get("/api/v1/m1/seats", params=params, headers=headers).json()["total"] == 0
    with factory() as session, session.begin():
        from sqlalchemy import select

        block = session.scalar(select(ResourceBlock).where(ResourceBlock.source_id == 702))
        block.status = "RELEASED"
        block.released_at = start
    assert client.get("/api/v1/m1/seats", params=params, headers=headers).json()["total"] == 3


def test_policy_start_boundary_and_subsecond_before_now():
    from datetime import datetime, timedelta

    from app.core.errors import DomainError
    from app.modules.m1.period import validate_booking_period

    start = datetime.fromisoformat("2026-11-11T09:00:00+08:00")
    assert validate_booking_period(start, start + timedelta(hours=4), now=start)
    with pytest.raises(DomainError):
        validate_booking_period(start, start + timedelta(hours=1), now=start + timedelta(seconds=1))


def test_search_scope_and_revoked_permission_are_enforced(client, data, factory):
    from conftest import token_headers
    from sqlalchemy import delete, select

    from app.modules.auth.models import RolePermission, UserRole

    student = data["users"]["student"]
    headers = token_headers(student)
    params = {
        "identity_id": student.identity_id,
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    with factory() as session, session.begin():
        grant = session.scalar(select(UserRole).where(UserRole.user_id == student.user_id))
        grant.scope_type, grant.scope_value = "ROOM", str(data["private_room"])
    response = client.get("/api/v1/m1/seats", params=params, headers=headers)
    assert response.status_code == 200 and response.json()["total"] == 0
    with factory() as session, session.begin():
        session.execute(
            delete(RolePermission).where(
                RolePermission.role_code == "USER",
                RolePermission.permission_code == "m1:seat:search",
            )
        )
    assert client.get("/api/v1/m1/seats", params=params, headers=headers).status_code == 403


def test_search_pagination_fields_and_socket_combinations(client, data):
    from conftest import token_headers

    student = data["users"]["student"]
    headers = token_headers(student)
    params = {
        "identity_id": student.identity_id,
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
        "room_id": data["room"],
        "page_size": 1,
    }
    response = client.get("/api/v1/m1/seats", params={**params, "page": 2}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"total", "page", "page_size", "items"}
    assert body["total"] == 3 and body["items"][0]["seat_id"] == str(data["seats"][1])
    assert set(body["items"][0]) == {
        "seat_id",
        "room_id",
        "seat_no",
        "room_name",
        "building_name",
        "has_fixed_socket",
        "has_mobile_socket",
        "resource_version",
    }
    empty = client.get("/api/v1/m1/seats", params={**params, "page": 4}, headers=headers).json()
    assert empty["total"] == 3 and empty["items"] == []
    for fixed, mobile, expected in [
        (True, False, [data["seats"][0]]),
        (False, True, [data["seats"][1]]),
        (False, False, [data["seats"][2]]),
        (True, True, []),
    ]:
        result = client.get(
            "/api/v1/m1/seats",
            params={**params, "has_fixed_socket": fixed, "has_mobile_socket": mobile},
            headers=headers,
        ).json()
        assert result["total"] == len(expected)
        assert [item["seat_id"] for item in result["items"]] == [str(x) for x in expected]
    foreign = client.get(
        "/api/v1/m1/seats", params={**params, "room_id": data["private_room"]}, headers=headers
    )
    assert foreign.status_code == 200 and foreign.json()["total"] == 0
    assert foreign.json()["items"] == []


def test_overlapping_sources_only_release_their_own_restriction(client, data, factory):
    from conftest import token_headers
    from sqlalchemy import select

    from app.core.time import to_utc
    from app.modules.resources.models import ResourceBlock

    student = data["users"]["student"]
    params = {
        "identity_id": student.identity_id,
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    headers = token_headers(student)
    start, end = to_utc(data["start"]), to_utc(data["end"])
    with factory() as session, session.begin():
        session.add_all(
            [
                ResourceBlock(
                    room_id=data["room"],
                    seat_id=data["seats"][0],
                    source_module="M1",
                    source_id=800,
                    start_at=start,
                    end_at=end,
                    status="ACTIVE",
                ),
                ResourceBlock(
                    room_id=data["room"],
                    seat_id=None,
                    source_module="M2",
                    source_id=801,
                    start_at=start,
                    end_at=end,
                    status="ACTIVE",
                ),
                ResourceBlock(
                    room_id=data["room"],
                    seat_id=None,
                    source_module="M3",
                    source_id=802,
                    start_at=start,
                    end_at=None,
                    status="ACTIVE",
                ),
            ]
        )

    def seat_ids():
        response = client.get("/api/v1/m1/seats", params=params, headers=headers)
        assert response.status_code == 200
        return [item["seat_id"] for item in response.json()["items"]]

    assert seat_ids() == []
    for source_id, expected in [
        (802, []),
        (801, [str(x) for x in data["seats"][1:]]),
        (800, [str(x) for x in data["seats"]]),
    ]:
        with factory() as session, session.begin():
            block = session.scalar(
                select(ResourceBlock).where(ResourceBlock.source_id == source_id)
            )
            block.status, block.released_at = "RELEASED", start
        assert seat_ids() == expected


def test_search_filters_disabled_resources_and_inactive_identity(client, data, factory):
    from conftest import token_headers

    from app.modules.auth.models import UserIdentity
    from app.modules.resources.models import Room, Seat

    student = data["users"]["student"]
    headers = token_headers(student)
    params = {
        "identity_id": student.identity_id,
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    with factory() as session, session.begin():
        session.get(Seat, data["seats"][0]).is_active = False
    assert client.get("/api/v1/m1/seats", params=params, headers=headers).json()["total"] == 2
    with factory() as session, session.begin():
        session.get(Room, data["room"]).is_active = False
    assert client.get("/api/v1/m1/seats", params=params, headers=headers).json()["total"] == 0
    with factory() as session, session.begin():
        session.get(UserIdentity, student.identity_id).is_active = False
    assert client.get("/api/v1/m1/seats", params=params, headers=headers).status_code == 401


@pytest.mark.parametrize("permission", ["m1:seat:search", "m1:reservation:create"])
def test_search_requires_each_action_permission(client, data, factory, permission):
    from conftest import token_headers
    from sqlalchemy import delete

    from app.modules.auth.models import RolePermission

    student = data["users"]["student"]
    headers = token_headers(student)
    params = {
        "identity_id": student.identity_id,
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    with factory() as session, session.begin():
        session.execute(
            delete(RolePermission).where(
                RolePermission.role_code == "USER", RolePermission.permission_code == permission
            )
        )
    assert client.get("/api/v1/m1/seats", params=params, headers=headers).status_code == 403


def test_period_rejects_submillisecond_values():
    from datetime import datetime, timedelta

    from app.core.errors import DomainError
    from app.modules.m1.period import validate_booking_period

    start = datetime.fromisoformat("2026-11-11T09:00:00+08:00")
    with pytest.raises(DomainError):
        validate_booking_period(
            start + timedelta(microseconds=1), start + timedelta(hours=1), now=start
        )


def test_search_query_count_is_bounded_when_seats_grow(client, data, factory):
    from conftest import token_headers
    from sqlalchemy import event

    from app.core.database import get_session_factory
    from app.modules.resources.models import Seat

    student = data["users"]["student"]
    headers = token_headers(student)
    with factory() as session, session.begin():
        session.add_all(
            Seat(
                room_id=data["room"],
                seat_no=f"extra-{i}",
                is_active=True,
                has_fixed_socket=False,
                has_mobile_socket=False,
            )
            for i in range(100)
        )
    params = {
        "identity_id": student.identity_id,
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
        "page_size": 10,
    }
    statements = []

    def count(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    engine = get_session_factory().kw["bind"]
    event.listen(engine, "before_cursor_execute", count)
    try:
        response = client.get("/api/v1/m1/seats", params=params, headers=headers)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert response.status_code == 200 and response.json()["total"] == 103
    assert any("d_room_opening_rules" in statement for statement in statements)
    assert len(statements) <= 20, len(statements)


def test_search_retries_when_resource_version_changes_during_read(client, data, factory):
    from conftest import token_headers
    from sqlalchemy import event

    from app.core.database import get_session_factory
    from app.core.time import to_utc
    from app.modules.resources.models import ResourceBlock, Room

    student = data["users"]["student"]
    headers = token_headers(student)
    params = {
        "identity_id": student.identity_id,
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    changed = []

    def change_between_queries(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "d_room_opening_rules" not in statement or changed:
            return
        changed.append(True)
        with factory() as session, session.begin():
            room = session.get(Room, data["room"])
            room.resource_version += 1
            session.add(
                ResourceBlock(
                    room_id=room.id,
                    seat_id=data["seats"][0],
                    source_module="M1",
                    source_id=900,
                    start_at=to_utc(data["start"]),
                    end_at=to_utc(data["end"]),
                    status="ACTIVE",
                )
            )

    engine = get_session_factory().kw["bind"]
    event.listen(engine, "before_cursor_execute", change_between_queries)
    try:
        response = client.get("/api/v1/m1/seats", params=params, headers=headers)
    finally:
        event.remove(engine, "before_cursor_execute", change_between_queries)
    assert changed and response.status_code == 200
    result = response.json()
    assert result["total"] == 2
    assert all(item["resource_version"] == "1" for item in result["items"])


def test_search_returns_conflict_after_repeated_concurrent_changes(client, data, factory):
    from conftest import token_headers
    from sqlalchemy import event

    from app.core.database import get_session_factory
    from app.modules.resources.models import Room

    student = data["users"]["student"]
    headers = token_headers(student)
    params = {
        "identity_id": student.identity_id,
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    changes = []

    def modify(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "d_room_opening_rules" not in statement:
            return
        with factory() as session, session.begin():
            room = session.get(Room, data["room"])
            room.resource_version += 1
        changes.append(1)

    engine = get_session_factory().kw["bind"]
    event.listen(engine, "before_cursor_execute", modify)
    try:
        response = client.get("/api/v1/m1/seats", params=params, headers=headers)
    finally:
        event.remove(engine, "before_cursor_execute", modify)
    assert len(changes) == 3
    assert response.status_code == 409


def test_search_query_count_is_bounded_across_rooms(client, data, factory):
    from datetime import time

    from conftest import token_headers
    from sqlalchemy import event

    from app.core.database import get_session_factory
    from app.modules.resources.models import Room, Seat

    student = data["users"]["student"]
    headers = token_headers(student)
    with factory() as session, session.begin():
        for i in range(30):
            room = Room(
                code=f"test-room-{i}",
                name="Test",
                building_name="B",
                capacity=1,
                open_time=time(8),
                close_time=time(22),
                is_active=True,
            )
            session.add(room)
            session.flush()
            session.add(
                Seat(
                    room_id=room.id,
                    seat_no="1",
                    is_active=True,
                    has_fixed_socket=False,
                    has_mobile_socket=False,
                )
            )
    params = {
        "identity_id": student.identity_id,
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
        "page_size": 10,
    }
    statements = []

    def count(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    engine = get_session_factory().kw["bind"]
    event.listen(engine, "before_cursor_execute", count)
    try:
        response = client.get("/api/v1/m1/seats", params=params, headers=headers)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert response.status_code == 200 and response.json()["total"] == 33
    assert any("d_room_opening_rules" in statement for statement in statements)
    assert len(statements) <= 20, len(statements)


def test_period_rejects_start_one_microsecond_in_past():
    from datetime import datetime, timedelta

    from app.core.errors import DomainError
    from app.modules.m1.period import validate_booking_period

    start = datetime.fromisoformat("2026-11-11T09:00:00+08:00")
    with pytest.raises(DomainError):
        validate_booking_period(
            start, start + timedelta(hours=1), now=start + timedelta(microseconds=1)
        )


def test_search_excludes_m2_whole_room_even_if_it_has_seats(client, data, factory):
    from conftest import token_headers

    from app.modules.resources.models import Room

    with factory() as session, session.begin():
        session.get(Room, data["room"]).booking_mode = "WHOLE_ROOM"
    student = data["users"]["student"]
    response = client.get(
        "/api/v1/m1/seats", headers=token_headers(student),
        params={"identity_id": student.identity_id, "start_at": data["start"].isoformat(),
                "end_at": data["end"].isoformat()},
    )
    assert response.status_code == 200
    assert response.json()["items"] == []
