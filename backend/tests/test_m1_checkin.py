from datetime import timedelta

import pytest
from conftest import token_headers
from sqlalchemy import func, select

from app.core.time import to_utc
from app.modules.m1.models import CheckinAttempt, ReservationEvent


def test_checkin_rotation_and_replay(client, data, factory, monkeypatch):
    monkeypatch.setenv("M1_CHECKIN_SECRET", "test-only-checkin-secret-at-least-32-characters")
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow",
        lambda: to_utc(data["start"]) - timedelta(hours=1),
    )
    monkeypatch.setattr("app.modules.m1.checkin.precise_utcnow", lambda: to_utc(data["start"]))
    student = data["users"]["student"]
    sh = {**token_headers(student), "Idempotency-Key": "checkin-test"}
    admin = token_headers(data["users"]["admin"])
    payload = {
        "identity_id": student.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    reservation = client.post("/api/v1/m1/reservations", headers=sh, json=payload)
    assert reservation.status_code == 201
    url = f"/api/v1/m1/reservations/{reservation.json()['id']}/check-in"
    code_url = f"/api/v1/m1/admin/rooms/{data['room']}/check-in-code"
    first = client.get(code_url, headers=admin)
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    old = first.json()["code"]
    rotated = client.post(code_url + "/rotate", headers=admin)
    assert rotated.status_code == 200
    code = rotated.json()["code"]
    assert old != code
    assert client.post(url, headers=sh, json={"code": old}).status_code == 400
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(CheckinAttempt)) == 1
    assert client.post(url, headers=sh, json={"code": code}).status_code == 200
    assert client.post(url, headers=sh, json={"code": "00000000"}).status_code == 200
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ReservationEvent)
                .where(ReservationEvent.event_type == "CHECKED_IN")
            )
            == 1
        )


def test_window_limits_scope_and_persistent_attempts(client, data, factory, monkeypatch):
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.modules.auth.models import UserRole

    monkeypatch.setenv("M1_CHECKIN_SECRET", "test-only-checkin-secret-at-least-32-characters")
    get_settings.cache_clear()
    start = to_utc(data["start"])
    now = [start - timedelta(hours=1)]
    monkeypatch.setattr("app.modules.m1.reservations.precise_utcnow", lambda: now[0])
    monkeypatch.setattr("app.modules.m1.checkin.precise_utcnow", lambda: now[0])
    student, other = data["users"]["student"], data["users"]["other"]
    sh, ah = token_headers(student), token_headers(data["users"]["admin"])
    payload = {
        "identity_id": student.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    result = client.post(
        "/api/v1/m1/reservations", headers={**sh, "Idempotency-Key": "limit"}, json=payload
    )
    url = f"/api/v1/m1/reservations/{result.json()['id']}/check-in"
    code_url = f"/api/v1/m1/admin/rooms/{data['room']}/check-in-code"
    assert client.get(code_url, headers=sh).status_code == 403
    assert (
        client.post(url, headers=token_headers(other), json={"code": "11111111"}).status_code == 404
    )
    with factory() as session, session.begin():
        grant = session.scalar(
            select(UserRole).where(UserRole.user_id == data["users"]["admin"].user_id)
        )
        grant.scope_type, grant.scope_value = "ROOM", str(data["private_room"])
    assert client.get(code_url, headers=ah).status_code == 403
    with factory() as session, session.begin():
        grant = session.scalar(
            select(UserRole).where(UserRole.user_id == data["users"]["admin"].user_id)
        )
        grant.scope_type, grant.scope_value = "GLOBAL", "*"
    code = client.get(code_url, headers=ah).json()["code"]
    now[0] = start - timedelta(minutes=15, milliseconds=1)
    assert client.post(url, headers=sh, json={"code": code}).status_code == 409
    now[0] = start + timedelta(minutes=15)
    assert client.post(url, headers=sh, json={"code": code}).status_code == 409
    now[0] = start - timedelta(minutes=15)
    wrong = "99999999" if code != "99999999" else "00000000"
    for _ in range(5):
        assert client.post(url, headers=sh, json={"code": wrong}).status_code == 400
    get_settings.cache_clear()
    assert client.post(url, headers=sh, json={"code": code}).status_code == 429
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(CheckinAttempt)) == 5
    now[0] += timedelta(minutes=10)
    assert client.post(url, headers=sh, json={"code": code}).status_code == 200


