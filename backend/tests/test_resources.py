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


def test_source_revisions_and_terminal_tombstone(factory, data):
    actor = data["users"]["student"]
    with factory() as s, s.begin():
        first = reserve(s, data, source=701, revision=3)
        assert reserve(s, data, source=701, revision=3).id == first.id
        with pytest.raises(DomainError):
            reserve(s, data, source=701, revision=3, seat=1)
        with pytest.raises(DomainError):
            reserve(s, data, source=701, revision=2)
        assert reserve(s, data, source=701, revision=5).revision == 5
        release_source(
            s, room_id=data["room"], source_module="M1", source_id=701, actor=actor, revision=6
        )
        assert (
            release_source(
                s, room_id=data["room"], source_module="M1", source_id=701, actor=actor, revision=6
            ).revision
            == 6
        )
        with pytest.raises(DomainError):
            reserve(s, data, source=701, revision=7)
    with factory() as s, s.begin():
        tombstone = release_source(
            s, room_id=data["room"], source_module="M1", source_id=702, actor=actor, revision=4
        )
        assert tombstone.status == "RELEASED" and tombstone.revision == 4
        with pytest.raises(DomainError):
            reserve(s, data, source=702, revision=5)


def test_same_source_in_two_rooms_conflicts_without_poisoning_transaction(factory, data):
    barrier = Barrier(2)

    def create(room_id):
        with factory() as s, s.begin():
            barrier.wait()
            try:
                start_maintenance(
                    s,
                    room_id=room_id,
                    work_order_id=9821,
                    actor=data["users"]["repairer"],
                    revision=1,
                )
                return "created"
            except DomainError:
                # The failed insertion must leave the outer transaction usable.
                assert s.scalar(select(func.count()).select_from(Room)) > 0
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, [data["room"], data["private_room"]]))
    assert sorted(results) == ["conflict", "created"]


def test_personal_and_room_booking_race_one_wins(factory, data):
    barrier = Barrier(2)

    def attempt(kind):
        try:
            with factory() as s, s.begin():
                barrier.wait(timeout=5)
                if kind == "M1":
                    reserve(s, data, source=991)
                else:
                    occupy_room(
                        s,
                        room_id=data["room"],
                        application_id=992,
                        applicant_id=data["users"]["teacher"].user_id,
                        start_at=data["start"],
                        end_at=data["end"],
                        actor=data["users"]["admin"],
                    )
            return "OK"
        except DomainError as exc:
            return str(exc.status)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, ("M1", "M2"))) == ["409", "OK"]


def test_new_seat_respects_existing_room_source(factory, data):
    from app.modules.resources.schemas import SeatInput
    from app.modules.resources.service import save_seat

    with factory() as s, s.begin():
        start_maintenance(
            s, room_id=data["room"], work_order_id=993, actor=data["users"]["repairer"]
        )
        seat = save_seat(s, data["users"]["admin"], data["room"], SeatInput(seat_no="new"))
        seat_id = seat.id
    with pytest.raises(DomainError), factory() as s, s.begin():
        reserve_seat(
            s,
            room_id=data["room"],
            seat_id=seat_id,
            reservation_id=994,
            start_at=data["start"],
            end_at=data["end"],
            actor=data["users"]["student"],
        )


def test_m2_release_payload_and_unknown_tombstone(factory, data):
    release = dict(
        room_id=data["room"],
        source_module="M2",
        source_id=1200,
        actor=data["users"]["admin"],
        revision=3,
        start_at=data["start"],
        end_at=data["end"],
    )
    with factory() as s, s.begin():
        row = release_source(s, **release)
        assert (row.start_at, row.end_at) == (to_utc(data["start"]), to_utc(data["end"]))
        assert release_source(s, **release).id == row.id
        with pytest.raises(DomainError):
            release_source(s, **{**release, "end_at": data["end"] + timedelta(hours=1)})
        assert row.revision == 3


def test_revision_advances_update_room_version_and_audit(factory, data):
    with factory() as s, s.begin():
        row = reserve(s, data, source=1201, revision=1)
        room = s.get(Room, data["room"])
        version = room.resource_version
        audits = s.scalar(select(func.count()).select_from(OperationLog))
        reserve(s, data, source=1201, revision=3)
        s.flush()
        assert room.resource_version == version + 1
        assert s.scalar(select(func.count()).select_from(OperationLog)) == audits + 1
        release_source(
            s,
            room_id=data["room"],
            source_module="M1",
            source_id=1201,
            actor=data["users"]["student"],
            revision=4,
        )
        version = room.resource_version
        audits = s.scalar(select(func.count()).select_from(OperationLog))
        release_source(
            s,
            room_id=data["room"],
            source_module="M1",
            source_id=1201,
            actor=data["users"]["student"],
            revision=7,
        )
        s.flush()
        assert row.revision == 7 and room.resource_version == version + 1
        assert s.scalar(select(func.count()).select_from(OperationLog)) == audits + 1


