"""Allocate M1 IDs above pre-existing source identifiers."""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "0009_m1_source_sequence"
down_revision = "0008_resource_revision"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "d_m1_source_sequence",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True),
        sa.Column("high_water", mysql.BIGINT(unsigned=True), nullable=False),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.execute(
        sa.text("""INSERT INTO d_m1_source_sequence (id, high_water)
        SELECT 1, GREATEST(COALESCE((SELECT MAX(source_id) FROM d_resource_blocks WHERE source_module = 'M1'), 0),
                           COALESCE((SELECT MAX(id) FROM m1_reservations), 0))""")
    )


def downgrade():
    op.drop_table("d_m1_source_sequence")
