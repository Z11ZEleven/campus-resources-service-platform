from sqlalchemy import create_engine

from alembic import context
from app import models  # noqa: F401 - register metadata
from app.core.config import get_settings
from app.core.database import Base

target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(
        url=get_settings().database_url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = create_engine(get_settings().database_url)
    with engine.connect() as connection:
        connection.exec_driver_sql("SET time_zone = '+00:00'")
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
