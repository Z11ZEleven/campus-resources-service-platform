from datetime import timedelta

import pytest
from conftest import token_headers
from sqlalchemy import func, select

from app.core.time import to_utc
from app.modules.m1.models import Reservation, Violation
from app.modules.resources.models import Building, ResourceBlock, Room


@pytest.mark.parametrize(
    "open_time,close_time,affected_hour",
    [
        ("08:00:00", "20:00:00", 20),
        ("10:00:00", "22:00:00", 9),
    ],
)
def test_shorter_room_hours_preserve_unaffected_reservations(
    client, data, factory, monkeypatch, open_time, close_time, affected_hour
):
    start = to_utc(data["start"])
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow", lambda: start - timedelta(hours=1)
    )
    ids = []
    for index, hour in enumerate([11, affected_hour]):
        student = data["users"]["student" if index == 0 else "other"]
        begin = data["start"].replace(hour=hour)
        response = client.post(
            "/api/v1/m1/reservations",
            headers={**token_headers(student), "Idempotency-Key": f"hours-{index}"},
            json={
                "identity_id": student.identity_id,
                "seat_id": data["seats"][index],
                "start_at": begin.isoformat(),
                "end_at": (begin + timedelta(hours=1)).isoformat(),
            },
        )
        assert response.status_code == 201, response.text
        ids.append(int(response.json()["id"]))
    with factory() as session, session.begin():
        building = Building(code="HOURS", name="测试楼", is_active=True)
        session.add(building)
        session.flush()
        session.get(Room, data["room"]).building_id = building.id
    headers = token_headers(data["users"]["admin"])
    path = f"/api/v1/resources/rooms/{data['room']}"
    current = client.get(path, headers=headers).json()
    body = {
        key: current[key]
        for key in ("code", "name", "building_id", "capacity", "department_code", "is_active")
    }
    response = client.put(
        path, headers=headers, json={**body, "open_time": open_time, "close_time": close_time}
    )
    assert response.status_code == 200, response.text
    with factory() as session:
        assert session.get(Reservation, ids[0]).status == "RESERVED"
        assert session.get(Reservation, ids[1]).status == "CANCELLED"
        blocks = {
            row.source_id: row.status
            for row in session.scalars(
                select(ResourceBlock).where(ResourceBlock.source_module == "M1")
            )
        }
        assert blocks[ids[0]] == "ACTIVE"
        assert blocks[ids[1]] == "RELEASED"
        assert session.scalar(select(func.count()).select_from(Violation)) == 0


def test_maintenance_cancels_and_interrupts_without_violations(client, data, factory, monkeypatch):
    from app.core.config import get_settings

    start = to_utc(data["start"])
    monkeypatch.setenv("M1_CHECKIN_SECRET", "test-only-checkin-secret-at-least-32-characters")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow", lambda: start - timedelta(hours=1)
    )
    monkeypatch.setattr("app.modules.m1.checkin.precise_utcnow", lambda: start)
    student = data["users"]["student"]
    headers = token_headers(student)
    ids = []
    for seat in data["seats"][:2]:
        response = client.post(
            "/api/v1/m1/reservations",
            headers={**headers, "Idempotency-Key": f"maint-{seat}"},
            json={
                "identity_id": student.identity_id,
                "seat_id": seat,
                "start_at": data["start"].isoformat(),
                "end_at": data["end"].isoformat(),
            },
        )
        assert response.status_code == 201
        ids.append(int(response.json()["id"]))
    code = client.get(
        f"/api/v1/m1/admin/rooms/{data['room']}/check-in-code",
        headers=token_headers(data["users"]["admin"]),
    ).json()["code"]
    assert (
        client.post(
            f"/api/v1/m1/reservations/{ids[0]}/check-in", headers=headers, json={"code": code}
        ).status_code
        == 200
    )
    from app.modules.m1.resource_impact import apply_maintenance
    from app.modules.resources.service import lock_room

    with factory() as session, session.begin():
        room = lock_room(session, data["room"])
        assert (
            apply_maintenance(
                session, room=room, effective_at=start + timedelta(minutes=5), source_id=41
            )
            == 2
        )
        assert (
            apply_maintenance(
                session, room=room, effective_at=start + timedelta(minutes=5), source_id=41
            )
            == 0
        )
    with factory() as session:
        checked, reserved = [session.get(Reservation, rid) for rid in ids]
        assert checked.status == "INTERRUPTED"
        assert checked.actual_end_at == start + timedelta(minutes=5)
        assert reserved.status == "CANCELLED"
        assert session.scalar(select(func.count()).select_from(Violation)) == 0
        blocks = list(
            session.scalars(
                select(ResourceBlock).where(
                    ResourceBlock.source_module == "M1", ResourceBlock.source_id.in_(ids)
                )
            )
        )
        assert all(block.status == "RELEASED" for block in blocks)
    get_settings.cache_clear()


