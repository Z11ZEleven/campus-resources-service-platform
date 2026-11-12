"""Isolate operation keys by authenticated service, retaining existing history."""

import sqlalchemy as sa

from alembic import op

revision = "0014_service_operation_scope"
down_revision = "0013_system_settings"
branch_labels = None
depends_on = None


def _replace(columns):
    indexes = sa.inspect(op.get_bind()).get_indexes("d_resource_operations")
    if not any(index["name"] == "ix_resource_operation_service" for index in indexes):
        op.create_index("ix_resource_operation_service", "d_resource_operations", ["service_id"])
    constraints = sa.inspect(op.get_bind()).get_unique_constraints("d_resource_operations")
    current = next(c for c in constraints if c["name"] == "uq_resource_operation_key")
    if current["column_names"] != columns:
        # MySQL atomic DDL: interruption leaves the old or new index intact.
        op.execute(
            sa.text(
                "ALTER TABLE d_resource_operations DROP INDEX uq_resource_operation_key, "
                f"ADD CONSTRAINT uq_resource_operation_key UNIQUE ({', '.join(columns)})"
            )
        )


def upgrade():
    _replace(["service_id", "operation_key"])


def downgrade():
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM d_resource_operations GROUP BY source_module, operation_key "
                "HAVING COUNT(*) > 1 LIMIT 1"
            )
        )
        .first()
    )
    if duplicates:
        raise RuntimeError("Cannot downgrade service-isolated operation history")
    _replace(["source_module", "operation_key"])
