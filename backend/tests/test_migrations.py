import pytest
from alembic.config import Config
from sqlalchemy import inspect

from alembic import command

pytestmark = pytest.mark.mysql


def test_schema_matches_models_and_empty_database_replay(factory, mysql_engine):
    cfg = Config("alembic.ini")
    command.check(cfg)
    command.downgrade(cfg, "base")
    assert not any(name.startswith("d_") for name in inspect(mysql_engine).get_table_names())
    command.upgrade(cfg, "head")
    assert (
        len([name for name in inspect(mysql_engine).get_table_names() if name.startswith("d_")])
        == 8
    )
    command.check(cfg)


def test_datetime_columns_preserve_milliseconds(mysql_engine):
    from sqlalchemy.dialects.mysql import DATETIME

    inspector = inspect(mysql_engine)
    columns = [
        column
        for name in inspector.get_table_names()
        if name.startswith("d_")
        for column in inspector.get_columns(name)
    ]
    temporal = [column for column in columns if isinstance(column["type"], DATETIME)]
    assert temporal
    assert all(column["type"].fsp == 3 for column in temporal)
