from datetime import time, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from app.core.errors import DomainError
from app.core.time import to_utc
from app.modules.audit.models import OperationLog
from app.modules.auth.permissions import Grant, Principal
from app.modules.resources.models import OpeningRule, Room, Seat
from app.modules.resources.schemas import BuildingInput, RoomInput, ScheduleInput, SeatInput
from app.modules.resources.service import (
    replace_schedule,
    reserve_seat,
    save_building,
    save_room,
    save_seat,
)

pytestmark = pytest.mark.mysql


def scoped(data, permission="resources:manage"):
    old = data["users"]["admin"]
    return Principal(
        old.user_id,
        old.username,
        old.display_name,
        old.identity_type,
        old.department_code,
        old.roles,
        grants=(Grant("ROOM_ADMIN", frozenset({permission}), "ROOM", str(data["room"])),),
    )


def test_seat_soft_deactivate_retains_history_and_version(factory, data):
    with factory() as s, s.begin():
        old = s.get(Room, data["room"]).resource_version
        save_seat(
            s, scoped(data), data["room"], SeatInput(seat_no="1", is_active=False), data["seats"][0]
        )
        assert s.get(Seat, data["seats"][0]).is_active == 0
        assert s.get(Room, data["room"]).resource_version == old + 1
        assert s.scalar(select(func.count()).select_from(OperationLog)) == 1
    with pytest.raises(DomainError) as error, factory() as s, s.begin():
        save_seat(s, scoped(data), data["private_room"], SeatInput(seat_no="forbidden"))
    assert error.value.status == 403


def test_active_source_prevents_deactivation_and_opening_gap(factory, data):
    with factory() as s, s.begin():
        reserve_seat(
            s,
            room_id=data["room"],
            seat_id=data["seats"][0],
            reservation_id=71,
            start_at=data["start"],
            end_at=data["end"],
            actor=data["users"]["student"],
        )
    with pytest.raises(DomainError) as error, factory() as s, s.begin():
        save_seat(
            s, scoped(data), data["room"], SeatInput(seat_no="1", is_active=False), data["seats"][0]
        )
    assert error.value.status == 409
    with pytest.raises(DomainError) as error, factory() as s, s.begin():
        replace_schedule(s, scoped(data), data["room"], ScheduleInput(weekly=[]))
    assert error.value.status == 409
    with factory() as s, s.begin():
        replace_schedule(
            s,
            scoped(data),
            data["room"],
            ScheduleInput(
                weekly=[
                    {"weekday": data["day"].weekday(), "start_time": time(8), "end_time": time(22)}
                ]
            ),
        )
        assert s.get(Room, data["room"]).opening_rules_enabled


def test_seat_cannot_move_rooms_and_duplicate_number_rejected(factory, data):
    with pytest.raises(DomainError) as error, factory() as s, s.begin():
        save_seat(
            s,
            data["users"]["admin"],
            data["private_room"],
            SeatInput(seat_no="new"),
            data["seats"][0],
        )
    assert error.value.status == 404
    with pytest.raises(DomainError) as error, factory() as s, s.begin():
        save_seat(s, scoped(data), data["room"], SeatInput(seat_no="1"))
    assert error.value.status == 409


def test_building_room_creation_and_name_compatibility(factory, data):
    with factory() as s, s.begin():
        admin = data["users"]["admin"]
        building = save_building(s, admin, BuildingInput(code="NEW", name="新楼"))
        room = save_room(
            s, admin, RoomInput(code="NEW", name="新教室", building_id=building.id, capacity=10)
        )
        assert room.building_name == "新楼"
        version = room.resource_version
        save_building(s, admin, BuildingInput(code="NEW", name="更名"), building.id)
        assert room.building_name == "更名" and room.resource_version == version + 1
    with pytest.raises(DomainError) as error, factory() as s, s.begin():
        save_building(s, scoped(data), BuildingInput(code="DENY", name="越权"))
    assert error.value.status == 403


def test_full_opening_used_for_availability_and_booking(factory, data):
    from app.modules.resources.service import availability

    with factory() as s, s.begin():
        replace_schedule(
            s,
            scoped(data),
            data["room"],
            ScheduleInput(
                weekly=[
                    {"weekday": data["day"].weekday(), "start_time": time(8), "end_time": time(22)}
                ],
                exceptions=[
                    {
                        "kind": "CLOSE",
                        "start_at": data["start"] + timedelta(minutes=15),
                        "end_at": data["start"] + timedelta(minutes=30),
                    }
                ],
            ),
        )
        _, _, rows = availability(
            s, room_id=data["room"], day=data["day"], actor=data["users"]["student"]
        )
        assert all(r.status == "CLOSED" for r in rows if r.slot_start == to_utc(data["start"]))
    with pytest.raises(DomainError) as error, factory() as s, s.begin():
        reserve_seat(
            s,
            room_id=data["room"],
            seat_id=data["seats"][0],
            reservation_id=72,
            start_at=data["start"],
            end_at=data["end"],
            actor=data["users"]["student"],
        )
    assert error.value.status == 409


def test_database_rejects_invalid_weekday(factory, data):
    with pytest.raises(DBAPIError, match="ck_opening_weekday"), factory() as s, s.begin():
        s.add(OpeningRule(room_id=data["room"], weekday=7, start_time=time(8), end_time=time(9)))
        s.flush()


def test_scoped_lists_counts_details_and_writes_do_not_leak(data):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.modules.auth.dependencies import current_user

    app = create_app()
    app.dependency_overrides[current_user] = lambda: scoped(data)
    with TestClient(app) as scoped_client:
        response = scoped_client.get("/api/v1/resources/rooms?page_size=1")
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["id"] == str(data["room"])
        assert (
            scoped_client.get(f"/api/v1/resources/rooms/{data['private_room']}").status_code == 404
        )
        assert (
            scoped_client.get(f"/api/v1/resources/rooms/{data['private_room']}/seats").status_code
            == 404
        )
        assert (
            scoped_client.post(
                f"/api/v1/resources/rooms/{data['private_room']}/seats", json={"seat_no": "denied"}
            ).status_code
            == 403
        )


def test_schedule_rows_have_utc_millisecond_creation_timestamp(factory, data):
    from sqlalchemy import text

    from app.modules.resources.models import OpeningException

    with factory() as session, session.begin():
        session.execute(
            text(
                "INSERT INTO d_room_opening_rules (room_id,weekday,start_time,end_time) "
                'VALUES (:room_id,0,"08:00:00","09:00:00")'
            ),
            {"room_id": data["room"]},
        )
        rule = session.scalar(select(OpeningRule).where(OpeningRule.room_id == data["room"]))
        assert rule.created_at is not None
        assert rule.created_at.microsecond % 1000 == 0
        exception = OpeningException(
            room_id=data["room"],
            kind="OPEN",
            start_at=to_utc(data["start"]),
            end_at=to_utc(data["end"]),
        )
        session.add(exception)
        session.flush()
        assert exception.created_at is not None
        assert exception.created_at.microsecond % 1000 == 0
