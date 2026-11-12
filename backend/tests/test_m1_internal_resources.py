from datetime import timedelta

from conftest import token_headers
from sqlalchemy import select

from app.core.time import to_utc
from app.modules.auth.models import ServicePrincipal, ServiceRoomScope
from app.modules.auth.sessions import credential_hash
from app.modules.m1.models import Reservation


def test_m3_service_maintenance_and_replay(client, data, factory, monkeypatch):
    token = "test-only-m3-service-token"
    with factory() as session, session.begin():
        service = ServicePrincipal(name="test-m3", module="M3", token_hash=credential_hash(token))
        session.add(service)
        session.flush()
        session.add(ServiceRoomScope(service_id=service.id, room_id=data["room"]))
    start = to_utc(data["start"])
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow", lambda: start - timedelta(hours=1)
    )
    student = data["users"]["student"]
    created = client.post(
        "/api/v1/m1/reservations",
        headers={**token_headers(student), "Idempotency-Key": "m3-impact"},
        json={
            "identity_id": student.identity_id,
            "seat_id": data["seats"][0],
            "start_at": data["start"].isoformat(),
            "end_at": data["end"].isoformat(),
        },
    )
    assert created.status_code == 201
    rid = int(created.json()["id"])
    url = "/api/v1/internal/resource-holds/M3/41"
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "operation-41"}
    body = {"room_id": str(data["room"]), "active": True, "revision": 1, "reason": "维修"}
    result = client.put(url, headers=headers, json=body)
    assert result.status_code == 200
    assert result.json()["status"] == "ACTIVE"
    assert result.json()["affected_count"] == 1
    assert client.put(url, headers=headers, json=body).json() == result.json()
    assert client.put(url, headers=headers, json={**body, "reason": "不同"}).status_code == 409
    assert (
        client.get("/api/v1/internal/resource-operations/operation-41", headers=headers).json()
        == result.json()
    )
    assert client.get(url, headers=headers).json()["status"] == "ACTIVE"
    with factory() as session:
        assert (
            session.scalar(select(Reservation.status).where(Reservation.id == rid)) == "CANCELLED"
        )
    released = client.put(
        url,
        headers={**headers, "Idempotency-Key": "operation-42"},
        json={**body, "active": False, "revision": 2},
    )
    assert released.status_code == 200 and released.json()["status"] == "RELEASED"
    stale = client.put(url, headers={**headers, "Idempotency-Key": "operation-43"}, json=body)
    assert stale.status_code == 409
    assert (
        client.put("/api/v1/internal/resource-holds/M2/9", headers=headers, json=body).status_code
        == 403
    )


def test_m2_conflict_is_recorded_and_release_tombstone_cannot_be_revived(
    client, data, factory, monkeypatch
):
    token = "test-only-m2-service-token"
    with factory() as session, session.begin():
        service = ServicePrincipal(name="test-m2", module="M2", token_hash=credential_hash(token))
        session.add(service)
        session.flush()
        session.add(ServiceRoomScope(service_id=service.id, room_id=data["room"]))
    start = to_utc(data["start"])
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow", lambda: start - timedelta(hours=1)
    )
    student = data["users"]["student"]
    created = client.post(
        "/api/v1/m1/reservations",
        headers={**token_headers(student), "Idempotency-Key": "m2-conflict"},
        json={
            "identity_id": student.identity_id,
            "seat_id": data["seats"][0],
            "start_at": data["start"].isoformat(),
            "end_at": data["end"].isoformat(),
        },
    )
    assert created.status_code == 201
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "m2-conflict-op"}
    body = {
        "room_id": str(data["room"]),
        "active": True,
        "revision": 1,
        "reason": "教室申请",
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    result = client.put("/api/v1/internal/resource-holds/M2/55", headers=headers, json=body)
    assert result.status_code == 409
    assert (
        client.get("/api/v1/internal/resource-operations/m2-conflict-op", headers=headers).json()
        == result.json()
    )
    assert client.get("/api/v1/internal/resource-holds/M2/55", headers=headers).status_code == 404
    tombstone = client.put(
        "/api/v1/internal/resource-holds/M2/56",
        headers={**headers, "Idempotency-Key": "m2-release-first"},
        json={**body, "active": False, "revision": 5},
    )
    assert tombstone.status_code == 200 and tombstone.json()["status"] == "RELEASED"
    stale = client.put(
        "/api/v1/internal/resource-holds/M2/56",
        headers={**headers, "Idempotency-Key": "m2-old-add"},
        json=body,
    )
    assert stale.status_code == 409
    assert (
        client.get("/api/v1/internal/resource-holds/M2/56", headers=headers).json()["status"]
        == "RELEASED"
    )
