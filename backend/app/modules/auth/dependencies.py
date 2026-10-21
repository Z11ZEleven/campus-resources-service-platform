from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import DomainError
from app.modules.auth.permissions import Principal, load_principal
from app.modules.auth.security import token_user_id

Db = Annotated[Session, Depends(get_db, scope="function")]
bearer = HTTPBearer(auto_error=False)


def current_user(
    session: Db,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    if credentials is None:
        raise DomainError(401, "NOT_AUTHENTICATED", "请先登录")
    return load_principal(session, token_user_id(credentials.credentials))


CurrentUser = Annotated[Principal, Depends(current_user)]
