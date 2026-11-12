from datetime import timedelta

import pytest
from conftest import token_headers
from sqlalchemy import func, select

from app.core.time import to_utc
from app.modules.m1.models import Reservation
from app.modules.outbox.models import OutboxEvent
from app.modules.resources.models import ResourceBlock


@pytest.fixture(autouse=True)
def clock(data, monkeypatch):
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow",
        lambda: to_utc(data["start"]) - timedelta(hours=1),
    )


def test_create_replay_cancel_and_ownership(client, data, factory):
    student, other = data["users"]["student"], data["users"]["other"]
    headers = {**token_headers(student), "Idempotency-Key": "test-key"}
    payload = {
        "identity_id": student.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    first = client.post("/api/v1/m1/reservations", json=payload, headers=headers)
    assert first.status_code == 201, first.text
    replay = client.post("/api/v1/m1/reservations", json=payload, headers=headers)
    assert replay.status_code == 201 and replay.json() == first.json()
    assert (
        client.post(
            "/api/v1/m1/reservations",
            json={**payload, "seat_id": data["seats"][1]},
            headers=headers,
        ).status_code
        == 409
    )
    reservation_id = first.json()["id"]
    assert (
        client.get(
            f"/api/v1/m1/reservations/{reservation_id}", headers=token_headers(other)
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/m1/reservations/{reservation_id}/cancel", headers=token_headers(other)
        ).status_code
        == 404
    )
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Reservation)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.handler == "m1_timer")
            )
            == 4
        )
    assert (
        client.post(f"/api/v1/m1/reservations/{reservation_id}/cancel", headers=headers).status_code
        == 200
    )
    assert (
        client.post(f"/api/v1/m1/reservations/{reservation_id}/cancel", headers=headers).status_code
        == 200
    )
    with factory() as session:
        assert (
            session.scalar(select(ResourceBlock).where(ResourceBlock.source_module == "M1")).status
            == "RELEASED"
        )


def test_conflict_rolls_back_and_history_seat_cannot_be_reused(client, data, factory):
    from app.modules.resources.models import Seat

    actor = data["users"]["student"]
    payload = {
        "identity_id": actor.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }

    def headers(key):
        return {**token_headers(actor), "Idempotency-Key": key}

    first = client.post("/api/v1/m1/reservations", json=payload, headers=headers("one"))
    assert first.status_code == 201
    assert (
        client.post("/api/v1/m1/reservations", json=payload, headers=headers("two")).status_code
        == 409
    )
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Reservation)) == 1
        assert session.scalar(select(func.count()).select_from(ResourceBlock)) == 1
    assert (
        client.post(
            f"/api/v1/m1/reservations/{first.json()['id']}/cancel", headers=headers("x")
        ).status_code
        == 200
    )
    with factory() as session, session.begin():
        session.get(Seat, data["seats"][0]).is_active = False
    assert (
        client.post("/api/v1/m1/reservations", json=payload, headers=headers("three")).status_code
        == 409
    )
    history = client.get("/api/v1/m1/reservations", headers=token_headers(actor)).json()
    assert history["total"] == 1 and history["items"][0]["status"] == "CANCELLED"
    assert history["items"][0]["resource_snapshot"]["seat_no"]


def test_timer_worker_processes_registered_tasks(client, data, factory, monkeypatch):
    from app.worker import run_once

    actor = data["users"]["student"]
    payload = {
        "identity_id": actor.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    assert (
        client.post(
            "/api/v1/m1/reservations",
            json=payload,
            headers={**token_headers(actor), "Idempotency-Key": "worker"},
        ).status_code
        == 201
    )
    now = to_utc(data["end"]) + timedelta(hours=1)
    monkeypatch.setattr("app.modules.m1.period.precise_utcnow", lambda: now)
    run_once(factory, worker_id="test", now=now)
    with factory() as session:
        timers = list(session.scalars(select(OutboxEvent).where(OutboxEvent.handler == "m1_timer")))
        assert len(timers) == 4
        assert all(
            t.status == "SUCCEEDED" and t.max_attempts is None and t.attempts == 1 for t in timers
        )


def test_concurrent_same_key_different_rooms(client, data, factory):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import time
    from threading import Barrier

    from app.modules.resources.models import Room, Seat

    actor = data["users"]["student"]
    with factory() as session, session.begin():
        room = Room(
            code="parallel-room",
            name="Test",
            building_name="B",
            capacity=1,
            open_time=time(8),
            close_time=time(22),
            is_active=True,
        )
        session.add(room)
        session.flush()
        seat = Seat(
            room_id=room.id,
            seat_no="1",
            is_active=True,
            has_fixed_socket=False,
            has_mobile_socket=False,
        )
        session.add(seat)
        session.flush()
        second_seat = seat.id
    barrier = Barrier(2)

    def create(seat_id):
        payload = {
            "identity_id": actor.identity_id,
            "seat_id": seat_id,
            "start_at": data["start"].isoformat(),
            "end_at": data["end"].isoformat(),
        }
        barrier.wait()
        return client.post(
            "/api/v1/m1/reservations",
            json=payload,
            headers={**token_headers(actor), "Idempotency-Key": "parallel"},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(create, [data["seats"][0], second_seat]))
    assert sorted(result) == [201, 409]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Reservation)) == 1
        assert session.scalar(select(func.count()).select_from(ResourceBlock)) == 1