def test_concurrent_checkins_create_one_event(client, data, factory, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.core.config import get_settings

    monkeypatch.setenv("M1_CHECKIN_SECRET", "test-only-checkin-secret-at-least-32-characters")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow",
        lambda: to_utc(data["start"]) - timedelta(hours=1),
    )
    monkeypatch.setattr("app.modules.m1.checkin.precise_utcnow", lambda: to_utc(data["start"]))
    student = data["users"]["student"]
    sh = {**token_headers(student), "Idempotency-Key": "concurrent-checkin"}
    payload = {
        "identity_id": student.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    result = client.post("/api/v1/m1/reservations", headers=sh, json=payload)
    url = f"/api/v1/m1/reservations/{result.json()['id']}/check-in"
    code = client.get(
        f"/api/v1/m1/admin/rooms/{data['room']}/check-in-code",
        headers=token_headers(data["users"]["admin"]),
    ).json()["code"]
    barrier = Barrier(2)

    def submit(_):
        barrier.wait()
        return client.post(url, headers=sh, json={"code": code}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(submit, range(2))) == [200, 200]
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ReservationEvent)
                .where(ReservationEvent.event_type == "CHECKED_IN")
            )
            == 1
        )


def test_campus_date_and_wrong_room(client, data, factory, monkeypatch):
    from datetime import datetime

    from app.core.config import get_settings
    from app.core.time import BUSINESS_TZ
    from app.modules.m1.models import Reservation

    monkeypatch.setenv("M1_CHECKIN_SECRET", "test-only-checkin-secret-at-least-32-characters")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow",
        lambda: to_utc(data["start"]) - timedelta(hours=1),
    )
    midnight = datetime.combine(data["day"], datetime.min.time(), tzinfo=BUSINESS_TZ)
    now = [to_utc(midnight - timedelta(minutes=1))]
    monkeypatch.setattr("app.modules.m1.checkin.precise_utcnow", lambda: now[0])
    student = data["users"]["student"]
    sh = {**token_headers(student), "Idempotency-Key": "midnight"}
    ah = token_headers(data["users"]["admin"])
    payload = {
        "identity_id": student.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    result = client.post("/api/v1/m1/reservations", headers=sh, json=payload)
    rid = int(result.json()["id"])
    with factory() as session, session.begin():
        row = session.get(Reservation, rid)
        row.start_at = to_utc(midnight)
        row.end_at = row.start_at + timedelta(hours=1)
    code_url = f"/api/v1/m1/admin/rooms/{data['room']}/check-in-code"
    other_url = f"/api/v1/m1/admin/rooms/{data['private_room']}/check-in-code"
    old = client.get(code_url, headers=ah).json()["code"]
    now[0] = to_utc(midnight)
    current = client.get(code_url, headers=ah).json()["code"]
    other = client.get(other_url, headers=ah).json()["code"]
    url = f"/api/v1/m1/reservations/{rid}/check-in"
    assert old != current and other != current
    assert client.post(url, headers=sh, json={"code": old}).status_code == 400
    assert client.post(url, headers=sh, json={"code": other}).status_code == 400
    assert client.post(url, headers=sh, json={"code": current}).status_code == 200


def test_rotation_serializes_with_checkin(client, data, factory, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.core.config import get_settings

    monkeypatch.setenv("M1_CHECKIN_SECRET", "test-only-checkin-secret-at-least-32-characters")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow",
        lambda: to_utc(data["start"]) - timedelta(hours=1),
    )
    monkeypatch.setattr("app.modules.m1.checkin.precise_utcnow", lambda: to_utc(data["start"]))
    student = data["users"]["student"]
    sh = {**token_headers(student), "Idempotency-Key": "rotate-race"}
    ah = token_headers(data["users"]["admin"])
    payload = {
        "identity_id": student.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    reservation = client.post("/api/v1/m1/reservations", headers=sh, json=payload)
    url = f"/api/v1/m1/reservations/{reservation.json()['id']}/check-in"
    code_url = f"/api/v1/m1/admin/rooms/{data['room']}/check-in-code"
    old = client.get(code_url, headers=ah).json()["code"]
    barrier = Barrier(2)

    def submit():
        barrier.wait()
        return client.post(url, headers=sh, json={"code": old}).status_code

    def rotate():
        barrier.wait()
        return client.post(code_url + "/rotate", headers=ah).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(submit), pool.submit(rotate)
        assert a.result() in (200, 400)
        assert b.result() == 200
    with factory() as session:
        events = session.scalar(
            select(func.count())
            .select_from(ReservationEvent)
            .where(ReservationEvent.event_type == "CHECKED_IN")
        )
        failures = session.scalar(select(func.count()).select_from(CheckinAttempt))
        assert (events, failures) in ((1, 0), (0, 1))


def test_code_read_and_rotation_audited_without_code(client, data, factory, monkeypatch):
    from app.core.config import get_settings
    from app.modules.audit.models import OperationLog
    from app.modules.auth.models import UserRole

    monkeypatch.setenv("M1_CHECKIN_SECRET", "test-only-checkin-secret-at-least-32-characters")
    get_settings.cache_clear()
    admin = data["users"]["admin"]
    headers = token_headers(admin)
    url = f"/api/v1/m1/admin/rooms/{data['room']}/check-in-code"
    first = client.get(url, headers=headers)
    rotated = client.post(url + "/rotate", headers=headers)
    assert first.status_code == rotated.status_code == 200
    assert first.headers["cache-control"] == rotated.headers["cache-control"] == "no-store"
    with factory() as session:
        logs = list(
            session.scalars(
                select(OperationLog)
                .where(OperationLog.module == "M1", OperationLog.business_id == data["room"])
                .order_by(OperationLog.id)
            )
        )
        assert [log.action for log in logs] == ["READ_CHECKIN_CODE", "ROTATE_CHECKIN_CODE"]
        assert all(log.actor_id == admin.user_id and log.result == "SUCCESS" for log in logs)
        assert all(
            first.json()["code"] not in (log.note or "")
            and rotated.json()["code"] not in (log.note or "")
            for log in logs
        )
    with factory() as session, session.begin():
        grant = session.scalar(select(UserRole).where(UserRole.user_id == admin.user_id))
        grant.scope_type, grant.scope_value = "ROOM", str(data["private_room"])
    assert client.post(url + "/rotate", headers=headers).status_code == 403
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(OperationLog)
                .where(OperationLog.module == "M1", OperationLog.business_id == data["room"])
            )
            == 2
        )


