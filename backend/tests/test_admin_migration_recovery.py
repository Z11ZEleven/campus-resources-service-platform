import runpy

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command


@pytest.mark.parametrize("state", ["table_old", "table_no_check", "table_new"])
def test_interrupted_admin_migration_recovers_without_losing_data(
    factory, mysql_engine, data, state
):
    migration = runpy.run_path("alembic/versions/0011_admin_restrictions.py")
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "0010_m1_rotate_perm")
    command.upgrade(cfg, "head")
    with mysql_engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num='0010_m1_rotate_perm'"))
        if state != "table_new":
            connection.execute(text("ALTER TABLE d_resource_blocks DROP CHECK ck_block_scope"))
        if state == "table_old":
            connection.execute(
                text(
                    "ALTER TABLE d_resource_blocks ADD CONSTRAINT ck_block_scope CHECK ("
                    + migration["OLD"]
                    + ")"
                )
            )
    try:
        command.upgrade(cfg, "head")
        command.upgrade(cfg, "head")
        with mysql_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT COUNT(*) FROM d_rooms WHERE id=:id"), {"id": data["room"]}
                )
                == 1
            )
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0014_service_operation_scope"
            )
        assert "d_admin_restriction_requests" in inspect(mysql_engine).get_table_names()
    finally:
        with mysql_engine.begin() as connection:
            if any(
                c["name"] == "ck_block_scope"
                for c in inspect(connection).get_check_constraints("d_resource_blocks")
            ):
                connection.execute(text("ALTER TABLE d_resource_blocks DROP CHECK ck_block_scope"))
            connection.execute(
                text(
                    "ALTER TABLE d_resource_blocks ADD CONSTRAINT ck_block_scope CHECK ("
                    + migration["NEW"]
                    + ")"
                )
            )
            connection.execute(
                text("UPDATE alembic_version SET version_num='0014_service_operation_scope'")
            )
