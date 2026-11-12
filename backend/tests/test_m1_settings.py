from datetime import timedelta

from conftest import token_headers
from sqlalchemy import select

from app.core.time import to_utc
from app.modules.auth.models import UserRole
from app.modules.m1.models import Reservation


def test_settings_require_global_grant_version_and_preserve_booking_snapshot(
    client, data, factory, monkeypatch
):
    admin = data["users"]["admin"]
    headers = token_headers(admin)
    first = client.get("/api/v1/m1/admin/settings", headers=headers)
    assert first.status_code == 200 and first.json()["max_hours"] == 4
    start = to_utc(data["start"])
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow", lambda: start - timedelta(hours=1)
    )
    student = data["users"]["student"]
    body = {
        "identity_id": student.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    old = client.post(
        "/api/v1/m1/reservations",
        headers={**token_headers(student), "Idempotency-Key": "old-setting"},
        json=body,
    )
    assert old.status_code == 201
    changed = client.patch(
        "/api/v1/m1/admin/settings",
        headers=headers,
        json={"expected_version": first.json()["version"], "max_hours": 1},
    )
    assert changed.status_code == 200 and changed.json()["max_hours"] == 1
    assert (
        client.patch(
            "/api/v1/m1/admin/settings",
            headers=headers,
            json={"expected_version": first.json()["version"], "max_hours": 2},
        ).status_code
        == 409
    )
    with factory() as session, session.begin():
        row = session.get(Reservation, int(old.json()["id"]))
        assert row.settings_snapshot["max_hours"] == 4
        grant = session.scalar(select(UserRole).where(UserRole.user_id == admin.user_id))
        grant.scope_type, grant.scope_value = "ROOM", str(data["room"])
    assert (
        client.patch(
            "/api/v1/m1/admin/settings",
            headers=headers,
            json={"expected_version": changed.json()["version"], "max_hours": 2},
        ).status_code
        == 403
    )
    long_booking = {
        **body,
        "seat_id": data["seats"][1],
        "end_at": (data["end"] + timedelta(hours=1)).isoformat(),
    }
    assert (
        client.post(
            "/api/v1/m1/reservations",
            headers={**token_headers(student), "Idempotency-Key": "new-setting"},
            json=long_booking,
        ).status_code
        == 422
    )