def test_revoked_identity_and_permission_reject_checkin(client, data, factory, monkeypatch):
    from sqlalchemy import delete

    from app.core.config import get_settings
    from app.modules.auth.models import AuthSession, RolePermission, UserIdentity
    from app.modules.auth.sessions import credential_hash
    from app.modules.m1.models import Reservation

    monkeypatch.setenv("M1_CHECKIN_SECRET", "test-only-checkin-secret-at-least-32-characters")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow",
        lambda: to_utc(data["start"]) - timedelta(hours=1),
    )
    monkeypatch.setattr("app.modules.m1.checkin.precise_utcnow", lambda: to_utc(data["start"]))
    student = data["users"]["student"]
    sh = {**token_headers(student), "Idempotency-Key": "checkin-auth"}
    ah = token_headers(data["users"]["admin"])
    payload = {
        "identity_id": student.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    result = client.post("/api/v1/m1/reservations", headers=sh, json=payload)
    rid = int(result.json()["id"])
    url = f"/api/v1/m1/reservations/{rid}/check-in"
    code = client.get(f"/api/v1/m1/admin/rooms/{data['room']}/check-in-code", headers=ah).json()[
        "code"
    ]
    with factory() as session, session.begin():
        session.get(UserIdentity, student.identity_id).is_active = False
    assert client.post(url, headers=sh, json={"code": code}).status_code == 401
    with factory() as session, session.begin():
        session.get(UserIdentity, student.identity_id).is_active = True
        replacement = UserIdentity(
            user_id=student.user_id,
            identity_type="STUDENT",
            identity_no="replacement",
            department_code="CS",
            is_active=True,
        )
        session.add(replacement)
        session.flush()
        auth = session.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == credential_hash(sh["Authorization"].split()[1])
            )
        )
        auth.identity_id = replacement.id
    assert client.post(url, headers=sh, json={"code": code}).status_code == 403
    with factory() as session, session.begin():
        auth = session.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == credential_hash(sh["Authorization"].split()[1])
            )
        )
        auth.identity_id = student.identity_id
        session.execute(
            delete(RolePermission).where(
                RolePermission.role_code == "USER",
                RolePermission.permission_code == "m1:reservation:checkin",
            )
        )
    assert client.post(url, headers=sh, json={"code": code}).status_code == 403
    with factory() as session:
        assert session.get(Reservation, rid).status == "RESERVED"
        assert session.scalar(select(func.count()).select_from(CheckinAttempt)) == 0


