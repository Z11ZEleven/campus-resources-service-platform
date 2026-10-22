import pytest
from sqlalchemy import func, select

from app.modules.resources.allocation_models import SeatAllocation
from app.modules.resources.models import ResourceBlock
from app.modules.resources.service import (
    occupy_room,
    release_source,
    reserve_seat,
    start_maintenance,
)

pytestmark = pytest.mark.mysql


def test_personal_allocation_is_idempotent_and_release_preserves_other_source(factory, data):
    actor = data["users"]["student"]
    args = dict(
        room_id=data["room"],
        seat_id=data["seats"][0],
        reservation_id=901,
        start_at=data["start"],
        end_at=data["end"],
        actor=actor,
    )
    with factory() as session, session.begin():
        block = reserve_seat(session, **args)
        reserve_seat(session, **args)
        allocation = session.scalar(
            select(SeatAllocation).where(SeatAllocation.block_id == block.id)
        )
        assert allocation is not None
        assert allocation.seat_id == data["seats"][0]
        assert allocation.start_at == block.start_at and allocation.end_at == block.end_at
        assert allocation.status == "ACTIVE"
        assert session.scalar(select(func.count()).select_from(SeatAllocation)) == 1
        maintenance = start_maintenance(
            session, room_id=data["room"], work_order_id=902, actor=data["users"]["repairer"]
        )
        release_source(
            session, room_id=data["room"], source_module="M1", source_id=901, actor=actor
        )
        assert allocation.status == "RELEASED" and allocation.released_at is not None
        assert session.get(ResourceBlock, maintenance.id).status == "ACTIVE"
        assert session.scalar(select(func.count()).select_from(SeatAllocation)) == 1


def test_room_allocation_materializes_active_seats_and_releases_exact_source(factory, data):
    with factory() as session, session.begin():
        block = occupy_room(
            session,
            room_id=data["room"],
            application_id=903,
            applicant_id=data["users"]["teacher"].user_id,
            start_at=data["start"],
            end_at=data["end"],
            actor=data["users"]["admin"],
        )
        allocations = list(
            session.scalars(select(SeatAllocation).where(SeatAllocation.block_id == block.id))
        )
        assert {row.seat_id for row in allocations} == set(data["seats"])
        release_source(
            session,
            room_id=data["room"],
            source_module="M2",
            source_id=903,
            actor=data["users"]["admin"],
        )
        assert all(row.status == "RELEASED" and row.released_at for row in allocations)