def test_cancel_preserves_other_sources_and_filters_history(client, data, factory):
    actor = data["users"]["student"]
    headers = {**token_headers(actor), "Idempotency-Key": "overlap"}
    payload = {
        "identity_id": actor.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    first = client.post("/api/v1/m1/reservations", json=payload, headers=headers)
    assert first.status_code == 201
    with factory() as session, session.begin():
        session.add_all(
            [
                ResourceBlock(
                    room_id=data["room"],
                    seat_id=None,
                    source_module=module,
                    source_id=source_id,
                    start_at=to_utc(data["start"]),
                    end_at=to_utc(data["end"]) if module == "M2" else None,
                    status="ACTIVE",
                )
                for module, source_id in [("M2", 123), ("M3", 124)]
            ]
        )
    assert (
        client.post(
            f"/api/v1/m1/reservations/{first.json()['id']}/cancel", headers=headers
        ).status_code
        == 200
    )
    with factory() as session:
        statuses = dict(
            session.execute(select(ResourceBlock.source_module, ResourceBlock.status)).all()
        )
        assert statuses == {"M1": "RELEASED", "M2": "ACTIVE", "M3": "ACTIVE"}
    params = {"status": "CANCELLED", "day": data["start"].date().isoformat(), "page_size": 1}
    assert (
        client.get("/api/v1/m1/reservations", params=params, headers=headers).json()["total"] == 1
    )
    assert (
        client.get(
            "/api/v1/m1/reservations", params={**params, "status": "RESERVED"}, headers=headers
        ).json()["total"]
        == 0
    )


def test_create_requires_selected_student_and_scoped_permission(client, data, factory):
    from sqlalchemy import delete

    from app.modules.auth.models import RolePermission

    actor = data["users"]["student"]
    payload = {
        "identity_id": actor.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    headers = {**token_headers(actor), "Idempotency-Key": "auth"}
    assert (
        client.post(
            "/api/v1/m1/reservations",
            json={**payload, "identity_id": data["users"]["other"].identity_id},
            headers=headers,
        ).status_code
        == 403
    )
    assert client.post(
        "/api/v1/m1/reservations",
        json={**payload, "seat_id": data["private_seat"]},
        headers=headers,
    ).status_code in (403, 404)
    with factory() as session, session.begin():
        session.execute(
            delete(RolePermission).where(
                RolePermission.role_code == "USER",
                RolePermission.permission_code == "m1:reservation:create",
            )
        )
    assert client.post("/api/v1/m1/reservations", json=payload, headers=headers).status_code == 403
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Reservation)) == 0


