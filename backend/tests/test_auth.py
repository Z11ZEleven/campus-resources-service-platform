import pytest
from conftest import token_headers
from sqlalchemy import delete, select

from app.modules.auth.models import User, UserIdentity, UserRole

pytestmark = pytest.mark.mysql


def test_login_me_and_no_password_leak(client, data):
    result = client.post(
        "/api/v1/auth/login",
        json={
            "username": " STUDENT ",
            "password": "test-only-password-123",
        },
    )
    assert result.status_code == 200
    assert result.json() == {"authenticated": True}
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["id"] == str(data["users"]["student"].user_id)
    assert me.json()["roles"] == ["USER"]
    assert "password_hash" not in me.text


def test_wrong_password_unknown_user_and_unknown_fields(client):
    for username in ("student", "missing"):
        result = client.post(
            "/api/v1/auth/login",
            json={
                "username": username,
                "password": "wrong-password",
            },
        )
        assert result.status_code == 401
        assert result.json()["detail"]["code"] == "INVALID_CREDENTIALS"
    assert (
        client.post(
            "/api/v1/auth/login",
            json={
                "username": "student",
                "password": "x",
                "role_code": "SYS_ADMIN",
            },
        ).status_code
        == 422
    )


def test_missing_bad_expired_token(client, data):
    from datetime import UTC, datetime, timedelta

    import jwt

    from app.core.config import get_settings

    config = get_settings()
    expired = jwt.encode(
        {
            "sub": str(data["users"]["student"].user_id),
            "iat": datetime.now(UTC) - timedelta(hours=2),
            "exp": datetime.now(UTC) - timedelta(hours=1),
            "iss": config.jwt_issuer,
            "aud": config.jwt_audience,
        },
        config.jwt_signing_key.get_secret_value(),
        algorithm="HS256",
    )
    assert client.get("/api/v1/auth/me").status_code == 401
    for value in ("bad-token", expired):
        assert (
            client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + value}).status_code
            == 401
        )


def test_user_disable_and_role_revocation_apply_to_existing_token(client, data, factory):
    actor = data["users"]["admin"]
    headers = token_headers(actor)
    assert client.get("/api/v1/auth/users?role_code=REPAIRER", headers=headers).status_code == 200
    with factory() as session, session.begin():
        session.execute(delete(UserRole).where(UserRole.user_id == actor.user_id))
    assert client.get("/api/v1/auth/users", headers=headers).status_code == 403
    with factory() as session, session.begin():
        session.get(User, actor.user_id).is_active = False
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_inactive_identity_and_user_list_authorization(client, data, factory):
    headers = token_headers(data["users"]["student"])
    assert client.get("/api/v1/auth/users", headers=headers).status_code == 403
    with factory() as session, session.begin():
        identity = session.scalar(
            select(UserIdentity).where(UserIdentity.user_id == data["users"]["student"].user_id)
        )
        identity.is_active = False
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