def test_concurrent_failed_checkins_enforce_limit(client, data, factory, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.core.config import get_settings

    monkeypatch.setenv("M1_CHECKIN_SECRET", "test-only-checkin-secret-at-least-32-characters")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow",
        lambda: to_utc(data["start"]) - timedelta(hours=1),
    )
    monkeypatch.setattr("app.modules.m1.checkin.precise_utcnow", lambda: to_utc(data["start"]))
    student = data["users"]["student"]
    sh = {**token_headers(student), "Idempotency-Key": "concurrent-failed"}
    payload = {
        "identity_id": student.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    result = client.post("/api/v1/m1/reservations", headers=sh, json=payload)
    url = f"/api/v1/m1/reservations/{result.json()['id']}/check-in"
    code = client.get(
        f"/api/v1/m1/admin/rooms/{data['room']}/check-in-code",
        headers=token_headers(data["users"]["admin"]),
    ).json()["code"]
    wrong = "99999999" if code != "99999999" else "00000000"
    barrier = Barrier(8)

    def submit(_):
        barrier.wait()
        return client.post(url, headers=sh, json={"code": wrong}).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sorted(pool.map(submit, range(8))) == [400] * 5 + [429] * 3
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(CheckinAttempt)) == 5


def test_read_and_rotate_are_separate_scoped_permissions(client, data, factory, monkeypatch):
    from sqlalchemy import delete

    from app.core.config import get_settings
    from app.modules.auth.models import RolePermission, UserRole

    monkeypatch.setenv("M1_CHECKIN_SECRET", "test-only-checkin-secret-at-least-32-characters")
    get_settings.cache_clear()
    admin = data["users"]["admin"]
    headers = token_headers(admin)
    url = f"/api/v1/m1/admin/rooms/{data['room']}/check-in-code"
    with factory() as session, session.begin():
        session.execute(
            delete(RolePermission).where(
                RolePermission.role_code == "ROOM_ADMIN",
                RolePermission.permission_code == "m1:checkin_code:rotate",
            )
        )
    assert client.get(url, headers=headers).status_code == 200
    assert client.post(url + "/rotate", headers=headers).status_code == 403
    with factory() as session, session.begin():
        session.execute(
            delete(RolePermission).where(
                RolePermission.role_code == "ROOM_ADMIN",
                RolePermission.permission_code == "m1:checkin_code:read",
            )
        )
        session.add(
            RolePermission(role_code="ROOM_ADMIN", permission_code="m1:checkin_code:rotate")
        )
        grant = session.scalar(select(UserRole).where(UserRole.user_id == admin.user_id))
        grant.scope_type, grant.scope_value = "ROOM", str(data["room"])
    assert client.get(url, headers=headers).status_code == 403
    assert client.post(url + "/rotate", headers=headers).status_code == 200
    other = f"/api/v1/m1/admin/rooms/{data['private_room']}/check-in-code/rotate"
    assert client.post(other, headers=headers).status_code == 403
    with factory() as session, session.begin():
        session.execute(
            delete(RolePermission).where(
                RolePermission.role_code == "ROOM_ADMIN",
                RolePermission.permission_code == "m1:checkin_code:rotate",
            )
        )
    assert client.post(url + "/rotate", headers=headers).status_code == 403


