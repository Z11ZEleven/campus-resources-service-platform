"""Explicit first D administrator bootstrap; never restores a removed grant.

Use a deployment operator account and BOOTSTRAP_ADMIN_PASSWORD from the environment.
Run from backend: python -m scripts.bootstrap_admin --username ... --display-name ...
"""

import argparse
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session_factory
from app.modules.audit.service import record_operation
from app.modules.auth.models import User, UserIdentity, UserRole
from app.modules.auth.security import passwords


def bootstrap_admin(session: Session, *, username: str, display_name: str, password: str) -> User:
    username = username.strip().lower()
    if not username or len(username) > 64 or not display_name or len(display_name) > 100:
        raise ValueError("Invalid administrator name")
    if len(password) < 12:
        raise ValueError("BOOTSTRAP_ADMIN_PASSWORD must contain at least 12 characters")
    user = session.scalar(select(User).where(User.username == username).with_for_update())
    if user is not None:
        return user  # Preserve revoked roles, disabled users, identities and passwords.
    user = User(
        username=username, display_name=display_name, password_hash=passwords.hash(password)
    )
    session.add(user)
    session.flush()
    session.add(
        UserIdentity(
            user_id=user.id,
            identity_type="STAFF",
            identity_no=f"BOOTSTRAP-{user.id}",
            department_code="OPS",
        )
    )
    session.add(
        UserRole(user_id=user.id, role_code="SYS_ADMIN", scope_type="GLOBAL", scope_value="*")
    )
    record_operation(
        session,
        module="D",
        business_id=user.id,
        action="ADMIN_BOOTSTRAPPED",
        actor_id=user.id,
        note="Explicit deployment bootstrap; new account only",
    )
    return user


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name", required=True)
    args = parser.parse_args()
    with get_session_factory()() as session, session.begin():
        bootstrap_admin(
            session,
            username=args.username,
            display_name=args.display_name,
            password=os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", ""),
        )
    print("Bootstrap checked; existing accounts and grants were preserved.")


if __name__ == "__main__":
    main()
