import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.time import utcnow
from app.modules.auth.models import AuthSession
from app.modules.auth.permissions import load_principal

SESSION_COOKIE = "campus_session"
CSRF_COOKIE = "campus_csrf"


def credential_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_session(session: Session, user_id: int, identity_id: int | None = None):
    principal = load_principal(session, user_id, identity_id)
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    row = AuthSession(
        token_hash=credential_hash(token),
        csrf_hash=credential_hash(csrf),
        user_id=user_id,
        identity_id=principal.identity_id,
        expires_at=utcnow() + timedelta(minutes=get_settings().session_minutes),
    )
    session.add(row)
    session.flush()
    return token, csrf, row


def resolve_session(session: Session, token: str) -> AuthSession:
    row = session.scalar(
        select(AuthSession).where(AuthSession.token_hash == credential_hash(token))
    )
    if not row or row.revoked_at is not None or row.expires_at <= utcnow():
        raise DomainError(401, "INVALID_SESSION", "会话无效或已过期")
    return row
