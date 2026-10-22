from fastapi import APIRouter, Query
from sqlalchemy import select

from app.core.errors import DomainError
from app.modules.auth.dependencies import CurrentUser, Db
from app.modules.auth.models import User, UserIdentity, UserRole
from app.modules.auth.permissions import load_principal
from app.modules.auth.schemas import Login, Token, UserView
from app.modules.auth.security import DUMMY_HASH, create_token, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["身份与权限"])


def view(principal) -> UserView:
    return UserView(
        id=principal.user_id,
        username=principal.username,
        display_name=principal.display_name,
        identity_type=principal.identity_type,
        department_code=principal.department_code,
        roles=sorted(principal.roles),
    )


@router.post("/login", response_model=Token)
def login(body: Login, session: Db):
    user = session.scalar(select(User).where(User.username == body.username.strip().lower()))
    valid = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
    if not user or not valid:
        raise DomainError(401, "INVALID_CREDENTIALS", "用户名或密码错误")
    principal = load_principal(session, user.id)
    return Token(access_token=create_token(principal.user_id))


@router.get("/me", response_model=UserView)
def me(user: CurrentUser):
    return view(user)


@router.get("/users")
def users(
    session: Db,
    user: CurrentUser,
    role_code: str | None = Query(
        default=None, pattern="^(USER|ROOM_ADMIN|PROPERTY_ADMIN|REPAIRER|SYS_ADMIN|DEPT_ADMIN|CAMPUS_ADMIN)$"
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    user.require("users:read")
    query = (
        select(User.id)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(User.is_active.is_(True), UserIdentity.is_active.is_(True))
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
