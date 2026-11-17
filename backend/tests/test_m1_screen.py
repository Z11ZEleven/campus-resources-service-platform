from datetime import timedelta

from conftest import token_headers

from app.core.time import utcnow
from app.modules.auth.models import User, UserIdentity, UserRole
from app.modules.auth.permissions import load_principal
from app.modules.auth.sessions import issue_session


def _screen_principal(factory, password_hash, room_id):
    with factory() as session, session.begin():
        user = User(
            username="screen",
            password_hash=password_hash,
            display_name="screen",
            is_active=True,
        )
        session.add(user)
        session.flush()
        session.add(
            UserIdentity(
                user_id=user.id,
                identity_type="STAFF",
                identity_no="SCREEN-1",
                department_code="OPS",
                is_active=True,
            )
        )
        session.add(
            UserRole(
                user_id=user.id,
                role_code="SCREEN",
                scope_type="ROOM",
                scope_value=str(room_id),
            )
        )
        session.flush()
        return load_principal(session, user.id)


def test_screen_code_matches_admin_and_respects_scope(
    client, data, factory, password_hash, monkeypatch
):
    monkeypatch.setenv("M1_CHECKIN_SECRET", "test-only-checkin-secret-at-least-32-characters")
    from app.core.config import get_settings

    get_settings.cache_clear()
    screen = _screen_principal(factory, password_hash, data["room"])
    headers = token_headers(screen)
    url = f"/api/v1/m1/screen/rooms/{data['room']}/code"

    response = client.get(url, headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert len(body["code"]) == 8 and body["code"].isdigit()
    assert body["room"]["id"] == str(data["room"])
    assert body["checkin_path"] == f"/m1/checkin?room={data['room']}&code={body['code']}"

    admin = client.get(
        f"/api/v1/m1/admin/rooms/{data['room']}/check-in-code",
        headers=token_headers(data["users"]["admin"]),
    )
    assert admin.status_code == 200 and admin.json()["code"] == body["code"]

    out_of_scope = client.get(
        f"/api/v1/m1/screen/rooms/{data['private_room']}/code", headers=headers
    )
    assert out_of_scope.status_code == 403
    assert (
        client.get(url, headers=token_headers(data["users"]["student"])).status_code == 403
    )


def test_screen_role_gets_long_lived_session(data, factory, password_hash):
    screen = _screen_principal(factory, password_hash, data["room"])
    with factory() as session, session.begin():
        _, _, row = issue_session(session, screen.user_id, screen.identity_id)
        assert row.expires_at - utcnow() > timedelta(days=29)
