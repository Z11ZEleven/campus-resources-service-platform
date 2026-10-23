import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker

# Test-only keys; never used by deployed app.
os.environ.setdefault("JWT_SIGNING_KEY", "test-only-jwt-key-with-at-least-32-characters")
os.environ.setdefault("MYSQL_PASSWORD", "test-only")
os.environ["M3_TIMEOUT_SCAN_ENABLED"] = "false"
os.environ["M2_WORKER_ENABLED"] = "false"
os.environ.setdefault("RESOURCE_SERVICE_KEY", "test-only-m2-resource-service-key-at-least-32-characters")


def pytest_addoption(parser):
    parser.addoption(
        "--require-mysql",
        action="store_true",
        help="Fail rather than skip when TEST_DATABASE_URL is absent",
    )


@pytest.fixture(scope="session")
def mysql_engine(request):
    from alembic.config import Config
    from sqlalchemy.engine import make_url

    from alembic import command
    from app import models  # noqa: F401
    from app.core.config import get_settings
    from app.core.database import get_engine, get_session_factory

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        if request.config.getoption("--require-mysql"):
            pytest.fail("TEST_DATABASE_URL is required")
        pytest.skip("Set TEST_DATABASE_URL to a dedicated MySQL *_test database")
    parsed = make_url(url)
    if parsed.drivername != "mysql+pymysql" or not parsed.database.endswith("_test"):
        pytest.fail("Tests require mysql+pymysql and a database ending in _test")
    for key, value in {
        "MYSQL_HOST": parsed.host,
        "MYSQL_PORT": str(parsed.port or 3306),
        "MYSQL_DATABASE": parsed.database,
        "MYSQL_USER": parsed.username,
        "MYSQL_PASSWORD": parsed.password or "",
    }.items():
        os.environ[key] = value
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    engine = create_engine(url, isolation_level="READ COMMITTED", pool_pre_ping=True)
    with engine.begin() as connection:
        connection.exec_driver_sql("SET time_zone = '+00:00'")
    command.upgrade(Config("alembic.ini"), "head")
    yield engine
    get_engine().dispose()
    engine.dispose()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
def factory(mysql_engine):
    from app.core.database import Base

    # Never touches a database without the explicit *_test guard above.
    with mysql_engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(delete(table))
    return sessionmaker(mysql_engine, expire_on_commit=False)


@pytest.fixture(scope="session")
def password_hash():
    from app.modules.auth.security import passwords

    return passwords.hash("test-only-password-123")


@pytest.fixture
def data(factory, password_hash):
    from datetime import time

    from app.modules.auth.models import User, UserIdentity, UserRole
    from app.modules.auth.permissions import load_principal
    from app.modules.resources.models import Room, Seat

    principals = {}
    with factory() as session, session.begin():
        specs = (
            ("student", "STUDENT", "CS", ["USER"]),
            ("other", "STUDENT", "EE", ["USER"]),
            ("teacher", "TEACHER", "CS", ["USER"]),
            ("admin", "STAFF", "OPS", ["ROOM_ADMIN"]),
            ("property", "STAFF", "OPS", ["PROPERTY_ADMIN"]),
            ("repairer", "STAFF", "OPS", ["REPAIRER"]),
        )
        for username, kind, department, roles in specs:
            user = User(
                username=username,
                password_hash=password_hash,
                display_name=username,
                is_active=True,
            )
            session.add(user)
            session.flush()
            session.add(
                UserIdentity(
                    user_id=user.id,
                    identity_type=kind,
                    identity_no=username,
                    department_code=department,
                    is_active=True,
                )
            )
            session.add_all(UserRole(user_id=user.id, role_code=role) for role in roles)
            session.flush()
            principals[username] = load_principal(session, user.id)
        room = Room(
            code="PUBLIC",
            name="公共教室",
            building_name="B1",
            capacity=3,
            open_time=time(8),
            close_time=time(22),
            is_active=True,
            resource_version=0,
        )
        private = Room(
            code="PRIVATE",
            name="院系教室",
            building_name="B1",
            capacity=3,
            department_code="EE",
            open_time=time(8),
            close_time=time(22),
            is_active=True,
            resource_version=0,
        )
        session.add_all([room, private])
        session.flush()
        seats = [
            Seat(
                room_id=room.id,
                seat_no=str(i),
                has_fixed_socket=i == 1,
                has_mobile_socket=i == 2,
                is_active=True,
            )
            for i in (1, 2, 3)
        ]
        private_seat = Seat(
            room_id=private.id,
            seat_no="1",
            is_active=True,
            has_fixed_socket=False,
            has_mobile_socket=False,
        )
        session.add_all([*seats, private_seat])
        session.flush()
        tomorrow = (datetime.now(UTC) + timedelta(days=1)).date()
        from app.core.time import BUSINESS_TZ

        # Use tomorrow in business time even near UTC/local date boundaries.
        tomorrow = (datetime.now(BUSINESS_TZ) + timedelta(days=1)).date()
        start = datetime.combine(tomorrow, time(9), tzinfo=BUSINESS_TZ)
        return {
            "users": principals,
            "room": room.id,
            "private_room": private.id,
            "seats": [seat.id for seat in seats],
            "private_seat": private_seat.id,
            "start": start,
            "end": start + timedelta(hours=1),
            "day": tomorrow,
        }


@pytest.fixture
def client(data):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as value:
        yield value


def token_headers(principal):
    from app.modules.auth.security import create_token

    return {"Authorization": "Bearer " + create_token(principal.user_id)}
