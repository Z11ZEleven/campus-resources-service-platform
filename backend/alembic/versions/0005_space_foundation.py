"""D buildings, editable opening schedules, preserved legacy room links."""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "0005_space_foundation"
down_revision = "0004_auth_foundation"
branch_labels = None
depends_on = None
OPTIONS = {"mysql_charset": "utf8mb4", "mysql_engine": "InnoDB"}


def upgrade():
    op.create_table(
        "d_buildings",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("is_active", mysql.TINYINT(1), nullable=False, server_default="1"),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=3), nullable=False),
        **OPTIONS,
    )
    op.add_column("d_rooms", sa.Column("building_id", mysql.BIGINT(unsigned=True), nullable=True))
    op.add_column(
        "d_rooms",
        sa.Column("opening_rules_enabled", mysql.TINYINT(1), nullable=False, server_default="0"),
    )
    op.create_foreign_key("fk_room_building", "d_rooms", "d_buildings", ["building_id"], ["id"])
    op.execute("""INSERT INTO d_buildings (code,name,is_active,created_at,updated_at)
        SELECT CONCAT('LEGACY-',MIN(id)),building_name,1,UTC_TIMESTAMP(3),UTC_TIMESTAMP(3)
        FROM d_rooms GROUP BY building_name""")
    op.execute(
        "UPDATE d_rooms r JOIN d_buildings b ON b.name=r.building_name SET r.building_id=b.id"
    )
    op.create_table(
        "d_room_opening_rules",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True),
        sa.Column(
            "room_id", mysql.BIGINT(unsigned=True), sa.ForeignKey("d_rooms.id"), nullable=False
        ),
        sa.Column("weekday", mysql.TINYINT(unsigned=True), nullable=False),
        sa.Column("start_time", mysql.TIME(), nullable=False),
        sa.Column("end_time", mysql.TIME(), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
        ),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_opening_weekday"),
        sa.CheckConstraint("start_time <> end_time", name="ck_opening_rule_period"),
        sa.UniqueConstraint("room_id", "weekday", "start_time", "end_time", name="uq_opening_rule"),
        **OPTIONS,
    )
    op.create_table(
        "d_room_opening_exceptions",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True),
        sa.Column(
            "room_id", mysql.BIGINT(unsigned=True), sa.ForeignKey("d_rooms.id"), nullable=False
        ),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("start_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("end_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
        ),
        sa.CheckConstraint("start_at < end_at", name="ck_opening_exception_period"),
        sa.CheckConstraint("kind IN ('OPEN','CLOSE')", name="ck_opening_exception_kind"),
        **OPTIONS,
    )
    op.create_index(
        "ix_opening_exception_room_start", "d_room_opening_exceptions", ["room_id", "start_at"]
    )
    op.create_unique_constraint("uq_block_seat", "d_resource_blocks", ["id", "seat_id"])
    op.create_unique_constraint(
        "uq_block_owner", "d_resource_blocks", ["id", "room_id", "source_module", "source_id"]
    )


def downgrade():
    op.drop_constraint("uq_block_seat", "d_resource_blocks", type_="unique")
    op.drop_constraint("uq_block_owner", "d_resource_blocks", type_="unique")
    op.drop_table("d_room_opening_exceptions")
    op.drop_table("d_room_opening_rules")
    op.drop_constraint("fk_room_building", "d_rooms", type_="foreignkey")
    op.drop_column("d_rooms", "opening_rules_enabled")
    op.drop_column("d_rooms", "building_id")
    op.drop_table("d_buildings")
