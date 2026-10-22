"""D revocable sessions, multiple identities and scoped grants; legacy JWTs expire at upgrade."""

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "0004_auth_foundation"
down_revision = "0003_m3_extensions"
branch_labels = None
depends_on = None
OPTIONS = {"mysql_charset": "utf8mb4", "mysql_engine": "InnoDB"}


def upgrade():
    op.create_index("ix_identity_user", "d_user_identities", ["user_id"])
    connection = op.get_bind()
    for index in sa.inspect(connection).get_unique_constraints("d_user_identities"):
        if index["column_names"] == ["user_id"]:
            op.drop_index(index["name"], table_name="d_user_identities")
    op.create_unique_constraint("uq_identity_user", "d_user_identities", ["id", "user_id"])
    op.add_column(
        "d_user_roles",
        sa.Column("scope_type", sa.String(16), server_default="GLOBAL", nullable=False),
    )
    op.add_column(
        "d_user_roles", sa.Column("scope_value", sa.String(64), server_default="*", nullable=False)
    )
    op.create_index("ix_role_user", "d_user_roles", ["user_id"])
    op.drop_index("uq_user_role", table_name="d_user_roles")
    op.create_unique_constraint(
        "uq_user_role_scope", "d_user_roles", ["user_id", "role_code", "scope_type", "scope_value"]
    )
    op.create_check_constraint(
        "ck_role_scope", "d_user_roles", "scope_type IN ('GLOBAL','ROOM','DEPARTMENT','BUILDING')"
    )
    op.create_table(
        "d_role_permissions",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True),
        sa.Column("role_code", sa.String(32), nullable=False),
        sa.Column("permission_code", sa.String(64), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.UniqueConstraint("role_code", "permission_code", name="uq_role_permission"),
        **OPTIONS,
    )
    permissions = {
        "USER": [
            "m1:create",
            "m1:cancel",
            "m2:create",
            "m3:create",
            "m1:seat:search",
            "m1:reservation:create",
            "m1:reservation:read_own",
            "m1:reservation:cancel_own",
            "m1:reservation:checkin",
            "m1:violation:read_own",
        ],
        "ROOM_ADMIN": [
            "m2:approve",
            "m2:release",
            "users:read",
            "resources:manage",
            "m1:reservation:read_scoped",
            "m1:statistics:read",
            "m1:checkin_code:read",
            "m1:settings:write",
        ],
        "PROPERTY_ADMIN": ["m3:assign", "m3:accept", "users:read", "resources:manage"],
        "REPAIRER": ["m3:start", "m3:complete"],
        "SYS_ADMIN": ["users:read", "resources:manage", "auth:manage"],
        "_SYSTEM": ["_INITIALIZED"],
    }
    table = sa.table(
        "d_role_permissions",
        sa.column("role_code"),
        sa.column("permission_code"),
        sa.column("created_at"),
    )
    op.bulk_insert(
        table,
        [
            {
                "role_code": role,
                "permission_code": permission,
                "created_at": datetime.now(UTC).replace(tzinfo=None),
            }
            for role, values in permissions.items()
            for permission in values
        ],
    )
    op.create_table(
        "d_auth_sessions",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column(
            "user_id", mysql.BIGINT(unsigned=True), sa.ForeignKey("d_users.id"), nullable=False
        ),
        sa.Column(
            "identity_id",
            mysql.BIGINT(unsigned=True),
            sa.ForeignKey("d_user_identities.id"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["identity_id", "user_id"],
            ["d_user_identities.id", "d_user_identities.user_id"],
            name="fk_session_identity_owner",
        ),
        sa.Column("expires_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("revoked_at", mysql.DATETIME(fsp=3)),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        **OPTIONS,
    )
    op.create_table(
        "d_service_principals",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True),
        sa.Column("name", sa.String(64), unique=True, nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("module", sa.String(8), nullable=False),
        sa.Column("is_active", mysql.TINYINT(1), server_default="1", nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.CheckConstraint("module IN ('M1','M2','M3')", name="ck_service_module"),
        **OPTIONS,
    )
    op.create_table(
        "d_service_room_scopes",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True),
        sa.Column(
            "service_id",
            mysql.BIGINT(unsigned=True),
            sa.ForeignKey("d_service_principals.id"),
            nullable=False,
        ),
        sa.Column(
            "room_id", mysql.BIGINT(unsigned=True), sa.ForeignKey("d_rooms.id"), nullable=False
        ),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.UniqueConstraint("service_id", "room_id", name="uq_service_room_scope"),
        **OPTIONS,
    )


def downgrade():
    connection = op.get_bind()
    multiple_identities = connection.execute(
        sa.text(
            "SELECT user_id FROM d_user_identities GROUP BY user_id HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    scoped_grants = connection.execute(
        sa.text(
            "SELECT id FROM d_user_roles WHERE scope_type <> 'GLOBAL' OR scope_value <> '*' LIMIT 1"
        )
    ).first()
    if multiple_identities or scoped_grants:
        raise RuntimeError("Auth downgrade requires reviewed identity/scope data reconciliation")
    # Dropping sessions deliberately revokes credentials; old JWT credentials remain
    # a deployment rollback concern and must not be re-enabled on a public host.
    op.drop_table("d_service_room_scopes")
    op.drop_table("d_service_principals")
    op.drop_table("d_auth_sessions")
    op.drop_table("d_role_permissions")
    op.create_unique_constraint("uq_user_role", "d_user_roles", ["user_id", "role_code"])
    op.drop_index("uq_user_role_scope", table_name="d_user_roles")
    op.drop_constraint("ck_role_scope", "d_user_roles", type_="check")
    op.drop_index("ix_role_user", table_name="d_user_roles")
    op.drop_column("d_user_roles", "scope_value")
    op.drop_column("d_user_roles", "scope_type")
    op.create_unique_constraint("user_id", "d_user_identities", ["user_id"])
    op.drop_index("uq_identity_user", table_name="d_user_identities")
    op.drop_index("ix_identity_user", table_name="d_user_identities")
