"""External resource operation ledger, pending team review."""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "0012_resource_operations"
down_revision = "0011_admin_restrictions"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("d_resource_operations"):
        return
    op.create_table(
        "d_resource_operations",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True),
        sa.Column("source_module", sa.String(8), nullable=False),
        sa.Column("source_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column(
            "room_id", mysql.BIGINT(unsigned=True), sa.ForeignKey("d_rooms.id"), nullable=False
        ),
        sa.Column(
            "service_id",
            mysql.BIGINT(unsigned=True),
            sa.ForeignKey("d_service_principals.id"),
            nullable=False,
        ),
        sa.Column("operation_key", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("request_payload", mysql.JSON(), nullable=False),
        sa.Column("status_code", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("result", mysql.JSON(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.UniqueConstraint("source_module", "operation_key", name="uq_resource_operation_key"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM d_resource_operations")).scalar():
        raise RuntimeError("Cannot downgrade while resource operation history exists")
    op.drop_table("d_resource_operations")
