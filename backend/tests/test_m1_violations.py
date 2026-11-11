from datetime import timedelta

from conftest import token_headers
from sqlalchemy import select

from app.core.time import to_utc
from app.modules.m1.models import Reservation, Violation


def test_violations_are_paginated_and_visible_only_to_the_owner(client, data, factory, monkeypatch):
    start = to_utc(data["start"])
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow", lambda: start - timedelta(hours=1)
    )
    student, other = data["users"]["student"], data["users"]["other"]
    response = client.post(
        "/api/v1/m1/reservations",
        headers={**token_headers(student), "Idempotency-Key": "violation-list"},
        json={
            "identity_id": student.identity_id,
            "seat_id": data["seats"][0],
            "start_at": data["start"].isoformat(),
            "end_at": data["end"].isoformat(),
        },
    )
    assert response.status_code == 201
    rid = int(response.json()["id"])
    with factory() as session, session.begin():
        row = session.scalar(select(Reservation).where(Reservation.id == rid))
        session.add(
            Violation(
                reservation_id=rid,
                user_id=row.user_id,
                violation_type="NO_SHOW",
                occurred_at=start + timedelta(minutes=15),
                reason="未按时签到",
            )
        )
    own = client.get("/api/v1/m1/violations?page=1&page_size=1", headers=token_headers(student))
    assert own.status_code == 200
    assert own.json()["total"] == 1
    assert own.json()["items"][0]["reservation_id"] == str(rid)
    assert isinstance(own.json()["items"][0]["id"], str)
    other_result = client.get("/api/v1/m1/violations", headers=token_headers(other))
    assert other_result.status_code == 200
    assert other_result.json()["total"] == 0
