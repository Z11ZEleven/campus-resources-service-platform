"""Public outbox and controlled audit receiving."""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "0007_outbox_audit"
down_revision = "0006_m1_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "d_outbox_events",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("source_module", sa.String(length=8), nullable=False),
        sa.Column("event_key", sa.String(length=160), nullable=False),
        sa.Column("handler", sa.String(length=40), nullable=False),
        sa.Column("business_id", mysql.BIGINT(unsigned=True), server_default="0", nullable=False),
        sa.Column(
            "business_version",
            mysql.BIGINT(unsigned=True),
            server_default="1",
            nullable=False,
        ),
        sa.Column("payload", mysql.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("available_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("attempts", mysql.INTEGER(unsigned=True), server_default="0", nullable=False),
        sa.Column("max_attempts", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_token", sa.String(length=36), nullable=True),
        sa.Column("lease_expires_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("version", mysql.BIGINT(unsigned=True), server_default="1", nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("completed_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.CheckConstraint("source_module IN ('D','M1','M2','M3')", name="ck_outbox_source"),
        sa.CheckConstraint("CHAR_LENGTH(event_key) > 0", name="ck_outbox_event_key"),
        sa.CheckConstraint("CHAR_LENGTH(handler) > 0", name="ck_outbox_handler"),
        sa.CheckConstraint(
            "status IN ('PENDING','PROCESSING','SUCCEEDED','MANUAL')",
            name="ck_outbox_status",
        ),
        sa.CheckConstraint("business_version > 0", name="ck_outbox_business_version"),
        sa.CheckConstraint("attempts >= 0", name="ck_outbox_attempts"),
        sa.CheckConstraint(
            "max_attempts IS NULL OR max_attempts > 0",
            name="ck_outbox_max_attempts",
        ),
        sa.CheckConstraint("version > 0", name="ck_outbox_version"),
        sa.CheckConstraint(
            "(status = 'PROCESSING' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'PROCESSING' AND lease_owner IS NULL AND lease_token IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_outbox_lease",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_module", "event_key", name="uq_outbox_source_event"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_outbox_due", "d_outbox_events", ["status", "available_at", "id"], unique=False
    )
    op.create_index(
        "ix_outbox_lease", "d_outbox_events", ["status", "lease_expires_at"], unique=False
    )

    op.add_column(
        "d_operation_logs",
        sa.Column("actor_type", sa.String(length=8), server_default="USER", nullable=False),
    )
    op.add_column(
        "d_operation_logs",
        sa.Column("service_principal_id", mysql.BIGINT(unsigned=True), nullable=True),
    )
    op.add_column(
        "d_operation_logs",
        sa.Column("result", sa.String(length=16), server_default="SUCCESS", nullable=False),
    )
    op.add_column(
        "d_operation_logs",
        sa.Column("event_key", sa.String(length=160), nullable=True),
    )
    op.alter_column(
        "d_operation_logs",
        "actor_id",
        existing_type=mysql.BIGINT(unsigned=True),
        nullable=True,
    )
    op.create_foreign_key(
        "fk_log_service_principal",
        "d_operation_logs",
        "d_service_principals",
        ["service_principal_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_log_module_event", "d_operation_logs", ["module", "event_key"]
    )
    op.create_check_constraint(
        "ck_log_actor_type",
        "d_operation_logs",
        "actor_type IN ('USER','SERVICE','SYSTEM')",
    )
    op.create_check_constraint(
        "ck_log_actor",
        "d_operation_logs",
        "(actor_type = 'USER' AND actor_id IS NOT NULL AND service_principal_id IS NULL) OR "
        "(actor_type = 'SERVICE' AND actor_id IS NULL AND service_principal_id IS NOT NULL) OR "
        "(actor_type = 'SYSTEM' AND actor_id IS NULL AND service_principal_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_log_result",
        "d_operation_logs",
        "result IN ('SUCCESS','FAILURE')",
    )


def downgrade():
    op.drop_constraint("ck_log_result", "d_operation_logs", type_="check")
    op.drop_constraint("ck_log_actor", "d_operation_logs", type_="check")
    op.drop_constraint("ck_log_actor_type", "d_operation_logs", type_="check")
    op.drop_constraint("uq_log_module_event", "d_operation_logs", type_="unique")
    op.drop_constraint(
        "fk_log_service_principal", "d_operation_logs", type_="foreignkey"
    )
    op.drop_column("d_operation_logs", "event_key")
    op.drop_column("d_operation_logs", "result")
    op.drop_column("d_operation_logs", "service_principal_id")
    op.drop_column("d_operation_logs", "actor_type")
    op.alter_column(
        "d_operation_logs",
        "actor_id",
        existing_type=mysql.BIGINT(unsigned=True),
        nullable=False,
    )
    op.drop_index("ix_outbox_lease", table_name="d_outbox_events")
    op.drop_index("ix_outbox_due", table_name="d_outbox_events")
    op.drop_table("d_outbox_events")
