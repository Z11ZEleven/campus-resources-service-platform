"""Real database regressions for delivery gaps found during acceptance."""

from datetime import timedelta

import pytest
from conftest import token_headers
from sqlalchemy import func, select
from test_m3 import advance

from app.core.time import to_utc
from app.modules.m1.models import Reservation, Violation


def booking(client, data, monkeypatch):
    start = to_utc(data["start"])
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow", lambda: start - timedelta(hours=1)
    )
    student = data["users"]["student"]
    result = client.post(
        "/api/v1/m1/reservations",
        headers={**token_headers(student), "Idempotency-Key": "delivery-booking"},
        json={
            "identity_id": str(student.identity_id),
            "seat_id": str(data["seats"][0]),
            "start_at": data["start"].isoformat(),
            "end_at": data["end"].isoformat(),
        },
    )
    assert result.status_code == 201, result.text
    return int(result.json()["id"])


def test_real_m3_start_ends_existing_reservation(client, data, factory, monkeypatch):
    rid = booking(client, data, monkeypatch)
    advance(client, data)
    with factory() as session:
        assert session.get(Reservation, rid).status == "CANCELLED"
        assert session.scalar(select(func.count()).select_from(Violation)) == 0


def test_closing_schedule_ends_existing_reservation(client, data, factory, monkeypatch):
    rid = booking(client, data, monkeypatch)
    result = client.put(
        f"/api/v1/resources/rooms/{data['room']}/opening",
        headers=token_headers(data["users"]["admin"]),
        json={"weekly": [], "exceptions": []},
    )
    assert result.status_code == 200, result.text
    with factory() as session:
        assert session.get(Reservation, rid).status == "CANCELLED"
        assert session.scalar(select(func.count()).select_from(Violation)) == 0


