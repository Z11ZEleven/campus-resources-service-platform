from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import DBAPIError


@contextmanager
def constraint_rejected():
    with pytest.raises(DBAPIError) as error:
        yield
    assert error.value.orig.args[0] in {1062, 1452, 3819}


def test_m1_foundation_registered():
    from importlib.util import find_spec

    assert find_spec("app.modules.m1") is not None
    from app import models  # noqa: F401
    from app.core.database import Base

    expected = {
        "m1_reservations",
        "m1_reservation_events",
        "m1_violations",
        "m1_room_checkin_codes",
        "m1_checkin_attempts",
        "d_seat_allocations",
    }
    assert expected <= set(Base.metadata.tables)


def reservation_values(session, data):
    from app.modules.auth.models import UserIdentity

    user_id = data["users"]["student"].user_id
    identity_id = session.scalar(select(UserIdentity.id).where(UserIdentity.user_id == user_id))
    return dict(
        user_id=user_id,
        identity_id=identity_id,
        room_id=data["room"],
        seat_id=data["seats"][0],
        start_at=datetime(2026, 11, 1, 1),
        end_at=datetime(2026, 11, 1, 2),
        status="RESERVED",
        create_request_key="one",
        request_hash="a" * 64,
        settings_snapshot={"duration": 60},
        resource_snapshot={"seat": "1"},
    )


@pytest.mark.mysql
@pytest.mark.parametrize(
    "invalid", ["period", "state", "seat_room", "identity_user", "duplicate_key"]
)
def test_reservations_reject_invalid_relationships(factory, data, invalid):
    from app.modules.m1.models import Reservation

    with factory() as session, session.begin():
        values = reservation_values(session, data)
        if invalid == "period":
            values["end_at"] = values["start_at"]
        elif invalid == "state":
            values["status"] = "UNKNOWN"
        elif invalid == "seat_room":
            values["seat_id"] = data["private_seat"]
        elif invalid == "identity_user":
            values["user_id"] = data["users"]["other"].user_id
        else:
            session.add(Reservation(**values))
            session.flush()
        with constraint_rejected(), session.begin_nested():
            session.add(Reservation(**values))
            session.flush()


@pytest.mark.mysql
def test_reservation_read_repository_and_related_records(factory, data):
    from app.modules.m1.models import CheckinAttempt, Reservation, ReservationEvent, Violation
    from app.modules.m1.repository import ReservationRepository

    with factory() as session, session.begin():
        reservation = Reservation(**reservation_values(session, data))
        session.add(reservation)
        session.flush()
        uid = reservation.user_id
        session.add(
            ReservationEvent(
                reservation_id=reservation.id,
                event_type="CREATED",
                from_status=None,
                to_status="RESERVED",
                actor_type="USER",
                actor_id=uid,
                correlation_id="request-1",
                reason=None,
                details={},
            )
        )
        session.add(
            Violation(
                reservation_id=reservation.id,
                user_id=uid,
                violation_type="NO_SHOW",
                occurred_at=reservation.end_at,
                reason="test",
            )
        )
        session.add(
            CheckinAttempt(
                user_id=uid,
                reservation_id=reservation.id,
                room_id=data["room"],
                attempted_at=reservation.start_at,
                result="INVALID_CODE",
            )
        )
        session.flush()
        repository = ReservationRepository(session)
        assert repository.get(reservation.id).settings_snapshot == {"duration": 60}
        assert [row.id for row in repository.list_for_user(uid)] == [reservation.id]
        assert len(repository.list_events(reservation.id)) == 1
        assert repository.get_by_request_key(uid, "one").id == reservation.id
        assert repository.list_for_user(data["users"]["other"].user_id) == []
        with constraint_rejected(), session.begin_nested():
            session.add(
                Violation(
                    reservation_id=reservation.id,
                    user_id=uid,
                    violation_type="NO_SHOW",
                    occurred_at=reservation.end_at,
                    reason="duplicate",
                )
            )
            session.flush()
        with constraint_rejected(), session.begin_nested():
            session.add(
                CheckinAttempt(
                    user_id=data["users"]["other"].user_id,
                    reservation_id=reservation.id,
                    room_id=data["room"],
                    attempted_at=reservation.start_at,
                    result="INVALID_CODE",
                )
            )
            session.flush()


