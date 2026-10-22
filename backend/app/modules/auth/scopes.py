"""D 的公开审批授权：有效身份、当前角色、有效范围必须同时满足。"""

from sqlalchemy import select

from app.core.errors import forbidden
from app.modules.auth.models import RoleScope, User, UserIdentity, UserRole


def scope_grants(session, user_id):
    return list(
        session.execute(
            select(RoleScope.role_code, RoleScope.scope_type, RoleScope.scope_key)
            .join(
                UserRole,
                (UserRole.user_id == RoleScope.user_id)
                & (UserRole.role_code == RoleScope.role_code),
            )
            .join(User, User.id == RoleScope.user_id)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .where(
                RoleScope.user_id == user_id,
                RoleScope.is_active.is_(True),
                User.is_active.is_(True),
                UserIdentity.is_active.is_(True),
            )
        )
    )


def approval_requirement(room, level):
    if level == 2:
        return "ROOM_ADMIN", "ROOM", str(room.id)
    if room.department_code is not None:
        return "DEPT_ADMIN", "DEPARTMENT", room.department_code
    return "CAMPUS_ADMIN", "CAMPUS", room.campus_code


def require_scope(session, actor, requirement):
    if tuple(requirement) not in scope_grants(session, actor.user_id):
        raise forbidden()


def approver_ids(session, requirement):
    role, scope_type, scope_key = requirement
    return list(
        session.scalars(
            select(RoleScope.user_id)
            .join(
                UserRole,
                (UserRole.user_id == RoleScope.user_id)
                & (UserRole.role_code == RoleScope.role_code),
            )
            .join(User, User.id == RoleScope.user_id)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .where(
                RoleScope.role_code == role,
                RoleScope.scope_type == scope_type,
                RoleScope.scope_key == scope_key,
                RoleScope.is_active.is_(True),
                User.is_active.is_(True),
                UserIdentity.is_active.is_(True),
            )
            .distinct()
        )
    )
