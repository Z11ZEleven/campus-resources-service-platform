from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event

import pytest
from conftest import token_headers
from sqlalchemy import func, select

from app.core.errors import DomainError
from app.core.time import to_utc
from app.modules.audit.models import OperationLog
from app.modules.resources.models import ResourceBlock, Room, SeatStatusSummary
from app.modules.resources.service import (
    availability,
    lock_room,
    occupy_room,
    release_source,
    reserve_seat,
    set_seat_active,
    start_maintenance,
)

pytestmark = pytest.mark.mysql


def reserve(session, data, source=1, seat=0, **kwargs):
    return reserve_seat(
        session,
        room_id=data["room"],
        seat_id=data["seats"][seat],
        reservation_id=source,
        start_at=data["start"],
        end_at=data["end"],
        actor=data["users"]["student"],
        **kwargs,
    )


def test_room_scope_and_socket_filter(client, data):
    headers = token_headers(data["users"]["student"])
    rooms = client.get("/api/v1/resources/rooms", headers=headers)
    assert rooms.status_code == 200
    assert [x["code"] for x in rooms.json()["items"]] == ["PUBLIC"]
    assert (
        client.get(f"/api/v1/resources/rooms/{data['private_room']}", headers=headers).status_code
        == 404
    )
    seats = client.get(
        f"/api/v1/resources/rooms/{data['room']}/seats?has_socket=true", headers=headers
    )
    assert seats.json()["total"] == 2
    assert isinstance(seats.json()["items"][0]["id"], str)


def test_reservation_idempotency_conflict_and_adjacent_period(factory, data):
    with factory() as s, s.begin():
        first = reserve(s, data)
        assert reserve(s, data).id == first.id
    with pytest.raises(DomainError) as result, factory() as s, s.begin():
        reserve(s, data, source=2)
    assert result.value.status == 409
    with factory() as s, s.begin():
        reserve(s, data, source=3, seat=1)
        reserve_seat(
            s,
            room_id=data["room"],
            seat_id=data["seats"][0],
            reservation_id=4,
            start_at=data["end"],
            end_at=data["end"] + timedelta(hours=1),
            actor=data["users"]["student"],
        )


def test_transaction_rolls_back_source_log_and_version(factory, data):
    with pytest.raises(RuntimeError), factory() as s, s.begin():
        reserve(s, data)
        raise RuntimeError("simulated module failure after D operation")
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(ResourceBlock)) == 0
        assert s.scalar(select(func.count()).select_from(OperationLog)) == 0
        assert s.get(Room, data["room"]).resource_version == 0


def test_m2_conflict_then_release_and_block_m1(factory, data):
    with factory() as s, s.begin():
        reserve(s, data)
    with pytest.raises(DomainError), factory() as s, s.begin():
        occupy_room(
            s,
            room_id=data["room"],
            application_id=1,
            applicant_id=data["users"]["teacher"].user_id,
            start_at=data["start"],
            end_at=data["end"],
            actor=data["users"]["admin"],
        )
    with factory() as s, s.begin():
        release_source(
            s, room_id=data["room"], source_module="M1", source_id=1, actor=data["users"]["student"]
        )
        occupy_room(
            s,
            room_id=data["room"],
            application_id=1,
            applicant_id=data["users"]["teacher"].user_id,
            start_at=data["start"],
            end_at=data["end"],
            actor=data["users"]["admin"],
        )
    with pytest.raises(DomainError), factory() as s, s.begin():
        reserve(s, data, source=2, seat=2)


def test_multiple_maintenance_sources_do_not_release_other_sources(factory, data):
    with factory() as s, s.begin():
        for source in (1, 2):
            start_maintenance(
                s, room_id=data["room"], work_order_id=source, actor=data["users"]["repairer"]
            )
        assert (
            start_maintenance(
                s, room_id=data["room"], work_order_id=1, actor=data["users"]["repairer"]
            ).source_id
            == 1
        )
    with factory() as s, s.begin():
        release_source(
            s,
            room_id=data["room"],
            source_module="M3",
            source_id=1,
            actor=data["users"]["property"],
        )
    with pytest.raises(DomainError), factory() as s, s.begin():
        reserve(s, data)
    with factory() as s, s.begin():
        release_source(
            s,
            room_id=data["room"],
            source_module="M3",
            source_id=2,
            actor=data["users"]["property"],
        )
        reserve(s, data)
    with pytest.raises(DomainError), factory() as s, s.begin():
        start_maintenance(s, room_id=data["room"], work_order_id=1, actor=data["users"]["repairer"])


def test_maintenance_over_m2_then_release_preserves_m2(factory, data):
    with factory() as s, s.begin():
        occupy_room(
            s,
            room_id=data["room"],
            application_id=1,
            applicant_id=data["users"]["teacher"].user_id,
            start_at=data["start"],
            end_at=data["end"],
            actor=data["users"]["admin"],
        )
        start_maintenance(s, room_id=data["room"], work_order_id=1, actor=data["users"]["repairer"])
        _, _, rows = availability(
            s, room_id=data["room"], day=data["day"], actor=data["users"]["student"]
        )
        target = next(r for r in rows if r.slot_start == to_utc(data["start"]))
        assert target.status == "MAINTENANCE" and target.source_count == 2
    with factory() as s, s.begin():
        release_source(
            s,
            room_id=data["room"],
            source_module="M3",
            source_id=1,
            actor=data["users"]["property"],
        )
        _, _, rows = availability(
            s, room_id=data["room"], day=data["day"], actor=data["users"]["student"]
        )
        target = next(r for r in rows if r.slot_start == to_utc(data["start"]))
        assert target.status == "ROOM_BOOKED" and target.source_count == 1


