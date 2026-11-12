"""Grant or revoke ROOM_ADMIN's M1 code rotation permission explicitly.

This administrative CLI requires trusted deployment access; --actor-id records an
active global auth administrator but does not itself authenticate the operator.
"""

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session_factory
from app.modules.audit.service import record_operation
from app.modules.auth.models import RolePermission
from app.modules.auth.permissions import load_principal


def set_rotation_permission(session: Session, *, actor_id: int, grant: bool) -> bool:
    actor = load_principal(session, actor_id)
    actor.require("auth:manage")
    row = session.scalar(
        select(RolePermission)
        .where(
            RolePermission.role_code == "ROOM_ADMIN",
            RolePermission.permission_code == "m1:checkin_code:rotate",
        )
        .with_for_update()
    )
    if bool(row) == grant:
        return False
    if grant:
        session.add(
            RolePermission(role_code="ROOM_ADMIN", permission_code="m1:checkin_code:rotate")
        )
    else:
        session.delete(row)
    record_operation(
        session,
        module="D",
        business_id=actor_id,
        action="M1_ROTATE_PERMISSION_GRANTED" if grant else "M1_ROTATE_PERMISSION_REVOKED",
        actor_id=actor_id,
        note="role=ROOM_ADMIN;permission=m1:checkin_code:rotate",
    )
    session.flush()
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor-id", type=int, required=True)
    parser.add_argument("action", choices=["grant", "revoke"])
    args = parser.parse_args()
    with get_session_factory()() as session, session.begin():
        changed = set_rotation_permission(
            session, actor_id=args.actor_id, grant=args.action == "grant"
        )
    print("Permission changed." if changed else "Permission already in requested state.")


if __name__ == "__main__":
    main()
