import pytest
from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command

pytestmark = pytest.mark.mysql


def test_schema_matches_models_and_empty_database_replay(factory, mysql_engine):
    cfg = Config("alembic.ini")
    command.check(cfg)
    expected_public = {
        "d_users",
        "d_user_identities",
        "d_user_roles",
        "d_rooms",
        "d_seats",
        "d_resource_blocks",
        "d_seat_status_summary",
        "d_operation_logs",
    }
    expected_extensions = {
        "d_role_permissions",
        "d_auth_sessions",
        "d_service_principals",
        "d_service_room_scopes",
        "d_buildings",
        "d_room_opening_rules",
        "d_room_opening_exceptions",
        "d_seat_allocations",
        "m1_reservations",
        "m1_reservation_events",
        "m1_violations",
        "m1_room_checkin_codes",
        "m1_checkin_attempts",
    }
    expected_head = (
        expected_public
        | expected_extensions
        | {
            "m3_work_orders",
            "m3_work_order_ratings",
            "m3_work_order_escalations",
            "d_notifications",
            "alembic_version",
        }
    )
    assert set(inspect(mysql_engine).get_table_names()) == expected_head
    with mysql_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO d_users (username, password_hash, display_name, is_active, created_at, updated_at) "
                "VALUES ('migration-sentinel', 'test-only', 'migration sentinel', 1, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3))"
            )
        )
    # Rolling back only M3 leaves the existing public schema and data intact.
    command.downgrade(cfg, "0001_public_layer")
    assert set(inspect(mysql_engine).get_table_names()) == expected_public | {"alembic_version"}
    with mysql_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM d_users WHERE username = 'migration-sentinel'")
            )
            == 1
        )
    command.upgrade(cfg, "head")
    command.check(cfg)
    command.downgrade(cfg, "base")
    assert not any(
        name.startswith(("d_", "m1_", "m3_")) for name in inspect(mysql_engine).get_table_names()
    )
    command.upgrade(cfg, "head")
    assert set(inspect(mysql_engine).get_table_names()) == expected_head
    command.upgrade(cfg, "head")
    assert set(inspect(mysql_engine).get_table_names()) == expected_head
    command.check(cfg)


def test_datetime_columns_preserve_milliseconds(mysql_engine):
    from sqlalchemy.dialects.mysql import DATETIME

    inspector = inspect(mysql_engine)
    columns = [
        column
        for name in inspector.get_table_names()
        if name.startswith(("d_", "m1_", "m3_"))
        for column in inspector.get_columns(name)
    ]
    temporal = [column for column in columns if isinstance(column["type"], DATETIME)]
    assert temporal
    assert all(column["type"].fsp == 3 for column in temporal)


def test_m3_schema_has_unsigned_keys_foreign_keys_and_idempotency_constraint(mysql_engine):
    from sqlalchemy.dialects.mysql import BIGINT, INTEGER

    inspector = inspect(mysql_engine)
    columns = {column["name"]: column for column in inspector.get_columns("m3_work_orders")}
    required = {
        "id",
        "created_at",
        "reporter_id",
        "room_id",
        "title",
        "description",
        "category",
        "status",
        "assignee_id",
        "assigned_by",
        "assigned_at",
        "started_at",
        "completion_note",
        "submitted_at",
        "completion_round",
        "accepted_by",
        "accepted_at",
        "acceptance_comment",
        "request_key",
        "updated_at",
        "stage_started_at",
        "escalation_count",
    }
    assert set(columns) == required
    for name in ("id", "reporter_id", "room_id", "assignee_id", "assigned_by", "accepted_by"):
        assert isinstance(columns[name]["type"], BIGINT) and columns[name]["type"].unsigned
    assert isinstance(columns["completion_round"]["type"], INTEGER)
    assert columns["completion_round"]["type"].unsigned
    assert isinstance(columns["escalation_count"]["type"], INTEGER)
    assert columns["escalation_count"]["type"].unsigned
    assert columns["title"]["type"].length == 100
    assert columns["acceptance_comment"]["type"].length == 500
    assert columns["request_key"]["type"].length == 36
    assert not columns["request_key"]["nullable"]
    unique_columns = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("m3_work_orders")
    }
    assert ("reporter_id", "request_key") in unique_columns
    foreign_keys = {
        (tuple(key["constrained_columns"]), key["referred_table"], tuple(key["referred_columns"]))
        for key in inspector.get_foreign_keys("m3_work_orders")
    }
    assert foreign_keys == {
        (("reporter_id",), "d_users", ("id",)),
        (("room_id",), "d_rooms", ("id",)),
        (("assignee_id",), "d_users", ("id",)),
        (("assigned_by",), "d_users", ("id",)),
        (("accepted_by",), "d_users", ("id",)),
    }


