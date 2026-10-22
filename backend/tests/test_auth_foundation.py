from types import SimpleNamespace

import pytest

from app.core.errors import DomainError
from app.modules.auth.permissions import Grant, Principal, room_scope_predicate


def test_role_action_and_scope_cannot_be_spliced():
    actor = Principal(
        1,
        "a",
        "A",
        "STAFF",
        "CS",
        frozenset({"ROOM_ADMIN", "USER"}),
        grants=(
            Grant("ROOM_ADMIN", frozenset({"resources:manage"}), "ROOM", "1"),
            Grant("USER", frozenset({"m1:create"}), "ROOM", "2"),
        ),
    )
    room = SimpleNamespace(id=2, department_code="CS", building_id=1)
    assert not actor.can_for_room("resources:manage", room)
    with pytest.raises(DomainError):
        actor.require_for_room("resources:manage", room)
    assert actor.can_for_room("m1:create", room)


def test_database_principal_defaults_to_deny():
    actor = Principal(1, "a", "A", "STAFF", "CS", frozenset({"SYS_ADMIN"}), grants=())
    assert not actor.has("resources:manage")
    assert str(room_scope_predicate(actor, "resources:manage")) == "false"


def test_login_cookie_csrf_logout_and_legacy_rejected(client, data):
    from app.modules.auth.security import create_token

    result = client.post(
        "/api/v1/auth/login", json={"username": "student", "password": "test-only-password-123"}
    )
    assert result.status_code == 200
    assert "HttpOnly" in result.headers["set-cookie"]
    assert client.get("/api/v1/auth/me").status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 403
    token = client.cookies["campus_session"]
    assert token not in result.text
    csrf = client.cookies["campus_csrf"]
    assert client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    assert result.json() == {"authenticated": True}
    assert (
        client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + token}).status_code
        == 401
    )
    assert (
        client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer " + create_token(data["users"]["student"].user_id)},
        ).status_code
        == 401
    )


def test_identity_switch_rejects_other_owner(client, data):
    client.post(
        "/api/v1/auth/login", json={"username": "student", "password": "test-only-password-123"}
    )
    result = client.post(
        "/api/v1/auth/identity",
        json={"identity_id": str(data["users"]["other"].identity_id)},
        headers={"X-CSRF-Token": client.cookies["campus_csrf"]},
    )
    assert result.status_code == 403


def test_role_revocation_and_identity_deactivation_apply_next_request(client, factory, data):
    from sqlalchemy import delete

    from app.modules.auth.models import UserIdentity, UserRole

    result = client.post(
        "/api/v1/auth/login", json={"username": "student", "password": "test-only-password-123"}
    )
    assert result.status_code == 200
    assert "m1:create" in client.get("/api/v1/auth/me").json()["permissions"]
    student = data["users"]["student"]
    with factory() as session, session.begin():
        session.execute(delete(UserRole).where(UserRole.user_id == student.user_id))
    assert "m1:create" not in client.get("/api/v1/auth/me").json()["permissions"]
    with factory() as session, session.begin():
        session.get(UserIdentity, student.identity_id).is_active = False
    assert client.get("/api/v1/auth/me").status_code == 401


def test_expired_session_and_service_module_binding(factory, data):
    from datetime import timedelta

    from app.core.time import utcnow
    from app.modules.auth.dependencies import authenticate_service
    from app.modules.auth.models import ServicePrincipal, ServiceRoomScope
    from app.modules.auth.sessions import credential_hash, issue_session, resolve_session

    with factory() as session, session.begin():
        token, _, row = issue_session(session, data["users"]["student"].user_id)
        row.expires_at = utcnow() - timedelta(seconds=1)
        session.flush()
        with pytest.raises(DomainError):
            resolve_session(session, token)
        service = ServicePrincipal(
            name="m2-test", token_hash=credential_hash("service-test-only"), module="M2"
        )
        session.add(service)
        session.flush()
        session.add(ServiceRoomScope(service_id=service.id, room_id=data["room"]))
        session.flush()
        assert (
            authenticate_service(session, "service-test-only", "M2", data["room"]).id == service.id
        )
        for key, module, room in [
            ("wrong", "M2", data["room"]),
            ("service-test-only", "M3", data["room"]),
            ("service-test-only", "M2", data["private_room"]),
            (token, "M2", data["room"]),
        ]:
            with pytest.raises(DomainError):
                authenticate_service(session, key, module, room)