@pytest.mark.parametrize("change", ["permission", "identity", "department", "scope"])
def test_replay_requires_current_identity_and_authorization(client, data, factory, change):
    from sqlalchemy import delete

    from app.modules.auth.models import AuthSession, RolePermission, UserIdentity, UserRole
    from app.modules.auth.sessions import credential_hash
    from app.modules.resources.models import Room

    actor = data["users"]["student"]
    headers = {**token_headers(actor), "Idempotency-Key": "replay-auth"}
    payload = {
        "identity_id": actor.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    assert client.post("/api/v1/m1/reservations", json=payload, headers=headers).status_code == 201
    with factory() as session, session.begin():
        if change == "permission":
            session.execute(
                delete(RolePermission).where(
                    RolePermission.role_code == "USER",
                    RolePermission.permission_code == "m1:reservation:create",
                )
            )
        elif change == "identity":
            identity = UserIdentity(
                user_id=actor.user_id,
                identity_type="STUDENT",
                identity_no="new-student",
                department_code="CS",
                is_active=True,
            )
            session.add(identity)
            session.flush()
            auth = session.scalar(
                select(AuthSession).where(
                    AuthSession.token_hash == credential_hash(headers["Authorization"].split()[1])
                )
            )
            auth.identity_id = identity.id
        elif change == "department":
            session.get(Room, data["room"]).department_code = "EE"
        else:
            grant = session.scalar(select(UserRole).where(UserRole.user_id == actor.user_id))
            grant.scope_type, grant.scope_value = "ROOM", str(data["private_room"])
    assert client.post("/api/v1/m1/reservations", json=payload, headers=headers).status_code == 403


def test_failure_after_partial_timer_registration_rolls_back_everything(
    client, data, factory, monkeypatch
):
    from app.modules.m1 import reservations
    from app.modules.m1.models import ReservationEvent
    from app.modules.resources.allocation_models import SeatAllocation

    actor = data["users"]["student"]
    headers = {**token_headers(actor), "Idempotency-Key": "rollback-injection"}
    payload = {
        "identity_id": actor.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    real_register = reservations.register_event
    calls = []

    def fail_after_timer(*args, **kwargs):
        result = real_register(*args, **kwargs)
        calls.append(result.id)
        if len(calls) == 2:
            raise RuntimeError("injected failure")
        return result

    monkeypatch.setattr(reservations, "register_event", fail_after_timer)
    assert client.post("/api/v1/m1/reservations", json=payload, headers=headers).status_code == 500
    assert len(calls) == 2
    with factory() as session:
        for model in (Reservation, ReservationEvent, ResourceBlock, SeatAllocation, OutboxEvent):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_concurrent_same_key_same_payload_replays(client, data, factory):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    actor = data["users"]["student"]
    headers = {**token_headers(actor), "Idempotency-Key": "same-concurrent"}
    payload = {
        "identity_id": actor.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    barrier = Barrier(2)

    def submit(_):
        barrier.wait()
        return client.post("/api/v1/m1/reservations", json=payload, headers=headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))
    assert [r.status_code for r in responses] == [201, 201]
    assert responses[0].json() == responses[1].json()
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Reservation)) == 1
        assert session.scalar(select(func.count()).select_from(ResourceBlock)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.handler == "m1_timer")
            )
            == 4
        )


def test_replay_does_not_recheck_time_or_resource_availability(client, data, factory, monkeypatch):
    from app.modules.resources.models import Room, Seat

    actor = data["users"]["student"]
    headers = {**token_headers(actor), "Idempotency-Key": "late-replay"}
    payload = {
        "identity_id": actor.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    first = client.post("/api/v1/m1/reservations", json=payload, headers=headers)
    assert first.status_code == 201
    with factory() as session, session.begin():
        session.get(Room, data["room"]).is_active = False
        session.get(Seat, data["seats"][0]).is_active = False
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow",
        lambda: to_utc(data["end"]) + timedelta(days=1),
    )
    replay = client.post("/api/v1/m1/reservations", json=payload, headers=headers)
    assert replay.status_code == 201 and replay.json() == first.json()


def test_concurrent_key_conflict_precedes_loser_room_authorization(
    client, data, factory, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import event

    from app.core.database import get_session_factory
    from app.modules.m1 import reservations

    actor = data["users"]["student"]
    headers = {**token_headers(actor), "Idempotency-Key": "conflict-scope"}
    payload = {
        "identity_id": actor.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    acquired, waiting, release = Event(), Event(), Event()
    real_acquire = reservations.acquire_m1_reservation

    def pause_acquire(*args, **kwargs):
        acquired.set()
        assert release.wait(5)
        return real_acquire(*args, **kwargs)

    monkeypatch.setattr(reservations, "acquire_m1_reservation", pause_acquire)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post, "/api/v1/m1/reservations", json=payload, headers=headers)
        assert acquired.wait(5)
        engine = get_session_factory().kw["bind"]

        def observe_lock(_conn, _cursor, statement, _parameters, _context, _executemany):
            if "d_users.id" in statement and "FOR UPDATE" in statement:
                waiting.set()

        event.listen(engine, "before_cursor_execute", observe_lock)
        second = pool.submit(
            client.post,
            "/api/v1/m1/reservations",
            json={**payload, "seat_id": data["private_seat"]},
            headers=headers,
        )
        assert waiting.wait(5)
        release.set()
        event.remove(engine, "before_cursor_execute", observe_lock)
        assert first.result().status_code == 201
        assert second.result().status_code == 409
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Reservation)) == 1


