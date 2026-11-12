import secrets
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import DomainError, forbidden
from app.modules.auth.models import ServicePrincipal, ServiceRoomScope
from app.modules.auth.permissions import Principal, load_principal
from app.modules.auth.sessions import SESSION_COOKIE, credential_hash, resolve_session

Db = Annotated[Session, Depends(get_db, scope="function")]
bearer = HTTPBearer(auto_error=False)


def current_user(
    request: Request,
    session: Db,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    # Cookie always wins, so an Authorization header cannot bypass its CSRF requirement.
    cookie = request.cookies.get(SESSION_COOKIE)
    token = cookie or (credentials.credentials if credentials else None)
    if not token:
        raise DomainError(401, "NOT_AUTHENTICATED", "请先登录")
    row = resolve_session(session, token)
    if cookie and request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise DomainError(403, "INVALID_ORIGIN", "请求来源无效")
        csrf = request.headers.get("X-CSRF-Token", "")
        if not csrf or not secrets.compare_digest(credential_hash(csrf), row.csrf_hash):
            raise DomainError(403, "CSRF_REQUIRED", "请求校验失败，请刷新页面")
    request.state.auth_session = row
    return load_principal(session, row.user_id, row.identity_id)


CurrentUser = Annotated[Principal, Depends(current_user)]


def authenticate_service(
    session: Session, token: str, module: str, room_id: int
) -> ServicePrincipal:
    principal = session.scalar(
        select(ServicePrincipal).where(
            ServicePrincipal.token_hash == credential_hash(token),
            ServicePrincipal.is_active.is_(True),
        )
    )
    if not principal:
        raise DomainError(401, "INVALID_SERVICE", "服务凭据无效")
    if principal.module != module or not session.scalar(
        select(ServiceRoomScope.id).where(
            ServiceRoomScope.service_id == principal.id, ServiceRoomScope.room_id == room_id
        )
    ):
        raise forbidden()
    return principal


def require_service(module: str):
    def dependency(
        room_id: int,
        session: Db,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        if credentials is None:
            raise DomainError(401, "INVALID_SERVICE", "服务凭据无效")
        return authenticate_service(session, credentials.credentials, module, room_id)

    return dependency
