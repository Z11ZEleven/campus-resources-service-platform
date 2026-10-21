import pytest
from sqlalchemy import func, select

from app.modules.auth.models import User, UserIdentity, UserRole
from app.modules.resources.models import Room, Seat
from scripts.seed import seed

pytestmark = pytest.mark.mysql


def test_seed_repeated_and_does_not_reset_password_or_disabled_account(factory):
    with factory() as s, s.begin():
        seed(s, "seed-test-password-123")
    with factory() as s, s.begin():
        user = s.scalar(select(User).where(User.username == "student"))
        original_hash = user.password_hash
        user.is_active = False
    with factory() as s, s.begin():
        seed(s, "different-password-123")
    with factory() as s:
        user = s.scalar(select(User).where(User.username == "student"))
        assert not user.is_active and user.password_hash == original_hash
        assert s.scalar(select(func.count()).select_from(User)) == 6
        assert s.scalar(select(func.count()).select_from(UserIdentity)) == 6
        assert s.scalar(select(func.count()).select_from(UserRole)) == 6
        assert s.scalar(select(func.count()).select_from(Room)) == 2
        assert s.scalar(select(func.count()).select_from(Seat)) == 6