def test_operation_keys_are_isolated_by_service(client, data, factory):
    from app.modules.auth.models import ServicePrincipal, ServiceRoomScope
    from app.modules.auth.sessions import credential_hash

    tokens = ["delivery-service-one", "delivery-service-two"]
    with factory() as session, session.begin():
        for token in tokens:
            principal = ServicePrincipal(name=token, module="M3", token_hash=credential_hash(token))
            session.add(principal)
            session.flush()
            session.add(ServiceRoomScope(service_id=principal.id, room_id=data["room"]))
    for index, token in enumerate(tokens):
        response = client.put(
            f"/api/v1/internal/resource-holds/M3/{800 + index}",
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "same-key"},
            json={"room_id": str(data["room"]), "active": True, "revision": 1, "reason": "维修"},
        )
        assert response.status_code == 200, response.text
        result = client.get(
            "/api/v1/internal/resource-operations/same-key",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert result.json()["source_id"] == str(800 + index)


@pytest.mark.parametrize(
    ("minutes", "kind", "notifications", "status"),
    [
        (-16, "before_start", 0, "RESERVED"),
        (-15, "before_start", 1, "RESERVED"),
        (0, "before_start", 0, "RESERVED"),
        (9, "after_start_10", 0, "RESERVED"),
        (10, "after_start_10", 1, "RESERVED"),
        (15, "after_start_10", 0, "RESERVED"),
        (14, "after_start_15", 0, "RESERVED"),
        (15, "after_start_15", 0, "NO_SHOW"),
        (180, "after_start_15", 0, "NO_SHOW"),
    ],
)
def test_timer_boundaries(client, data, factory, monkeypatch, minutes, kind, notifications, status):
    from app.modules.m1.timers import handle_timer
    from app.modules.notifications.models import Notification

    rid = booking(client, data, monkeypatch)
    now = to_utc(data["start"]) + timedelta(minutes=minutes)
    monkeypatch.setattr("app.modules.m1.period.precise_utcnow", lambda: now)
    for _ in range(2):
        with factory() as session, session.begin():
            handle_timer(session, {"reservation_id": rid, "kind": kind})
    with factory() as session:
        assert session.get(Reservation, rid).status == status
        assert session.scalar(select(func.count()).select_from(Notification)) == notifications
        assert session.scalar(select(func.count()).select_from(Violation)) == (status == "NO_SHOW")


def test_concurrent_expiry_records_one_violation(client, data, factory, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.modules.m1.timers import handle_timer

    rid = booking(client, data, monkeypatch)
    now = to_utc(data["start"]) + timedelta(minutes=15)
    monkeypatch.setattr("app.modules.m1.period.precise_utcnow", lambda: now)
    barrier = Barrier(2)

    def expire():
        with factory() as session, session.begin():
            barrier.wait(timeout=5)
            handle_timer(session, {"reservation_id": rid, "kind": "after_start_15"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: expire(), range(2)))
    with factory() as session:
        assert session.get(Reservation, rid).status == "NO_SHOW"
        assert session.scalar(select(func.count()).select_from(Violation)) == 1


def test_department_policy_change_preserves_existing_booking(client, data, factory, monkeypatch):
    from app.core.config import get_settings
    from app.modules.resources.models import Building, Room

    rid = booking(client, data, monkeypatch)
    with factory() as session, session.begin():
        building = Building(code="DELIVERY", name="测试楼")
        session.add(building)
        session.flush()
        session.get(Room, data["room"]).building_id = building.id
    headers = token_headers(data["users"]["admin"])
    current = client.get(f"/api/v1/resources/rooms/{data['room']}", headers=headers).json()
    body = {
        key: current[key]
        for key in (
            "code",
            "name",
            "building_id",
            "capacity",
            "department_code",
            "open_time",
            "close_time",
            "is_active",
        )
    }
    result = client.put(
        f"/api/v1/resources/rooms/{data['room']}",
        headers=headers,
        json={**body, "department_code": "EE"},
    )
    assert result.status_code == 200, result.text
    monkeypatch.setenv("M1_CHECKIN_SECRET", "test-only-delivery-secret-at-least-32-characters")
    get_settings.cache_clear()
    monkeypatch.setattr("app.modules.m1.checkin.precise_utcnow", lambda: to_utc(data["start"]))
    code = client.get(
        f"/api/v1/m1/admin/rooms/{data['room']}/check-in-code", headers=headers
    ).json()["code"]
    result = client.post(
        f"/api/v1/m1/reservations/{rid}/check-in",
        headers=token_headers(data["users"]["student"]),
        json={"code": code},
    )
    assert result.status_code == 200, result.text
    get_settings.cache_clear()


def test_shortening_legacy_hours_ends_affected_booking(client, data, factory, monkeypatch):
    from app.modules.resources.models import Building, Room

    rid = booking(client, data, monkeypatch)
    with factory() as session, session.begin():
        building = Building(code="HOURS", name="测试楼")
        session.add(building)
        session.flush()
        session.get(Room, data["room"]).building_id = building.id
    headers = token_headers(data["users"]["admin"])
    current = client.get(f"/api/v1/resources/rooms/{data['room']}", headers=headers).json()
    body = {
        key: current[key]
        for key in (
            "code",
            "name",
            "building_id",
            "capacity",
            "department_code",
            "open_time",
            "close_time",
            "is_active",
        )
    }
    result = client.put(
        f"/api/v1/resources/rooms/{data['room']}",
        headers=headers,
        json={**body, "close_time": "09:00:00"},
    )
    assert result.status_code == 200, result.text
    with factory() as session:
        assert session.get(Reservation, rid).status == "CANCELLED"


def test_schedule_conflict_rolls_back_m1_impact(client, data, factory, monkeypatch):
    from app.modules.resources.service import occupy_room

    rid = booking(client, data, monkeypatch)
    with factory() as session, session.begin():
        occupy_room(
            session,
            room_id=data["room"],
            application_id=800,
            applicant_id=data["users"]["teacher"].user_id,
            start_at=data["start"] + timedelta(hours=2),
            end_at=data["end"] + timedelta(hours=2),
            actor=data["users"]["admin"],
        )
    result = client.put(
        f"/api/v1/resources/rooms/{data['room']}/opening",
        headers=token_headers(data["users"]["admin"]),
        json={"weekly": []},
    )
    assert result.status_code == 409
    with factory() as session:
        assert session.get(Reservation, rid).status == "RESERVED"


def test_reminder_delivery_failure_does_not_block_expiry(client, data, factory, monkeypatch):
    from app.modules.notifications.service import publish_notifications
    from app.worker import run_once

    rid = booking(client, data, monkeypatch)
    start = to_utc(data["start"])
    monkeypatch.setattr(
        "app.modules.m1.period.precise_utcnow", lambda: start - timedelta(minutes=15)
    )

    def unavailable(*args, **kwargs):
        raise ConnectionError("test-only notification outage")

    monkeypatch.setattr("app.modules.m1.timers.publish_notifications", unavailable)
    run_once(factory, worker_id="delivery-outage", now=start - timedelta(minutes=15))
    monkeypatch.setattr("app.modules.m1.timers.publish_notifications", publish_notifications)
    monkeypatch.setattr(
        "app.modules.m1.period.precise_utcnow", lambda: start + timedelta(minutes=16)
    )
    for _ in range(8):
        run_once(factory, worker_id="delivery-recovery", now=start + timedelta(minutes=16))
    with factory() as session:
        assert session.get(Reservation, rid).status == "NO_SHOW"
        assert session.scalar(select(func.count()).select_from(Violation)) == 1


def test_twenty_competing_bookings_have_one_winner(client, data, factory, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.modules.resources.models import ResourceBlock

    start = to_utc(data["start"])
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow", lambda: start - timedelta(hours=1)
    )
    student = data["users"]["student"]
    headers = token_headers(student)
    barrier = Barrier(20)

    def submit(index):
        barrier.wait(timeout=10)
        return client.post(
            "/api/v1/m1/reservations",
            headers={**headers, "Idempotency-Key": f"race-{index}"},
            json={
                "identity_id": str(student.identity_id),
                "seat_id": str(data["seats"][0]),
                "start_at": data["start"].isoformat(),
                "end_at": data["end"].isoformat(),
            },
        ).status_code

    with ThreadPoolExecutor(max_workers=20) as pool:
        statuses = list(pool.map(submit, range(20)))
    assert statuses.count(201) == 1
    assert statuses.count(409) == 19
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Reservation)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(ResourceBlock)
                .where(ResourceBlock.source_module == "M1", ResourceBlock.status == "ACTIVE")
            )
            == 1
        )


