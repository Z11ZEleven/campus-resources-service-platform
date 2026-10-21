from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine():
    engine = create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        isolation_level="READ COMMITTED",
    )

    @event.listens_for(engine, "connect")
    def set_timezone(connection, _):
        with connection.cursor() as cursor:
            cursor.execute("SET time_zone = '+00:00'")

    return engine


@lru_cache
def get_session_factory():
    return sessionmaker(get_engine(), expire_on_commit=False)


def get_db():
    # One request, one transaction. FastAPI dependency uses scope="function":
    # commit/rollback completes BEFORE a success response is sent.
    with get_session_factory()() as session, session.begin():
        yield session


def require_transaction(session: Session):
    if not session.in_transaction():
        raise RuntimeError("Open a transaction before invoking a shared service")