def test_multiple_identities_are_owned_persisted_and_revalidated(client, factory, data):
    from app.modules.auth.models import UserIdentity

    student = data["users"]["student"]
    with factory() as session, session.begin():
        identity = UserIdentity(
            user_id=student.user_id,
            identity_type="TEACHER",
            identity_no="second-test-only",
            department_code="EE",
        )
        session.add(identity)
        session.flush()
        identity_id = identity.id
    client.post(
        "/api/v1/auth/login", json={"username": "student", "password": "test-only-password-123"}
    )
    assert len(client.get("/api/v1/auth/identities").json()) == 2
    result = client.post(
        "/api/v1/auth/identity",
        json={"identity_id": str(identity_id)},
        headers={"X-CSRF-Token": client.cookies["campus_csrf"]},
    )
    assert result.status_code == 200
    assert result.json()["department_code"] == "EE"
    assert client.get("/api/v1/auth/me").json()["identity_id"] == str(identity_id)


def test_permission_seed_does_not_restore_revoked_mapping(factory):
    from app.modules.auth.models import RolePermission
    from app.modules.auth.permissions import seed_role_permissions

    with factory() as session, session.begin():
        seed_role_permissions(session)
        from sqlalchemy import select

        query = select(RolePermission).where(
            RolePermission.role_code == "USER", RolePermission.permission_code == "m1:create"
        )
        mapping = session.scalar(query)
        assert mapping.id > 0 and mapping.created_at is not None
        session.delete(mapping)
        session.flush()
        seed_role_permissions(session)
        assert session.scalar(query) is None


def test_scoped_database_grants_filter_before_count(factory, data):
    from sqlalchemy import delete, func, select

    from app.modules.auth.models import UserRole
    from app.modules.auth.permissions import load_principal
    from app.modules.resources.models import Room

    user_id = data["users"]["admin"].user_id
    with factory() as session, session.begin():
        session.execute(delete(UserRole).where(UserRole.user_id == user_id))
        session.add_all(
            [
                UserRole(
                    user_id=user_id,
                    role_code="ROOM_ADMIN",
                    scope_type="ROOM",
                    scope_value=str(data["room"]),
                ),
                UserRole(
                    user_id=user_id,
                    role_code="USER",
                    scope_type="ROOM",
                    scope_value=str(data["private_room"]),
                ),
            ]
        )
        session.flush()
        actor = load_principal(session, user_id)
        assert not actor.has("resources:manage")
        assert actor.can_for_room("resources:manage", session.get(Room, data["room"]))
        assert not actor.can_for_room("resources:manage", session.get(Room, data["private_room"]))
        predicate = room_scope_predicate(actor, "resources:manage", Room)
        assert session.scalar(select(func.count()).select_from(Room).where(predicate)) == 1
        assert list(session.scalars(select(Room.id).where(predicate))) == [data["room"]]


def test_cross_origin_login_and_cookie_mutation_rejected(client):
    result = client.post(
        "/api/v1/auth/login",
        json={"username": "student", "password": "test-only-password-123"},
        headers={"Origin": "https://untrusted.example"},
    )
    assert result.status_code == 403
    client.post(
        "/api/v1/auth/login", json={"username": "student", "password": "test-only-password-123"}
    )
    result = client.post(
        "/api/v1/auth/logout",
        headers={
            "Origin": "https://untrusted.example",
            "X-CSRF-Token": client.cookies["campus_csrf"],
        },
    )
    assert result.status_code == 403


def test_service_provision_is_audited_idempotent_and_module_immutable(factory, data):
    from sqlalchemy import func, select

    from app.modules.audit.models import OperationLog
    from app.modules.auth.models import ServiceRoomScope, UserRole
    from scripts.provision_service import provision_service

    with factory() as session, session.begin():
        actor_id = data["users"]["admin"].user_id
        session.add(UserRole(user_id=actor_id, role_code="SYS_ADMIN"))
        session.flush()
        options = dict(
            actor_id=actor_id,
            name="m2-deployment",
            module="M2",
            token="deployment-test-only-token-32-chars",
            room_ids=[data["room"]],
        )
        first = provision_service(session, **options)
        assert first.id > 0 and first.created_at is not None and first.updated_at is not None
        scope = session.scalar(select(ServiceRoomScope))
        assert scope.id > 0 and scope.created_at is not None
        assert provision_service(session, **options).id == first.id
        assert session.scalar(select(func.count()).select_from(ServiceRoomScope)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(OperationLog)
                .where(OperationLog.action == "SERVICE_CONFIGURED")
            )
            == 2
        )
        with pytest.raises(ValueError, match="module binding"):
            provision_service(session, **{**options, "module": "M3"})
        with pytest.raises(ValueError, match="requires --rotate"):
            provision_service(
                session, **{**options, "token": "replacement-test-only-token-32-chars"}
            )