def test_migration_preserves_legacy_source_and_allocates_above_it(
    client, data, factory, mysql_engine
):
    from alembic.config import Config

    from alembic import command
    from app.modules.resources.allocation_models import SeatAllocation
    from app.modules.resources.models import M1SourceSequence

    cfg = Config("alembic.ini")
    command.downgrade(cfg, "0008_resource_revision")
    try:
        with factory() as session, session.begin():
            session.add(
                ResourceBlock(
                    room_id=data["room"],
                    seat_id=data["seats"][0],
                    source_module="M1",
                    source_id=1000000,
                    start_at=to_utc(data["start"]),
                    end_at=to_utc(data["end"]),
                    status="ACTIVE",
                )
            )
        command.upgrade(cfg, "head")
        with factory() as session:
            assert session.get(M1SourceSequence, 1).high_water == 1000000
        actor = data["users"]["student"]
        payload = {
            "identity_id": actor.identity_id,
            "seat_id": data["seats"][1],
            "start_at": data["start"].isoformat(),
            "end_at": data["end"].isoformat(),
        }
        response = client.post(
            "/api/v1/m1/reservations",
            json=payload,
            headers={**token_headers(actor), "Idempotency-Key": "legacy"},
        )
        assert response.status_code == 201
        new_id = int(response.json()["id"])
        assert new_id > 1000000
        with factory() as session:
            legacy = session.scalar(
                select(ResourceBlock).where(
                    ResourceBlock.source_module == "M1", ResourceBlock.source_id == 1000000
                )
            )
            assert legacy.status == "ACTIVE"
            assert not session.scalar(
                select(SeatAllocation).where(
                    SeatAllocation.source_id == 1000000,
                    SeatAllocation.m1_reservation_id.is_not(None),
                )
            )
            new = session.scalar(select(SeatAllocation).where(SeatAllocation.source_id == new_id))
            assert new.m1_reservation_id == new_id
    finally:
        command.upgrade(cfg, "head")


def test_paused_timers_do_not_starve_worker_and_can_be_enabled(client, data, factory):
    from app.modules.outbox.service import register_event
    from app.worker import run_once

    actor = data["users"]["student"]
    payload = {
        "identity_id": actor.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    assert (
        client.post(
            "/api/v1/m1/reservations",
            json=payload,
            headers={**token_headers(actor), "Idempotency-Key": "paused"},
        ).status_code
        == 201
    )
    now = to_utc(data["end"]) + timedelta(hours=1)
    seen = []

    def timer_handler(session, payload):
        seen.append(payload["kind"])

    with factory() as session, session.begin():
        register_event(
            session,
            source_module="M1",
            event_key="ordinary",
            handler="other_test",
            payload={},
            available_at=to_utc(data["start"]),
        )
    assert (
        run_once(
            factory,
            worker_id="test",
            batch_size=2,
            now=now,
            handlers={
                "other_test": lambda session, payload: seen.append("ordinary"),
                "audit": lambda session, payload: None,
            },
        )
        == 2
    )
    assert seen == ["ordinary"]
    with factory() as session:
        timers = list(session.scalars(select(OutboxEvent).where(OutboxEvent.handler == "m1_timer")))
        assert all(t.status == "PENDING" and t.attempts == 0 for t in timers)
    assert (
        run_once(
            factory, worker_id="test", batch_size=10, now=now, handlers={"m1_timer": timer_handler}
        )
        == 4
    )
    assert len(seen) == 5
    with factory() as session:
        assert all(
            t.status == "SUCCEEDED"
            for t in session.scalars(select(OutboxEvent).where(OutboxEvent.handler == "m1_timer"))
        )


def test_concurrent_users_allocate_distinct_ids(client, data, factory):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    actors = [data["users"]["student"], data["users"]["other"]]
    headers = [{**token_headers(actor), "Idempotency-Key": "distinct"} for actor in actors]
    payloads = [
        {
            "identity_id": actor.identity_id,
            "seat_id": (data["seats"][0], data["private_seat"])[i],
            "start_at": data["start"].isoformat(),
            "end_at": data["end"].isoformat(),
        }
        for i, actor in enumerate(actors)
    ]
    barrier = Barrier(2)

    def submit(i):
        barrier.wait()
        return client.post("/api/v1/m1/reservations", json=payloads[i], headers=headers[i])

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))
    assert all(r.status_code == 201 for r in responses)
    assert responses[0].json()["id"] != responses[1].json()["id"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Reservation)) == 2