def test_building_and_room_updates_use_compatible_lock_order(factory, data):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import event

    from app.modules.resources.models import Building, Room
    from app.modules.resources.schemas import BuildingInput, RoomInput
    from app.modules.resources.service import lock_room, save_building, save_room

    actor = data["users"]["admin"]
    with factory() as session, session.begin():
        building = Building(code="LOCKS", name="测试楼")
        session.add(building)
        session.flush()
        bid = building.id
        session.get(Room, data["room"]).building_id = bid
    attempted_room_lock = Event()
    engine = factory.kw["bind"]

    def observe(conn, cursor, statement, parameters, context, executemany):
        if "d_rooms.building_id =" in statement and "FOR UPDATE" in statement:
            attempted_room_lock.set()

    def rename():
        with factory() as session, session.begin():
            save_building(session, actor, BuildingInput(code="LOCKS", name="更名"), bid)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with factory() as session, session.begin():
                room = lock_room(session, data["room"])
                task = pool.submit(rename)
                assert attempted_room_lock.wait(timeout=5)
                save_room(
                    session,
                    actor,
                    RoomInput(
                        code=room.code,
                        name="更新教室",
                        building_id=bid,
                        capacity=room.capacity,
                        open_time=room.open_time,
                        close_time=room.close_time,
                    ),
                    room.id,
                )
            task.result(timeout=10)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    with factory() as session:
        assert session.get(Room, data["room"]).building_name == "更名"
        assert session.get(Room, data["room"]).name == "更新教室"


