"""Iteration 1: M3 maintenance work orders and request idempotency."""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "0002_m3_work_orders"
down_revision = "0001_public_layer"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "m3_work_orders",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("reporter_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("room_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="REPORTED", nullable=False),
        sa.Column("assignee_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("assigned_by", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("assigned_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("started_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("completion_note", sa.Text(), nullable=True),
        sa.Column("submitted_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("completion_round", mysql.INTEGER(unsigned=True), server_default="0", nullable=False),
        sa.Column("accepted_by", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("accepted_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("acceptance_comment", sa.String(length=500), nullable=True),
        sa.Column("request_key", mysql.CHAR(length=36), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.CheckConstraint(
            "category IN ('AIR_CONDITIONING','LIGHTING','POWER_SOCKET','FURNITURE','OTHER')",
            name="ck_m3_work_order_category",
        ),
        sa.CheckConstraint(
            "status IN ('REPORTED','ASSIGNED','REPAIRING','WAIT_ACCEPTANCE','COMPLETED')",
            name="ck_m3_work_order_status",
        ),
        sa.CheckConstraint("completion_round >= 0", name="ck_m3_work_order_round"),
        sa.ForeignKeyConstraint(["reporter_id"], ["d_users.id"]),
        sa.ForeignKeyConstraint(["room_id"], ["d_rooms.id"]),
        sa.ForeignKeyConstraint(["assignee_id"], ["d_users.id"]),
        sa.ForeignKeyConstraint(["assigned_by"], ["d_users.id"]),
        sa.ForeignKeyConstraint(["accepted_by"], ["d_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reporter_id", "request_key", name="uq_m3_work_order_reporter_request"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_m3_work_order_reporter_created", "m3_work_orders", ["reporter_id", "created_at"]
    )
    op.create_index(
        "ix_m3_work_order_assignee_status", "m3_work_orders", ["assignee_id", "status"]
    )
    op.create_index("ix_m3_work_order_room_status", "m3_work_orders", ["room_id", "status"])


def downgrade():
    op.drop_table("m3_work_orders")