def test_real_clock_checkin_and_aware_window_boundaries(client, data, factory, monkeypatch):
    from datetime import UTC, datetime

    from app.core.config import get_settings
    from app.modules.m1.models import Reservation

    monkeypatch.setenv("M1_CHECKIN_SECRET", "test-only-checkin-secret-at-least-32-characters")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.modules.m1.reservations.precise_utcnow",
        lambda: to_utc(data["start"]) - timedelta(hours=1),
    )
    student = data["users"]["student"]
    sh = {**token_headers(student), "Idempotency-Key": "real-clock"}
    payload = {
        "identity_id": student.identity_id,
        "seat_id": data["seats"][0],
        "start_at": data["start"].isoformat(),
        "end_at": data["end"].isoformat(),
    }
    result = client.post("/api/v1/m1/reservations", headers=sh, json=payload)
    rid = int(result.json()["id"])
    now = datetime.now(UTC).replace(microsecond=0)
    with factory() as session, session.begin():
        row = session.get(Reservation, rid)
        row.start_at = now.replace(tzinfo=None)
        row.end_at = row.start_at + timedelta(hours=1)
    code = client.get(
        f"/api/v1/m1/admin/rooms/{data['room']}/check-in-code",
        headers=token_headers(data["users"]["admin"]),
    ).json()["code"]
    url = f"/api/v1/m1/reservations/{rid}/check-in"
    monkeypatch.setattr(
        "app.modules.m1.checkin.precise_utcnow", lambda: now - timedelta(minutes=15, microseconds=1)
    )
    assert client.post(url, headers=sh, json={"code": code}).status_code == 409
    monkeypatch.setattr(
        "app.modules.m1.checkin.precise_utcnow", lambda: now + timedelta(minutes=15)
    )
    assert client.post(url, headers=sh, json={"code": code}).status_code == 409
    monkeypatch.undo()
    monkeypatch.setenv("M1_CHECKIN_SECRET", "test-only-checkin-secret-at-least-32-characters")
    get_settings.cache_clear()
    assert client.post(url, headers=sh, json={"code": code}).status_code == 200


def test_rotate_permission_migration_preserves_existing_revocations(data, factory):
    from alembic.config import Config
    from sqlalchemy import delete

    from alembic import command
    from app.modules.auth.models import RolePermission
    from app.modules.auth.permissions import seed_role_permissions

    cfg = Config("alembic.ini")
    command.downgrade(cfg, "0009_m1_source_sequence")
    try:
        with factory() as session, session.begin():
            session.execute(
                delete(RolePermission).where(
                    RolePermission.role_code == "ROOM_ADMIN",
                    RolePermission.permission_code.in_(
                        ["m1:checkin_code:rotate", "m1:checkin_code:read"]
                    ),
                )
            )
        command.upgrade(cfg, "head")
        with factory() as session, session.begin():
            seed_role_permissions(session)
            rows = list(
                session.scalars(
                    select(RolePermission.permission_code).where(
                        RolePermission.role_code == "ROOM_ADMIN",
                        RolePermission.permission_code.in_(
                            ["m1:checkin_code:read", "m1:checkin_code:rotate"]
                        ),
                    )
                )
            )
            assert rows == []
    finally:
        command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def explicitly_grant_rotation_for_checkin_tests(data, factory):
    from app.modules.auth.models import RolePermission

    with factory() as session, session.begin():
        session.add(
            RolePermission(role_code="ROOM_ADMIN", permission_code="m1:checkin_code:rotate")
        )