def test_bootstrap_admin_never_restores_removed_role(factory):
    from sqlalchemy import delete, func, select

    from app.modules.audit.models import OperationLog
    from app.modules.auth.models import UserRole
    from app.modules.auth.permissions import load_principal, seed_role_permissions
    from scripts.bootstrap_admin import bootstrap_admin

    with factory() as session, session.begin():
        seed_role_permissions(session)
        options = dict(
            username="bootstrap-test",
            display_name="管理员测试",
            password="bootstrap-test-only-password",
        )
        user = bootstrap_admin(session, **options)
        assert load_principal(session, user.id).has("auth:manage")
        session.execute(delete(UserRole).where(UserRole.user_id == user.id))
        user.is_active = False
        old_hash = user.password_hash
        session.flush()
        assert (
            bootstrap_admin(session, **{**options, "password": "other-test-only-password"}).id
            == user.id
        )
        assert user.password_hash == old_hash
        assert not user.is_active
        assert session.scalar(select(func.count()).select_from(UserRole)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(OperationLog)
                .where(OperationLog.action == "ADMIN_BOOTSTRAPPED")
            )
            == 1
        )


def test_admin_grant_scope_and_revoke_are_audited(client, factory, data):
    from conftest import token_headers
    from sqlalchemy import func, select

    from app.modules.audit.models import OperationLog
    from app.modules.auth.models import UserRole

    admin = data["users"]["admin"]
    target = data["users"]["student"]
    path = f"/api/v1/auth/users/{target.user_id}/roles"
    body = {"role_code": "ROOM_ADMIN", "scope_type": "ROOM", "scope_value": str(data["room"])}
    assert client.put(path, headers=token_headers(target), json=body).status_code == 403
    with factory() as session, session.begin():
        session.add(UserRole(user_id=admin.user_id, role_code="SYS_ADMIN"))
    headers = token_headers(admin)
    response = client.put(path, headers=headers, json=body)
    assert response.status_code == 200
    grant_id = response.json()["id"]
    assert client.put(path, headers=headers, json=body).json()["id"] == grant_id
    with factory() as session, session.begin():
        assert (
            session.scalar(
                select(func.count())
                .select_from(OperationLog)
                .where(OperationLog.action == "ROLE_GRANTED")
            )
            == 1
        )
    assert client.delete(f"{path}/{grant_id}", headers=headers).status_code == 200
    with factory() as session, session.begin():
        assert session.get(UserRole, int(grant_id)) is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(OperationLog)
                .where(OperationLog.action == "ROLE_REVOKED")
            )
            == 1
        )


def test_m1_named_permissions_are_persisted_revocable_and_scope_bound(factory, data):
    from sqlalchemy import select

    from app.modules.auth.models import RolePermission, UserRole
    from app.modules.auth.permissions import load_principal, seed_role_permissions
    from app.modules.resources.models import Room

    with factory() as session, session.begin():
        student = load_principal(session, data["users"]["student"].user_id)
        for action in [
            "m1:seat:search",
            "m1:reservation:create",
            "m1:reservation:read_own",
            "m1:reservation:cancel_own",
            "m1:reservation:checkin",
            "m1:violation:read_own",
        ]:
            assert student.has(action)
        actor_id = data["users"]["other"].user_id
        session.add(
            UserRole(
                user_id=actor_id,
                role_code="ROOM_ADMIN",
                scope_type="ROOM",
                scope_value=str(data["room"]),
            )
        )
        session.flush()
        admin = load_principal(session, actor_id)
        for action in ["m1:reservation:read_scoped", "m1:statistics:read", "m1:checkin_code:read"]:
            assert admin.can_for_room(action, session.get(Room, data["room"]))
            assert not admin.can_for_room(action, session.get(Room, data["private_room"]))
        assert not admin.has("m1:settings:write")
        mapping = session.scalar(
            select(RolePermission).where(
                RolePermission.role_code == "USER",
                RolePermission.permission_code == "m1:reservation:checkin",
            )
        )
        session.delete(mapping)
        session.flush()
        seed_role_permissions(session)
        assert not load_principal(session, student.user_id).has("m1:reservation:checkin")
