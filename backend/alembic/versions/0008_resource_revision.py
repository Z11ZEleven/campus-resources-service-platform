"""Versioned source updates and release tombstones (pending team review)."""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "0008_resource_revision"
down_revision = "0007_outbox_audit"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_block_scope", "d_resource_blocks", type_="check")
    op.add_column(
        "d_resource_blocks",
        sa.Column("revision", mysql.BIGINT(unsigned=True), nullable=False, server_default="0"),
    )
    op.add_column(
        "d_resource_blocks",
        sa.Column("is_versioned", mysql.TINYINT(1), nullable=False, server_default="0"),
    )
    op.alter_column(
        "d_resource_blocks", "start_at", existing_type=mysql.DATETIME(fsp=3), nullable=True
    )
    op.create_check_constraint(
        "ck_block_scope",
        "d_resource_blocks",
        "(status = 'RELEASED' AND start_at IS NULL AND seat_id IS NULL AND end_at IS NULL) OR (source_module = 'M1' AND seat_id IS NOT NULL AND end_at IS NOT NULL) OR (source_module = 'M2' AND seat_id IS NULL AND end_at IS NOT NULL) OR (source_module = 'M3' AND seat_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_block_start",
        "d_resource_blocks",
        "start_at IS NOT NULL OR (status = 'RELEASED' AND seat_id IS NULL AND end_at IS NULL)",
    )


def downgrade():
    # Tombstones cannot be represented in the old schema; fail rather than erase history.
    bind = op.get_bind()
    if bind.execute(
        sa.text("SELECT COUNT(*) FROM d_resource_blocks WHERE start_at IS NULL")
    ).scalar():
        raise RuntimeError("Cannot downgrade while release tombstones exist")
    if bind.execute(
        sa.text("SELECT COUNT(*) FROM d_resource_blocks WHERE revision <> 0 OR is_versioned <> 0")
    ).scalar():
        raise RuntimeError("Cannot downgrade while source revisions would be lost")
    op.drop_constraint("ck_block_start", "d_resource_blocks", type_="check")
    op.drop_constraint("ck_block_scope", "d_resource_blocks", type_="check")
    op.alter_column(
        "d_resource_blocks", "start_at", existing_type=mysql.DATETIME(fsp=3), nullable=False
    )
    op.drop_column("d_resource_blocks", "is_versioned")
    op.drop_column("d_resource_blocks", "revision")
    op.create_check_constraint(
        "ck_block_scope",
        "d_resource_blocks",
        "(source_module = 'M1' AND seat_id IS NOT NULL AND end_at IS NOT NULL) OR (source_module = 'M2' AND seat_id IS NULL AND end_at IS NOT NULL) OR (source_module = 'M3' AND seat_id IS NULL)",
    )