def test_disabling_seat_cancels_affected_reservation_without_touching_other_seats(
    client, data, factory, monkeypatch
):
    start = to_utc(data["start"])
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow", lambda: start - timedelta(hours=1)
    )
    student = data["users"]["student"]
    headers = token_headers(student)
    ids = []
    for seat in data["seats"][:2]:
        created = client.post(
            "/api/v1/m1/reservations",
            headers={**headers, "Idempotency-Key": f"seat-disable-{seat}"},
            json={
                "identity_id": student.identity_id,
                "seat_id": seat,
                "start_at": data["start"].isoformat(),
                "end_at": data["end"].isoformat(),
            },
        )
        assert created.status_code == 201
        ids.append(int(created.json()["id"]))
    path = f"/api/v1/resources/rooms/{data['room']}/seats/{data['seats'][0]}"
    body = {
        "seat_no": "1",
        "has_fixed_socket": True,
        "has_mobile_socket": False,
        "is_active": False,
    }
    assert client.put(path, json=body, headers=headers).status_code == 403
    result = client.put(path, json=body, headers=token_headers(data["users"]["admin"]))
    assert result.status_code == 200
    assert result.json()["affected_reservation_count"] == 1
    assert result.json()["affected_reservation_ids"] == [str(ids[0])]
    with factory() as session:
        assert session.get(Reservation, ids[0]).status == "CANCELLED"
        assert session.get(Reservation, ids[1]).status == "RESERVED"
        assert session.scalar(select(func.count()).select_from(Violation)) == 0


def test_disabling_room_cancels_reservations_without_creating_violations(
    client, data, factory, monkeypatch
):
    start = to_utc(data["start"])
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow", lambda: start - timedelta(hours=1)
    )
    student = data["users"]["student"]
    created = client.post(
        "/api/v1/m1/reservations",
        headers={**token_headers(student), "Idempotency-Key": "room-disable-impact"},
        json={
            "identity_id": student.identity_id,
            "seat_id": data["seats"][0],
            "start_at": data["start"].isoformat(),
            "end_at": data["end"].isoformat(),
        },
    )
    assert created.status_code == 201
    rid = int(created.json()["id"])
    with factory() as session, session.begin():
        building = Building(code="TEST-B1", name="测试楼", is_active=True)
        session.add(building)
        session.flush()
        room_row = session.get(Room, data["room"])
        room_row.building_id = building.id
    admin_headers = token_headers(data["users"]["admin"])
    room = client.get(f"/api/v1/resources/rooms/{data['room']}", headers=admin_headers)
    assert room.status_code == 200
    current = room.json()
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
        )
    }
    body["is_active"] = False
    updated = client.put(
        f"/api/v1/resources/rooms/{data['room']}", json=body, headers=admin_headers
    )
    assert updated.status_code == 200
    with factory() as session:
        assert session.get(Reservation, rid).status == "CANCELLED"
        assert session.scalar(select(func.count()).select_from(Violation)) == 0