def test_extension_upgrade_preserves_existing_users_orders_and_phase_clock(
    client, data, mysql_engine
):
    from uuid import uuid4

    from conftest import token_headers

    response = client.post(
        "/api/v1/m3/work-orders",
        headers=token_headers(data["users"]["student"]),
        json={
            "room_id": str(data["room"]),
            "title": "迁移前已存在的故障",
            "description": "保留原工单和用户数据",
            "category": "OTHER",
            "request_key": str(uuid4()),
        },
    )
    assert response.status_code == 201
    order_id = int(response.json()["id"])
    with mysql_engine.connect() as connection:
        previous = connection.execute(
            text("SELECT title,updated_at,request_key FROM m3_work_orders WHERE id=:id"),
            {"id": order_id},
        ).one()
        users = connection.execute(
            text("SELECT id,username,password_hash FROM d_users ORDER BY id")
        ).all()
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "0002_m3_work_orders")
    with mysql_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT title FROM m3_work_orders WHERE id=:id"), {"id": order_id}
            )
            == previous.title
        )
        assert (
            connection.execute(
                text("SELECT id,username,password_hash FROM d_users ORDER BY id")
            ).all()
            == users
        )
    command.upgrade(cfg, "head")
    with mysql_engine.connect() as connection:
        migrated = connection.execute(
            text(
                "SELECT title,stage_started_at,escalation_count,request_key FROM m3_work_orders WHERE id=:id"
            ),
            {"id": order_id},
        ).one()
        assert migrated.title == previous.title
        assert migrated.stage_started_at == previous.updated_at
        assert migrated.escalation_count == 0
        assert migrated.request_key == previous.request_key
        assert (
            connection.execute(
                text("SELECT id,username,password_hash FROM d_users ORDER BY id")
            ).all()
            == users
        )
    command.check(cfg)


def test_m1_upgrade_backfills_legacy_sources_without_inventing_reservations(
    factory, data, mysql_engine
):
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "0003_m3_extensions")
    with mysql_engine.begin() as connection:
        for module, source_id, seat_id, released_at in (
            ("M1", 9001, data["seats"][0], None),
            ("M2", 9002, None, "2026-11-01 03:00:00.123"),
            ("M3", 9003, None, None),
        ):
            connection.execute(
                text("""
                INSERT INTO d_resource_blocks
                    (room_id,seat_id,source_module,source_id,start_at,end_at,status,
                     released_at,created_at,updated_at)
                VALUES (:room,:seat,:module,:source,'2026-11-01 01:00:00.123',
                        '2026-11-01 02:00:00.123',:status,:released,
                        '2026-10-22 00:00:00.123','2026-10-22 00:00:00.123')
            """),
                {
                    "room": data["room"],
                    "seat": seat_id,
                    "module": module,
                    "source": source_id,
                    "status": "RELEASED" if released_at else "ACTIVE",
                    "released": released_at,
                },
            )
        original = connection.execute(text("SELECT * FROM d_resource_blocks ORDER BY id")).all()
        connection.execute(
            text("""
            INSERT INTO d_notifications
                (recipient_id,source_module,business_id,event_key,title,content,link,created_at)
            VALUES (:user,'M3',9003,'m1-upgrade-preserve','test','test','/test',UTC_TIMESTAMP(3))
        """),
            {"user": data["users"]["student"].user_id},
        )
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")
    with mysql_engine.connect() as connection:
        assert (
            connection.execute(text("SELECT * FROM d_resource_blocks ORDER BY id")).all()
            == original
        )
        allocations = connection.execute(
            text("""
            SELECT source_module,source_id,seat_id,m1_reservation_id,status,released_at,start_at
            FROM d_seat_allocations ORDER BY source_module,seat_id
        """)
        ).all()
        assert len(allocations) == 1 + len(data["seats"])
        assert allocations[0].source_module == "M1"
        assert allocations[0].seat_id == data["seats"][0]
        assert all(row.m1_reservation_id is None for row in allocations)
        assert {row.seat_id for row in allocations if row.source_module == "M2"} == set(
            data["seats"]
        )
        assert all(
            row.status == "RELEASED" and row.released_at is not None
            for row in allocations
            if row.source_module == "M2"
        )
        assert all(row.start_at.microsecond == 123000 for row in allocations)
        assert connection.scalar(text("SELECT count(*) FROM m1_reservations")) == 0
        assert (
            connection.scalar(
                text("SELECT count(*) FROM d_notifications WHERE event_key='m1-upgrade-preserve'")
            )
            == 1
        )
    command.check(cfg)
