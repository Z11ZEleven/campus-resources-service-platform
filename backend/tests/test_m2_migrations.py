from uuid import uuid4

import pytest
from alembic.config import Config
from conftest import token_headers
from sqlalchemy import delete, inspect, select, text
from test_m2 import (
    body,
    decide,
    first_approved,
    headers,
)
from test_m2 import (
    m2_data as m2_data,
)
from test_m2 import (
    resource_server as resource_server,
)

from alembic import command
from app.core.time import utcnow
from app.modules.auth.models import RoleScope, User, UserRole
from app.modules.m2 import service
from app.modules.m2.dispatch import dispatch_one
from app.modules.m2.models import Application, ResourceJob
from app.modules.resources.commands import ResourceCommand
from scripts.seed_m2 import seed_m2

pytestmark = pytest.mark.mysql


def test_m2_upgrade_preserves_existing_main_m3_and_public_data(client, data, mysql_engine):
    response = client.post(
        "/api/v1/m3/work-orders",
        headers=token_headers(data["users"]["student"]),
        json={
            "room_id": str(data["room"]),
            "title": "升级前的工单",
            "description": "保持工单、通知和公共数据",
            "category": "OTHER",
            "request_key": str(uuid4()),
        },
    )
    assert response.status_code == 201
    queries = {
        "users": "SELECT * FROM d_users ORDER BY id",
        "rooms": "SELECT id,code,name,department_code,capacity,open_time,close_time,is_active,resource_version,created_at,updated_at FROM d_rooms ORDER BY id",
        "orders": "SELECT * FROM m3_work_orders ORDER BY id",
        "notifications": "SELECT * FROM d_notifications ORDER BY id",
        "logs": "SELECT id,module,business_id,action,actor_id,from_status,to_status,note,created_at FROM d_operation_logs ORDER BY id",
    }

    def snapshot():
        with mysql_engine.connect() as connection:
            return {name: connection.execute(text(query)).all() for name, query in queries.items()}

    before = snapshot()
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "0003_m3_extensions")
    assert "m2_applications" not in inspect(mysql_engine).get_table_names()
    assert snapshot() == before
    command.upgrade(cfg, "head")
    command.check(cfg)
    assert snapshot() == before
    with mysql_engine.connect() as connection:
        assert set(
            connection.execute(text("SELECT booking_mode,campus_code FROM d_rooms")).all()
        ) == {("SEAT", "MAIN")}
        assert set(
            connection.execute(text("SELECT actor_type FROM d_operation_logs")).scalars()
        ) == {"USER"}


def test_rollback_guard_refuses_to_discard_new_business_or_authorization(
    factory, m2_data, mysql_engine
):
    cfg = Config("alembic.ini")
    try:
        with pytest.raises(RuntimeError, match="备份"):
            command.downgrade(cfg, "0003_m3_extensions")
    finally:
        # The guarded M2 step runs after newer M1 downgrades; restore those
        # schemas so this test cannot corrupt subsequent modules' fixtures.
        command.upgrade(cfg, "head")
    assert "m2_applications" in inspect(mysql_engine).get_table_names()
    with factory() as session:
        assert session.scalar(select(UserRole.id).where(UserRole.role_code == "CAMPUS_ADMIN"))


def test_demo_seed_does_not_reset_password_roles_scope_or_room_type(factory):
    with factory() as session, session.begin():
        seed_m2(session, "m2-test-only-password-123")
        campus = session.scalar(select(User).where(User.username == "campus_admin"))
        user_id, old_hash = campus.id, campus.password_hash
        session.execute(delete(UserRole).where(UserRole.user_id == user_id))
        scope = session.scalar(select(RoleScope).where(RoleScope.user_id == user_id))
        scope.is_active = False
    with factory() as session, session.begin():
        seed_m2(session, "another-test-only-password")
    with factory() as session:
        assert session.get(User, user_id).password_hash == old_hash
        assert session.scalar(select(UserRole.id).where(UserRole.user_id == user_id)) is None
        assert not session.scalar(select(RoleScope).where(RoleScope.user_id == user_id)).is_active


def test_notification_failure_rolls_back_application_creation(
    client, m2_data, factory, monkeypatch
):
    original = service.publish_notifications

    def fail_after_write(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected notification storage failure")

    monkeypatch.setattr(service, "publish_notifications", fail_after_write)
    response = client.post(
        "/api/v1/m2/applications", headers=headers(m2_data, "student"), json=body(m2_data)
    )
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "INTERNAL_ERROR"
    assert "injected" not in response.text
    with factory() as session:
        assert session.scalar(select(Application.id)) is None


def test_failed_local_confirmation_after_remote_success_can_recover(
    client, m2_data, factory, resource_server, monkeypatch
):
    app = first_approved(client, m2_data)
    original = service.publish_notifications

    def fail_final(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs["title"] == "教室申请已批准":
            raise RuntimeError("injected confirmation transaction failure")
        return result

    monkeypatch.setattr(service, "publish_notifications", fail_final)
    response = decide(client, m2_data, app, "room_reviewer")
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "INTERNAL_ERROR"
    assert "injected" not in response.text
    with factory() as session, session.begin():
        assert session.scalar(select(ResourceCommand.status)) == "SUCCEEDED"
        assert session.get(Application, int(app["id"])).status == "RESOURCE_CONFIRMING"
        job = session.scalar(select(ResourceJob))
        job.lease_until = job.next_attempt_at = utcnow()
    monkeypatch.setattr(service, "publish_notifications", original)
    assert dispatch_one(int(app["id"]))
    with factory() as session:
        assert session.get(Application, int(app["id"])).status == "APPROVED"


def test_m1_upgrade_preserves_m2_approval_and_m3_work_order_data(
    client, m2_data, mysql_engine
):
    # Start with real M2 first-stage approval and a real M3 report, then verify
    # every legacy business row across the main -> M1 schema upgrade.
    app = first_approved(client, m2_data)
    response = client.post(
        "/api/v1/m3/work-orders",
        headers=token_headers(m2_data["users"]["student"]),
        json={
            "room_id": str(m2_data["whole"]), "title": "M1升级保留工单",
            "description": "检查既有M2审批及M3数据", "category": "OTHER",
            "request_key": str(uuid4()),
        },
    )
    assert response.status_code == 201
    tables = (
        "m2_applications", "m2_approval_steps", "m2_application_events", "m2_resource_jobs",
        "d_resource_commands", "d_role_scopes", "m3_work_orders", "m3_work_order_ratings",
        "m3_work_order_escalations", "d_notifications", "d_users",
    )

    def snapshot():
        with mysql_engine.connect() as connection:
            result = {t: connection.execute(text(f"SELECT * FROM {t} ORDER BY id")).all() for t in tables}
            result["logs"] = connection.execute(text(
                "SELECT id,module,business_id,action,actor_id,actor_type,from_status,to_status,note,created_at "
                "FROM d_operation_logs ORDER BY id"
            )).all()
            result["roles"] = connection.execute(text(
                "SELECT id,user_id,role_code,created_at FROM d_user_roles ORDER BY id"
            )).all()
            return result

    before = snapshot()
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "0004_m2_two_stage")
    assert snapshot() == before
    command.upgrade(cfg, "head")
    command.check(cfg)
    assert snapshot() == before
    # Reauthenticate after the migration deliberately revoked old sessions.
    response = client.get(
        BASE_FOR_UPGRADE + app["id"], headers=token_headers(m2_data["users"]["student"])
    )
    assert response.status_code == 200
    assert response.json()["status"] == app["status"]


BASE_FOR_UPGRADE = "/api/v1/m2/applications/"
