"""M2 首次迭代申请表；追加在已有 M3/D 迁移之后。

Revision ID: 0004_m2_applications
Revises: 0003_m3_extensions
"""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "0004_m2_applications"
down_revision = "0003_m3_extensions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "m2_applications",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("applicant_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("room_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("participant_count", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("start_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("end_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("reviewer_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("reviewed_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("review_comment", sa.String(length=500), nullable=True),
        sa.Column("cancelled_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("request_key", mysql.CHAR(length=36), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.CheckConstraint(
            "(status = 'PENDING' AND reviewer_id IS NULL AND reviewed_at IS NULL AND cancelled_at IS NULL) OR (status IN ('APPROVED', 'REJECTED') AND reviewer_id IS NOT NULL AND reviewed_at IS NOT NULL AND cancelled_at IS NULL) OR (status = 'CANCELLED' AND reviewer_id IS NULL AND reviewed_at IS NULL AND cancelled_at IS NOT NULL)",
            name="ck_m2_state",
        ),
        sa.CheckConstraint(
            "status <> 'REJECTED' OR (review_comment IS NOT NULL AND CHAR_LENGTH(TRIM(review_comment)) > 0)",
            name="ck_m2_reject_reason",
        ),
        sa.CheckConstraint("participant_count > 0", name="ck_m2_participants"),
        sa.CheckConstraint(
            "reviewer_id IS NULL OR reviewer_id <> applicant_id", name="ck_m2_no_self_review"
        ),
        sa.CheckConstraint("start_at < end_at", name="ck_m2_period"),
        sa.ForeignKeyConstraint(
            ["applicant_id"],
            ["d_users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"],
            ["d_users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["d_rooms.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("applicant_id", "request_key", name="uq_m2_applicant_request"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_m2_applicant_created", "m2_applications", ["applicant_id", "created_at"], unique=False
    )
    op.create_index("ix_m2_room_start", "m2_applications", ["room_id", "start_at"], unique=False)
    op.create_index(
        "ix_m2_status_created", "m2_applications", ["status", "created_at"], unique=False
    )


def downgrade():
    # MySQL uses these indexes for foreign keys; dropping the table removes them safely.
    op.drop_table("m2_applications")
