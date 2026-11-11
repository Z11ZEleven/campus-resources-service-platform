from datetime import timedelta

import pytest
from conftest import token_headers


def test_admin_restriction_blocks_search_and_reservation(client, data, monkeypatch):
    from app.core.time import to_utc

    monkeypatch.setattr(
        "app.modules.m1.search.precise_utcnow", lambda: to_utc(data["start"]) - timedelta(hours=1)
    )
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow",
        lambda: to_utc(data["start"]) - timedelta(hours=1),
    )
    admin, student = data["users"]["admin"], data["users"]["student"]
    path = f"/api/v1/resources/rooms/{data['room']}/restrictions"
    payload = {
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    headers = {**token_headers(admin), "Idempotency-Key": "restriction-one"}
    first = client.post(path, json=payload, headers=headers)
    assert first.status_code == 201, first.text
    assert client.post(path, json=payload, headers=headers).json() == first.json()
    assert (
        client.post(
            path, json={**payload, "seat_id": data["seats"][1]}, headers=headers
        ).status_code
        == 409
    )
    params = {
        "identity_id": student.identity_id,
        "start_at": payload["start_at"],
        "end_at": payload["end_at"],
    }
    result = client.get("/api/v1/m1/seats", params=params, headers=token_headers(student)).json()
    assert result["total"] == 2
    attempt = client.post(
        "/api/v1/m1/reservations",
        json={**payload, "identity_id": student.identity_id},
        headers={**token_headers(student), "Idempotency-Key": "reservation"},
    )
    assert attempt.status_code == 409
    restriction_id = first.json()["id"]
    assert client.delete(f"{path}/{restriction_id}", headers=headers).status_code == 200
    assert client.delete(f"{path}/{restriction_id}", headers=headers).status_code == 200
    assert (
        client.get("/api/v1/m1/seats", params=params, headers=token_headers(student)).json()[
            "total"
        ]
        == 3
    )


@pytest.mark.parametrize("source_module", ["M1", "M2"])
def test_restriction_rejects_existing_booking_without_modification(
    client, data, factory, source_module
):
    from sqlalchemy import select

    from app.core.time import to_utc
    from app.modules.resources.models import ResourceBlock

    start, end = to_utc(data["start"]), to_utc(data["end"])
    with factory() as session, session.begin():
        session.add(
            ResourceBlock(
                room_id=data["room"],
                seat_id=data["seats"][0] if source_module == "M1" else None,
                source_module=source_module,
                source_id=999,
                start_at=start,
                end_at=end,
                status="ACTIVE",
            )
        )
    path = f"/api/v1/resources/rooms/{data['room']}/restrictions"
    payload = {"start_at": data["start"].isoformat(), "end_at": data["end"].isoformat()}
    headers = {**token_headers(data["users"]["admin"]), "Idempotency-Key": "conflict"}
    assert client.post(path, json=payload, headers=headers).status_code == 409
    with factory() as session:
        assert (
            session.scalar(
                select(ResourceBlock).where(ResourceBlock.source_module == source_module)
            ).status
            == "ACTIVE"
        )
        assert (
            session.scalar(select(ResourceBlock).where(ResourceBlock.source_module == "D")) is None
        )


def test_admin_and_maintenance_sources_remain_isolated(client, data, factory, monkeypatch):
    from sqlalchemy import select

    from app.core.time import to_utc
    from app.modules.audit.models import OperationLog
    from app.modules.resources.models import ResourceBlock

    monkeypatch.setattr(
        "app.modules.m1.search.precise_utcnow", lambda: to_utc(data["start"]) - timedelta(hours=1)
    )
    start = to_utc(data["start"])
    with factory() as session, session.begin():
        session.add(
            ResourceBlock(
                room_id=data["room"],
                seat_id=None,
                source_module="M3",
                source_id=777,
                start_at=start,
                end_at=None,
                status="ACTIVE",
            )
        )
    path = f"/api/v1/resources/rooms/{data['room']}/restrictions"
    payload = {"start_at": data["start"].isoformat(), "end_at": data["end"].isoformat()}
    admin_headers = token_headers(data["users"]["admin"])
    first = client.post(path, json=payload, headers={**admin_headers, "Idempotency-Key": "first"})
    second = client.post(path, json=payload, headers={**admin_headers, "Idempotency-Key": "second"})
    assert first.status_code == second.status_code == 201
    params = {"identity_id": data["users"]["student"].identity_id, **payload}
    student_headers = token_headers(data["users"]["student"])

    def total():
        return client.get("/api/v1/m1/seats", params=params, headers=student_headers).json()[
            "total"
        ]

    assert total() == 0
    assert client.delete(f"{path}/{first.json()['id']}", headers=admin_headers).status_code == 200
    assert total() == 0
    from app.modules.resources.service import release_source

    with factory() as session, session.begin():
        release_source(
            session,
            room_id=data["room"],
            source_module="M3",
            source_id=777,
            actor=data["users"]["property"],
        )
    assert total() == 0
    assert client.delete(f"{path}/{second.json()['id']}", headers=admin_headers).status_code == 200
    assert total() == 3
    with factory() as session:
        logs = list(
            session.scalars(
                select(OperationLog).where(
                    OperationLog.module == "D",
                    OperationLog.business_id == int(first.json()["id"]),
                    OperationLog.action.in_(["ACQUIRE_RESOURCE", "RELEASE_RESOURCE"]),
                )
            )
        )
        assert {log.action for log in logs} == {"ACQUIRE_RESOURCE", "RELEASE_RESOURCE"}
        assert all(log.actor_id == data["users"]["admin"].user_id for log in logs)


def test_restriction_enforces_room_scope_and_period(client, data, factory):
    from sqlalchemy import select

    from app.modules.auth.models import UserRole

    admin = data["users"]["admin"]
    with factory() as session, session.begin():
        grant = session.scalar(select(UserRole).where(UserRole.user_id == admin.user_id))
        grant.scope_type, grant.scope_value = "ROOM", str(data["room"])
    headers = {**token_headers(admin), "Idempotency-Key": "test"}
    path = f"/api/v1/resources/rooms/{data['private_room']}/restrictions"
    payload = {"start_at": data["start"].isoformat(), "end_at": data["end"].isoformat()}
    assert client.post(path, json=payload, headers=headers).status_code == 403
    path = f"/api/v1/resources/rooms/{data['room']}/restrictions"
    assert (
        client.post(
            path,
            json=payload,
            headers={**token_headers(data["users"]["student"]), "Idempotency-Key": "denied"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            path, json={**payload, "seat_id": data["private_seat"]}, headers=headers
        ).status_code
        == 404
    )
    assert (
        client.post(
            path, json={**payload, "end_at": payload["start_at"]}, headers=headers
        ).status_code
        == 422
    )
    assert (
        client.post(
            path, json={**payload, "start_at": "2026-11-11T09:00:00"}, headers=headers
        ).status_code
        == 422
    )


def test_legacy_availability_marks_admin_restriction_unbookable(client, data):
    admin_headers = {**token_headers(data["users"]["admin"]), "Idempotency-Key": "slot"}
    path = f"/api/v1/resources/rooms/{data['room']}/restrictions"
    payload = {
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    assert client.post(path, json=payload, headers=admin_headers).status_code == 201
    response = client.get(
        f"/api/v1/resources/rooms/{data['room']}/availability",
        params={"date": data["day"].isoformat()},
        headers=token_headers(data["users"]["student"]),
    )
    assert response.status_code == 200
    slot = next(
        item
        for item in response.json()["slots"]
        if item["seat_id"] == str(data["seats"][0])
        and item["slot_start"].startswith(
            data["start"].astimezone(__import__("datetime").UTC).isoformat()[:13]
        )
    )
    assert slot["status"] == "DISABLED" and not slot["bookable"] and slot["source_module"] == "D"


def test_admin_history_prevents_lossy_migration_downgrade(client, data, mysql_engine):
    from alembic.config import Config
    from sqlalchemy import text

    from alembic import command

    path = f"/api/v1/resources/rooms/{data['room']}/restrictions"
    payload = {"start_at": data["start"].isoformat(), "end_at": data["end"].isoformat()}
    headers = {**token_headers(data["users"]["admin"]), "Idempotency-Key": "history"}
    assert client.post(path, json=payload, headers=headers).status_code == 201
    with pytest.raises(RuntimeError, match="administrator restriction history"):
        command.downgrade(Config("alembic.ini"), "0010_m1_rotate_perm")
    with mysql_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0011_admin_restrictions"
        )
        assert (
            connection.scalar(
                text("SELECT COUNT(*) FROM d_resource_blocks WHERE source_module = 'D'")
            )
            == 1
        )
    command.upgrade(Config("alembic.ini"), "head")


@pytest.mark.parametrize("operation", ["create", "release"])
@pytest.mark.parametrize("revoked", ["session", "role"])
def test_restriction_revalidates_actor_after_room_lock(
    client, data, factory, monkeypatch, operation, revoked
):
    from sqlalchemy import delete, update

    from app.core.time import utcnow
    from app.modules.auth.models import AuthSession, UserRole
    from app.modules.resources import restrictions

    admin = data["users"]["admin"]
    headers = {**token_headers(admin), "Idempotency-Key": "revoked"}
    path = f"/api/v1/resources/rooms/{data['room']}/restrictions"
    payload = {"start_at": data["start"].isoformat(), "end_at": data["end"].isoformat()}
    if operation == "release":
        response = client.post(path, json=payload, headers=headers)
        assert response.status_code == 201
        path += "/" + response.json()["id"]
    original_lock = restrictions.lock_room

    def lock_then_revoke(session, room_id):
        room = original_lock(session, room_id)
        # A separate committed transaction models revocation during lock waiting.
        with factory() as other, other.begin():
            if revoked == "session":
                other.execute(
                    update(AuthSession)
                    .where(AuthSession.user_id == admin.user_id)
                    .values(revoked_at=utcnow())
                )
            else:
                other.execute(delete(UserRole).where(UserRole.user_id == admin.user_id))
        return room

    monkeypatch.setattr(restrictions, "lock_room", lock_then_revoke)
    response = (
        client.post(path, json=payload, headers=headers)
        if operation == "create"
        else client.delete(path, headers=headers)
    )
    assert response.status_code == (401 if revoked == "session" else 403)
