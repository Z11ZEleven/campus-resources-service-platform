"""m2_two_stage

Revision ID: 0004_m2_two_stage
Revises: 0003_m3_extensions
"""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "0004_m2_two_stage"
down_revision = "0003_m3_extensions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "d_role_scopes",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("role_code", sa.String(length=32), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_key", sa.String(length=64), nullable=False),
        sa.Column("is_active", mysql.TINYINT(display_width=1), server_default="1", nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.CheckConstraint(
            "role_code IN ('DEPT_ADMIN','CAMPUS_ADMIN','ROOM_ADMIN')", name="ck_scope_role"
        ),
        sa.CheckConstraint("scope_type IN ('DEPARTMENT','CAMPUS','ROOM')", name="ck_scope_type"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["d_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "role_code", "scope_type", "scope_key", name="uq_role_scope"
        ),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "d_resource_commands",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("source_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("revision", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("room_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("applicant_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("actor_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("start_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("end_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("expires_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("resource_hold_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("reason_code", sa.String(length=40), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.CheckConstraint(
            "status IN ('PROCESSING','SUCCEEDED','REJECTED','CANCELLED')",
            name="ck_resource_command_status",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["d_users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["applicant_id"],
            ["d_users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["resource_hold_id"],
            ["d_resource_blocks.id"],
        ),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["d_rooms.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", name="uq_resource_command_source"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "m2_applications",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("applicant_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("applicant_identity_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("identity_type", sa.String(length=20), nullable=False),
        sa.Column("department_code", sa.String(length=32), nullable=False),
        sa.Column("room_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("start_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("end_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_level", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("step_deadline_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column(
            "resource_sync_status", sa.String(length=24), server_default="NONE", nullable=False
        ),
        sa.Column("resource_hold_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("final_reason", sa.String(length=1000), nullable=True),
        sa.Column("finalized_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("withdrawn_request_key", mysql.CHAR(length=36), nullable=True),
        sa.Column("request_key", mysql.CHAR(length=36), nullable=False),
        sa.Column("version", mysql.BIGINT(unsigned=True), server_default="1", nullable=False),
        sa.Column("submitted_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.CheckConstraint(
            "status <> 'APPROVED' OR (resource_hold_id IS NOT NULL AND resource_sync_status='SUCCEEDED')",
            name="ck_m2_approved_hold",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_FIRST_APPROVAL','PENDING_ROOM_APPROVAL','RESOURCE_CONFIRMING','APPROVED','REJECTED','WITHDRAWN','AUTO_CANCELLED')",
            name="ck_m2_status",
        ),
        sa.CheckConstraint(
            "start_at < end_at AND TIMESTAMPDIFF(SECOND,start_at,end_at) BETWEEN 3600 AND 14400",
            name="ck_m2_period",
        ),
        sa.CheckConstraint("version > 0", name="ck_m2_version"),
        sa.ForeignKeyConstraint(
            ["applicant_id"],
            ["d_users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["applicant_identity_id"],
            ["d_user_identities.id"],
        ),
        sa.ForeignKeyConstraint(
            ["resource_hold_id"],
            ["d_resource_blocks.id"],
        ),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["d_rooms.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("applicant_id", "request_key", name="uq_m2_applicant_key"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_m2_owner_created", "m2_applications", ["applicant_id", "created_at"], unique=False
    )
    op.create_index("ix_m2_room_start", "m2_applications", ["room_id", "start_at"], unique=False)
    op.create_index(
        "ix_m2_status_deadline", "m2_applications", ["status", "step_deadline_at"], unique=False
    )
    op.create_table(
        "m2_application_events",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("application_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("actor_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column("version", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["d_users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["m2_applications.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "version", "event_type", name="uq_m2_event"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "m2_approval_steps",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("application_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("level", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("required_role", sa.String(length=32), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("deadline_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("decided_by", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("decision_reason", sa.String(length=500), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=True),
        sa.Column("decided_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("request_key", mysql.CHAR(length=36), nullable=True),
        sa.Column("version", mysql.BIGINT(unsigned=True), server_default="1", nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING','CONFIRMING','APPROVED','REJECTED','CANCELLED','EXPIRED')",
            name="ck_m2_step_status",
        ),
        sa.CheckConstraint("level IN (1,2)", name="ck_m2_step_level"),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["m2_applications.id"],
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["d_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "level", name="uq_m2_step_level"),
        sa.UniqueConstraint("decided_by", "request_key", name="uq_m2_review_key"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "m2_resource_jobs",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("application_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("action", sa.String(length=16), server_default="ACQUIRE", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("attempts", mysql.INTEGER(unsigned=True), server_default="0", nullable=False),
        sa.Column("next_attempt_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("lease_until", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("lease_token", mysql.CHAR(length=36), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["m2_applications.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.add_column(
        "d_operation_logs",
        sa.Column("actor_type", sa.String(length=16), server_default="USER", nullable=False),
    )
    op.alter_column(
        "d_operation_logs", "actor_id", existing_type=mysql.BIGINT(unsigned=True), nullable=True
    )
    op.add_column(
        "d_rooms",
        sa.Column("booking_mode", sa.String(length=16), server_default="SEAT", nullable=False),
    )
    op.add_column(
        "d_rooms",
        sa.Column("campus_code", sa.String(length=32), server_default="MAIN", nullable=False),
    )

    op.drop_constraint("ck_role_code", "d_user_roles", type_="check")
    op.create_check_constraint(
        "ck_role_code",
        "d_user_roles",
        "role_code IN ('USER','ROOM_ADMIN','PROPERTY_ADMIN','REPAIRER','SYS_ADMIN','DEPT_ADMIN','CAMPUS_ADMIN')",
    )
    op.create_check_constraint(
        "ck_room_booking_mode", "d_rooms", "booking_mode IN ('SEAT','WHOLE_ROOM')"
    )


def downgrade():
    connection = op.get_bind()
    # 不把整室教室静默转换为个人座位，也不丢失新角色和系统审计数据。
    guards = [
        "SELECT COUNT(*) FROM m2_applications",
        "SELECT COUNT(*) FROM d_resource_commands",
        "SELECT COUNT(*) FROM d_role_scopes",
        "SELECT COUNT(*) FROM d_rooms WHERE booking_mode='WHOLE_ROOM'",
        "SELECT COUNT(*) FROM d_user_roles WHERE role_code IN ('DEPT_ADMIN','CAMPUS_ADMIN')",
        "SELECT COUNT(*) FROM d_operation_logs WHERE actor_id IS NULL",
    ]
    if any(connection.scalar(sa.text(query)) for query in guards):
        raise RuntimeError("M2/D 扩展已有业务或权限数据，须先备份并制定数据回退方案")
    op.drop_table("m2_resource_jobs")
    op.drop_table("m2_approval_steps")
    op.drop_table("m2_application_events")
    op.drop_table("m2_applications")
    op.drop_table("d_resource_commands")
    op.drop_table("d_role_scopes")
    op.drop_constraint("ck_room_booking_mode", "d_rooms", type_="check")
    op.drop_column("d_rooms", "campus_code")
    op.drop_column("d_rooms", "booking_mode")
    op.alter_column(
        "d_operation_logs", "actor_id", existing_type=mysql.BIGINT(unsigned=True), nullable=False
    )
    op.drop_column("d_operation_logs", "actor_type")
    op.drop_constraint("ck_role_code", "d_user_roles", type_="check")
    op.create_check_constraint(
        "ck_role_code",
        "d_user_roles",
        "role_code IN ('USER','ROOM_ADMIN','PROPERTY_ADMIN','REPAIRER','SYS_ADMIN')",
    )