def test_new_seat_respects_existing_m2_room_source(factory, data):
    from app.modules.resources.schemas import SeatInput
    from app.modules.resources.service import save_seat

    with factory() as s, s.begin():
        occupy_room(
            s,
            room_id=data["room"],
            application_id=1202,
            applicant_id=data["users"]["teacher"].user_id,
            start_at=data["start"],
            end_at=data["end"],
            actor=data["users"]["admin"],
        )
        seat_id = save_seat(s, data["users"]["admin"], data["room"], SeatInput(seat_no="new-m2")).id
    with pytest.raises(DomainError), factory() as s, s.begin():
        reserve_seat(
            s,
            room_id=data["room"],
            seat_id=seat_id,
            reservation_id=1203,
            start_at=data["start"],
            end_at=data["end"],
            actor=data["users"]["student"],
        )


def test_existing_m2_release_rejects_changed_period(factory, data):
    with factory() as s, s.begin():
        occupy_room(
            s,
            room_id=data["room"],
            application_id=1204,
            applicant_id=data["users"]["teacher"].user_id,
            start_at=data["start"],
            end_at=data["end"],
            actor=data["users"]["admin"],
            revision=1,
        )
        args = dict(
            room_id=data["room"],
            source_module="M2",
            source_id=1204,
            actor=data["users"]["admin"],
            revision=2,
            start_at=data["start"],
            end_at=data["end"],
        )
        row = release_source(s, **args)
        with pytest.raises(DomainError):
            release_source(s, **{**args, "end_at": data["end"] + timedelta(hours=1)})
        assert row.revision == 2


def test_release_rejects_inapplicable_or_missing_period(factory, data):
    with factory() as s, s.begin():
        for module, actor in (("M1", data["users"]["student"]), ("M3", data["users"]["property"])):
            with pytest.raises(DomainError) as err:
                release_source(
                    s,
                    room_id=data["room"],
                    source_module=module,
                    source_id=1301,
                    actor=actor,
                    revision=1,
                    start_at=data["start"],
                    end_at=data["end"],
                )
            assert err.value.status == 422
        with pytest.raises(DomainError) as err:
            release_source(
                s,
                room_id=data["room"],
                source_module="M2",
                source_id=1302,
                actor=data["users"]["admin"],
            )
        assert err.value.status == 422


def test_m2_tombstone_submillisecond_retry_across_transactions(factory, data):
    args = dict(
        room_id=data["room"],
        source_module="M2",
        source_id=1303,
        actor=data["users"]["admin"],
        revision=4,
        start_at=data["start"].replace(microsecond=123456),
        end_at=data["end"].replace(microsecond=654321),
    )
    with factory() as s, s.begin():
        row = release_source(s, **args)
        first_id = row.id
        assert row.start_at.microsecond == 123000
        assert row.end_at.microsecond == 654000
    with factory() as s, s.begin():
        assert release_source(s, **args).id == first_id


def test_versioned_source_rejects_legacy_requests_but_legacy_m3_works(factory, data):
    with factory() as s, s.begin():
        row = start_maintenance(
            s, room_id=data["room"], work_order_id=1401, actor=data["users"]["repairer"], revision=0
        )
        with pytest.raises(DomainError):
            release_source(
                s,
                room_id=data["room"],
                source_module="M3",
                source_id=1401,
                actor=data["users"]["property"],
            )
        with pytest.raises(DomainError):
            start_maintenance(
                s, room_id=data["room"], work_order_id=1401, actor=data["users"]["repairer"]
            )
        assert row.status == "ACTIVE"
        start_maintenance(
            s, room_id=data["room"], work_order_id=1402, actor=data["users"]["repairer"]
        )
        release_source(
            s,
            room_id=data["room"],
            source_module="M3",
            source_id=1402,
            actor=data["users"]["property"],
        )


def test_revision_outside_unsigned_bigint_is_rejected(factory, data):
    with factory() as s, s.begin():
        with pytest.raises(DomainError) as exc:
            start_maintenance(
                s,
                room_id=data["room"],
                work_order_id=1403,
                actor=data["users"]["repairer"],
                revision=2**64,
            )
        assert exc.value.status == 422


def test_unrelated_pending_integrity_error_is_not_reported_as_source_conflict(factory, data):
    from sqlalchemy.exc import IntegrityError

    from app.modules.resources.models import Room

    with factory() as s:
        with pytest.raises(IntegrityError):
            with s.begin():
                room = s.get(Room, data["room"])
                s.add(
                    Room(
                        code=room.code,
                        name="duplicate",
                        building_name="duplicate",
                        capacity=1,
                        open_time=room.open_time,
                        close_time=room.close_time,
                    )
                )
                with s.no_autoflush:
                    start_maintenance(
                        s, room_id=data["room"], work_order_id=1404, actor=data["users"]["repairer"]
                    )


def test_legacy_release_at_maximum_revision_rejects_overflow(factory, data):
    with factory() as s, s.begin():
        row = start_maintenance(
            s, room_id=data["room"], work_order_id=1405, actor=data["users"]["repairer"]
        )
        row.revision = 2**64 - 1
        s.flush()
        with pytest.raises(DomainError) as exc:
            release_source(
                s,
                room_id=data["room"],
                source_module="M3",
                source_id=1405,
                actor=data["users"]["property"],
            )
        assert exc.value.status == 409 and row.status == "ACTIVE"
