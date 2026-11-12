import subprocess
import sys

import pytest
from alembic.config import Config
from sqlalchemy import select

from alembic import command
from app.core.errors import DomainError
from app.modules.audit.models import OperationLog
from app.modules.auth.models import RolePermission, User
from scripts.bootstrap_admin import bootstrap_admin
from scripts.manage_m1_rotation import set_rotation_permission
from scripts.seed import seed


def test_fresh_migration_seed_and_explicit_authorization(factory):
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "base")
    try:
        command.upgrade(cfg, "head")
        with factory() as session, session.begin():
            seed(session, "test-seed-password-123")
            assert not session.scalar(
                select(RolePermission).where(
                    RolePermission.role_code == "ROOM_ADMIN",
                    RolePermission.permission_code == "m1:checkin_code:rotate",
                )
            )
            admin = bootstrap_admin(
                session,
                username="operator",
                display_name="Operator",
                password="test-bootstrap-password",
            )
            actor_id = admin.id
            student_id = session.scalar(select(User.id).where(User.username == "student"))
        with pytest.raises(DomainError):
            with factory() as session, session.begin():
                set_rotation_permission(session, actor_id=student_id, grant=True)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.manage_m1_rotation",
                "--actor-id",
                str(actor_id),
                "grant",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        with factory() as session, session.begin():
            assert not set_rotation_permission(session, actor_id=actor_id, grant=True)
            assert set_rotation_permission(session, actor_id=actor_id, grant=False)
        with factory() as session, session.begin():
            seed(session, "test-seed-password-123")
            assert not session.scalar(
                select(RolePermission).where(
                    RolePermission.role_code == "ROOM_ADMIN",
                    RolePermission.permission_code == "m1:checkin_code:rotate",
                )
            )
            assert [
                r.action
                for r in session.scalars(
                    select(OperationLog)
                    .where(
                        OperationLog.action.in_(
                            ["M1_ROTATE_PERMISSION_GRANTED", "M1_ROTATE_PERMISSION_REVOKED"]
                        )
                    )
                    .order_by(OperationLog.id)
                )
            ] == ["M1_ROTATE_PERMISSION_GRANTED", "M1_ROTATE_PERMISSION_REVOKED"]
    finally:
        command.upgrade(cfg, "head")
