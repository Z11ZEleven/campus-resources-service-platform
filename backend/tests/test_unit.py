from datetime import UTC, datetime

import pytest

from app.core.errors import DomainError
from app.core.schema import Id, Output, UtcDateTime
from app.core.time import to_utc
from app.modules.auth.permissions import Principal
from app.modules.auth.security import create_token, token_user_id


def test_id_and_utc_serialization():
    class Example(Output):
        id: Id
        at: UtcDateTime

    item = Example(id=9007199254740993, at=datetime(2026, 1, 1))
    assert item.model_dump(mode="json") == {"id": "9007199254740993", "at": "2026-01-01T00:00:00Z"}
    assert to_utc(datetime(2026, 1, 1, tzinfo=UTC)) == datetime(2026, 1, 1)
    with pytest.raises(ValueError):
        to_utc(datetime(2026, 1, 1))


def test_jwt_roundtrip_and_bad_signature():
    token = create_token(42)
    assert token_user_id(token) == 42
    with pytest.raises(DomainError):
        token_user_id(token + "tampered")


def test_sys_admin_does_not_bypass_business_roles():
    user = Principal(1, "sys", "sys", "STAFF", "OPS", frozenset({"SYS_ADMIN"}))
    assert user.has("resources:manage")
    assert not user.has("m2:approve")
    with pytest.raises(DomainError):
        user.require("m3:start")
