import pytest
from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command

pytestmark = pytest.mark.mysql


def test_schema_matches_models_and_empty_database_replay(factory, mysql_engine):
    cfg = Config("alembic.ini")
    command.check(cfg)
    expected_public = {
        "d_users", "d_user_identities", "d_user_roles", "d_rooms", "d_seats",
        "d_resource_blocks", "d_seat_status_summary", "d_operation_logs",
    }
    assert set(inspect(mysql_engine).get_table_names()) == expected_public | {
        "m3_work_orders", "alembic_version"
    }
    with mysql_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO d_users (username, password_hash, display_name, is_active, created_at, updated_at) "
            "VALUES ('migration-sentinel', 'test-only', 'migration sentinel', 1, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3))"
        ))
    # Rolling back only M3 leaves the existing public schema and data intact.
    command.downgrade(cfg, "0001_public_layer")
    assert set(inspect(mysql_engine).get_table_names()) == expected_public | {"alembic_version"}
    with mysql_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM d_users WHERE username = 'migration-sentinel'")) == 1
    command.upgrade(cfg, "head")
    command.check(cfg)
    command.downgrade(cfg, "base")
    assert not any(name.startswith(("d_", "m3_")) for name in inspect(mysql_engine).get_table_names())
    command.upgrade(cfg, "head")
    assert (
        len([name for name in inspect(mysql_engine).get_table_names() if name.startswith("d_")])
        == 8
    )
    assert "m3_work_orders" in inspect(mysql_engine).get_table_names()
    command.check(cfg)


def test_datetime_columns_preserve_milliseconds(mysql_engine):
    from sqlalchemy.dialects.mysql import DATETIME

    inspector = inspect(mysql_engine)
    columns = [
        column
        for name in inspector.get_table_names()
        if name.startswith(("d_", "m3_"))
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
        "id", "created_at", "reporter_id", "room_id", "title", "description", "category",
        "status", "assignee_id", "assigned_by", "assigned_at", "started_at", "completion_note",
        "submitted_at", "completion_round", "accepted_by", "accepted_at", "acceptance_comment",
        "request_key", "updated_at",
    }
    assert set(columns) == required
    for name in ("id", "reporter_id", "room_id", "assignee_id", "assigned_by", "accepted_by"):
        assert isinstance(columns[name]["type"], BIGINT) and columns[name]["type"].unsigned
    assert isinstance(columns["completion_round"]["type"], INTEGER)
    assert columns["completion_round"]["type"].unsigned
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
