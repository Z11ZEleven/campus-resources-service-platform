"""Public versioned integer settings; no implicit business data reset."""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "0013_system_settings"
down_revision = "0012_resource_operations"
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("d_system_settings"):
        op.create_table(
            "d_system_settings",
            sa.Column("key", sa.String(100), primary_key=True),
            sa.Column("value", mysql.INTEGER(unsigned=True), nullable=False),
            sa.Column("version", mysql.BIGINT(unsigned=True), nullable=False, server_default="1"),
            sa.Column("updated_at", mysql.DATETIME(fsp=3), nullable=False),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT COUNT(*) FROM d_system_settings WHERE `key` <> 'm1.max_hours' OR `value` <> 4 OR version <> 1"
            )
        )
        .scalar()
    ):
        raise RuntimeError("Cannot downgrade while system settings exist")
    op.drop_table("d_system_settings")