def test_summary_invalidates_and_disabled_priority(factory, data, client):
    headers = token_headers(data["users"]["student"])
    path = f"/api/v1/resources/rooms/{data['room']}/availability?date={data['day']}"
    first = client.get(path, headers=headers)
    assert first.status_code == 200, first.text
    assert len(first.json()["slots"]) == 72
    assert first.json()["slots"][0]["slot_start"].endswith("Z")
    with factory() as s, s.begin():
        reserve(s, data)
    second = client.get(path, headers=headers)
    target = next(
        x
        for x in second.json()["slots"]
        if (
            x["seat_id"] == str(data["seats"][0])
            and x["slot_start"] == to_utc(data["start"]).isoformat() + "Z"
        )
    )
    assert target["status"] == "RESERVED" and not target["bookable"]
    assert target["resource_version"] == 1
    # Maintenance changes cannot silently interrupt the active reservation.
    with pytest.raises(DomainError) as result, factory() as s, s.begin():
        set_seat_active(
            s,
            room_id=data["room"],
            seat_id=data["seats"][0],
            is_active=False,
            actor=data["users"]["admin"],
        )
    assert result.value.status == 409
    with factory() as s, s.begin():
        release_source(
            s, room_id=data["room"], source_module="M1", source_id=1, actor=data["users"]["student"]
        )
        set_seat_active(
            s,
            room_id=data["room"],
            seat_id=data["seats"][0],
            is_active=False,
            actor=data["users"]["admin"],
        )
    third = client.get(path, headers=headers)
    assert all(
        x["status"] == "DISABLED"
        for x in third.json()["slots"]
        if x["seat_id"] == str(data["seats"][0])
    )
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(SeatStatusSummary)) == 72


def test_period_scope_and_permission_validation(factory, data):
    cases = [
        (data["start"], data["end"] + timedelta(hours=4)),
        (data["start"] + timedelta(minutes=30), data["end"]),
        (data["start"] - timedelta(hours=2), data["end"]),
        (data["start"].replace(tzinfo=None), data["end"]),
    ]
    for start, end in cases:
        with pytest.raises((DomainError, ValueError)), factory() as s, s.begin():
            reserve_seat(
                s,
                room_id=data["room"],
                seat_id=data["seats"][0],
                reservation_id=10,
                start_at=start,
                end_at=end,
                actor=data["users"]["student"],
            )
    with pytest.raises(DomainError), factory() as s, s.begin():
        reserve_seat(
            s,
            room_id=data["private_room"],
            seat_id=data["private_seat"],
            reservation_id=11,
            start_at=data["start"],
            end_at=data["end"],
            actor=data["users"]["student"],
        )
    with pytest.raises(DomainError), factory() as s, s.begin():
        start_maintenance(s, room_id=data["room"], work_order_id=12, actor=data["users"]["student"])


def test_source_cannot_change_payload_and_release_is_idempotent(factory, data):
    with factory() as s, s.begin():
        reserve(s, data)
    with pytest.raises(DomainError), factory() as s, s.begin():
        reserve(s, data, seat=1)
    with factory() as s, s.begin():
        a = release_source(
            s, room_id=data["room"], source_module="M1", source_id=1, actor=data["users"]["student"]
        )
        b = release_source(
            s, room_id=data["room"], source_module="M1", source_id=1, actor=data["users"]["student"]
        )
        assert a.id == b.id and b.status == "RELEASED"
        assert s.get(Room, data["room"]).resource_version == 2


def test_database_rejects_seat_room_mismatch(factory, data):
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), factory() as s, s.begin():
        s.add(
            ResourceBlock(
                room_id=data["private_room"],
                seat_id=data["seats"][0],
                source_module="M1",
                source_id=22,
                start_at=to_utc(data["start"]),
                end_at=to_utc(data["end"]),
                status="ACTIVE",
            )
        )
        s.flush()


def test_two_concurrent_reservations_one_wins(factory, data):
    barrier = Barrier(2)

    def attempt(source):
        with factory() as s:
            try:
                with s.begin():
                    barrier.wait(timeout=5)
                    reserve(s, data, source=source)
                return "OK"
            except DomainError as exc:
                return exc.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (100, 101)))
    assert sorted(map(str, results)) == ["409", "OK"]
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(ResourceBlock)) == 1


def test_room_lock_actually_blocks_second_transaction(factory, data):
    # Removing with_for_update() makes this test fail; validates real MySQL locking.
    entered, finished = Event(), Event()

    def second():
        with factory() as s, s.begin():
            entered.set()
            lock_room(s, data["room"])
            finished.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with factory() as s, s.begin():
            lock_room(s, data["room"])
            future = pool.submit(second)
            assert entered.wait(timeout=5)
            assert not finished.wait(timeout=0.25)
        future.result(timeout=5)
    assert finished.is_set()
