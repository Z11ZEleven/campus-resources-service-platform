from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import DomainError, forbidden
from app.modules.auth.models import User, UserIdentity, UserRole

ROLE_PERMISSIONS = {
    "USER": frozenset({"m1:create", "m1:cancel", "m2:create", "m3:create"}),
    "ROOM_ADMIN": frozenset({"m2:approve", "m2:release", "users:read", "resources:manage"}),
    "PROPERTY_ADMIN": frozenset({"m3:assign", "m3:accept", "users:read", "resources:manage"}),
    "REPAIRER": frozenset({"m3:start", "m3:complete"}),
    "SYS_ADMIN": frozenset({"users:read", "resources:manage"}),
}


@dataclass(frozen=True)
class Principal:
    user_id: int
    username: str
    display_name: str
    identity_type: str
    department_code: str
    roles: frozenset[str]

    def has(self, permission: str) -> bool:
        return any(permission in ROLE_PERMISSIONS.get(role, ()) for role in self.roles)

    def require(self, permission: str):
        if not self.has(permission):
            raise forbidden()


def load_principal(session: Session, user_id: int) -> Principal:
    user = session.get(User, user_id)
    identity = session.scalar(select(UserIdentity).where(UserIdentity.user_id == user_id))
    if not user or not user.is_active or not identity or not identity.is_active:
        raise DomainError(401, "INACTIVE_USER", "用户或身份无效")
    roles = frozenset(
        session.scalars(select(UserRole.role_code).where(UserRole.user_id == user_id))
    )
    return Principal(
        user.id,
        user.username,
        user.display_name,
        identity.identity_type,
        identity.department_code,
        roles,
    )


def require_permission(permission: str) -> Callable:
    from app.modules.auth.dependencies import current_user

    def dependency(user: Annotated[Principal, Depends(current_user)]) -> Principal:
        user.require(permission)
        return user

    return dependency
