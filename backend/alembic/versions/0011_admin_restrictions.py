"""D administrator restrictions, pending team review."""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "0011_admin_restrictions"
down_revision = "0010_m1_rotate_perm"
branch_labels = None
depends_on = None

OLD = "(status = 'RELEASED' AND start_at IS NULL AND seat_id IS NULL AND end_at IS NULL) OR (source_module = 'M1' AND seat_id IS NOT NULL AND end_at IS NOT NULL) OR (source_module = 'M2' AND seat_id IS NULL AND end_at IS NOT NULL) OR (source_module = 'M3' AND seat_id IS NULL)"
NEW = OLD + " OR (source_module = 'D' AND start_at IS NOT NULL AND end_at IS NOT NULL)"


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("d_admin_restriction_requests"):
        op.create_table(
            "d_admin_restriction_requests",
            sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True),
            sa.Column(
                "actor_id", mysql.BIGINT(unsigned=True), sa.ForeignKey("d_users.id"), nullable=False
            ),
            sa.Column("key_hash", sa.String(64), nullable=False),
            sa.UniqueConstraint("actor_id", "key_hash", name="uq_admin_restriction_key"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )
    checks = sa.inspect(op.get_bind()).get_check_constraints("d_resource_blocks")
    drop = (
        "DROP CHECK ck_block_scope, " if any(c["name"] == "ck_block_scope" for c in checks) else ""
    )
    # One atomic MySQL DDL statement avoids leaving an unconstrained table.
    op.execute(
        sa.text(
            "ALTER TABLE d_resource_blocks "
            + drop
            + "ADD CONSTRAINT ck_block_scope CHECK ("
            + NEW
            + ")"
        )
    )


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT COUNT(*) FROM d_resource_blocks WHERE source_module = 'D'"))
        .scalar()
    ):
        raise RuntimeError("Cannot downgrade while administrator restriction history exists")
    op.drop_constraint("ck_block_scope", "d_resource_blocks", type_="check")
    op.create_check_constraint("ck_block_scope", "d_resource_blocks", OLD)
    op.drop_table("d_admin_restriction_requests")
