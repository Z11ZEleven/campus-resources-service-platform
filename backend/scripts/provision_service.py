"""Provision D service credentials from deployment environment, never command arguments.

Run from backend: python -m scripts.provision_service --help.
The operator must supply an active D administrator user ID and explicit room IDs.
"""

import argparse
import os

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.database import get_session_factory
from app.modules.audit.service import record_operation
from app.modules.auth.models import ServicePrincipal, ServiceRoomScope
from app.modules.auth.permissions import load_principal
from app.modules.auth.sessions import credential_hash
from app.modules.resources.models import Room


def provision_service(
    session: Session,
    *,
    actor_id: int,
    name: str,
    module: str,
    token: str,
    room_ids: list[int],
    rotate: bool = False,
    disable: bool = False,
) -> ServicePrincipal:
    actor = load_principal(session, actor_id)
    actor.require("auth:manage")
    if module not in {"M1", "M2", "M3"} or not name or len(name) > 64:
        raise ValueError("Invalid service name or module")
    if len(token) < 32:
        raise ValueError("SERVICE_AUTH_TOKEN must contain at least 32 characters")
    scopes = set(room_ids)
    if not scopes and not disable:
        raise ValueError("At least one explicit room scope is required")
    rooms = list(
        session.scalars(select(Room).where(Room.id.in_(scopes)).order_by(Room.id).with_for_update())
    )
    if {room.id for room in rooms} != scopes:
        raise ValueError("Room scope contains nonexistent resources")
    row = session.scalar(
        select(ServicePrincipal).where(ServicePrincipal.name == name).with_for_update()
    )
    digest = credential_hash(token)
    if row is None:
        row = ServicePrincipal(name=name, module=module, token_hash=digest, is_active=not disable)
        session.add(row)
        session.flush()
    else:
        if row.module != module:
            raise ValueError("Service module binding cannot be changed")
        if row.token_hash != digest and not rotate:
            raise ValueError("Credential change requires --rotate")
        row.token_hash = digest
        row.is_active = not disable
    existing = set(
        session.scalars(
            select(ServiceRoomScope.room_id).where(ServiceRoomScope.service_id == row.id)
        )
    )
    if existing != scopes:
        session.execute(delete(ServiceRoomScope).where(ServiceRoomScope.service_id == row.id))
        session.add_all(
            ServiceRoomScope(service_id=row.id, room_id=room_id) for room_id in sorted(scopes)
        )
    record_operation(
        session,
        module="D",
        business_id=row.id,
        action="SERVICE_DISABLED" if disable else "SERVICE_CONFIGURED",
        actor_id=actor_id,
        note=f"module={module};rooms={','.join(map(str, sorted(scopes)))}",
    )
    session.flush()
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor-id", type=int, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--module", choices=["M1", "M2", "M3"], required=True)
    parser.add_argument("--room-id", type=int, action="append", default=[])
    parser.add_argument("--rotate", action="store_true")
    parser.add_argument("--disable", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("SERVICE_AUTH_TOKEN", "")
    with get_session_factory()() as session, session.begin():
        provision_service(
            session,
            actor_id=args.actor_id,
            name=args.name,
            module=args.module,
            token=token,
            room_ids=args.room_id,
            rotate=args.rotate,
            disable=args.disable,
        )
    print("Service configuration applied; credentials are not displayed.")


if __name__ == "__main__":
    main()