def test_checkin_and_expiry_race_has_one_business_outcome(client, data, factory, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.core.config import get_settings
    from app.modules.m1.timers import handle_timer

    rid = booking(client, data, monkeypatch)
    start = to_utc(data["start"])
    monkeypatch.setenv("M1_CHECKIN_SECRET", "test-only-race-secret-at-least-32-characters")
    get_settings.cache_clear()
    monkeypatch.setattr("app.modules.m1.checkin.precise_utcnow", lambda: start)
    code = client.get(
        f"/api/v1/m1/admin/rooms/{data['room']}/check-in-code",
        headers=token_headers(data["users"]["admin"]),
    ).json()["code"]
    # The two operations straddle the cutoff. The winner of the room lock
    # determines whether check-in commits or the reservation becomes NO_SHOW.
    monkeypatch.setattr(
        "app.modules.m1.checkin.precise_utcnow",
        lambda: start + timedelta(minutes=15, microseconds=-1000),
    )
    monkeypatch.setattr(
        "app.modules.m1.period.precise_utcnow", lambda: start + timedelta(minutes=15)
    )
    headers = token_headers(data["users"]["student"])
    barrier = Barrier(2)

    def checkin():
        barrier.wait(timeout=5)
        return client.post(
            f"/api/v1/m1/reservations/{rid}/check-in", headers=headers, json={"code": code}
        ).status_code

    def expiry():
        barrier.wait(timeout=5)
        with factory() as session, session.begin():
            handle_timer(session, {"reservation_id": rid, "kind": "after_start_15"})

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempt = pool.submit(checkin)
            timeout = pool.submit(expiry)
            status = attempt.result(timeout=10)
            timeout.result(timeout=10)
        with factory() as session:
            row = session.get(Reservation, rid)
            violations = session.scalar(select(func.count()).select_from(Violation))
            assert (row.status, status, violations) in [("CHECKED_IN", 200, 0), ("NO_SHOW", 409, 1)]
    finally:
        get_settings.cache_clear()


def test_m3_start_and_checkin_race_is_exempt(client, data, factory, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.core.config import get_settings

    rid = booking(client, data, monkeypatch)
    order = advance(client, data, status="ASSIGNED")
    monkeypatch.setenv(
        "M1_CHECKIN_SECRET", "test-only-maintenance-race-secret-at-least-32-characters"
    )
    get_settings.cache_clear()
    monkeypatch.setattr("app.modules.m1.checkin.precise_utcnow", lambda: to_utc(data["start"]))
    code = client.get(
        f"/api/v1/m1/admin/rooms/{data['room']}/check-in-code",
        headers=token_headers(data["users"]["admin"]),
    ).json()["code"]
    student = token_headers(data["users"]["student"])
    repairer = token_headers(data["users"]["repairer"])
    barrier = Barrier(2)

    def checkin():
        barrier.wait(timeout=5)
        return client.post(
            f"/api/v1/m1/reservations/{rid}/check-in", headers=student, json={"code": code}
        ).status_code

    def maintenance():
        barrier.wait(timeout=5)
        return client.post(
            f"/api/v1/m3/work-orders/{order['id']}/start", headers=repairer
        ).status_code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(checkin), pool.submit(maintenance)
            checkin_status, maintenance_status = a.result(timeout=10), b.result(timeout=10)
        assert maintenance_status == 200
        with factory() as session:
            row = session.get(Reservation, rid)
            assert (row.status, checkin_status) in [("CANCELLED", 409), ("INTERRUPTED", 200)]
            assert session.scalar(select(func.count()).select_from(Violation)) == 0
    finally:
        get_settings.cache_clear()
