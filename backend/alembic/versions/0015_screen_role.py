"""Add the SCREEN role used by classroom check-in displays."""

import sqlalchemy as sa

from alembic import op

revision = "0015_screen_role"
down_revision = "0014_service_operation_scope"
branch_labels = None
depends_on = None

OLD_ROLES = (
    "role_code IN ('USER','ROOM_ADMIN','PROPERTY_ADMIN','REPAIRER',"
    "'SYS_ADMIN','DEPT_ADMIN','CAMPUS_ADMIN')"
)
NEW_ROLES = (
    "role_code IN ('USER','ROOM_ADMIN','PROPERTY_ADMIN','REPAIRER',"
    "'SYS_ADMIN','DEPT_ADMIN','CAMPUS_ADMIN','SCREEN')"
)


def upgrade():
    checks = sa.inspect(op.get_bind()).get_check_constraints("d_user_roles")
    drop = "DROP CHECK ck_role_code, " if any(c["name"] == "ck_role_code" for c in checks) else ""
    # One atomic statement avoids leaving d_user_roles unconstrained mid-migration.
    op.execute(
        sa.text(
            "ALTER TABLE d_user_roles " + drop + "ADD CONSTRAINT ck_role_code CHECK (" + NEW_ROLES + ")"
        )
    )
    op.execute(
        sa.text(
            "INSERT IGNORE INTO d_role_permissions (role_code, permission_code, created_at) "
            "VALUES ('SCREEN', 'm1:checkin_code:read', UTC_TIMESTAMP(3))"
        )
    )


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM d_user_roles WHERE role_code = 'SCREEN'")).scalar():
        raise RuntimeError("Cannot downgrade while SCREEN role grants exist")
    op.execute(
        sa.text(
            "DELETE FROM d_role_permissions "
            "WHERE role_code = 'SCREEN' AND permission_code = 'm1:checkin_code:read'"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE d_user_roles DROP CHECK ck_role_code, "
            "ADD CONSTRAINT ck_role_code CHECK (" + OLD_ROLES + ")"
        )
    )
