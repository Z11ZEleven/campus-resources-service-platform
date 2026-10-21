from datetime import UTC, datetime, timedelta

import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

from app.core.config import get_settings
from app.core.errors import DomainError

passwords = PasswordHash.recommended()
DUMMY_HASH = passwords.hash("not-a-real-account-password")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return passwords.verify(password, hashed)
    except (ValueError, UnknownHashError):
        return False


def create_token(user_id: int) -> str:
    config = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": now,
            "exp": now + timedelta(minutes=config.access_token_minutes),
            "iss": config.jwt_issuer,
            "aud": config.jwt_audience,
        },
        config.jwt_signing_key.get_secret_value(),
        algorithm="HS256",
    )


def token_user_id(token: str) -> int:
    config = get_settings()
    try:
        payload = jwt.decode(
            token,
            config.jwt_signing_key.get_secret_value(),
            algorithms=["HS256"],
            issuer=config.jwt_issuer,
            audience=config.jwt_audience,
            options={"require": ["sub", "exp", "iat", "iss", "aud"]},
        )
        value = payload["sub"]
        if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
            raise ValueError
        user_id = int(value)
        if not 0 < user_id <= 18446744073709551615:
            raise ValueError
        return user_id
    except (InvalidTokenError, ValueError, TypeError) as exc:
        raise DomainError(401, "INVALID_TOKEN", "登录凭据无效或已过期") from exc