def test_legacy_source_writer_serializes_with_new_reservation(client, data, factory, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import event

    from app.core.database import get_session_factory
    from app.modules.resources import service as resources

    legacy_actor = data["users"]["other"]
    new_actor = data["users"]["student"]
    entered, waiting, release = Event(), Event(), Event()
    original = resources._lock_m1_source_sequence

    def pause_legacy(session):
        result = original(session)
        if session.info.get("legacy_test"):
            entered.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(resources, "_lock_m1_source_sequence", pause_legacy)

    def legacy():
        with factory() as session, session.begin():
            session.info["legacy_test"] = True
            resources.reserve_seat(
                session,
                room_id=data["private_room"],
                seat_id=data["private_seat"],
                reservation_id=7777777,
                start_at=data["start"],
                end_at=data["end"],
                actor=legacy_actor,
            )

    payload = {
        "identity_id": new_actor.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    headers = {**token_headers(new_actor), "Idempotency-Key": "legacy-parallel"}

    def observe_sequence(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "INSERT INTO d_m1_source_sequence" in statement:
            waiting.set()

    engine = get_session_factory().kw["bind"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(legacy)
        assert entered.wait(5)
        event.listen(engine, "before_cursor_execute", observe_sequence)
        second = pool.submit(client.post, "/api/v1/m1/reservations", json=payload, headers=headers)
        assert waiting.wait(5)
        release.set()
        event.remove(engine, "before_cursor_execute", observe_sequence)
        first.result()
        response = second.result()
    assert response.status_code == 201
    assert int(response.json()["id"]) > 7777777
    with factory() as session:
        legacy_source = session.scalar(
            select(ResourceBlock).where(
                ResourceBlock.source_module == "M1", ResourceBlock.source_id == 7777777
            )
        )
        assert legacy_source is not None and legacy_source.status == "ACTIVE"


def test_same_user_legacy_and_new_booking_lock_order(client, data, factory, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import time
    from threading import Event

    from sqlalchemy import event

    from app.core.database import get_session_factory
    from app.modules.resources import service as resources
    from app.modules.resources.models import Room, Seat

    actor = data["users"]["student"]
    headers = {**token_headers(actor), "Idempotency-Key": "same-user-legacy"}
    with factory() as session, session.begin():
        room = Room(
            code="same-user-room",
            name="test",
            building_name="B",
            capacity=1,
            open_time=time(8),
            close_time=time(22),
            is_active=True,
        )
        session.add(room)
        session.flush()
        seat = Seat(
            room_id=room.id,
            seat_no="1",
            is_active=True,
            has_fixed_socket=False,
            has_mobile_socket=False,
        )
        session.add(seat)
        session.flush()
        room_id, seat_id = room.id, seat.id
    entered, release, user_waiting, sequence_entered = Event(), Event(), Event(), Event()
    original = resources._lock_m1_source_sequence

    def pause_legacy(session):
        result = original(session)
        if session.info.get("legacy_test"):
            entered.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(resources, "_lock_m1_source_sequence", pause_legacy)

    def legacy():
        with factory() as session, session.begin():
            session.info["legacy_test"] = True
            resources.reserve_seat(
                session,
                room_id=room_id,
                seat_id=seat_id,
                reservation_id=77777,
                start_at=data["start"],
                end_at=data["end"],
                actor=actor,
            )

    payload = {
        "identity_id": actor.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }

    def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "d_users.id" in statement and "FOR UPDATE" in statement:
            user_waiting.set()
        if "INSERT INTO d_m1_source_sequence" in statement:
            sequence_entered.set()

    engine = get_session_factory().kw["bind"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(legacy)
        assert entered.wait(5)
        event.listen(engine, "before_cursor_execute", observe)
        try:
            second = pool.submit(
                client.post, "/api/v1/m1/reservations", json=payload, headers=headers
            )
            assert user_waiting.wait(5)
            assert not sequence_entered.wait(0.5)
        finally:
            release.set()
            event.remove(engine, "before_cursor_execute", observe)
        first.result()
        assert second.result().status_code == 201
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ResourceBlock)) == 2
