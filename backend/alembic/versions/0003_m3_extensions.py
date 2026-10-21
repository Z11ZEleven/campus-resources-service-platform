"""M3 timeout escalation, immutable rating, and D in-app notifications."""
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "0003_m3_extensions"
down_revision = "0002_m3_work_orders"
branch_labels = None
depends_on = None

OPTIONS = {"mysql_charset": "utf8mb4", "mysql_engine": "InnoDB"}


def upgrade():
    op.add_column("m3_work_orders", sa.Column("stage_started_at", mysql.DATETIME(fsp=3), nullable=True))
    op.add_column("m3_work_orders", sa.Column("escalation_count", mysql.INTEGER(unsigned=True), server_default="0", nullable=False))
    # Before this extension, updated_at records the last real state transition.
    op.execute("UPDATE m3_work_orders SET stage_started_at = updated_at")
    op.alter_column("m3_work_orders", "stage_started_at", existing_type=mysql.DATETIME(fsp=3), nullable=False)
    op.create_check_constraint("ck_m3_work_order_escalation_count", "m3_work_orders", "escalation_count >= 0")
    op.create_index("ix_m3_work_order_stage_started", "m3_work_orders", ["status", "stage_started_at"])
    op.create_table(
        "m3_work_order_ratings",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("work_order_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("reporter_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("score", mysql.TINYINT(unsigned=True), nullable=False),
        sa.Column("comment", sa.String(500), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["work_order_id"], ["m3_work_orders.id"]),
        sa.ForeignKeyConstraint(["reporter_id"], ["d_users.id"]),
        sa.UniqueConstraint("work_order_id", name="uq_m3_rating_work_order"),
        sa.CheckConstraint("score BETWEEN 1 AND 5", name="ck_m3_rating_score"),
        **OPTIONS,
    )
    op.create_table(
        "m3_work_order_escalations",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("work_order_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("stage", sa.String(24), nullable=False),
        sa.Column("completion_round", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("stage_started_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("due_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("escalated_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("triggered_by", mysql.BIGINT(unsigned=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["work_order_id"], ["m3_work_orders.id"]),
        sa.ForeignKeyConstraint(["triggered_by"], ["d_users.id"]),
        sa.CheckConstraint("stage IN ('REPORTED','ASSIGNED','REPAIRING','WAIT_ACCEPTANCE')", name="ck_m3_escalation_stage"),
        sa.CheckConstraint("completion_round >= 0", name="ck_m3_escalation_round"),
        sa.UniqueConstraint("work_order_id", "stage", "completion_round", name="uq_m3_escalation_stage_round"),
        **OPTIONS,
    )
    op.create_index("ix_m3_escalation_order_created", "m3_work_order_escalations", ["work_order_id", "escalated_at"])
    op.create_table(
        "d_notifications",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("recipient_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("source_module", sa.String(8), nullable=False),
        sa.Column("business_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("event_key", sa.String(160), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("content", sa.String(1000), nullable=False),
        sa.Column("link", sa.String(200), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("read_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["recipient_id"], ["d_users.id"]),
        sa.CheckConstraint("source_module IN ('D','M1','M2','M3')", name="ck_notification_module"),
        sa.UniqueConstraint("recipient_id", "event_key", name="uq_notification_recipient_event"),
        **OPTIONS,
    )
    op.create_index("ix_notification_recipient_read_created", "d_notifications", ["recipient_id", "read_at", "created_at"])


def downgrade():
    op.drop_table("d_notifications")
    op.drop_table("m3_work_order_escalations")
    op.drop_table("m3_work_order_ratings")
    op.drop_index("ix_m3_work_order_stage_started", table_name="m3_work_orders")
    op.drop_constraint("ck_m3_work_order_escalation_count", "m3_work_orders", type_="check")
    op.drop_column("m3_work_orders", "escalation_count")
    op.drop_column("m3_work_orders", "stage_started_at")
