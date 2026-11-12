from datetime import timedelta

from conftest import token_headers
from sqlalchemy import func, select

from app.core.time import to_utc
from app.modules.m1.models import Reservation, Violation
from app.modules.outbox.handlers import HANDLERS
from app.modules.resources.models import ResourceBlock
from app.worker import run_once


def test_expired_reservation_is_released_once_after_worker_restart(
    client, data, factory, monkeypatch
):
    start = to_utc(data["start"])
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow", lambda: start - timedelta(hours=1)
    )
    student = data["users"]["student"]
    response = client.post(
        "/api/v1/m1/reservations",
        headers={**token_headers(student), "Idempotency-Key": "timer-restart"},
        json={
            "identity_id": student.identity_id,
            "seat_id": data["seats"][0],
            "start_at": data["start"].isoformat(),
            "end_at": data["end"].isoformat(),
        },
    )
    assert response.status_code == 201
    reservation_id = int(response.json()["id"])
    monkeypatch.setattr(
        "app.modules.m1.period.precise_utcnow", lambda: start + timedelta(minutes=16)
    )
    for attempt in range(8):
        run_once(
            factory,
            worker_id=f"restart-test-{attempt}",
            now=start + timedelta(minutes=16),
            handlers=HANDLERS,
        )
    with factory() as session:
        row = session.get(Reservation, reservation_id)
        assert row.status == "NO_SHOW"
        assert (
            session.scalar(
                select(func.count())
                .select_from(Violation)
                .where(Violation.reservation_id == reservation_id)
            )
            == 1
        )
        block = session.scalar(
            select(ResourceBlock).where(
                ResourceBlock.source_module == "M1", ResourceBlock.source_id == reservation_id
            )
        )
        assert block.status == "RELEASED"


def test_reminders_stop_after_checkin_and_reservation_completes(client, data, factory, monkeypatch):
    from app.core.config import get_settings
    from app.modules.notifications.models import Notification

    monkeypatch.setenv("M1_CHECKIN_SECRET", "test-only-checkin-secret-at-least-32-characters")
    get_settings.cache_clear()
    start = to_utc(data["start"])
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow", lambda: start - timedelta(hours=1)
    )
    now = [start - timedelta(minutes=15)]
    monkeypatch.setattr("app.modules.m1.period.precise_utcnow", lambda: now[0])
    monkeypatch.setattr("app.modules.m1.checkin.precise_utcnow", lambda: now[0])
    student = data["users"]["student"]
    headers = token_headers(student)
    result = client.post(
        "/api/v1/m1/reservations",
        headers={**headers, "Idempotency-Key": "timer-checkin"},
        json={
            "identity_id": student.identity_id,
            "seat_id": data["seats"][0],
            "start_at": data["start"].isoformat(),
            "end_at": data["end"].isoformat(),
        },
    )
    assert result.status_code == 201
    rid = int(result.json()["id"])
    for _ in range(4):
        run_once(factory, worker_id="reminder-test", now=now[0], handlers=HANDLERS)
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.business_id == rid)
            )
            == 1
        )
    code = client.get(
        f"/api/v1/m1/admin/rooms/{data['room']}/check-in-code",
        headers=token_headers(data["users"]["admin"]),
    ).json()["code"]
    assert (
        client.post(
            f"/api/v1/m1/reservations/{rid}/check-in", headers=headers, json={"code": code}
        ).status_code
        == 200
    )
    now[0] = start + timedelta(hours=1)
    for _ in range(6):
        run_once(factory, worker_id="completion-test", now=now[0], handlers=HANDLERS)
    with factory() as session:
        row = session.get(Reservation, rid)
        assert row.status == "COMPLETED"
        assert row.actual_end_at == to_utc(data["end"])
        assert (
            session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.business_id == rid)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count()).select_from(Violation).where(Violation.reservation_id == rid)
            )
            == 0
        )
    get_settings.cache_clear()
