"""M1 foundation and source-owned seat allocations; retains legacy blocks."""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "0006_m1_foundation"
down_revision = "0005_space_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "m1_reservations",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("identity_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("room_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("seat_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("start_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("end_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="RESERVED", nullable=False),
        sa.Column("checked_in_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("actual_end_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("cancelled_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("end_reason", sa.String(length=500), nullable=True),
        sa.Column("settings_snapshot", mysql.JSON(), nullable=False),
        sa.Column("resource_snapshot", mysql.JSON(), nullable=False),
        sa.Column("create_request_key", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("version", mysql.BIGINT(unsigned=True), server_default="1", nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.CheckConstraint(
            "status IN ('RESERVED','CHECKED_IN','CANCELLED','NO_SHOW','COMPLETED','INTERRUPTED')",
            name="ck_m1_reservation_status",
        ),
        sa.CheckConstraint(
            "CHAR_LENGTH(create_request_key) > 0 AND CHAR_LENGTH(request_hash) = 64",
            name="ck_m1_reservation_request",
        ),
        sa.CheckConstraint("start_at < end_at", name="ck_m1_reservation_period"),
        sa.CheckConstraint("version > 0", name="ck_m1_reservation_version"),
        sa.ForeignKeyConstraint(
            ["identity_id", "user_id"],
            ["d_user_identities.id", "d_user_identities.user_id"],
            name="fk_m1_reservation_identity_user",
        ),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["d_rooms.id"],
        ),
        sa.ForeignKeyConstraint(
            ["seat_id", "room_id"],
            ["d_seats.id", "d_seats.room_id"],
            name="fk_m1_reservation_seat_room",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["d_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "room_id", "seat_id", name="uq_m1_reservation_resource"),
        sa.UniqueConstraint("id", "user_id", "room_id", name="uq_m1_reservation_user_room"),
        sa.UniqueConstraint("id", "user_id", name="uq_m1_reservation_user"),
        sa.UniqueConstraint("user_id", "create_request_key", name="uq_m1_reservation_request"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_m1_reservation_room_status_start",
        "m1_reservations",
        ["room_id", "status", "start_at"],
        unique=False,
    )
    op.create_index(
        "ix_m1_reservation_status_end", "m1_reservations", ["status", "end_at"], unique=False
    )
    op.create_index(
        "ix_m1_reservation_status_start", "m1_reservations", ["status", "start_at"], unique=False
    )
    op.create_index(
        "ix_m1_reservation_user_created",
        "m1_reservations",
        ["user_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "m1_reservation_events",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("reservation_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=False),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("actor_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("details", mysql.JSON(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.CheckConstraint(
            "(actor_type IN ('USER','SERVICE') AND actor_id IS NOT NULL) OR (actor_type = 'SYSTEM' AND actor_id IS NULL)",
            name="ck_m1_event_actor",
        ),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status IN ('RESERVED','CHECKED_IN','CANCELLED','NO_SHOW','COMPLETED','INTERRUPTED')",
            name="ck_m1_event_from_status",
        ),
        sa.CheckConstraint(
            "to_status IN ('RESERVED','CHECKED_IN','CANCELLED','NO_SHOW','COMPLETED','INTERRUPTED')",
            name="ck_m1_event_to_status",
        ),
        sa.CheckConstraint("CHAR_LENGTH(event_type) > 0", name="ck_m1_event_type"),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["m1_reservations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_m1_event_reservation_created",
        "m1_reservation_events",
        ["reservation_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "m1_violations",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("reservation_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("violation_type", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.CheckConstraint("violation_type = 'NO_SHOW'", name="ck_m1_violation_type"),
        sa.ForeignKeyConstraint(
            ["reservation_id", "user_id"],
            ["m1_reservations.id", "m1_reservations.user_id"],
            name="fk_m1_violation_reservation_user",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["d_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reservation_id", name="uq_m1_violation_reservation"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_m1_violation_user_occurred",
        "m1_violations",
        ["user_id", "occurred_at", "id"],
        unique=False,
    )
    op.create_table(
        "m1_room_checkin_codes",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("room_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("version", mysql.BIGINT(unsigned=True), server_default="1", nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.CheckConstraint("CHAR_LENGTH(nonce) >= 32", name="ck_m1_checkin_code_nonce"),
        sa.CheckConstraint("version > 0", name="ck_m1_checkin_code_version"),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["d_rooms.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("room_id", "business_date", name="uq_m1_checkin_room_date"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "m1_checkin_attempts",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("reservation_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("room_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("attempted_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.CheckConstraint("CHAR_LENGTH(result) > 0", name="ck_m1_attempt_result"),
        sa.ForeignKeyConstraint(
            ["reservation_id", "user_id", "room_id"],
            ["m1_reservations.id", "m1_reservations.user_id", "m1_reservations.room_id"],
            name="fk_m1_attempt_reservation_user_room",
        ),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["d_rooms.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["d_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_m1_attempt_user_room_time",
        "m1_checkin_attempts",
        ["user_id", "room_id", "attempted_at"],
        unique=False,
    )
    op.create_table(
        "d_seat_allocations",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("block_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("room_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("seat_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("source_module", sa.String(length=8), nullable=False),
        sa.Column("source_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("source_seat_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("m1_reservation_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("start_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("end_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ACTIVE", nullable=False),
        sa.Column("released_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND released_at IS NULL) OR (status = 'RELEASED' AND released_at IS NOT NULL)",
            name="ck_allocation_status",
        ),
        sa.CheckConstraint(
            "m1_reservation_id IS NULL OR (source_module = 'M1' AND m1_reservation_id = source_id)",
            name="ck_allocation_m1_source",
        ),
        sa.CheckConstraint(
            "(source_module = 'M1' AND source_seat_id IS NOT NULL AND source_seat_id = seat_id) OR (source_module = 'M2' AND source_seat_id IS NULL)",
            name="ck_allocation_source",
        ),
        sa.CheckConstraint("start_at < end_at", name="ck_allocation_period"),
        sa.ForeignKeyConstraint(
            ["block_id", "room_id", "source_module", "source_id"],
            [
                "d_resource_blocks.id",
                "d_resource_blocks.room_id",
                "d_resource_blocks.source_module",
                "d_resource_blocks.source_id",
            ],
            name="fk_allocation_block_owner",
        ),
        sa.ForeignKeyConstraint(
            ["block_id", "source_seat_id"],
            ["d_resource_blocks.id", "d_resource_blocks.seat_id"],
            name="fk_allocation_source_seat",
        ),
        sa.ForeignKeyConstraint(
            ["m1_reservation_id", "room_id", "seat_id"],
            ["m1_reservations.id", "m1_reservations.room_id", "m1_reservations.seat_id"],
            name="fk_allocation_m1_reservation",
        ),
        sa.ForeignKeyConstraint(
            ["seat_id", "room_id"],
            ["d_seats.id", "d_seats.room_id"],
            name="fk_allocation_seat_room",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("block_id", "seat_id", name="uq_allocation_block_seat"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_allocation_room_status_start",
        "d_seat_allocations",
        ["room_id", "status", "start_at"],
        unique=False,
    )
    op.create_index(
        "ix_allocation_seat_status_start_end",
        "d_seat_allocations",
        ["seat_id", "status", "start_at", "end_at"],
        unique=False,
    )

    # Legacy M1 source IDs are logical references, not invented reservations.
    # M2 authority remains its room block; expand the currently retained seats,
    # including inactive seats so historical room ownership is not lost.
    op.execute(
        sa.text("""
        INSERT INTO d_seat_allocations
            (block_id, room_id, seat_id, source_module, source_id, source_seat_id,
             m1_reservation_id, start_at, end_at, status, released_at, created_at, updated_at)
        SELECT b.id, b.room_id, s.id, b.source_module, b.source_id, b.seat_id,
               NULL, b.start_at, b.end_at, b.status, b.released_at, b.created_at, b.updated_at
        FROM d_resource_blocks b
        JOIN d_seats s ON s.room_id = b.room_id
            AND (b.source_module = 'M2' OR s.id = b.seat_id)
        WHERE b.source_module IN ('M1', 'M2')
    """)
    )


def downgrade():
    # Drop derived allocations before private reservations. Source blocks and all
    # pre-existing M3/notification/user records are deliberately retained.
    op.drop_table("d_seat_allocations")
    op.drop_table("m1_checkin_attempts")
    op.drop_table("m1_room_checkin_codes")
    op.drop_table("m1_violations")
    op.drop_table("m1_reservation_events")
    op.drop_table("m1_reservations")
