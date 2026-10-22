from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import select

from app.core.errors import DomainError
from app.modules.auth.dependencies import CurrentUser, Db
from app.modules.auth.models import User, UserIdentity, UserRole
from app.modules.auth.permissions import load_principal
from app.modules.auth.schemas import IdentitySelection, Login, RoleGrantInput, UserView
from app.modules.auth.security import DUMMY_HASH, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["身份与权限"])


def view(principal) -> UserView:
    return UserView(
        id=principal.user_id,
        username=principal.username,
        display_name=principal.display_name,
        identity_type=principal.identity_type,
        department_code=principal.department_code,
        roles=sorted(principal.roles),
        identity_id=principal.identity_id,
        permissions=sorted({p for g in principal.effective_grants() for p in g.permissions}),
    )


@router.post("/login")
def login(body: Login, session: Db, request: Request, response: Response):
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise DomainError(403, "INVALID_ORIGIN", "登录来源无效")
    user = session.scalar(select(User).where(User.username == body.username.strip().lower()))
    valid = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
    if not user or not valid:
        raise DomainError(401, "INVALID_CREDENTIALS", "用户名或密码错误")
    principal = load_principal(session, user.id)
    from app.core.config import get_settings
    from app.modules.auth.sessions import CSRF_COOKIE, SESSION_COOKIE, issue_session

    token, csrf, row = issue_session(session, principal.user_id, principal.identity_id)
    config = get_settings()
    options = dict(
        secure=getattr(config, "session_cookie_secure", False),
        samesite=getattr(config, "session_cookie_samesite", "lax"),
        max_age=config.session_minutes * 60,
        path="/",
    )
    response.set_cookie(SESSION_COOKIE, token, httponly=True, **options)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, **options)
    return {"authenticated": True}


@router.get("/me", response_model=UserView)
def me(user: CurrentUser):
    return view(user)


@router.get("/users")
def users(
    session: Db,
    user: CurrentUser,
    role_code: str | None = Query(
        default=None, pattern="^(USER|ROOM_ADMIN|PROPERTY_ADMIN|REPAIRER|SYS_ADMIN)$"
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    user.require("users:read")
    query = (
        select(User.id)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(User.is_active.is_(True), UserIdentity.is_active.is_(True))
        .distinct()
        .order_by(User.id)
    )
    if role_code:
        query = query.where(
            User.id.in_(select(UserRole.user_id).where(UserRole.role_code == role_code))
        )
    from sqlalchemy import func

    total = session.scalar(select(func.count()).select_from(query.subquery()))
    ids = session.scalars(query.offset((page - 1) * page_size).limit(page_size))
    return {
        "items": [view(load_principal(session, item)) for item in ids],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/logout")
def logout(request: Request, response: Response, user: CurrentUser, session: Db):
    from app.core.time import utcnow
    from app.modules.auth.sessions import CSRF_COOKIE, SESSION_COOKIE

    request.state.auth_session.revoked_at = utcnow()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.get("/identities")
def identities(session: Db, user: CurrentUser):
    return [
        {
            "id": str(row.id),
            "identity_type": row.identity_type,
            "department_code": row.department_code,
        }
        for row in session.scalars(
            select(UserIdentity).where(
                UserIdentity.user_id == user.user_id, UserIdentity.is_active.is_(True)
            )
        )
    ]


@router.post("/identity", response_model=UserView)
def select_identity(body: IdentitySelection, request: Request, session: Db, user: CurrentUser):
    identity = session.get(UserIdentity, body.identity_id)
    if not identity or identity.user_id != user.user_id or not identity.is_active:
        raise DomainError(403, "INVALID_IDENTITY", "身份无效或不属于当前用户")
    request.state.auth_session.identity_id = identity.id
    return view(load_principal(session, user.user_id, identity.id))


@router.put("/users/{user_id}/roles")
def grant_role(user_id: int, body: RoleGrantInput, session: Db, actor: CurrentUser):
    from app.modules.audit.service import record_operation

    # Administration of grants requires its own global grant; a room-scoped
    # business privilege cannot promote users or enlarge its own scope.
    if not any(
        "auth:manage" in g.permissions and g.scope_type == "GLOBAL" and g.scope_value == "*"
        for g in actor.effective_grants()
    ):
        raise DomainError(403, "FORBIDDEN", "没有授权管理权限")
    if not session.scalar(select(User).where(User.id == user_id).with_for_update()):
        raise DomainError(404, "USER_NOT_FOUND", "用户不存在")
    if body.scope_type == "GLOBAL" and body.scope_value != "*":
        raise DomainError(422, "INVALID_SCOPE", "全局范围必须为 *")
    if body.scope_type in {"ROOM", "BUILDING"}:
        from app.modules.resources.models import Room

        if not body.scope_value.isascii() or not body.scope_value.isdecimal():
            raise DomainError(422, "INVALID_SCOPE", "资源范围必须为 ID")
        body.scope_value = str(int(body.scope_value))
        if body.scope_type == "ROOM" and not session.get(Room, int(body.scope_value)):
            raise DomainError(422, "INVALID_SCOPE", "教室不存在")
        if body.scope_type == "BUILDING":
            from app.modules.resources.models import Building

            if not session.get(Building, int(body.scope_value)):
                raise DomainError(422, "INVALID_SCOPE", "建筑不存在")
    row = session.scalar(
        select(UserRole).where(
            UserRole.user_id == user_id,
            UserRole.role_code == body.role_code,
            UserRole.scope_type == body.scope_type,
            UserRole.scope_value == body.scope_value,
        )
    )
    if row is None:
        row = UserRole(user_id=user_id, **body.model_dump())
        session.add(row)
        session.flush()
        record_operation(
            session,
            module="D",
            business_id=user_id,
            action="ROLE_GRANTED",
            actor_id=actor.user_id,
            note=f"{body.role_code}:{body.scope_type}:{body.scope_value}",
        )
    return {"id": str(row.id)}


@router.delete("/users/{user_id}/roles/{grant_id}")
def revoke_role(user_id: int, grant_id: int, session: Db, actor: CurrentUser):
    from app.modules.audit.service import record_operation

    if not any(
        "auth:manage" in g.permissions and g.scope_type == "GLOBAL" and g.scope_value == "*"
        for g in actor.effective_grants()
    ):
        raise DomainError(403, "FORBIDDEN", "没有授权管理权限")
    row = session.get(UserRole, grant_id)
    if not row or row.user_id != user_id:
        raise DomainError(404, "GRANT_NOT_FOUND", "授权不存在")
    record_operation(
        session,
        module="D",
        business_id=user_id,
        action="ROLE_REVOKED",
        actor_id=actor.user_id,
        note=f"{row.role_code}:{row.scope_type}:{row.scope_value}",
    )
    session.delete(row)
    return {"ok": True}
