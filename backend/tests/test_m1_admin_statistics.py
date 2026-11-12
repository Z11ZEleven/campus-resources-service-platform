from datetime import timedelta

from conftest import token_headers
from sqlalchemy import select

from app.core.time import to_utc
from app.modules.auth.models import UserRole
from app.modules.m1.models import Reservation


def test_statistics_respect_room_scope_and_clip_usage(client, data, factory, monkeypatch):
    start = to_utc(data["start"])
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow", lambda: start - timedelta(hours=1)
    )
    for who, seat in [("student", data["seats"][0]), ("other", data["private_seat"])]:
        actor = data["users"][who]
        response = client.post(
            "/api/v1/m1/reservations",
            headers={**token_headers(actor), "Idempotency-Key": f"stats-{who}"},
            json={
                "identity_id": actor.identity_id,
                "seat_id": seat,
                "start_at": data["start"].isoformat(),
                "end_at": data["end"].isoformat(),
            },
        )
        assert response.status_code == 201
        if who == "student":
            rid = int(response.json()["id"])
    admin = data["users"]["admin"]
    with factory() as session, session.begin():
        grant = session.scalar(select(UserRole).where(UserRole.user_id == admin.user_id))
        grant.scope_type, grant.scope_value = "ROOM", str(data["room"])
        reservation = session.get(Reservation, rid)
        reservation.status = "INTERRUPTED"
        reservation.checked_in_at = start + timedelta(minutes=10)
        reservation.actual_end_at = start + timedelta(minutes=40)
    response = client.get(
        "/api/v1/m1/admin/statistics",
        headers=token_headers(admin),
        params={"start_at": data["start"].isoformat(), "end_at": data["end"].isoformat()},
    )
    assert response.status_code == 200
    assert response.json()["reservation_count"] == 1
    assert response.json()["planned_minutes"] == 60
    assert response.json()["actual_minutes"] == 30
    assert response.json()["interrupted_count"] == 1
    assert (
        client.get(
            "/api/v1/m1/admin/statistics",
            headers=token_headers(data["users"]["student"]),
            params={"start_at": data["start"].isoformat(), "end_at": data["end"].isoformat()},
        ).status_code
        == 403
    )


def test_admin_reservation_list_is_scoped_before_pagination(client, data, factory, monkeypatch):
    start = to_utc(data["start"])
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow", lambda: start - timedelta(hours=1)
    )
    for who, seat in [("student", data["seats"][0]), ("other", data["private_seat"])]:
        actor = data["users"][who]
        response = client.post(
            "/api/v1/m1/reservations",
            headers={**token_headers(actor), "Idempotency-Key": f"admin-list-{who}"},
            json={
                "identity_id": actor.identity_id,
                "seat_id": seat,
                "start_at": data["start"].isoformat(),
                "end_at": data["end"].isoformat(),
            },
        )
        assert response.status_code == 201
    admin = data["users"]["admin"]
    with factory() as session, session.begin():
        grant = session.scalar(select(UserRole).where(UserRole.user_id == admin.user_id))
        grant.scope_type, grant.scope_value = "ROOM", str(data["room"])
    result = client.get(
        "/api/v1/m1/admin/reservations?page=1&page_size=1", headers=token_headers(admin)
    )
    assert result.status_code == 200
    assert result.json()["total"] == 1
    assert result.json()["items"][0]["room_id"] == str(data["room"])
    assert (
        client.get(
            "/api/v1/m1/admin/reservations", headers=token_headers(data["users"]["student"])
        ).status_code
        == 403
    )