@pytest.mark.mysql
@pytest.mark.parametrize(
    "invalid", ["period", "source", "seat_room", "release", "typed_source", "source_seat"]
)
def test_allocations_enforce_source_ownership(factory, data, invalid):
    from app.modules.resources.allocation_models import SeatAllocation
    from app.modules.resources.models import ResourceBlock

    with factory() as session, session.begin():
        start = datetime(2026, 11, 1, 1)
        block = ResourceBlock(
            room_id=data["room"],
            seat_id=data["seats"][0],
            source_module="M1",
            source_id=123,
            start_at=start,
            end_at=start + timedelta(hours=1),
        )
        session.add(block)
        session.flush()
        values = dict(
            block_id=block.id,
            room_id=block.room_id,
            seat_id=block.seat_id,
            source_module="M1",
            source_id=123,
            source_seat_id=block.seat_id,
            start_at=start,
            end_at=block.end_at,
            status="ACTIVE",
        )
        if invalid == "period":
            values["end_at"] = start
        elif invalid == "source":
            values["source_module"] = "M2"
        elif invalid == "seat_room":
            values["seat_id"] = data["private_seat"]
        elif invalid == "release":
            values["status"] = "RELEASED"
        elif invalid == "source_seat":
            values["seat_id"] = data["seats"][1]
            values["source_seat_id"] = data["seats"][1]
        else:
            values["m1_reservation_id"] = 999
        with constraint_rejected(), session.begin_nested():
            session.add(SeatAllocation(**values))
            session.flush()


@pytest.mark.mysql
def test_checkin_code_unique_and_no_submitted_secret_columns(factory, data, mysql_engine):
    from app.modules.m1.models import RoomCheckinCode

    with factory() as session, session.begin():
        values = dict(
            room_id=data["room"], business_date=datetime(2026, 11, 1).date(), nonce="a" * 64
        )
        session.add(RoomCheckinCode(**values))
        session.flush()
        with constraint_rejected(), session.begin_nested():
            session.add(RoomCheckinCode(**values))
            session.flush()
    columns = {
        column["name"] for column in inspect(mysql_engine).get_columns("m1_checkin_attempts")
    }
    assert not columns & {"code", "submitted_code", "secret", "key"}


@pytest.mark.mysql
@pytest.mark.parametrize("invalid", ["reservation", "from_status", "to_status", "actor"])
def test_events_reject_invalid_relations_and_states(factory, data, invalid):
    from app.modules.m1.models import Reservation, ReservationEvent

    with factory() as session, session.begin():
        reservation = Reservation(**reservation_values(session, data))
        session.add(reservation)
        session.flush()
        values = dict(
            reservation_id=reservation.id,
            event_type="CREATED",
            from_status=None,
            to_status="RESERVED",
            actor_type="SYSTEM",
            actor_id=None,
            correlation_id="request-2",
            details={},
        )
        if invalid == "reservation":
            values["reservation_id"] += 1000
        elif invalid == "from_status":
            values["from_status"] = "UNKNOWN"
        elif invalid == "to_status":
            values["to_status"] = "UNKNOWN"
        else:
            values["actor_type"] = "USER"
        with constraint_rejected(), session.begin_nested():
            session.add(ReservationEvent(**values))
            session.flush()


@pytest.mark.mysql
def test_typed_m1_allocation_links_actual_reservation_and_resource(factory, data):
    from app.modules.m1.models import Reservation
    from app.modules.resources.allocation_models import SeatAllocation
    from app.modules.resources.models import ResourceBlock

    with factory() as session, session.begin():
        reservation = Reservation(**reservation_values(session, data))
        session.add(reservation)
        session.flush()
        block = ResourceBlock(
            room_id=reservation.room_id,
            seat_id=reservation.seat_id,
            source_module="M1",
            source_id=reservation.id,
            start_at=reservation.start_at,
            end_at=reservation.end_at,
        )
        session.add(block)
        session.flush()
        allocation = SeatAllocation(
            block_id=block.id,
            room_id=reservation.room_id,
            seat_id=reservation.seat_id,
            source_module="M1",
            source_id=reservation.id,
            source_seat_id=reservation.seat_id,
            m1_reservation_id=reservation.id,
            start_at=reservation.start_at,
            end_at=reservation.end_at,
        )
        session.add(allocation)
        session.flush()
        assert session.get(SeatAllocation, allocation.id).m1_reservation_id == reservation.id
        with constraint_rejected(), session.begin_nested():
            allocation.source_id = reservation.id + 1
            session.flush()
