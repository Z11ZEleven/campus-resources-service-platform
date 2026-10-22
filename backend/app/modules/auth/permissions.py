from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from sqlalchemy import false, or_, select, true
from sqlalchemy.orm import Session

from app.core.errors import DomainError, forbidden
from app.modules.auth.models import RolePermission, User, UserIdentity, UserRole

ROLE_PERMISSIONS = {
    "USER": frozenset(
        {
            "m1:create",
            "m1:cancel",
            "m2:create",
            "m3:create",
            "m1:seat:search",
            "m1:reservation:create",
            "m1:reservation:read_own",
            "m1:reservation:cancel_own",
            "m1:reservation:checkin",
            "m1:violation:read_own",
        }
    ),
    "ROOM_ADMIN": frozenset(
        {
            "m2:approve",
            "m2:release",
            "users:read",
            "resources:manage",
            "m1:reservation:read_scoped",
            "m1:statistics:read",
            "m1:checkin_code:read",
            "m1:settings:write",
        }
    ),
    "PROPERTY_ADMIN": frozenset({"m3:assign", "m3:accept", "users:read", "resources:manage"}),
    "REPAIRER": frozenset({"m3:start", "m3:complete"}),
    "SYS_ADMIN": frozenset({"users:read", "resources:manage", "auth:manage"}),
}


@dataclass(frozen=True)
class Grant:
    role_code: str
    permissions: frozenset[str]
    scope_type: str
    scope_value: str

    def matches(self, room) -> bool:
        if self.scope_type == "GLOBAL":
            return self.scope_value == "*"
        value = {
            "ROOM": room.id,
            "DEPARTMENT": room.department_code,
            "BUILDING": getattr(room, "building_id", None),
        }.get(self.scope_type)
        return value is not None and str(value) == self.scope_value


@dataclass(frozen=True)
class Principal:
    user_id: int
    username: str
    display_name: str
    identity_type: str
    department_code: str
    roles: frozenset[str]
    identity_id: int | None = None
    grants: tuple[Grant, ...] | None = None

    def effective_grants(self):
        # Compatibility for explicitly constructed internal test principals only.
        if self.grants is None:
            return tuple(
                Grant(r, ROLE_PERMISSIONS.get(r, frozenset()), "GLOBAL", "*") for r in self.roles
            )
        return self.grants

    def has(self, permission: str) -> bool:
        return any(
            permission in grant.permissions
            and grant.scope_type == "GLOBAL"
            and grant.scope_value == "*"
            for grant in self.effective_grants()
        )

    def has_any(self, permission: str) -> bool:
        return any(permission in grant.permissions for grant in self.effective_grants())

    def require(self, permission: str):
        if not self.has(permission):
            raise forbidden()

    def can_for_room(self, permission: str, room) -> bool:
        return any(
            permission in grant.permissions and grant.matches(room)
            for grant in self.effective_grants()
        )

    def require_for_room(self, permission: str, room):
        if not self.can_for_room(permission, room):
            raise forbidden()


def room_scope_predicate(actor: Principal, permission: str, room_model=None):
    if room_model is None:
        from app.modules.resources.models import Room

        room_model = Room
    predicates = []
    for grant in actor.effective_grants():
        if permission not in grant.permissions:
            continue
        if grant.scope_type == "GLOBAL" and grant.scope_value == "*":
            return true()
        column = {
            "ROOM": room_model.id,
            "DEPARTMENT": room_model.department_code,
            "BUILDING": getattr(room_model, "building_id", None),
        }.get(grant.scope_type)
        if column is not None:
            predicates.append(column == grant.scope_value)
    return or_(*predicates) if predicates else false()


def seed_role_permissions(session: Session):
    """Insert initial mappings only once. Never resurrect later revoked mappings."""
    # A durable sentinel separates an empty/revoked mapping from a fresh installation.
    if session.scalar(
        select(RolePermission).where(
            RolePermission.role_code == "_SYSTEM", RolePermission.permission_code == "_INITIALIZED"
        )
    ):
        return
    session.add(RolePermission(role_code="_SYSTEM", permission_code="_INITIALIZED"))
    for role, permissions in ROLE_PERMISSIONS.items():
        for permission in permissions:
            if not session.scalar(
                select(RolePermission).where(
                    RolePermission.role_code == role, RolePermission.permission_code == permission
                )
            ):
                session.add(RolePermission(role_code=role, permission_code=permission))
    session.flush()


def load_principal(session: Session, user_id: int, identity_id: int | None = None) -> Principal:
    user = session.get(User, user_id)
    query = select(UserIdentity).where(
        UserIdentity.user_id == user_id, UserIdentity.is_active.is_(True)
    )
    if identity_id is not None:
        query = query.where(UserIdentity.id == identity_id)
    identity = session.scalar(query.order_by(UserIdentity.id))
    if not user or not user.is_active or not identity:
        raise DomainError(401, "INACTIVE_USER", "用户或身份无效")
    grants = []
    for row in session.scalars(select(UserRole).where(UserRole.user_id == user_id)):
        permissions = frozenset(
            session.scalars(
                select(RolePermission.permission_code).where(
                    RolePermission.role_code == row.role_code
                )
            )
        )
        grants.append(Grant(row.role_code, permissions, row.scope_type, row.scope_value))
    return Principal(
        user.id,
        user.username,
        user.display_name,
        identity.identity_type,
        identity.department_code,
        frozenset(g.role_code for g in grants),
        identity.id,
        tuple(grants),
    )


def require_permission(permission: str) -> Callable:
    from app.modules.auth.dependencies import current_user

    def dependency(user: Annotated[Principal, Depends(current_user)]) -> Principal:
        user.require(permission)
        return user

    return dependency
