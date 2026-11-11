"""Separate rotation authorization without promoting existing read grants.

Existing role mappings are intentionally unchanged: operators must grant the
new permission explicitly. Fresh installations also require an explicit grant.
"""

revision = "0010_m1_rotate_perm"
down_revision = "0009_m1_source_sequence"
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    # Preserve authorization decisions; old application versions must not be
    # deployed as a security rollback without reviewing their broader check.
    pass
